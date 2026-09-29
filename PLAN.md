# Corpus Cabinet — Desktop MVP Plan

## Current milestone — Local versioned documents (September 29, 2026)

Archive agent-generated HTML and Markdown as first-class local research
sources. Preserve the exact original file, generate an offline searchable
Reader representation, and let self-contained HTML keep its interactive
widgets inside an isolated local web view.

1. **M3: Local document import** — add `.html`, `.htm`, `.md`, and `.markdown`
   to the project-scoped Add Source flow, package bounded relative assets, and
   retain the original bytes alongside the Reader document.

   Decision gate: representative HTML and Markdown fixtures must reopen
   offline, remain searchable, and reproduce headings, prose, code, tables,
   embedded images, and self-contained widget scripts without giving the page
   network access or access outside its archived bundle.

2. **M4: Immutable revision history** — add explicit **Add revision**, retain
   every prior snapshot, switch the active version without deleting history,
   and compare normalized text between any two revisions.

   Decision gate: importing and activating revisions must survive restart,
   leave document-level notes/tags/projects intact, reject identical revisions,
   and provide a bounded readable diff.

Both decision gates passed on September 29, 2026. Representative HTML and
Markdown reopen offline, a JavaScript range-widget check runs inside the
isolated archive, and revision persistence, activation, duplicate rejection,
copying, and comparison are covered by automated tests.

### Explicitly not in this milestone

- Automatic filesystem watching or silently importing changed files
- Executing native programs, shell commands, or Python referenced by documents
- Visual pixel-by-pixel comparison of HTML layouts
- Downloading arbitrary remote dependencies required by an HTML application

## Current milestone — General research sources (September 25, 2026)

Expand Corpus Cabinet from a paper-only library into a local-first library of
research sources without regressing the existing paper and PDF workflow.

1. **M1: General source foundation** — add a backwards-compatible source type,
   canonical URL, structured metadata, and cached-content path to existing
   library records. Broaden the visible library language to sources, add type
   indicators and a type filter, and keep existing databases migrating in place.

   Decision gate: existing paper libraries and tests must continue to work,
   while article and repository records can participate in projects, tags,
   notes, favorites, duplicate checks, copying, and library-wide search.

2. **M2: Article and GitHub capture** — accept a public URL in Add Source,
   recognize GitHub repositories versus ordinary web articles, show a preview,
   and save user-confirmed metadata plus a local, searchable Reader document.
   Article captures keep the source HTML and readable content; repository
   captures keep a searchable offline README snapshot and a link to the full
   repository.

   Decision gate: mocked network tests must import both source types, reopen
   their cached Reader documents offline, find their content through library
   search, reject unsafe/non-HTTP URLs, and warn about destination duplicates.

### Explicitly not in this milestone

- YouTube metadata, captions, timestamped notes, or video downloads
- Full Git repository cloning, code indexing, or automatic refresh monitoring
- Authenticated/private websites and repositories
- JavaScript-rendered page capture or bypassing paywalls and access controls
- Browser extensions and macOS Share extensions

### Verification

Completed September 25, 2026. Existing records migrate in place as `paper`
sources. Articles and GitHub repository roots require a user-reviewed preview,
store immutable local Reader snapshots, reopen offline, and participate in
projects, ScrapBook movement, independent copies, tags, notes, favorites,
duplicate detection, type filtering, and library-wide search. The complete
offline test suite passes with mocked web and GitHub responses. GitHub capture
stores only the repository identity, original link, and README; detailed
repository information remains available on GitHub.

## Goal

Build a native, local-first desktop reference manager for macOS, Windows, and
Linux. The application must run as one PySide6 process with no browser and no
FastAPI server. Its core must be easy to extend with an AI paper-reading
assistant later.

## Immediate experiments

### Experimental comfortable Reader (September 19, 2026)

Keep the original PDF authoritative. Build a local, optional Reader with
single-column text, adjustable typography, original visual crops, source-page
links, and reference previews. No paraphrasing or cloud PDF uploads.

1. **Bounded local PDF conversion** — use Docling's layout analysis with
   prefetched models, CPU execution, and a separately cached structured document.
   Start with an explicitly labeled short preview rather than silently treating
   a partial conversion as a complete paper. Compare reading order, text, and
   visual regions against representative original PDFs.
   Decision gate: if the preview preserves content and order, expand coverage;
   otherwise keep the original PDF and document the conversion failures.

2. **Native Reader and reference previews** — display the cached structure in
   Qt, retaining original page locations and previewing linked destinations
   without navigating away. Verify typography, assets, offline behavior, stale
   background results, and unchanged PDF bytes with temporary-library tests.
   Decision gate: if the reading experience is useful without losing source
   access, trial it on more papers before making Reader the default.

1. **Native shell and PDF viewer**

   Build a Qt three-panel window with project navigation, paper navigation, a
   details panel, and an embedded PDF viewer.

   Decision gate: if the window and PDF viewer work on the development machine,
   continue with the local library implementation; otherwise resolve the Qt
   platform issue before adding features.

2. **Temporary-library integration**

   Create a library, create a project, import a PDF, extract bounded text, show
   the paper in the UI, and reopen its PDF.

   Decision gate: if the flow passes without a network service or installed
   Python environment beyond the packaged app, continue to packaging and the AI
   assistant milestone.

## MVP scope

- Local library/workspace selection and persistence
- SQLite-backed projects and papers
- Project creation, rename, deletion, and paper counts
- Multi-PDF import with background processing
- PDF title/author extraction and bounded text caching
- Paper search by title or author
- Native in-app PDF viewing
- Provider-independent assistant context preparation

## Completed milestone: online search and Offline Mode

Add user-confirmed online paper-title search while keeping the application
fully usable without internet access.

1. **Provider-backed search**

   Query Crossref, arXiv, and OpenAlex from a background task. Normalize and
   de-duplicate results before showing title, authors, venue, year, DOI,
   abstract, and open-access links. Keep Google Scholar as a user-driven
   browser handoff rather than scraping its result pages.

   Decision gate: mocked provider responses and a live read-only query must
   normalize into stable candidates; otherwise keep the provider boundary
   isolated and fix normalization first.

2. **Explicit offline mode**

   Add a persisted user-controlled Offline Mode indicator/toggle. When enabled,
   the app must not make network requests; local projects, local search, PDF
   reading, and AI context preparation remain available.

   Decision gate: if Offline Mode can be enabled, displayed, persisted, and
   tested without network access while local features continue to work, continue
   to open-access PDF linking and metadata enrichment; otherwise keep the
   milestone local-only until the state behavior is reliable.

### Verification

- Mocked Crossref, arXiv, and OpenAlex responses pass normalization and
  de-duplication tests.
- A live read-only title query returns normalized candidates.
- Offline Mode persistence, network blocking, local search, PDF handling, and
  assistant context preparation pass the test suite.
- The source distribution and wheel build successfully.

## Explicitly not now

- AI reading-assistant chat and streaming responses
- Automatic bibliographic enrichment beyond user-confirmed title matching
- Tags, citation graphs, clusters, and synchronization
- Automatic updates and signed release artifacts
