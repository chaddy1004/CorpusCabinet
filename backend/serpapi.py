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
    if not title or not title.strip():
        print("[serpapi] search_paper called with empty title — skipping")
        return None

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


def fetch_citations(result_id: str) -> list:
    """
    Call SerpAPI google_scholar_cite to get formatted citations (APA, MLA, etc).
    Returns the citations list, or [] on failure.
    No direct requests to Google Scholar — everything goes through SerpAPI.
    """
    try:
        resp = requests.get(SERPAPI_URL, params={
            "engine":  "google_scholar_cite",
            "q":       result_id,
            "api_key": SERPAPI_KEY,
        }, timeout=15)
        resp.raise_for_status()
        return resp.json().get("citations", [])
    except Exception as e:
        print(f"[serpapi] fetch_citations failed: {e}")
        return []


def build_bibtex(title: str, authors: str, apa: str) -> str:
    """
    Build a BibTeX entry using:
      - title:   from Scholar (canonical)
      - authors: full names from Haiku PDF extraction
      - apa:     APA citation from SerpAPI (provides venue, year, pages)

    Handles three cases:
      - @inproceedings: "In Proceedings of ..."
      - @article:       "Journal Name, vol(num), pages."
      - @misc:          "arXiv preprint arXiv:XXXX.XXXXX"
    """
    import re

    # --- Year ---
    year_match = re.search(r'\((\d{4})\)', apa)
    year = year_match.group(1) if year_match else ""

    # --- Everything after "(Year). " ---
    after_year = apa[year_match.end():].lstrip('. ').strip() if year_match else ""

    # Split into [apa_title, venue_str] on first ". "
    parts = re.split(r'\.\s+', after_year, maxsplit=1)
    venue_str = parts[1].strip() if len(parts) > 1 else ""

    # --- Authors ---
    # Haiku returns "Last, First and Last, First" — use as-is
    author_bib = authors if authors else "Unknown"

    # --- Citation key: first author's last name (first token before comma) ---
    first_last = authors.split(",")[0].strip().split()[0].lower() if authors else "unknown"
    first_last = re.sub(r'[^a-z]', '', first_last)
    title_word = re.sub(r'[^a-z]', '', title.split()[0].lower()) if title else "paper"
    cite_key   = f"{first_last}{year}{title_word}"

    def _fmt(fields: list[tuple[str, str]], entry_type: str) -> str:
        """Format a BibTeX entry: no trailing comma on last field."""
        body = ",\n".join(f"  {k}={{{v}}}" for k, v in fields)
        return f"@{entry_type}{{{cite_key},\n{body}\n}}"

    # --- Case 1: arXiv preprint ---
    arxiv_match = re.search(r'arXiv[:\s]+(\d{4}\.\d{4,5})', venue_str, re.I)
    if arxiv_match or re.search(r'arXiv', venue_str, re.I):
        fields = [("title", title), ("author", author_bib), ("year", year)]
        if arxiv_match:
            fields += [("eprint", arxiv_match.group(1)), ("archivePrefix", "arXiv")]
        return _fmt(fields, "misc")

    # --- Case 2: Conference proceedings ---
    if re.search(r'proceedings|conference|workshop|symposium', venue_str, re.I):
        venue = re.sub(r'^[Ii]n\s+', '', venue_str)
        venue = re.sub(r'\s*\(pp?\.\s*[\d\s,–-]+\)\.?$', '', venue)
        venue = venue.rstrip('.,').strip()

        pages_match = re.search(r'(\d+)\s*[-–]\s*(\d+)', venue_str)
        pages = f"{pages_match.group(1)}--{pages_match.group(2)}" if pages_match else ""

        fields = [("title", title), ("author", author_bib), ("booktitle", venue)]
        if pages:
            fields.append(("pages", pages))
        fields.append(("year", year))
        return _fmt(fields, "inproceedings")

    # --- Case 3: Journal article ---
    # APA journal format: "Journal Name, 45(3), 1234-1247."
    journal = ""
    volume = ""
    number = ""
    pages = ""

    journal_match = re.match(r'^(.+?),\s*(\d+)(?:\((\d+)\))?,\s*([\d]+\s*[-–]\s*[\d]+)', venue_str)
    if journal_match:
        journal = journal_match.group(1).strip().rstrip('.')
        volume  = journal_match.group(2)
        number  = journal_match.group(3) or ""
        p1, p2  = re.split(r'[-–]', journal_match.group(4).replace(' ', ''), maxsplit=1)
        pages   = f"{p1}--{p2}"
    else:
        journal = venue_str.rstrip('.,').strip()
        pages_match = re.search(r'(\d+)\s*[-–]\s*(\d+)', venue_str)
        pages = f"{pages_match.group(1)}--{pages_match.group(2)}" if pages_match else ""

    fields = [("title", title), ("author", author_bib), ("journal", journal)]
    if volume:
        fields.append(("volume", volume))
    if number:
        fields.append(("number", number))
    if pages:
        fields.append(("pages", pages))
    fields.append(("year", year))
    return _fmt(fields, "article")


