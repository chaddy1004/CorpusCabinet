"""Verify provider parsing, result merging, and explicit Offline Mode."""

import pytest

from corpus_cabinet.search import (
    ARXIV_URL,
    CROSSREF_URL,
    OPENALEX_URL,
    OfflineModeError,
    OnlineSearchService,
    google_scholar_url,
)


ARXIV_RESPONSE = """
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>A Useful Paper</title>
    <published>2024-01-15T00:00:00Z</published>
    <summary>An arXiv abstract.</summary>
    <author><name>Jane Doe</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2401.12345v2" />
  </entry>
</feed>
"""


class FakeResponse:
    """Minimal requests response for provider tests."""

    def __init__(self, json_data=None, text=""):
        self.json_data = json_data
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self.json_data


class FakeSession:
    """Return deterministic Crossref, arXiv, and OpenAlex payloads."""

    def __init__(self):
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        if url == CROSSREF_URL:
            return FakeResponse(
                json_data={
                    "message": {
                        "items": [
                            {
                                "title": ["A Useful Paper"],
                                "author": [
                                    {"given": "Jane", "family": "Doe"}
                                ],
                                "container-title": ["Journal of Examples"],
                                "published": {"date-parts": [[2024]]},
                                "DOI": "10.1000/example",
                                "URL": "https://doi.org/10.1000/example",
                                "abstract": "<p>A Crossref abstract.</p>",
                            }
                        ]
                    }
                }
            )
        if url == ARXIV_URL:
            return FakeResponse(text=ARXIV_RESPONSE)
        if url == OPENALEX_URL:
            return FakeResponse(
                json_data={
                    "results": [
                        {
                            "id": "https://openalex.org/W123",
                            "doi": "https://doi.org/10.1000/example",
                            "display_name": "A Useful Paper",
                            "publication_year": 2024,
                            "authorships": [
                                {"author": {"display_name": "Jane Doe"}}
                            ],
                            "primary_location": {
                                "landing_page_url": "https://example.org/paper",
                                "source": {"display_name": "Journal of Examples"},
                            },
                            "open_access": {
                                "is_oa": True,
                                "oa_url": "https://example.org/paper.pdf",
                            },
                            "best_oa_location": {
                                "pdf_url": "https://example.org/paper.pdf"
                            },
                            "abstract_inverted_index": {
                                "An": [0],
                                "OpenAlex": [1],
                                "abstract.": [2],
                            },
                        }
                    ]
                }
            )
        raise AssertionError("Unexpected URL: " + url)


def test_online_search_normalizes_and_merges_provider_results():
    session = FakeSession()
    service = OnlineSearchService(session=session)

    results = service.search("A Useful Paper")

    assert len(results) == 2
    merged = results[0]
    assert merged["title"] == "A Useful Paper"
    assert merged["doi"] == "10.1000/example"
    assert "Crossref" in merged["source"]
    assert "OpenAlex" in merged["source"]
    assert merged["is_open_access"] is True
    assert results[1]["external_id"] == "2401.12345"
    assert len(session.calls) == 3


def test_offline_mode_makes_no_provider_requests():
    session = FakeSession()
    service = OnlineSearchService(session=session)
    service.set_offline(True)

    with pytest.raises(OfflineModeError):
        service.search("A Useful Paper")

    assert session.calls == []


def test_google_scholar_url_is_a_user_driven_handoff():
    url = google_scholar_url("A Useful Paper")
    assert url == "https://scholar.google.com/scholar?q=A+Useful+Paper"
