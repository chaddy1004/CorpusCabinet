"""Verify deterministic scholarly metadata extraction from project pages."""

import pytest

from corpus_cabinet.links import (
    LinkImportError,
    project_page_metadata,
    validate_public_url,
)


def test_project_page_metadata_reads_citation_tags():
    html_text = """
    <html><head>
      <meta name="citation_title" content="A Project Paper">
      <meta name="citation_author" content="Jane Doe">
      <meta name="citation_author" content="John Roe">
      <meta name="citation_publication_date" content="2025-06-01">
      <meta name="citation_conference_title" content="Example Conference">
      <meta name="citation_doi" content="10.1000/project">
      <meta name="citation_pdf_url" content="paper.pdf">
    </head></html>
    """

    result = project_page_metadata(html_text, "https://papers.example.org/work/")

    assert result["title"] == "A Project Paper"
    assert result["authors"] == "Jane Doe, John Roe"
    assert result["year"] == 2025
    assert result["venue"] == "Example Conference"
    assert result["doi"] == "10.1000/project"
    assert result["pdf_url"] == "https://papers.example.org/work/paper.pdf"


def test_project_page_metadata_falls_back_to_visible_structure_and_arxiv():
    html_text = """
    <html><body>
      <h1>CHIP: A Humanoid Control Paper</h1>
      <a href="https://arxiv.org/abs/2512.14689">arXiv</a>
      <h2>Abstract</h2>
      <p>First abstract paragraph.</p>
      <p>Second abstract paragraph.</p>
      <h2>Method</h2>
      <p>This must not be included.</p>
    </body></html>
    """

    result = project_page_metadata(html_text, "https://example.github.io/CHIP/")

    assert result["title"] == "CHIP: A Humanoid Control Paper"
    assert result["arxiv_url"] == "https://arxiv.org/abs/2512.14689"
    assert result["abstract"] == (
        "First abstract paragraph.\n\nSecond abstract paragraph."
    )


def test_project_page_metadata_reads_arxiv_id_from_visible_bibtex():
    html_text = """
    <html><body>
      <h1>A New Robotics Paper</h1>
      <h2>Abstract</h2>
      <p>A complete visible abstract that is more useful than a meta summary.</p>
      <h2>BibTeX</h2>
      <pre>@misc{paper, eprint={2606.11628}, archivePrefix={arXiv}}</pre>
    </body></html>
    """

    result = project_page_metadata(html_text, "https://paper.github.io/")

    assert result["arxiv_url"] == "https://arxiv.org/abs/2606.11628"


def test_project_page_validation_rejects_localhost():
    with pytest.raises(LinkImportError, match="Local network"):
        validate_public_url("http://localhost:8000/paper")
