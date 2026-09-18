"""Verify provider parsing, result merging, and explicit Offline Mode."""

import pytest

import corpus_cabinet.search as search_module

from corpus_cabinet.search import (
    ARXIV_URL,
    CROSSREF_URL,
    OPENALEX_URL,
    OfflineModeError,
    OnlineSearchService,
    deduplicate_results,
    google_scholar_url,
    normalize_search_query,
    rank_search_result,
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


def inspect_project_page(url, config, session):
    """Return deterministic project-page metadata without network access."""
    return {
        "title": "A Project Page Paper",
        "authors": "Jane Doe",
        "abstract": "Readable abstract.",
        "external_url": url,
        "project_url": url,
        "pdf_url": "https://example.github.io/paper.pdf",
        "arxiv_url": "",
    }


def inspect_arxiv_page(url, config, session):
    """Return deterministic metadata from an arXiv abstract page."""
    return {
        "title": "Riemannian Motion Policies",
        "authors": "Nathan D. Ratliff, Jan Issac",
        "abstract": "An exact arXiv abstract.",
        "year": 2018,
        "doi": "10.48550/arXiv.1801.02854",
        "external_url": url,
        "project_url": url,
        "pdf_url": "https://arxiv.org/pdf/1801.02854",
        "arxiv_url": url,
    }


def fail_arxiv_search(title):
    """Simulate an unavailable arXiv metadata endpoint."""
    raise search_module.requests.Timeout("metadata endpoint timed out")


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
                                "is-referenced-by-count": 12,
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
                            "cited_by_count": 10,
                            "relevance_score": 42.0,
                        }
                    ]
                }
            )
        raise AssertionError("Unexpected URL: " + url)


def test_online_search_normalizes_and_merges_provider_results():
    session = FakeSession()
    service = OnlineSearchService(session=session)

    results = service.search("A Useful Paper")

    assert len(results) == 1
    merged = results[0]
    assert merged["title"] == "A Useful Paper"
    assert merged["doi"] == "10.1000/example"
    assert "Crossref" in merged["source"]
    assert "arXiv" in merged["source"]
    assert "OpenAlex" in merged["source"]
    assert merged["is_open_access"] is True
    assert merged["citation_count"] == 12
    assert merged["venue"] == "Journal of Examples"
    assert merged["external_url"] == "https://doi.org/10.1000/example"
    assert merged["pdf_url"] == "https://arxiv.org/pdf/2401.12345v2"
    assert len(session.calls) == 3
    crossref_call = session.calls[0]
    arxiv_call = session.calls[1]
    openalex_call = session.calls[2]
    assert crossref_call[1]["rows"] == 10
    assert arxiv_call[1]["search_query"] == 'all:"A Useful Paper"'
    assert openalex_call[1]["search"] == "A Useful Paper"
    assert "search.exact" not in openalex_call[1]


def test_deduplication_joins_changed_preprint_title_by_doi():
    published = {
        "title": "Published Conference Title",
        "doi": "10.1000/version",
        "venue": "Example Conference",
        "external_url": "https://doi.org/10.1000/version",
        "source": "Crossref",
        "citation_count": 8,
    }
    preprint = {
        "title": "Earlier Preprint Title",
        "doi": "10.1000/version",
        "pdf_url": "https://arxiv.org/pdf/1234.56789",
        "source": "arXiv",
        "citation_count": 0,
    }

    results = deduplicate_results(
        [published, preprint],
        "Published Conference Title",
    )

    assert len(results) == 1
    assert results[0]["title"] == "Published Conference Title"
    assert results[0]["venue"] == "Example Conference"
    assert results[0]["pdf_url"] == "https://arxiv.org/pdf/1234.56789"
    assert results[0]["source"] == "Crossref, arXiv"


def test_project_page_url_becomes_a_search_result(monkeypatch):
    monkeypatch.setattr(
        search_module,
        "inspect_project_page",
        inspect_project_page,
    )
    service = OnlineSearchService()
    service.providers = []

    results = service.search("https://example.github.io/paper/")

    assert len(results) == 1
    assert results[0]["title"] == "A Project Page Paper"
    assert results[0]["project_url"] == "https://example.github.io/paper/"
    assert results[0]["pdf_url"] == "https://example.github.io/paper.pdf"
    assert results[0]["source"] == "Project page"


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


