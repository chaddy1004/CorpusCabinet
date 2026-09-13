"""Search scholarly metadata from supported online providers.

The module reads a paper title and provider HTTP responses. It writes no files
and returns normalized dictionaries containing title, authors, venue, year,
DOI, abstract, source identifiers, landing URLs, and open-access PDF URLs.
"""

import html
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urlparse

import requests

from corpus_cabinet.links import inspect_project_page


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
        self.limit = config.get("search_result_limit", 10)

    def search(self, title):
        raise NotImplementedError

    def search_with_context(self, title, context):
        return []

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
                    citation_count=item.get("is-referenced-by-count", 0),
                    provider_score=item.get("score", 0),
                )
            )

        return results


class ArxivProvider(SearchProvider):
    """Search arXiv's official Atom metadata API."""

    name = "arXiv"

    def search(self, title):
        arxiv_id = arxiv_query_id(title)
        if arxiv_id:
            params = {
                "id_list": arxiv_id,
                "start": 0,
                "max_results": self.limit,
            }
            return self.search_params(params)
        query_title = title.replace('"', "")
        expression = 'all:"' + query_title + '"'
        return self.search_expression(expression)

    def search_with_context(self, title, context):
        query_title = title.replace('"', "")
        terms = context_query_terms(context)
        if not terms:
            return []
        clauses = []
        for term in terms:
            clauses.append("all:" + term)
        expression = (
            'all:"' + query_title + '" AND (' + " OR ".join(clauses) + ")"
        )
        return self.search_expression(expression)

    def search_expression(self, expression):
        params = {
            "search_query": expression,
            "start": 0,
            "max_results": self.limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        return self.search_params(params)

    def search_params(self, params):
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
                    citation_count=0,
                    provider_score=0,
                )
            )

        return results


