# Corpus Cabinet — Desktop MVP Plan

## Goal

Build a native, local-first desktop reference manager for macOS, Windows, and
Linux. The application must run as one PySide6 process with no browser and no
FastAPI server. Its core must be easy to extend with an AI paper-reading
assistant later.

## Immediate experiments

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
