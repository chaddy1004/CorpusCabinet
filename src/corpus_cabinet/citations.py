"""Create and retrieve reviewable BibTeX citations.

The module reads saved paper metadata or one DOI metadata response. It writes no
files and returns a BibTeX string for display, editing, copying, or persistence.
"""

import re
import unicodedata

import requests


class CitationLookupError(RuntimeError):
    """Raised when a DOI does not return usable BibTeX."""


def bibtex_escape(value):
    """Escape plain metadata characters without damaging existing LaTeX."""
    value = str(value or "")
    value = re.sub(r"(?<!\\)&", r"\\&", value)
    value = re.sub(r"(?<!\\)%", r"\\%", value)
    value = re.sub(r"(?<!\\)#", r"\\#", value)
    return " ".join(value.split())


def authors_to_bibtex(value):
    """Join the application's comma-separated author names for BibTeX."""
    authors = []
    for author in str(value or "").split(","):
        author = " ".join(author.split())
        if author:
            authors.append(author)
    return " and ".join(authors)


def citation_key(paper):
    """Build a readable deterministic key from author, year, and title."""
    authors = str(paper.get("authors") or "")
    first_author = authors.split(",", 1)[0].strip()
    family_name = first_author.rsplit(" ", 1)[-1]
    if not family_name:
        family_name = "paper"

    year = str(paper.get("year") or "nd")
    title_words = re.findall(r"[A-Za-z0-9]+", str(paper.get("title") or ""))
    title_word = "work"
    stop_words = {"a", "an", "and", "for", "of", "on", "the", "to", "with"}
    for word in title_words:
        if word.lower() not in stop_words:
            title_word = word
            break

    value = family_name + year + title_word
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9_-]", "", value)
    if not value:
        return "paper"
    return value[0].lower() + value[1:]


def generate_bibtex(paper):
    """Generate a conservative draft entry from the available metadata."""
    fields = []
    title = bibtex_escape(paper.get("title"))
    if title:
        fields.append(("title", title))
    authors = authors_to_bibtex(paper.get("authors"))
    if authors:
        fields.append(("author", bibtex_escape(authors)))
    if paper.get("year"):
        fields.append(("year", str(paper["year"])))
    if paper.get("conference"):
        fields.append(("howpublished", bibtex_escape(paper["conference"])))
    if paper.get("doi"):
        fields.append(("doi", bibtex_escape(paper["doi"])))
    if paper.get("external_url"):
        fields.append(("url", bibtex_escape(paper["external_url"])))
    elif paper.get("doi"):
        fields.append(("url", "https://doi.org/" + bibtex_escape(paper["doi"])))

    lines = ["@misc{" + citation_key(paper) + ","]
    for name, value in fields:
        lines.append("  " + name + " = {" + value + "},")
    lines.append("}")
    return "\n".join(lines)


def fetch_doi_bibtex(doi, config=None, session=None):
    """Retrieve registration-agency BibTeX through DOI content negotiation."""
    doi = str(doi or "").strip()
    if not doi:
        raise CitationLookupError("This paper has no DOI")
    if config is None:
        config = {}
    if session is None:
        session = requests.Session()

    user_agent = "CorpusCabinet/0.1"
    mailto = config.get("crossref_mailto", "")
    if mailto:
        user_agent += " (mailto:" + mailto + ")"
    response = session.get(
        "https://doi.org/" + doi,
        headers={
            "Accept": "application/x-bibtex",
            "User-Agent": user_agent,
        },
        timeout=config.get("search_timeout_seconds", 15),
    )
    response.raise_for_status()
    bibtex = str(response.text or "").strip()
    if not bibtex.startswith("@"):
        raise CitationLookupError("The DOI registry did not return BibTeX")
    return bibtex
