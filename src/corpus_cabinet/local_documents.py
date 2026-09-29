"""Prepare local HTML and Markdown for immutable library archival.

Reads one `.html`, `.htm`, `.md`, or `.markdown` file plus bounded relative
assets. Returns an in-memory capture; Library writes the exact original,
interactive HTML, assets, and normalized `document.json` below
`<library>/source_cache/`.
"""

import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from corpus_cabinet.web_sources import (
    block_record,
    load_web_source_config,
    markdown_blocks,
    meta_value,
)


SUPPORTED_DOCUMENT_EXTENSIONS = (".html", ".htm", ".md", ".markdown")


class LocalDocumentError(RuntimeError):
    """Report a local document that cannot be archived safely."""


def document_format(path):
    extension = os.path.splitext(path)[1].casefold()
    if extension in (".html", ".htm"):
        return "html"
    if extension in (".md", ".markdown"):
        return "markdown"
    raise LocalDocumentError("Choose an HTML or Markdown document.")


def read_document_bytes(path, max_bytes):
    if not os.path.isfile(path):
        raise LocalDocumentError("The selected document could not be found.")
    size = os.path.getsize(path)
    if size > max_bytes:
        raise LocalDocumentError("The document is larger than the configured import limit.")
    with open(path, "rb") as handle:
        return handle.read()


def decode_document(content):
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


def title_from_filename(path):
    title = os.path.splitext(os.path.basename(path))[0]
    return " ".join(title.replace("_", " ").replace("-", " ").split())


def local_asset_path(document_path, reference):
    parsed = urlsplit(str(reference or "").strip())
    if parsed.scheme or parsed.netloc or not parsed.path:
        return ""
    if parsed.path.startswith("/") or parsed.path.startswith("#"):
        return ""
    source_root = os.path.dirname(os.path.abspath(document_path))
    candidate = os.path.abspath(
        os.path.join(source_root, unquote(parsed.path))
    )
    try:
        inside_root = os.path.commonpath([source_root, candidate]) == source_root
    except ValueError:
        inside_root = False
    if not inside_root or not os.path.isfile(candidate):
        return ""
    return candidate


def asset_extension(path):
    extension = os.path.splitext(path)[1].casefold()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        return extension
    return ".bin"


def package_html_assets(soup, path, config):
    assets = []
    asset_paths = {}
    references = (
        ("img", "src"),
        ("script", "src"),
        ("link", "href"),
        ("source", "src"),
        ("video", "poster"),
    )
    for tag_name, attribute in references:
        for tag in soup.find_all(tag_name):
            value = str(tag.get(attribute) or "").strip()
            source_path = local_asset_path(path, value)
            if not source_path:
                continue
            if source_path in asset_paths:
                tag[attribute] = asset_paths[source_path]
                continue
            if len(assets) >= config["max_images"]:
                continue
            if os.path.getsize(source_path) > config["max_image_bytes"]:
                continue
            filename = "asset-" + str(len(assets) + 1) + asset_extension(source_path)
            with open(source_path, "rb") as handle:
                content = handle.read()
            assets.append({"filename": filename, "content": content})
            asset_paths[source_path] = filename
            tag[attribute] = filename
    return assets