def test_pasted_identifiers_are_normalized_for_direct_search():
    assert normalize_search_query(
        "https://arxiv.org/abs/2506.01185v2"
    ) == "2506.01185"
    assert normalize_search_query(
        "https://doi.org/10.1000/example"
    ) == "10.1000/example"

    session = FakeSession()
    service = OnlineSearchService(session=session)
    service.search("https://arxiv.org/abs/2506.01185v2")

    assert len(session.calls) == 3
    assert session.calls[0][0] == ARXIV_URL
    assert session.calls[0][1]["id_list"] == "2506.01185"
    assert "search_query" not in session.calls[0][1]
    assert session.calls[1][1]["query.bibliographic"] == "A Useful Paper"
    assert session.calls[2][1]["search"] == "A Useful Paper"


def test_arxiv_api_timeout_falls_back_to_exact_page(monkeypatch):
    session = FakeSession()
    service = OnlineSearchService(session=session)

    monkeypatch.setattr(service.providers[1], "search", fail_arxiv_search)
    monkeypatch.setattr(
        search_module,
        "inspect_project_page",
        inspect_arxiv_page,
    )

    results = service.search("https://arxiv.org/abs/1801.02854")

    assert len(results) == 1
    assert results[0]["title"] == "Riemannian Motion Policies"
    assert results[0]["external_id"] == "1801.02854"
    assert results[0]["pdf_url"] == "https://arxiv.org/pdf/1801.02854"
    assert results[0]["query_score"] == 1.0
    assert all("1801.02854" not in str(call[1]) for call in session.calls)
    assert service.last_errors == [
        "arXiv API: metadata endpoint timed out"
    ]


def test_project_context_reranks_ambiguous_homer_results():
    results = [
        {
            "title": "Homer: Odyssey, Book 1",
            "abstract": "A translation of the ancient Greek epic.",
            "citation_count": 5000,
            "source": "Crossref",
        },
        {
            "title": (
                "HoMeR: Learning In-the-Wild Mobile Manipulation via "
                "Hybrid Imitation and Whole-Body Control"
            ),
            "abstract": "A robotics framework for mobile manipulation.",
            "citation_count": 2,
            "source": "arXiv",
        },
    ]

    ranked = deduplicate_results(
        results,
        "HoMeR",
        "robotics, mobile, manipulation",
    )

    assert ranked[0]["title"].startswith("HoMeR: Learning")
    assert ranked[0]["context_score"] > ranked[1]["context_score"]


def test_short_query_uses_context_for_additional_retrieval():
    session = FakeSession()
    service = OnlineSearchService(session=session)

    service.search("HoMeR", "robotics, mobile, manipulation")

    assert len(session.calls) == 5
    contextual_arxiv_call = session.calls[2]
    contextual_openalex_call = session.calls[4]
    assert contextual_arxiv_call[1]["search_query"] == (
        'all:"HoMeR" AND (all:robotics OR all:mobile OR all:manipulation)'
    )
    assert contextual_openalex_call[1]["search"] == (
        "HoMeR robotics mobile manipulation"
    )


def test_acronym_in_abstract_can_beat_an_unrelated_title_match():
    results = [
        {
            "title": "Lapa River Tourism",
            "abstract": "A regional tourism study.",
            "citation_count": 0,
            "source": "Crossref",
        },
        {
            "title": "Latent Action Pretraining from Videos",
            "abstract": (
                "We introduce LAPA for latent action pretraining in "
                "robotics videos."
            ),
            "citation_count": 1,
            "source": "arXiv",
        },
    ]

    ranked = deduplicate_results(
        results,
        "LAPA",
        "robotics, action, videos",
    )

    assert ranked[0]["title"] == "Latent Action Pretraining from Videos"


def test_citations_are_a_small_ranking_tiebreaker():
    uncited = {
        "title": "An Exact Match",
        "abstract": "",
        "citation_count": 0,
    }
    cited = dict(uncited)
    cited["citation_count"] = 1000

    rank_search_result(uncited, "An Exact Match", "")
    rank_search_result(cited, "An Exact Match", "")

    assert cited["score"] > uncited["score"]
    assert cited["score"] - uncited["score"] <= 0.06
