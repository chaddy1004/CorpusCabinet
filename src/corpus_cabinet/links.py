"""Inspect public scholarly project pages for paper metadata and links.

The module reads one public HTTP(S) page with a bounded response size. It
writes no files and returns title, author, abstract, DOI, arXiv, and PDF fields.
"""

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests


class LinkImportError(RuntimeError):
    """Raised when a link cannot be safely inspected as a project page."""


class ProjectPageParser(HTMLParser):
    """Collect common citation metadata and visible project-page structure."""

    def __init__(self):
        super().__init__()
        self.meta = {}
        self.links = []
        self.page_title = ""
        self.first_heading = ""
        self.abstract_paragraphs = []
        self.in_abstract = False
        self.capture_tag = ""
        self.capture_kind = ""
        self.capture_parts = []
        self.all_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            name = attributes.get("name") or attributes.get("property") or ""
            content = attributes.get("content") or ""
            name = name.strip().lower()
            content = " ".join(content.split())
            if name and content:
                self.meta.setdefault(name, []).append(content)
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"].strip())
        elif tag == "title" and not self.capture_tag:
            self.start_capture(tag, "title")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if not self.capture_tag:
                self.start_capture(tag, "heading")
        elif tag == "p" and self.in_abstract and not self.capture_tag:
            self.start_capture(tag, "abstract")

    def handle_endtag(self, tag):
        if tag != self.capture_tag:
            return
        value = " ".join(" ".join(self.capture_parts).split())
        if self.capture_kind == "title" and not self.page_title:
            self.page_title = value
        elif self.capture_kind == "heading":
            if self.capture_tag == "h1" and not self.first_heading:
                self.first_heading = value
            normalized = value.strip().lower().rstrip(":")
            if normalized == "abstract":
                self.in_abstract = True
            else:
                self.in_abstract = False
        elif self.capture_kind == "abstract" and value:
            self.abstract_paragraphs.append(value)
        self.capture_tag = ""
        self.capture_kind = ""
        self.capture_parts = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if value:
            self.all_text.append(value)
        if self.capture_tag:
            self.capture_parts.append(data)

    def start_capture(self, tag, kind):
        self.capture_tag = tag
        self.capture_kind = kind
        self.capture_parts = []


def first_meta(meta, names):
    """Return the first populated value among common metadata names."""
    for name in names:
        values = meta.get(name) or []
        if values:
            return values[0]
    return ""


