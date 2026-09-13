"""Verify local BibTeX drafts and DOI content-negotiation responses."""

from corpus_cabinet.citations import fetch_doi_bibtex, generate_bibtex


class FakeResponse:
    """Return deterministic DOI BibTeX."""

    text = "@article{registered, title={Registered title}}"

    def raise_for_status(self):
        return None


class FakeSession:
    """Capture one DOI content-negotiation request."""

    def __init__(self):
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        return FakeResponse()


def test_generate_bibtex_creates_conservative_draft():
    bibtex = generate_bibtex(
        {
            "title": "Research & Development",
            "authors": "Jane Doe, John Roe",
            "conference": "Example Conference",
            "year": 2025,
            "doi": "10.1000/example",
        }
    )

    assert bibtex.startswith("@misc{doe2025Research,")
    assert "title = {Research \\& Development}" in bibtex
    assert "author = {Jane Doe and John Roe}" in bibtex
    assert "howpublished = {Example Conference}" in bibtex
    assert "doi = {10.1000/example}" in bibtex


def test_fetch_doi_bibtex_requests_registered_metadata():
    session = FakeSession()

    bibtex = fetch_doi_bibtex(
        "10.1000/example",
        {"crossref_mailto": "researcher@example.org"},
        session,
    )

    assert bibtex.startswith("@article")
    assert session.calls[0][0] == "https://doi.org/10.1000/example"
    assert session.calls[0][1]["Accept"] == "application/x-bibtex"
    assert "researcher@example.org" in session.calls[0][1]["User-Agent"]