class OpenAlexProvider(SearchProvider):
    """Search OpenAlex's open scholarly works catalog."""

    name = "OpenAlex"

    def search_with_context(self, title, context):
        terms = context_query_terms(context)
        if not terms:
            return []
        expanded_query = title + " " + " ".join(terms)
        return self.search(expanded_query)

    def search(self, title):
        params = {
            "search": title,
            "per-page": self.limit,
            "select": (
                "id,doi,display_name,publication_year,authorships,"
                "primary_location,open_access,best_oa_location,"
                "abstract_inverted_index,cited_by_count,relevance_score"
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
                    citation_count=item.get("cited_by_count", 0),
                    provider_score=item.get("relevance_score", 0),
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

    def search(self, title, context=""):
        raw_query = safe_text(title)
        if self.offline:
            raise OfflineModeError(
                "Online search is disabled while Offline Mode is enabled"
            )
        parsed_query = urlparse(raw_query)
        is_project_page = (
            parsed_query.scheme in ("http", "https")
            and bool(parsed_query.netloc)
            and not arxiv_query_id(raw_query)
            and "doi.org/" not in raw_query.lower()
            and not raw_query.lower().split("?", 1)[0].endswith(".pdf")
        )
        if is_project_page:
            return self.search_project_page(raw_query, context)

        title = normalize_search_query(raw_query)
        if not title:
            raise ValueError("Search title cannot be empty")

        return self.search_providers(title, context)

    def search_providers(self, title, context=""):
        """Search normalized provider queries and merge their results."""
        self.last_errors = []
        results = []
        for provider in self.providers:
            try:
                results.extend(provider.search(title))
            except (requests.RequestException, ET.ParseError, ValueError) as error:
                self.last_errors.append(provider.name + ": " + str(error))

            if should_expand_query(title, context):
                try:
                    results.extend(provider.search_with_context(title, context))
                except (
                    requests.RequestException,
                    ET.ParseError,
                    ValueError,
                ) as error:
                    self.last_errors.append(
                        provider.name + " context: " + str(error)
                    )

        if not results and self.last_errors:
            raise OnlineSearchError(
                "No online search provider returned results"
            )

        return deduplicate_results(results, title, context)

    def search_project_page(self, url, context=""):
        """Resolve a project website through visible scholarly links."""
        page = inspect_project_page(url, self.config, self.session)
        results = []
        if page.get("arxiv_url"):
            arxiv_query = normalize_search_query(page["arxiv_url"])
            try:
                results.extend(self.search_providers(arxiv_query, context))
            except OnlineSearchError:
                pass
        elif page.get("doi"):
            try:
                results.extend(self.search_providers(page["doi"], context))
            except OnlineSearchError:
                pass

        if page.get("title"):
            try:
                results.extend(self.search_providers(page["title"], context))
            except OnlineSearchError:
                pass
        elif results:
            page["title"] = results[0].get("title", "")

        page_result = make_search_result(
            title=page.get("title", ""),
            authors=page.get("authors", ""),
            venue=page.get("venue", ""),
            year=page.get("year"),
            doi=page.get("doi", ""),
            abstract=page.get("abstract", ""),
            external_id="",
            external_url=page.get("external_url", url),
            pdf_url=page.get("pdf_url", ""),
            source="Project page",
            is_open_access=bool(page.get("pdf_url") or page.get("arxiv_url")),
            citation_count=0,
            provider_score=0,
            project_url=page.get("project_url", url),
        )
        results.append(page_result)
        ranking_query = page.get("title") or url
        return deduplicate_results(results, ranking_query, context)


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


def arxiv_query_id(value):
    """Extract an arXiv identifier from an ID or pasted arXiv URL."""
    value = safe_text(value)
    match = re.search(
        r"(?:arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1)


def normalize_search_query(value):
    """Normalize common pasted scholarly identifiers before retrieval."""
    value = safe_text(value)
    arxiv_id = arxiv_query_id(value)
    if arxiv_id:
        return arxiv_id
    if re.match(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)10\.",
        value,
        flags=re.IGNORECASE,
    ):
        value = re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
            "",
            value,
            flags=re.IGNORECASE,
        )
    return value


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


def query_match_score(query, result):
    """Score exact terms and acronyms without penalizing long paper titles."""
    query = normalize_title(query)
    title = normalize_title(result.get("title"))
    if not query or not title:
        return 0

    score = title_similarity(query, title)
    if query == title:
        score = 1
    elif title.startswith(query + " "):
        score = max(score, 0.98)
    elif " " + query + " " in " " + title + " ":
        score = max(score, 0.94)
    elif query in title:
        score = max(score, 0.88)

    abstract = normalize_title(result.get("abstract"))
    if " " + query + " " in " " + abstract + " ":
        score = max(score, 0.9)

    return round(score, 4)


def context_words(value):
    """Return meaningful lowercase words for project-context matching."""
    stop_words = {
        "about", "after", "also", "among", "been", "before", "being",
        "between", "both", "could", "from", "have", "into", "more",
        "most", "other", "over", "paper", "results", "show", "than",
        "that", "their", "there", "these", "they", "this", "through",
        "using", "were", "which", "while", "with", "within", "would",
    }
    words = re.findall(r"[a-z][a-z0-9-]{2,}", safe_text(value).lower())
    output = []
    for word in words:
        if word not in stop_words:
            output.append(word)
    return output


def context_query_terms(value, limit=3):
    """Return a few unique terms for contextual provider retrieval."""
    terms = []
    for word in context_words(value):
        if word not in terms:
            terms.append(word)
        if len(terms) == limit:
            break
    return terms


def should_expand_query(query, context):
    """Use contextual retrieval only for short, ambiguous searches."""
    if arxiv_query_id(query) or normalize_doi(query) != query:
        return False
    query_terms = normalize_title(query).split()
    return len(query_terms) <= 2 and bool(context_query_terms(context))


def suggest_context_terms(value, limit=8):
    """Summarize project text as editable search-preference terms."""
    counts = Counter(context_words(value))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    terms = []
    for word, count in ranked[:limit]:
        terms.append(word)
    return ", ".join(terms)


def context_match_score(context, result):
    """Measure how much a result overlaps the user's visible context terms."""
    preferred = set(context_words(context))
    if not preferred:
        return 0

    searchable = " ".join(
        [
            safe_text(result.get("title")),
            safe_text(result.get("abstract")),
            safe_text(result.get("venue")),
        ]
    )
    available = set(context_words(searchable))
    matches = preferred.intersection(available)
    denominator = min(len(preferred), 6)
    if denominator == 0:
        return 0
    return round(min(len(matches) / denominator, 1), 4)


def citation_score(citation_count):
    """Compress citation counts so popularity cannot dominate relevance."""
    citation_count = safe_citation_count(citation_count)
    return round(min(math.log10(citation_count + 1) / 4, 1), 4)


def safe_citation_count(value):
    """Normalize a provider citation count to a non-negative integer."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


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
    citation_count,
    provider_score,
    project_url="",
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
        "citation_count": safe_citation_count(citation_count),
        "provider_score": provider_score or 0,
        "project_url": safe_text(project_url),
    }


def result_key(result):
    """Build a stable primary key for one provider result."""
    keys = result_keys(result)
    if keys:
        return keys[0]
    source = safe_text(result.get("source"))
    external_id = safe_text(result.get("external_id"))
    return "source:" + source + ":" + external_id


def result_keys(result):
    """Return DOI and title aliases used to join publication versions."""
    keys = []
    doi = normalize_doi(result.get("doi")).lower()
    if doi:
        keys.append("doi:" + doi)
    title = normalize_title(result.get("title"))
    if title:
        keys.append("title:" + title)
    return keys


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
        "provider_score",
        "project_url",
    ]
    for field in fields:
        if not existing.get(field) and candidate.get(field):
            existing[field] = candidate[field]

    existing["is_open_access"] = (
        bool(existing.get("is_open_access"))
        or bool(candidate.get("is_open_access"))
    )
    existing["citation_count"] = max(
        safe_citation_count(existing.get("citation_count", 0)),
        safe_citation_count(candidate.get("citation_count", 0)),
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


def rank_search_result(result, query, context):
    """Attach transparent query, context, citation, and final rank scores."""
    query_score = query_match_score(query, result)
    preference_score = context_match_score(context, result)
    popularity_score = citation_score(result.get("citation_count", 0))
    score = (
        query_score * 0.74
        + preference_score * 0.20
        + popularity_score * 0.06
    )
    result["query_score"] = query_score
    result["context_score"] = preference_score
    result["citation_score"] = popularity_score
    result["score"] = round(score, 4)


def deduplicate_results(results, query, context=""):
    """Merge duplicate records and rank them with visible user preferences."""
    merged = {}
    output = []
    for result in results:
        if not result.get("title"):
            continue
        existing = None
        for key in result_keys(result):
            if key in merged:
                existing = merged[key]
                break
        if existing is None:
            item = dict(result)
            output.append(item)
        else:
            item = existing
            merge_search_result(item, result)
        for key in result_keys(item):
            merged[key] = item
        for key in result_keys(result):
            merged[key] = item

    for result in output:
        rank_search_result(result, query, context)
    output.sort(key=result_sort_key, reverse=True)
    return output
