"""Capture public articles and GitHub repositories as local Reader documents.

Reads one public HTTP(S) URL and optional GitHub API responses. Returns a
bounded capture dictionary; Library writes it below
<library>/source_cache/<URL digest>/<content digest>/ as source.html,
document.json, and optional image files.
"""

import base64
import hashlib
import ipaddress
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
import yaml
from bs4 import BeautifulSoup


TRACKING_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src",
}


class SourceCaptureError(RuntimeError):
    """Report a URL or public-content capture that cannot be completed safely."""


def load_web_source_config():
    path = os.path.join(os.path.dirname(__file__), "configs", "config.yaml")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["web_sources"]


def request_headers(accept="text/html,application/xhtml+xml"):
    return {
        "Accept": accept,
        "User-Agent": "CorpusCabinet/0.1 (+local research source capture)",
    }


def canonicalize_source_url(value):
    """Return a stable public HTTP(S) URL without fragments or tracking data."""
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SourceCaptureError("Enter a complete public http:// or https:// URL.")
    if parsed.username or parsed.password:
        raise SourceCaptureError("URLs containing usernames or passwords are not supported.")
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise SourceCaptureError("Local-network URLs are not supported.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise SourceCaptureError("Local or private network addresses are not supported.")
    port = parsed.port
    if port and not ((parsed.scheme == "http" and port == 80) or
                     (parsed.scheme == "https" and port == 443)):
        hostname += ":" + str(port)
    parameters = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        if name.casefold().startswith("utm_") or name.casefold() in TRACKING_PARAMETERS:
            continue
        parameters.append((name, value))
    path = parsed.path or "/"
    if hostname == "github.com":
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            path = "/" + parts[0] + "/" + parts[1].removesuffix(".git")
            parameters = []
    return urlunsplit((parsed.scheme, hostname, path, urlencode(parameters), ""))


def github_repository_identity(url):
    """Return owner/repository for a GitHub repository root URL, or None."""
    parsed = urlsplit(canonicalize_source_url(url))
    if parsed.hostname != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    owner = parts[0]
    repository = parts[1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        return None
    return owner + "/" + repository


def identify_source_type(url):
    if github_repository_identity(url):
        return "github_repository"
    return "article"


def web_content_category(url, content):
    """Distinguish research project pages from general web articles."""
    hostname = (urlsplit(str(url or "")).hostname or "").casefold()
    if hostname in {"arxiv.org", "www.arxiv.org", "openreview.net"}:
        return "research"
    if not content:
        return "web"
    soup = BeautifulSoup(content, "html.parser")
    if soup.select_one('meta[name^="citation_"]'):
        return "research"
    headings = {
        " ".join(heading.get_text(" ", strip=True).casefold().split())
        for heading in soup.select("h1,h2,h3,h4")
    }
    has_abstract = "abstract" in headings
    has_bibtex = "bibtex" in headings or bool(
        re.search(r"@(article|inproceedings|conference|misc)\s*\{", soup.get_text(" "), re.I)
    )
    has_research_link = False
    for link in soup.select("a[href]"):
        target = urlsplit(urljoin(url, link.get("href", ""))).hostname or ""
        target = target.casefold()
        if target in {"arxiv.org", "www.arxiv.org", "openreview.net", "doi.org"}:
            has_research_link = True
            break
    if has_abstract and (has_bibtex or has_research_link):
        return "research"
    return "web"


def checked_response(response, max_bytes, expected_types=None):
    """Validate a bounded successful response before its content is parsed."""
    response.raise_for_status()
    content = response.content
    if len(content) > max_bytes:
        raise SourceCaptureError("The source is larger than the configured capture limit.")
    if expected_types:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].casefold()
        if content_type and content_type not in expected_types:
            raise SourceCaptureError("The URL did not return supported web content.")
    return content


def meta_value(soup, *selectors):
    for selector in selectors:
        tag = soup.select_one(selector)
        if not tag:
            continue
        value = tag.get("content", "").strip()
        if value:
            return value
    return ""


def meta_values(soup, selector):
    values = []
    for tag in soup.select(selector):
        value = tag.get("content", "").strip()
        if value and value not in values:
            values.append(value)
    return values


def published_year(value):
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    if match:
        return int(match.group())
    return None


def block_record(block_id, kind, text, role="body", html_value=""):
    block = {
        "id": block_id,
        "kind": kind,
        "role": role,
        "text": " ".join(str(text or "").split()),
        "links": [],
    }
    if html_value:
        block["html"] = html_value
    return block


def article_blocks(container, title):
    """Extract readable, ordered blocks without navigation or duplicated nesting."""
    blocks = [block_record(1, "title", title, "front_matter")]
    block_id = 2
    tags = container.find_all(["h1", "h2", "h3", "h4", "p", "pre", "blockquote", "li", "img"])
    for tag in tags:
        if tag.name == "img":
            source = str(tag.get("src") or tag.get("data-src") or "").strip()
            if not source:
                continue
            caption = str(tag.get("alt") or "").strip()
            blocks.append({
                "id": block_id,
                "kind": "picture",
                "role": "body",
                "text": caption,
                "links": [],
                "remote_asset": source,
            })
            block_id += 1
            continue
        text = " ".join(tag.get_text(" ", strip=True).split())
        if not text:
            continue
        if tag.name in ("h1", "h2", "h3", "h4"):
            kind = "section_header"
        elif tag.name == "pre":
            kind = "code"
        elif tag.name == "blockquote":
            kind = "blockquote"
        else:
            kind = "paragraph"
        if blocks and blocks[-1].get("text") == text:
            continue
        blocks.append(block_record(block_id, kind, text))
        block_id += 1
    return blocks


def append_markdown_block(blocks, block_id, kind, lines):
    """Append one accumulated Markdown block and return the next block id."""
    if kind == "code":
        text = "\n".join(lines).strip()
    else:
        text = " ".join(lines).strip()
    lines.clear()
    if not text:
        return block_id
    blocks.append(block_record(block_id, kind, text))
    return block_id + 1


def markdown_blocks(markdown, title):
    """Convert README Markdown into conservative readable text blocks."""
    blocks = [block_record(1, "title", title, "front_matter")]
    block_id = 2
    paragraph = []
    code = []
    in_code = False

    for raw_line in str(markdown or "").splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                block_id = append_markdown_block(blocks, block_id, "code", code)
            else:
                block_id = append_markdown_block(blocks, block_id, "paragraph", paragraph)
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            block_id = append_markdown_block(blocks, block_id, "paragraph", paragraph)
            text = re.sub(r"[*_`]+", "", heading.group(2)).strip()
            blocks.append(block_record(block_id, "section_header", text))
            block_id += 1
            continue
        if not line.strip():
            block_id = append_markdown_block(blocks, block_id, "paragraph", paragraph)
            continue
        cleaned = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
        cleaned = re.sub(r"^\s*[-*+]\s+", "• ", cleaned)
        cleaned = re.sub(r"^\s*\d+[.)]\s+", "", cleaned)
        cleaned = re.sub(r"[*_`]+", "", cleaned).strip()
        if cleaned:
            paragraph.append(cleaned)
    block_id = append_markdown_block(blocks, block_id, "paragraph", paragraph)
    append_markdown_block(blocks, block_id, "code", code)
    return blocks


def download_article_images(blocks, page_url, get, config):
    """Download a small bounded set of article images for offline Reader use."""
    assets = []
    count = 0
    for block in blocks:
        remote_asset = block.pop("remote_asset", "")
        if not remote_asset or count >= config["max_images"]:
            continue
        image_url = urljoin(page_url, remote_asset)
        try:
            parsed = urlsplit(image_url)
            if parsed.scheme not in ("http", "https"):
                continue
            response = get(
                image_url,
                headers=request_headers("image/*"),
                timeout=config["timeout_seconds"],
            )
            content = checked_response(response, config["max_image_bytes"])
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].casefold()
            extensions = {
                "image/gif": ".gif",
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }
            extension = extensions.get(content_type)
            if not extension:
                continue
            filename = "image-" + str(count + 1) + extension
            block["asset"] = filename
            assets.append({"filename": filename, "content": content})
            count += 1
        except (OSError, requests.RequestException, SourceCaptureError):
            continue
    return assets


