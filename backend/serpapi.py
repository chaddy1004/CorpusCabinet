import requests
import os
from dotenv import load_dotenv

load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
SERPAPI_URL = "https://serpapi.com/search"


def search_paper(title: str) -> dict | None:
    """
    Search Google Scholar for a paper by title.
    Returns the top organic result dict, or None if not found.
    """
    resp = requests.get(SERPAPI_URL, params={
        "engine":  "google_scholar",
        "q":       title,
        "num":     1,
        "api_key": SERPAPI_KEY,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("organic_results", [])
    return results[0] if results else None


def fetch_bibtex(result_id: str) -> str:
    """
    Given a Scholar result_id, fetch the raw BibTeX string.
    Returns empty string if anything fails.
    """
    # Step 1: get citation links
    resp = requests.get(SERPAPI_URL, params={
        "engine":  "google_scholar_cite",
        "q":       result_id,
        "api_key": SERPAPI_KEY,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Step 2: find BibTeX link
    bibtex_url = None
    for link in data.get("links", []):
        if link.get("name") == "BibTeX":
            bibtex_url = link["link"]
            break

    if not bibtex_url:
        return ""

    # Step 3: fetch raw .bib text
    bib_resp = requests.get(bibtex_url, timeout=15)
    bib_resp.raise_for_status()
    return bib_resp.text.strip()


def get_paper_metadata(title: str) -> dict:
    """
    High-level helper: search for a paper and return structured metadata + BibTeX.
    Returns a dict with keys: scholar_id, bibtex, authors, conference, year.
    Falls back to empty strings if Scholar search fails.
    """
    result = search_paper(title)
    if not result:
        return {"scholar_id": "", "bibtex": "", "authors": "", "conference": "", "year": None}

    scholar_id  = result.get("result_id", "")
    publication = result.get("publication_info", {})
    authors_raw = publication.get("authors", [])
    authors     = ", ".join(a.get("name", "") for a in authors_raw) if authors_raw else publication.get("summary", "")
    summary_str = publication.get("summary", "")  # e.g. "CVPR, 2019"

    # Try to extract conference and year from summary string
    conference, year = _parse_conference_year(summary_str)

    bibtex = fetch_bibtex(scholar_id) if scholar_id else ""

    return {
        "scholar_id": scholar_id,
        "bibtex":     bibtex,
        "authors":    authors,
        "conference": conference,
        "year":       year,
    }


def _parse_conference_year(summary: str) -> tuple[str, int | None]:
    """
    Parse 'CVPR, 2019' or 'IEEE Transactions..., 2020' into (conference, year).
    """
    import re
    year_match = re.search(r"\b(19|20)\d{2}\b", summary)
    year = int(year_match.group()) if year_match else None
    conference = summary.strip().rstrip(",").strip() if summary else ""
    return conference, year
