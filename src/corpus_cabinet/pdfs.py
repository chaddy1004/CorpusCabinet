"""Read PDF metadata and bounded text for the desktop application.

This module reads PDF files selected by the user. It writes no files; callers
decide where extracted metadata and text are stored.
"""

import re

import pymupdf


def extract_pdf_metadata(pdf_path):
    """Return a best-effort title, author string, and page count."""
    document = pymupdf.open(pdf_path)
    first_page = document[0]
    page_text = first_page.get_text("text").strip()
    metadata = document.metadata or {}

    title = normalize_pdf_text(metadata.get("title", "")).strip()
    authors = normalize_pdf_text(metadata.get("author", "")).strip()

    if not title:
        title = find_largest_text(first_page)

    if not title:
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if lines:
            title = lines[0]

    if not title:
        title = "Untitled paper"

    if not authors:
        authors = find_authors(first_page)

    abstract = find_abstract(first_page)
    arxiv_id = find_arxiv_id(page_text)
    external_url = ""
    pdf_url = ""
    if arxiv_id:
        external_url = "https://arxiv.org/abs/" + arxiv_id
        pdf_url = "https://arxiv.org/pdf/" + arxiv_id

    page_count = len(document)
    document.close()
    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "external_id": arxiv_id,
        "external_url": external_url,
        "pdf_url": pdf_url,
        "metadata_source": "Local PDF",
        "page_count": page_count,
    }


def find_largest_text(page):
    """Return the complete text line containing the largest-font span."""
    blocks = page.get_text("dict").get("blocks", [])
    best_text = ""
    best_size = 0

    for block in blocks:
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            size = line_font_size(line)
            text = line_text(line)
            if text and size > best_size:
                best_text = text
                best_size = size

    return best_text


def find_authors(page):
    """Extract the largest header lines between the title and abstract."""
    blocks = page.get_text("dict").get("blocks", [])
    title_size = 0
    title_bottom = 0
    body_top = page.rect.height * 0.4

    for block in blocks:
        if block.get("type") != 0:
            continue
        size = block_font_size(block)
        if size > title_size:
            title_size = size
            title_bottom = block.get("bbox", (0, 0, 0, 0))[3]
        text = block_text(block)
        if re.match(r"^abstract\b", text, flags=re.IGNORECASE):
            body_top = min(
                body_top,
                block.get("bbox", (0, body_top, 0, 0))[1],
            )

    candidates = []
    author_size = 0
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox", (0, 0, 0, 0))
            if bbox[1] <= title_bottom or bbox[1] >= body_top:
                continue
            text = line_text(line)
            if not text or "@" in text or re.search(r"\d", text):
                continue
            size = line_font_size(line)
            if size > author_size:
                candidates = [text]
                author_size = size
            elif abs(size - author_size) < 0.25:
                candidates.append(text)

    return ", ".join(candidates)


def find_abstract(page):
    """Return an abstract stored in one or more first-page text blocks."""
    blocks = page.get_text("dict").get("blocks", [])
    parts = []
    collecting = False
    for block in blocks:
        if block.get("type") != 0:
            continue
        text = block_text(block)
        if not collecting:
            match = re.match(
                r"^abstract\b\s*(?:[—–:-]\s*)?(.*)$",
                text,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            collecting = True
            if match.group(1).strip():
                parts.append(match.group(1).strip())
            continue

        if abstract_end_marker(text):
            break
        if text:
            parts.append(text)
        if len(" ".join(parts)) >= 8000:
            break

    return " ".join(parts).strip()


def abstract_end_marker(value):
    """Identify common section starts and page furniture after an abstract."""
    text = str(value or "").strip()
    if re.match(r"^\d+$", text):
        return True
    if re.match(r"^arxiv\s*:", text, flags=re.IGNORECASE):
        return True
    if re.match(r"^introduction\b", text, flags=re.IGNORECASE):
        return True
    if re.match(
        r"^(?:[ivxlcdm]+[.)]?|\d+(?:\.\d+)*)\s+introduction\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def find_arxiv_id(value):
    """Return a modern arXiv identifier printed on the first page."""
    match = re.search(
        r"\barxiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1)


def block_text(block):
    """Collapse all lines and spans from one PDF text block."""
    parts = []
    for line in block.get("lines", []):
        text = line_text(line)
        if text:
            parts.append(text)
    text = normalize_pdf_text("\n".join(parts))
    return " ".join(text.split())


def line_text(line):
    """Join every span in one PDF text line."""
    parts = []
    for span in line.get("spans", []):
        parts.append(span.get("text", ""))
    return normalize_pdf_text("".join(parts)).strip()


def block_font_size(block):
    """Return the largest font size used in one text block."""
    size = 0
    for line in block.get("lines", []):
        size = max(size, line_font_size(line))
    return size


def line_font_size(line):
    """Return the largest font size used in one text line."""
    size = 0
    for span in line.get("spans", []):
        size = max(size, span.get("size", 0))
    return size


def normalize_pdf_text(value):
    """Normalize common extraction ligatures and wrapped hyphenation."""
    text = str(value or "")
    replacements = {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
    for source, destination in replacements.items():
        text = text.replace(source, destination)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", text)
    return text


def extract_pdf_text(pdf_path, max_chars=100000):
    """Extract bounded plain text for search and future AI assistance."""
    document = pymupdf.open(pdf_path)
    parts = []
    total_chars = 0

    for page in document:
        if total_chars >= max_chars:
            break

        page_text = normalize_pdf_text(page.get_text("text"))
        remaining = max_chars - total_chars
        parts.append(page_text[:remaining])
        total_chars += len(parts[-1])

    document.close()
    return "".join(parts).strip()
