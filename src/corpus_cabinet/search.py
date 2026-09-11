"""Search scholarly metadata from supported online providers.

The module reads a paper title and provider HTTP responses. It writes no files
and returns normalized dictionaries containing title, authors, venue, year,
DOI, abstract, source identifiers, landing URLs, and open-access PDF URLs.
"""

import html
import re
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests


CROSSREF_URL = "https://api.crossref.org/v1/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
OPENALEX_URL = "https://api.openalex.org/works"
ATOM_NAMESPACES = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NAMESPACES = {"arxiv": "http://arxiv.org/schemas/atom"}


class OfflineModeError(RuntimeError):
    """Raised when an online request is attempted in Offline Mode."""


class OnlineSearchError(RuntimeError):
    """Raised when no online search provider can return results."""


class SearchProvider:
    """Base class for one online scholarly metadata provider."""

    name = "Provider"

    def __init__(self, config=None, session=None):
        if config is None:
            config = {}
        self.config = config
        if session is None:
            self.session = requests.Session()
        else:
            self.session = session
        self.timeout = config.get("search_timeout_seconds", 15)
        self.limit = config.get("search_result_limit", 5)

    def search(self, title):
        raise NotImplementedError

    def request_json(self, url, params, headers=None):
        if headers is None:
            headers = {}
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def request_text(self, url, params, headers=None):
        if headers is None:
            headers = {}
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text


class CrossrefProvider(SearchProvider):
    """Search Crossref's deposited bibliographic metadata."""

    name = "Crossref"

    def search(self, title):
        params = {
            "query.bibliographic": title,
            "rows": self.limit,
        }
        mailto = self.config.get("crossref_mailto", "")
        headers = {"User-Agent": "CorpusCabinet/0.1"}
        if mailto:
            params["mailto"] = mailto
            headers["User-Agent"] += " (mailto:" + mailto + ")"

        data = self.request_json(CROSSREF_URL, params, headers)
        message = data.get("message") or {}
        items = message.get("items") or []
        results = []

        for item in items:
            item_title = first_value(item.get("title"))
            if not item_title:
                continue

            doi = normalize_doi(item.get("DOI"))
            external_url = safe_text(item.get("URL"))
            if not external_url and doi:
                external_url = "https://doi.org/" + doi

            venue = first_value(item.get("container-title"))
            if not venue:
                event = item.get("event") or {}
                venue = safe_text(event.get("name"))

            results.append(
                make_search_result(
                    title=item_title,
                    authors=authors_to_text(item.get("author")),
                    venue=venue,
                    year=crossref_year(item),
                    doi=doi,
                    abstract=strip_markup(item.get("abstract")),
                    external_id=doi,
                    external_url=external_url,
                    pdf_url=crossref_pdf_url(item),
                    source=self.name,
                    is_open_access=False,
                )
            )

        return results