def data_image_asset(value, count, max_bytes):
    match = re.fullmatch(
        r"data:image/(png|jpeg|jpg|gif|webp);base64,(.+)",
        str(value or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    try:
        content = base64.b64decode(match.group(2), validate=True)
    except (ValueError, TypeError):
        return None
    if len(content) > max_bytes:
        return None
    extension = match.group(1).casefold()
    if extension == "jpeg":
        extension = "jpg"
    return {
        "filename": "embedded-image-" + str(count) + "." + extension,
        "content": content,
    }


def local_html_blocks(container, title, assets, config):
    blocks = [block_record(1, "title", title, "front_matter")]
    block_id = 2
    tags = container.find_all(
        ["h1", "h2", "h3", "h4", "p", "pre", "blockquote", "li", "img", "table"]
    )
    for tag in tags:
        if tag.name == "img":
            source = str(tag.get("src") or "").strip()
            caption = " ".join(str(tag.get("alt") or "").split())
            asset_name = ""
            if re.fullmatch(r"(?:asset|embedded-image)-[^/]+", source):
                asset_name = source
            elif source.startswith("data:image/"):
                asset = data_image_asset(
                    source,
                    len(assets) + 1,
                    config["max_image_bytes"],
                )
                if asset and len(assets) < config["max_images"]:
                    assets.append(asset)
                    asset_name = asset["filename"]
            if asset_name:
                blocks.append({
                    "id": block_id,
                    "kind": "picture",
                    "role": "body",
                    "text": caption,
                    "links": [],
                    "asset": asset_name,
                })
                block_id += 1
            continue
        if tag.name == "table":
            rows = []
            for row in tag.find_all("tr"):
                cells = [
                    " ".join(cell.get_text(" ", strip=True).split())
                    for cell in row.find_all(["th", "td"])
                ]
                if cells:
                    rows.append(" | ".join(cells))
            text = "\n".join(rows)
            kind = "code"
        else:
            text = tag.get_text(" ", strip=True)
            text = " ".join(text.split())
            if tag.name in ("h1", "h2", "h3", "h4"):
                kind = "section_header"
            elif tag.name == "pre":
                kind = "code"
            elif tag.name == "blockquote":
                kind = "blockquote"
            else:
                kind = "paragraph"
        if not text:
            continue
        if blocks and blocks[-1].get("text") == text:
            continue
        blocks.append(block_record(block_id, kind, text))
        block_id += 1
    return blocks


def first_body_paragraph(blocks):
    for block in blocks:
        if block.get("kind") == "paragraph" and block.get("text"):
            return block["text"][:600]
    return ""


def markdown_image_assets(markdown, path, blocks, config):
    assets = []
    for alt_text, reference in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", markdown):
        source_path = local_asset_path(path, reference.split()[0])
        if not source_path or len(assets) >= config["max_images"]:
            continue
        if os.path.getsize(source_path) > config["max_image_bytes"]:
            continue
        filename = "asset-" + str(len(assets) + 1) + asset_extension(source_path)
        with open(source_path, "rb") as handle:
            content = handle.read()
        assets.append({"filename": filename, "content": content})
        blocks.append({
            "id": len(blocks) + 1,
            "kind": "picture",
            "role": "body",
            "text": " ".join(alt_text.split()),
            "links": [],
            "asset": filename,
        })
    return assets


def capture_html_document(path, content, text, config):
    original_soup = BeautifulSoup(text, "html.parser")
    title = ""
    if original_soup.title:
        title = original_soup.title.get_text(" ", strip=True)
    if not title:
        heading = original_soup.find("h1")
        if heading:
            title = heading.get_text(" ", strip=True)
    title = " ".join(title.split()) or title_from_filename(path)

    interactive_soup = BeautifulSoup(text, "html.parser")
    assets = package_html_assets(interactive_soup, path, config)
    interactive_html = str(interactive_soup).encode("utf-8")
    interactive = bool(
        original_soup.find(["script", "input", "button", "select", "canvas", "textarea"])
    )

    reader_soup = BeautifulSoup(interactive_html, "html.parser")
    for tag in reader_soup.select("script,style,noscript,nav,form"):
        tag.decompose()
    container = reader_soup.find("main") or reader_soup.find("article") or reader_soup.body
    if not container:
        raise LocalDocumentError("The HTML document does not contain readable content.")
    blocks = local_html_blocks(container, title, assets, config)
    extracted_text = "\n\n".join(
        block["text"] for block in blocks if block.get("text")
    )
    if len(extracted_text) < config["minimum_text_characters"]:
        raise LocalDocumentError("The HTML document does not contain enough readable text.")
    author = meta_value(
        original_soup,
        'meta[name="author"]',
        'meta[property="article:author"]',
    )
    return title, author, blocks, extracted_text, assets, interactive_html, interactive


def capture_markdown_document(path, text, config):
    heading = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if heading:
        title = re.sub(r"[*_`]+", "", heading.group(1)).strip()
    else:
        title = title_from_filename(path)
    blocks = markdown_blocks(text, title)
    assets = markdown_image_assets(text, path, blocks, config)
    extracted_text = "\n\n".join(
        block["text"] for block in blocks if block.get("text")
    )
    if len(extracted_text) < config["minimum_text_characters"]:
        raise LocalDocumentError("The Markdown document does not contain enough readable text.")
    return title, blocks, extracted_text, assets


def capture_local_document(path, config=None):
    """Return an immutable capture for one local HTML or Markdown document."""
    if config is None:
        config = load_web_source_config()
    path = os.path.abspath(os.path.expanduser(path))
    source_format = document_format(path)
    content = read_document_bytes(path, config["max_html_bytes"])
    text = decode_document(content)
    author = ""
    interactive_html = b""
    interactive = False
    if source_format == "html":
        (
            title,
            author,
            blocks,
            extracted_text,
            assets,
            interactive_html,
            interactive,
        ) = capture_html_document(path, content, text, config)
    else:
        title, blocks, extracted_text, assets = capture_markdown_document(
            path, text, config
        )
    content_hash = hashlib.sha256(content).hexdigest()
    captured_at = datetime.now(timezone.utc).isoformat()
    document = {
        "version": config["cache_version"],
        "engine": "Local " + source_format.title(),
        "title": title,
        "source_url": "",
        "partial": False,
        "warnings": [],
        "blocks": blocks,
    }
    return {
        "source_type": "document",
        "title": title,
        "authors": author,
        "venue": "Local " + source_format.title(),
        "year": None,
        "abstract": first_body_paragraph(blocks),
        "canonical_url": "",
        "external_url": "",
        "external_id": content_hash,
        "source": "Local " + source_format.title(),
        "extracted_text": extracted_text[:config["max_index_characters"]],
        "source_metadata": {
            "original_filename": os.path.basename(path),
            "source_format": source_format,
            "interactive": interactive,
            "captured_at": captured_at,
        },
        "source_html": content,
        "source_extension": os.path.splitext(path)[1].casefold(),
        "interactive_html": interactive_html,
        "content_hash": content_hash,
        "document": document,
        "assets": assets,
    }