def capture_article(url, config, get=requests.get):
    response = get(
        url,
        headers=request_headers(),
        timeout=config["timeout_seconds"],
    )
    content = checked_response(
        response,
        config["max_html_bytes"],
        {"text/html", "application/xhtml+xml"},
    )
    category = web_content_category(response.url or url, content)
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.select("script,style,noscript,nav,header,footer,form,aside"):
        tag.decompose()
    canonical = canonicalize_source_url(response.url or url)
    canonical_tag = soup.select_one('link[rel="canonical"]')
    if canonical_tag and canonical_tag.get("href"):
        candidate = canonicalize_source_url(urljoin(canonical, canonical_tag["href"]))
        if urlsplit(candidate).hostname == urlsplit(canonical).hostname:
            canonical = candidate
    title = meta_value(
        soup,
        'meta[name="citation_title"]',
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
    )
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = " ".join(title.split())
    if not title:
        raise SourceCaptureError("The page does not expose a readable title.")
    citation_authors = meta_values(soup, 'meta[name="citation_author"]')
    if citation_authors:
        author = ", ".join(citation_authors)
    else:
        author = meta_value(soup, 'meta[name="author"]', 'meta[property="article:author"]')
    site_name = meta_value(
        soup,
        'meta[name="citation_conference_title"]',
        'meta[name="citation_journal_title"]',
        'meta[property="og:site_name"]',
    )
    if not site_name:
        site_name = urlsplit(canonical).hostname.removeprefix("www.")
    published = meta_value(
        soup,
        'meta[name="citation_publication_date"]',
        'meta[name="citation_date"]',
        'meta[property="article:published_time"]',
        'meta[name="date"]',
        'meta[name="datePublished"]',
    )
    description = meta_value(
        soup,
        'meta[name="citation_abstract"]',
        'meta[name="description"]',
        'meta[property="og:description"]',
    )
    container = soup.find("article") or soup.find("main") or soup.select_one('[role="main"]') or soup.body
    if not container:
        raise SourceCaptureError("The page does not contain readable article content.")
    blocks = article_blocks(container, title)
    text = "\n\n".join(block["text"] for block in blocks if block.get("text"))
    if len(text) < config["minimum_text_characters"]:
        raise SourceCaptureError("The page did not expose enough readable article content.")
    assets = download_article_images(blocks, canonical, get, config)
    if category == "research":
        engine = "Research project page"
        source = "Research project page"
    else:
        engine = "Web article"
        source = "Web article"
    document = {
        "version": config["cache_version"],
        "engine": engine,
        "title": title,
        "source_url": canonical,
        "partial": False,
        "warnings": [],
        "blocks": blocks,
    }
    return {
        "source_type": "article",
        "title": title,
        "authors": author,
        "venue": site_name,
        "year": published_year(published),
        "abstract": description,
        "canonical_url": canonical,
        "external_url": canonical,
        "source": source,
        "extracted_text": text[:config["max_index_characters"]],
        "source_metadata": {
            "published": published,
            "site_name": site_name,
            "content_category": category,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
        "source_html": content,
        "document": document,
        "assets": assets,
    }


def github_json(url, get, config):
    response = get(
        url,
        headers=request_headers("application/vnd.github+json"),
        timeout=config["timeout_seconds"],
    )
    content = checked_response(response, config["max_html_bytes"])
    try:
        return response.json()
    except ValueError as error:
        raise SourceCaptureError("GitHub returned an unreadable README response.") from error


def capture_github_repository(url, config, get=requests.get):
    identity = github_repository_identity(url)
    if not identity:
        raise SourceCaptureError("Enter a GitHub repository root URL.")
    owner, repository = identity.split("/", 1)
    api_root = "https://api.github.com/repos/" + owner + "/" + repository
    try:
        readme = github_json(api_root + "/readme", get, config)
        encoded_readme = str(readme.get("content") or "").replace("\n", "")
        readme_text = base64.b64decode(encoded_readme, validate=True).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, TypeError) as error:
        raise SourceCaptureError("The repository README could not be decoded.") from error
    except (requests.RequestException, SourceCaptureError) as error:
        raise SourceCaptureError(
            "This repository does not expose a public README that can be saved."
        ) from error
    canonical = "https://github.com/" + identity
    title = identity
    blocks = markdown_blocks(readme_text, title)
    text = "\n\n".join(block["text"] for block in blocks if block.get("text"))
    document = {
        "version": config["cache_version"],
        "engine": "GitHub README",
        "title": title,
        "source_url": canonical,
        "partial": False,
        "warnings": [],
        "blocks": blocks,
    }
    source_metadata = {
        "owner": owner,
        "repository": repository,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "source_type": "github_repository",
        "title": title,
        "authors": owner,
        "venue": "GitHub",
        "year": None,
        "abstract": "",
        "canonical_url": canonical,
        "external_url": canonical,
        "external_id": identity.casefold(),
        "source": "GitHub",
        "extracted_text": text[:config["max_index_characters"]],
        "source_metadata": source_metadata,
        "source_html": readme_text.encode("utf-8"),
        "document": document,
        "assets": [],
    }


def capture_web_source(url, config, get=requests.get):
    """Recognize and capture one public article or GitHub repository."""
    canonical = canonicalize_source_url(url)
    if identify_source_type(canonical) == "github_repository":
        return capture_github_repository(canonical, config, get)
    return capture_article(canonical, config, get)


def capture_digest(capture):
    """Return a stable short digest for one immutable captured representation."""
    digest = hashlib.sha256()
    identity = (
        capture.get("canonical_url")
        or capture.get("external_id")
        or capture.get("title")
        or "source"
    )
    digest.update(str(identity).encode("utf-8"))
    digest.update(capture.get("source_html", b""))
    return digest.hexdigest()[:20]


def safe_asset_name(value):
    return os.path.basename(str(value or ""))