def validate_public_url(url):
    """Reject non-web and non-public destinations before making a request."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LinkImportError("Enter a complete public http:// or https:// URL")
    if parsed.username or parsed.password:
        raise LinkImportError("Links containing credentials are not supported")

    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise LinkImportError("Local network links are not supported")
    try:
        port = parsed.port
    except ValueError as error:
        raise LinkImportError("The link contains an invalid port") from error
    if port is None:
        if parsed.scheme == "https":
            port = 443
        else:
            port = 80
    try:
        addresses = socket.getaddrinfo(hostname, port)
    except socket.gaierror as error:
        raise LinkImportError("The link host could not be resolved") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise LinkImportError("Local or private network links are not supported")
    return parsed.geturl()


def fetch_public_html(url, config=None, session=None):
    """Fetch bounded HTML while validating each redirect destination."""
    if config is None:
        config = {}
    if session is None:
        session = requests.Session()
    current_url = str(url or "").strip()
    max_bytes = config.get("project_page_max_bytes", 2 * 1024 * 1024)
    max_redirects = config.get("project_page_max_redirects", 5)
    timeout = config.get("search_timeout_seconds", 15)

    for redirect_count in range(max_redirects + 1):
        validate_public_url(current_url)
        response = session.get(
            current_url,
            headers={"User-Agent": "CorpusCabinet/0.1"},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            if not location:
                raise LinkImportError("The project page redirect had no destination")
            current_url = urljoin(current_url, location)
            continue
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            raise LinkImportError("The link is not an HTML project page")

        chunks = []
        byte_count = 0
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise LinkImportError("The project page is too large to inspect")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace"), current_url
    raise LinkImportError("The project page redirected too many times")


def normalize_arxiv_link(value):
    """Return a canonical arXiv abstract URL found in a page link."""
    match = re.search(
        r"arxiv\.org/(?:abs|pdf)/([a-z-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return "https://arxiv.org/abs/" + match.group(1)


def arxiv_from_text(value):
    """Return an arXiv URL from visible arXiv or BibTeX eprint text."""
    direct_link = normalize_arxiv_link(value)
    if direct_link:
        return direct_link
    match = re.search(
        r"(?:arxiv\s*:?\s*|eprint\s*=\s*[{\"']?)"
        r"([a-z-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return "https://arxiv.org/abs/" + match.group(1)


def doi_from_link(value):
    """Return a bare DOI from a DOI resolver link or visible text."""
    match = re.search(
        r"(?:doi\.org/)?(10\.\d{4,9}/[^\s\"'<>]+)",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1).rstrip(".,);]")


def project_page_metadata(html_text, page_url):
    """Extract explainable scholarly fields from one HTML document."""
    parser = ProjectPageParser()
    parser.feed(html_text)
    meta = parser.meta

    title = first_meta(meta, ["citation_title", "dc.title"])
    if not title:
        title = parser.first_heading
    if not title:
        title = first_meta(meta, ["og:title"])
    if not title:
        title = parser.page_title
    authors = meta.get("citation_author") or meta.get("dc.creator") or []
    meta_abstract = first_meta(
        meta,
        ["citation_abstract", "dc.description", "description", "og:description"],
    )
    visible_abstract = "\n\n".join(parser.abstract_paragraphs)
    if len(visible_abstract) > len(meta_abstract):
        abstract = visible_abstract
    else:
        abstract = meta_abstract
    venue = first_meta(
        meta,
        ["citation_conference_title", "citation_journal_title"],
    )
    date = first_meta(
        meta,
        ["citation_publication_date", "citation_date", "dc.date"],
    )
    year = None
    year_match = re.search(r"\b(?:19|20)\d{2}\b", date)
    if year_match:
        year = int(year_match.group(0))

    links = []
    for href in parser.links:
        links.append(urljoin(page_url, href))
    arxiv_url = ""
    arxiv_value = first_meta(meta, ["citation_arxiv_id"])
    if arxiv_value:
        arxiv_url = arxiv_from_text("arXiv: " + arxiv_value)
    doi_value = first_meta(meta, ["citation_doi", "dc.identifier"])
    doi = doi_from_link(doi_value)
    pdf_url = first_meta(meta, ["citation_pdf_url"])
    if pdf_url:
        pdf_url = urljoin(page_url, pdf_url)
    for link in links:
        if not arxiv_url:
            arxiv_url = normalize_arxiv_link(link)
        if not doi:
            doi = doi_from_link(link)
        if not pdf_url and re.search(r"\.pdf(?:[?#].*)?$", link, re.IGNORECASE):
            pdf_url = link
    if not arxiv_url:
        arxiv_url = arxiv_from_text(" ".join(parser.all_text))
    if not doi:
        doi = doi_from_link(" ".join(parser.all_text))

    return {
        "title": title,
        "authors": ", ".join(authors),
        "venue": venue,
        "year": year,
        "doi": doi,
        "abstract": abstract,
        "external_url": page_url,
        "project_url": page_url,
        "pdf_url": pdf_url,
        "arxiv_url": arxiv_url,
    }


def inspect_project_page(url, config=None, session=None):
    """Fetch and inspect one public project page."""
    html_text, final_url = fetch_public_html(url, config, session)
    metadata = project_page_metadata(html_text, final_url)
    if not metadata.get("title") and not metadata.get("arxiv_url"):
        raise LinkImportError(
            "No paper title or arXiv link was found on this page"
        )
    return metadata