def get_paper_metadata(title: str, pdf_authors: str = "") -> dict:
    """
    Fetch Scholar metadata and build BibTeX.

    Requests:
      1. SerpAPI google_scholar        — title, conference, year
      2. SerpAPI google_scholar_cite   — APA citation (venue, year, pages)

    No direct requests to Google Scholar — no rate limiting.
    Authors come from pdf_authors (extracted by Haiku) for full names.
    """
    result = search_paper(title)
    if not result:
        return {"scholar_id": "", "bibtex": "", "authors": "", "conference": "", "year": None, "title": ""}

    scholar_id    = result.get("result_id", "")
    scholar_title = result.get("title", "")
    publication   = result.get("publication_info", {})
    authors_raw   = publication.get("authors", [])
    serpapi_authors = ", ".join(a.get("name", "") for a in authors_raw) if authors_raw else ""
    summary_str   = publication.get("summary", "")

    conference, year = _parse_conference_year(summary_str)

    # Request 2: get APA from SerpAPI, build BibTeX using Haiku's full author names
    bibtex = ""
    if scholar_id:
        citations = fetch_citations(scholar_id)
        apa = next((c["snippet"] for c in citations if c["title"] == "APA"), "")
        if apa:
            authors_for_bibtex = pdf_authors or serpapi_authors
            bibtex = build_bibtex(scholar_title or title, authors_for_bibtex, apa)
            print(f"[serpapi] BibTeX built from APA + {'Haiku' if pdf_authors else 'SerpAPI'} authors")

    return {
        "scholar_id": scholar_id,
        "bibtex":     bibtex,
        "title":      scholar_title,
        "authors":    pdf_authors or serpapi_authors,
        "conference": conference,
        "year":       year,
    }


def _parse_conference_year(summary: str) -> tuple[str, int | None]:
    """
    Parse SerpAPI's publication summary string into (conference, year).

    SerpAPI summary format (two variants):
      "A Author, B Author - Proceedings of CVPR, 2025 - openaccess.thecvf.com"
      "A Author, B AuthorProceedings of CVPR …, 2025•openaccess.thecvf.com"
    """
    import re

    if not summary:
        return "", None

    # Extract year
    year_match = re.search(r"\b(19|20)\d{2}\b", summary)
    year = int(year_match.group()) if year_match else None

    conference = ""

    # Variant 1: " - " separators → "Authors - Venue, Year - Source"
    if " - " in summary:
        parts = summary.split(" - ")
        if len(parts) >= 2:
            venue = parts[1]
            venue = re.sub(r",?\s*(19|20)\d{2}.*$", "", venue).strip()
            conference = venue

    # Variant 2: no separators → look for "Proceedings of..." keyword
    if not conference:
        proc_match = re.search(r"((?:Proceedings|Conference|Workshop|Transactions)\s+(?:of\s+)?[^,•\d]+)", summary)
        if proc_match:
            conference = proc_match.group(1).strip().rstrip(",").strip()

    # Clean up trailing ellipsis / year / source URL
    conference = re.sub(r"\s*….*$", "", conference).strip()
    conference = re.sub(r",?\s*(19|20)\d{2}.*$", "", conference).strip()
    conference = re.sub(r"•.*$", "", conference).strip()

    return conference, year