class ArxivProvider(SearchProvider):
    """Search arXiv's official Atom metadata API."""

    name = "arXiv"

    def search(self, title):
        query_title = title.replace('"', "")
        params = {
            "search_query": 'ti:"' + query_title + '"',
            "start": 0,
            "max_results": self.limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        text = self.request_text(ARXIV_URL, params)
        root = ET.fromstring(text)
        results = []

        for entry in root.findall("atom:entry", ATOM_NAMESPACES):
            item_title = element_text(entry, "atom:title", ATOM_NAMESPACES)
            if not item_title:
                continue

            arxiv_url = element_text(entry, "atom:id", ATOM_NAMESPACES)
            arxiv_id = arxiv_id_from_url(arxiv_url)
            pdf_url = arxiv_pdf_url(entry)
            if not pdf_url and arxiv_id:
                pdf_url = "https://arxiv.org/pdf/" + arxiv_id

            authors = []
            for author in entry.findall("atom:author", ATOM_NAMESPACES):
                author_name = element_text(author, "atom:name", ATOM_NAMESPACES)
                if author_name:
                    authors.append(author_name)

            results.append(
                make_search_result(
                    title=item_title,
                    authors=", ".join(authors),
                    venue=element_text(
                        entry,
                        "arxiv:journal_ref",
                        ARXIV_NAMESPACES,
                    ),
                    year=parse_year(
                        element_text(entry, "atom:published", ATOM_NAMESPACES)
                    ),
                    doi=element_text(entry, "arxiv:doi", ARXIV_NAMESPACES),
                    abstract=element_text(
                        entry,
                        "atom:summary",
                        ATOM_NAMESPACES,
                    ),
                    external_id=arxiv_id,
                    external_url=arxiv_url,
                    pdf_url=pdf_url,
                    source=self.name,
                    is_open_access=True,
                )
            )

        return results


class OpenAlexProvider(SearchProvider):
    """Search OpenAlex's open scholarly works catalog."""

    name = "OpenAlex"

    def search(self, title):
        params = {
            "search.exact": title,
            "per-page": self.limit,
            "select": (
                "id,doi,display_name,publication_year,authorships,"
                "primary_location,open_access,best_oa_location,"
                "abstract_inverted_index"
            ),
        }
        data = self.request_json(OPENALEX_URL, params)
        items = data.get("results") or []
        results = []

        for item in items:
            item_title = safe_text(item.get("display_name"))
            if not item_title:
                item_title = safe_text(item.get("title"))
            if not item_title:
                continue

            primary_location = item.get("primary_location") or {}
            source = primary_location.get("source") or {}
            open_access = item.get("open_access") or {}
            best_location = item.get("best_oa_location") or {}
            doi = normalize_doi(item.get("doi"))
            external_url = safe_text(primary_location.get("landing_page_url"))
            if not external_url:
                external_url = safe_text(open_access.get("oa_url"))
            if not external_url and doi:
                external_url = "https://doi.org/" + doi
            if not external_url:
                external_url = safe_text(item.get("id"))

            authors = []
            for authorship in item.get("authorships") or []:
                authors.append(authorship.get("author") or {})

            results.append(
                make_search_result(
                    title=item_title,
                    authors=authors_to_text(authors),
                    venue=safe_text(source.get("display_name")),
                    year=item.get("publication_year"),
                    doi=doi,
                    abstract=reconstruct_abstract(
                        item.get("abstract_inverted_index")
                    ),
                    external_id=openalex_id_from_url(item.get("id")),
                    external_url=external_url,
                    pdf_url=safe_text(best_location.get("pdf_url")),
                    source=self.name,
                    is_open_access=bool(open_access.get("is_oa")),
                )
            )

        return results


class OnlineSearchService:
    """Search all configured providers behind one offline-aware interface."""

    def __init__(self, config=None, session=None):
        if config is None:
            config = {}
        if session is None:
            session = requests.Session()
        self.config = config
        self.session = session
        self.offline = False
        self.last_errors = []
        self.providers = [
            CrossrefProvider(config, session),
            ArxivProvider(config, session),
            OpenAlexProvider(config, session),
        ]

    def set_offline(self, enabled):
        self.offline = bool(enabled)

    def is_offline(self):
        return self.offline

    def search(self, title):
        title = safe_text(title)
        if not title:
            raise ValueError("Search title cannot be empty")
        if self.offline:
            raise OfflineModeError(
                "Online search is disabled while Offline Mode is enabled"
            )

        self.last_errors = []
        results = []
        for provider in self.providers:
            try:
                results.extend(provider.search(title))
            except (requests.RequestException, ET.ParseError, ValueError) as error:
                self.last_errors.append(provider.name + ": " + str(error))

        if not results and self.last_errors:
            raise OnlineSearchError(
                "No online search provider returned results"
            )

        return deduplicate_results(results, title)


def safe_text(value):
    """Return collapsed plain text for a provider field."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def first_value(value):
    """Return the first item from a provider list field."""
    if isinstance(value, list):
        if not value:
            return ""
        return safe_text(value[0])
    return safe_text(value)


def strip_markup(value):
    """Remove simple XML/HTML markup from provider abstracts."""
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return safe_text(value)


def authors_to_text(authors):
    """Format Crossref or OpenAlex author dictionaries."""
    names = []
    for author in authors or []:
        name = safe_text(author.get("name"))
        if not name:
            name = safe_text(author.get("display_name"))
        if not name:
            given = safe_text(author.get("given"))
            family = safe_text(author.get("family"))
            name = (given + " " + family).strip()
        if name:
            names.append(name)
    return ", ".join(names)


def crossref_year(item):
    """Read the first publication year in a Crossref record."""
    for field in ("published", "issued", "published-print", "published-online"):
        date_data = item.get(field) or {}
        date_parts = date_data.get("date-parts") or []
        if date_parts and date_parts[0]:
            return date_parts[0][0]
    return None


def crossref_pdf_url(item):
    """Return a PDF link when Crossref includes one."""
    for link in item.get("link") or []:
        if link.get("content-type") == "application/pdf":
            return safe_text(link.get("URL"))
    return ""


def element_text(parent, path, namespaces):
    """Read and normalize one XML element."""
    element = parent.find(path, namespaces)
    if element is None or element.text is None:
        return ""
    return safe_text(element.text)


def arxiv_pdf_url(entry):
    """Return the PDF link from an arXiv Atom entry."""
    for link in entry.findall("atom:link", ATOM_NAMESPACES):
        if link.get("title") == "pdf":
            return safe_text(link.get("href"))
        if link.get("type") == "application/pdf":
            return safe_text(link.get("href"))
    return ""


def arxiv_id_from_url(value):
    """Extract a version-independent arXiv identifier from an entry URL."""
    value = safe_text(value)
    if "/abs/" in value:
        value = value.split("/abs/", 1)[1]
    value = value.split("?", 1)[0]
    return re.sub(r"v\d+$", "", value)


def openalex_id_from_url(value):
    """Extract the short OpenAlex identifier from a work URL."""
    value = safe_text(value).rstrip("/")
    if not value:
        return ""
    return value.rsplit("/", 1)[-1]


def reconstruct_abstract(index):
    """Rebuild plain text from OpenAlex's inverted abstract index."""
    if not index:
        return ""
    words = {}
    for word, positions in index.items():
        for position in positions:
            words[position] = word
    ordered_words = []
    for position in sorted(words):
        ordered_words.append(words[position])
    return " ".join(ordered_words)


def parse_year(value):
    """Extract a four-digit publication year from a provider value."""
    match = re.search(r"\b(?:19|20)\d{2}\b", safe_text(value))
    if not match:
        return None
    return int(match.group(0))


def normalize_doi(value):
    """Normalize DOI URLs and prefixes to a bare DOI."""
    value = safe_text(value)
    value = re.sub(
        r"^https?://(?:dx\.)?doi\.org/",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.rstrip(" .")


def normalize_title(value):
    """Normalize title punctuation and whitespace for matching."""
    value = html.unescape(str(value or "")).lower()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def google_scholar_url(title):
    """Return a user-driven Google Scholar search URL without making a request."""
    return "https://scholar.google.com/scholar?q=" + quote_plus(safe_text(title))


def title_similarity(query, title):
    """Return a 0-to-1 similarity score for two titles."""
    query = normalize_title(query)
    title = normalize_title(title)
    if not query or not title:
        return 0
    return round(SequenceMatcher(None, query, title).ratio(), 4)


def make_search_result(
    title,
    authors,
    venue,
    year,
    doi,
    abstract,
    external_id,
    external_url,
    pdf_url,
    source,
    is_open_access,
):
    """Create the provider-neutral search-result shape."""
    return {
        "title": safe_text(title),
        "authors": safe_text(authors),
        "venue": safe_text(venue),
        "year": year,
        "doi": normalize_doi(doi),
        "abstract": strip_markup(abstract),
        "external_id": safe_text(external_id),
        "external_url": safe_text(external_url),
        "pdf_url": safe_text(pdf_url),
        "source": source,
        "is_open_access": bool(is_open_access),
    }


def result_key(result):
    """Build a stable key for merging provider duplicates."""
    doi = normalize_doi(result.get("doi")).lower()
    if doi:
        return "doi:" + doi

    if result.get("source") == "arXiv":
        arxiv_id = safe_text(result.get("external_id")).lower()
        if arxiv_id:
            return "arxiv:" + arxiv_id

    title = normalize_title(result.get("title"))
    year = result.get("year")
    if title and year:
        return "title:" + title + "|" + str(year)
    if title:
        return "title:" + title

    source = safe_text(result.get("source"))
    external_id = safe_text(result.get("external_id"))
    return "source:" + source + ":" + external_id


def merge_search_result(existing, candidate):
    """Fill missing fields and preserve provider provenance."""
    fields = [
        "title",
        "authors",
        "venue",
        "year",
        "doi",
        "abstract",
        "external_id",
        "external_url",
        "pdf_url",
    ]
    for field in fields:
        if not existing.get(field) and candidate.get(field):
            existing[field] = candidate[field]

    existing["is_open_access"] = (
        bool(existing.get("is_open_access"))
        or bool(candidate.get("is_open_access"))
    )
    existing_sources = existing.get("source", "").split(", ")
    candidate_source = safe_text(candidate.get("source"))
    if candidate_source and candidate_source not in existing_sources:
        existing_sources.append(candidate_source)
        existing["source"] = ", ".join(existing_sources)


def result_sort_key(result):
    """Return the ranking value for a normalized result."""
    score = result.get("score", 0)
    if result.get("is_open_access"):
        score += 0.001
    return score


def deduplicate_results(results, query):
    """Merge duplicate records and sort them by title match."""
    merged = {}
    for result in results:
        if not result.get("title"):
            continue
        key = result_key(result)
        if key not in merged:
            item = dict(result)
            item["score"] = title_similarity(query, item["title"])
            merged[key] = item
        else:
            merge_search_result(merged[key], result)

    output = list(merged.values())
    output.sort(key=result_sort_key, reverse=True)
    return output
