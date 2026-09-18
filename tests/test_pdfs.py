"""Verify deterministic local PDF metadata and text helpers."""

from corpus_cabinet.pdfs import (
    find_abstract,
    find_authors,
    find_largest_text,
)


class FakeRect:
    """Provide the page height used by header extraction."""

    height = 792


class FakePage:
    """Return a controlled PyMuPDF-style text dictionary."""

    def __init__(self, blocks):
        self.blocks = blocks
        self.rect = FakeRect()

    def get_text(self, output_format):
        assert output_format == "dict"
        return {"blocks": self.blocks}


def text_line(text, size, top, spans=None):
    """Create one PyMuPDF-style text line."""
    if spans is None:
        spans = [{"text": text, "size": size}]
    return {
        "bbox": (0, top, 500, top + size),
        "spans": spans,
    }


def text_block(lines, top, bottom):
    """Create one PyMuPDF-style text block."""
    return {
        "type": 0,
        "bbox": (0, top, 500, bottom),
        "lines": lines,
    }


def test_pdf_header_reconstructs_spans_authors_and_abstract():
    title_spans = [
        {"text": "Riemannian", "size": 24},
        {"text": " ", "size": 24},
        {"text": "Motion", "size": 24},
        {"text": " ", "size": 24},
        {"text": "Policies", "size": 24},
    ]
    blocks = [
        text_block([text_line("", 24, 60, title_spans)], 60, 84),
        text_block([text_line("Nathan D. Ratliff", 11, 101)], 101, 112),
        text_block([text_line("NVIDIA", 9, 115)], 115, 124),
        text_block([text_line("Jan Issac", 11, 101)], 101, 112),
        text_block(
            [
                text_line("Abstract-We introduce a modular liter-", 9, 178),
                text_line("ature for motion policies.", 9, 188),
            ],
            178,
            198,
        ),
    ]
    page = FakePage(blocks)

    assert find_largest_text(page) == "Riemannian Motion Policies"
    assert find_authors(page) == "Nathan D. Ratliff, Jan Issac"
    assert find_abstract(page) == (
        "We introduce a modular literature for motion policies."
    )


def test_pdf_abstract_can_follow_a_standalone_heading():
    blocks = [
        text_block([text_line("A Paper Title", 24, 60)], 60, 84),
        text_block([text_line("Abstract", 14, 200)], 200, 218),
        text_block(
            [text_line("First abstract block.", 10, 230)],
            230,
            242,
        ),
        text_block(
            [text_line("Second abstract block.", 10, 245)],
            245,
            257,
        ),
        text_block([text_line("1 Introduction", 14, 270)], 270, 288),
        text_block(
            [text_line("This is not part of the abstract.", 10, 295)],
            295,
            307,
        ),
    ]
    page = FakePage(blocks)

    assert find_abstract(page) == (
        "First abstract block. Second abstract block."
    )
