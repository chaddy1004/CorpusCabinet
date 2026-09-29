"""Convert official arXiv HTML into the shared comfortable-Reader document.

Reads public arXiv metadata/HTML and raster figure assets. Writes source.html,
document.json, and image assets under <library>/reader_cache/arxiv_html/. Only
HTTPS arxiv.org hosts are accepted. Publisher pages and local PDFs are untouched.
"""

import hashlib
import html
import json
import os
import re
import tempfile
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from corpus_cabinet.search import ArxivProvider, arxiv_query_id
from corpus_cabinet.storage import normalize_paper_title


class ArxivHtmlUnavailable(RuntimeError):
    """The paper has no usable official arXiv HTML representation."""


def allowed_arxiv_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and (host == "arxiv.org" or host.endswith(".arxiv.org"))


def read_limited_response(response, limit):
    declared = response.headers.get("content-length")
    if declared and int(declared) > limit:
        raise ArxivHtmlUnavailable("The arXiv HTML response exceeds the Reader limit.")
    chunks = []
    size = 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if size > limit:
            raise ArxivHtmlUnavailable("The arXiv HTML response exceeds the Reader limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_arxiv(session, url, config, byte_limit):
    if not allowed_arxiv_url(url):
        raise ArxivHtmlUnavailable("An unsafe arXiv URL was rejected.")
    response = session.get(url, timeout=config["html_timeout_seconds"], stream=True,
                           headers={"User-Agent": "CorpusCabinet/0.1 comfortable-reader"})
    if not allowed_arxiv_url(response.url):
        raise ArxivHtmlUnavailable("arXiv redirected outside its official hosts.")
    if response.status_code == 404:
        raise ArxivHtmlUnavailable("No official arXiv HTML version is available.")
    response.raise_for_status()
    return response, read_limited_response(response, byte_limit)


def resolve_arxiv_id(paper, config, session):
    for key in ("external_id", "external_url", "pdf_url"):
        identifier = arxiv_query_id(paper.get(key, ""))
        if identifier:
            return identifier
    title = paper.get("title", "").strip()
    if not title:
        raise ArxivHtmlUnavailable("This paper has no arXiv identifier or searchable title.")
    provider = ArxivProvider({"search_timeout_seconds": config["html_timeout_seconds"],
                              "search_result_limit": 5}, session)
    expected = normalize_paper_title(title)
    for result in provider.search(title):
        if normalize_paper_title(result.get("title", "")) != expected:
            continue
        identifier = arxiv_query_id(
            result.get("external_id") or result.get("external_url")
        )
        if identifier:
            return identifier
    raise ArxivHtmlUnavailable("No exact-title arXiv record was found.")


def html_cache_path(library_path, identifier, config):
    settings = {"version": config["html_cache_version"], "id": identifier}
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    safe_identifier = identifier.replace("/", "_")
    return os.path.join(library_path, "reader_cache", "arxiv_html", safe_identifier, digest)


def mathml_content(element):
    """Convert arXiv's presentation MathML into Qt-supported readable HTML."""
    if isinstance(element, NavigableString):
        return html.escape(str(element))
    if not isinstance(element, Tag):
        return ""
    name = element.name
    children = []
    for child in element.children:
        if isinstance(child, Tag) and child.name == "annotation":
            continue
        children.append(child)
    rendered = [mathml_content(child) for child in children]
    if name == "semantics":
        if rendered:
            return rendered[0]
        return ""
    if name in {"mi", "mn", "mo", "mtext"}:
        return html.escape(element.get_text("", strip=False))
    if name == "msub" and len(rendered) >= 2:
        return rendered[0] + "<sub>" + rendered[1] + "</sub>"
    if name == "msup" and len(rendered) >= 2:
        return rendered[0] + "<sup>" + rendered[1] + "</sup>"
    if name == "msubsup" and len(rendered) >= 3:
        return (rendered[0] + "<sub>" + rendered[1] + "</sub><sup>" +
                rendered[2] + "</sup>")
    if name == "mfrac" and len(rendered) >= 2:
        return "(" + rendered[0] + ")/​(" + rendered[1] + ")"
    if name == "msqrt":
        return "√(" + "".join(rendered) + ")"
    if name == "mroot" and len(rendered) >= 2:
        return "<sup>" + rendered[1] + "</sup>√(" + rendered[0] + ")"
    if name == "mfenced":
        opening = html.escape(element.get("open", "("))
        closing = html.escape(element.get("close", ")"))
        return opening + "".join(rendered) + closing
    if name == "mspace":
        return "&nbsp;"
    if name == "mtr":
        return "[" + ", ".join(rendered) + "]"
    if name == "mtable":
        return "[" + "; ".join(rendered) + "]"
    return "".join(rendered)


def math_html(element):
    """Render one MathML element without exposing its fallback TeX source."""
    content = mathml_content(element)
    if not content:
        content = html.escape(element.get("alttext", ""))
    return ("<span class=\"math\" style=\"font-family: 'Times New Roman', "
            "serif;\">" + content + "</span>")


def inline_content(element, block_id, links):
    output = []
    for child in element.children:
        if isinstance(child, NavigableString):
            output.append(html.escape(str(child)))
            continue
        if not isinstance(child, Tag):
            continue
        if child.name == "math":
            output.append(math_html(child))
            continue
        if child.name == "a":
            href = child.get("href", "")
            content = inline_content(child, block_id, links)
            if href.startswith("#bib."):
                index = len(links)
                links.append({"label": child.get_text(" ", strip=True),
                              "reference_id": href[1:]})
                output.append('<a href="preview:' + str(block_id) + ":" + str(index) + '">' + content + "</a>")
            else:
                output.append(content)
            continue
        content = inline_content(child, block_id, links)
        names = {"em": "em", "i": "em", "strong": "strong", "b": "strong",
                 "sup": "sup", "sub": "sub", "code": "code"}
        if child.name in names:
            tag = names[child.name]
            output.append("<" + tag + ">" + content + "</" + tag + ">")
        elif child.name == "br":
            output.append("<br>")
        else:
            output.append(content)
    return "".join(output)


def add_text_block(blocks, element, kind, role=None):
    links = []
    block_id = len(blocks)
    content = inline_content(element, block_id, links).strip()
    text = " ".join(BeautifulSoup(content, "html.parser").get_text(" ", strip=True).split())
    if not text:
        return
    block = {"id": block_id, "kind": kind, "text": text,
             "html": content, "links": links}
    if role:
        block["role"] = role
    blocks.append(block)


def download_figure(session, url, output_path, index, config):
    response, data = fetch_arxiv(session, url, config, config["max_html_image_bytes"])
    content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if content_type not in extensions:
        raise ArxivHtmlUnavailable("An unsupported arXiv figure format was skipped.")
    asset = "figure-" + str(index) + extensions[content_type]
    with open(os.path.join(output_path, asset), "wb") as handle:
        handle.write(data)
    return asset


def is_nested_candidate(element, candidates):
    parent = element.parent
    while parent is not None:
        if id(parent) in candidates:
            return True
        parent = parent.parent
    return False


def parse_arxiv_html(data, source_url, output_path, config, session):
    soup = BeautifulSoup(data, "html.parser")
    document = soup.select_one("article.ltx_document, article, .ltx_document")
    if document is None:
        raise ArxivHtmlUnavailable("No scientific document was found in the arXiv HTML response.")
    references = {}
    for item in document.select(".ltx_bibitem[id]"):
        references[item.get("id")] = " ".join(item.get_text(" ", strip=True).split())
    selectors = ("h1, h2, h3, h4, h5, h6, p.ltx_p, figure.ltx_figure, "
                 "table.ltx_eqn_table, div.ltx_equation, table.ltx_tabular, "
                 "li.ltx_bibitem")
    elements = document.select(selectors)
    candidate_ids = {id(element) for element in elements}
    blocks = []
    warnings = ["Official arXiv HTML is preferred, but this native rendering remains experimental."]
    figure_count = 0
    os.makedirs(output_path, exist_ok=True)
    for element in elements:
        if is_nested_candidate(element, candidate_ids):
            continue
        classes = set(element.get("class", []))
        if element.name.startswith("h"):
            role = None
            if "ltx_title_document" in classes or element.name == "h1":
                role = "front_matter"
            add_text_block(blocks, element, "section_header", role)
            continue
        if element.name == "figure":
            image = element.find("img")
            if image and figure_count < config["max_html_images"]:
                try:
                    figure_count += 1
                    url = urljoin(source_url, image.get("src", ""))
                    asset = download_figure(session, url, output_path, figure_count, config)
                    blocks.append({"id": len(blocks), "kind": "picture", "text": "",
                                   "asset": asset, "links": []})
                except (requests.RequestException, ArxivHtmlUnavailable) as error:
                    warnings.append(str(error))
            caption = element.select_one("figcaption")
            if caption:
                add_text_block(blocks, caption, "caption")
            continue
        if "ltx_eqn_table" in classes or "ltx_equation" in classes:
            formula = element.find("math")
            if formula:
                content = math_html(formula)
                text = " ".join(
                    BeautifulSoup(content, "html.parser").get_text(" ", strip=True).split()
                )
                blocks.append({"id": len(blocks), "kind": "formula",
                               "text": text, "html": content, "links": []})
            continue
        if element.name == "table":
            rows = []
            for row in element.select("tr"):
                cells = [" ".join(cell.get_text(" ", strip=True).split())
                         for cell in row.select("th, td")]
                if cells:
                    rows.append(" | ".join(cells))
            text = "\n".join(rows)
            if text:
                blocks.append({"id": len(blocks), "kind": "table", "text": text,
                               "html": "<pre>" + html.escape(text) + "</pre>", "links": []})
            continue
        kind = "reference" if element.name == "li" else "text"
        add_text_block(blocks, element, kind)
    if not blocks:
        raise ArxivHtmlUnavailable("The official HTML page contained no readable blocks.")
    return {"version": config["html_cache_version"], "engine": "arXiv HTML",
            "source_url": source_url, "page_count": 0, "pages_converted": 0,
            "partial": False, "warnings": sorted(set(warnings)), "references": references,
            "blocks": blocks}


def convert_arxiv_html(library_path, paper, config):
    session = requests.Session()
    identifier = resolve_arxiv_id(paper, config, session)
    cache_path = html_cache_path(library_path, identifier, config)
    document_path = os.path.join(cache_path, "document.json")
    if os.path.isfile(document_path):
        with open(document_path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["cache_path"] = cache_path
        return document
    url = "https://arxiv.org/html/" + quote(identifier, safe="/")
    response, data = fetch_arxiv(session, url, config, config["max_html_bytes"])
    parent = os.path.dirname(cache_path)
    os.makedirs(parent, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arxiv-html-", dir=parent) as temporary:
        document = parse_arxiv_html(data, response.url, temporary, config, session)
        document["arxiv_id"] = identifier
        with open(os.path.join(temporary, "source.html"), "wb") as handle:
            handle.write(data)
        with open(os.path.join(temporary, "document.json"), "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False)
        if not os.path.exists(cache_path):
            os.rename(temporary, cache_path)
    document["cache_path"] = cache_path
    return document
