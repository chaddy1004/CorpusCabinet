"""Read PDF metadata and bounded text for the desktop application.

This module reads PDF files selected by the user. It writes no files; callers
decide where extracted metadata and text are stored.
"""

import pymupdf


def extract_pdf_metadata(pdf_path):
    """Return a best-effort title, author string, and page count."""
    document = pymupdf.open(pdf_path)
    first_page = document[0]
    page_text = first_page.get_text("text").strip()
    metadata = document.metadata or {}

    title = metadata.get("title", "").strip()
    authors = metadata.get("author", "").strip()

    if not title:
        title = find_largest_text(first_page)

    if not title:
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if lines:
            title = lines[0]

    if not title:
        title = "Untitled paper"

    page_count = len(document)
    document.close()
    return {
        "title": title,
        "authors": authors,
        "page_count": page_count,
    }


def find_largest_text(page):
    """Return the text from the largest-font span on a PDF page."""
    blocks = page.get_text("dict").get("blocks", [])
    best_text = ""
    best_size = 0

    for block in blocks:
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                size = span.get("size", 0)
                if text and size > best_size:
                    best_text = text
                    best_size = size

    return best_text


def extract_pdf_text(pdf_path, max_chars=100000):
    """Extract bounded plain text for search and future AI assistance."""
    document = pymupdf.open(pdf_path)
    parts = []
    total_chars = 0

    for page in document:
        if total_chars >= max_chars:
            break

        page_text = page.get_text("text")
        remaining = max_chars - total_chars
        parts.append(page_text[:remaining])
        total_chars += len(parts[-1])

    document.close()
    return "".join(parts).strip()
