# Corpus Cabinet

Corpus Cabinet is a native desktop academic reference manager for macOS,
Windows, and Linux. It is designed around a local library of papers: choose a
library folder, organize papers into projects, and read PDFs without a browser
or local web server.

## Current MVP

- Native PySide6/Qt desktop window
- Local library and workspace persistence
- Project creation, renaming, deletion, and paper counts
- Multi-PDF import with safe copied filenames
- Title and author metadata extraction from PDFs
- Bounded extracted text stored for future AI assistance
- Search by paper title or authors
- In-app PDF viewing
- Home “Recently opened” with saved PDF page and zoom, including full-screen reading
- Unread / Reading / Read status, favorites, and reading-status sorting
- Separate library-wide search across titles, authors, abstracts, extracted PDF text,
  personal notes, and PDF comments, with match excerpts
- Autosaved project research notes, separate from individual paper notes
- Native confirmation dialogs and background PDF importing
- Online title search through Crossref, arXiv, and OpenAlex
- User-controlled Offline Mode that keeps all local features available
- Confirmed metadata application and citation-only paper records
- Background download and import of direct open-access PDF links
- Google Scholar browser handoff for manual Scholar searching
- A provider-independent `AssistantEngine` foundation for future paper chat

The AI reading assistant, retrieval from sources without a direct open-access
PDF, tagging, citation management, and synchronization are planned follow-up
milestones. The old FastAPI/browser
prototype remains in the repository as reference code but is not the desktop
launch path.

## Run locally

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
./run.sh
```

The equivalent direct command is:

```bash
uv run run_desktop.py
```

Set `WORKSPACE_DIR` when developing against a specific library folder:

```bash
WORKSPACE_DIR=/path/to/library uv run run_desktop.py
```

Optionally set `CROSSREF_MAILTO` to identify requests to Crossref politely:

```bash
CROSSREF_MAILTO=you@example.com uv run run_desktop.py
```

If `WORKSPACE_DIR` is not set, the app creates its default library under the
platform's application-data directory.

## Online search and Offline Mode

Use **+ Add paper** in the currently selected project's Papers pane to upload
PDFs or search online by title or paper link. The app searches Crossref, arXiv,
and OpenAlex in the background. It combines duplicate records and shows the
title, authors, venue, year, DOI, abstract, and available links for confirmation.
You can apply the metadata to the selected local paper or save it to the current
project without a local PDF.

When a result exposes a direct open-access PDF URL, **Download PDF** downloads it
in the background, imports it into the current project, extracts its text, and
keeps the online metadata and source links. Results with only a publisher page
remain available through **Open source**; the app does not bypass paywalls.

Once a paper has saved source metadata, its detail view keeps **Open source** and
**Google Scholar** actions available, so you do not need to search for the paper
again. Offline Mode disables those external actions while preserving local use.

Enable **Offline mode** in the header whenever you want a visibly local-only
session. The toggle is persisted in the workspace registry. While it is on,
the online search and external-link actions are disabled, but projects, local
search, PDFs, citation-only records, and AI context preparation continue to
work. Search results are never accepted or written automatically.

## Reading and research workflow

- **Recently opened** on Home reopens a recently opened PDF at its saved page and
  zoom. Opening Details alone does not add a paper to reading history.
- Zoom PDFs with the percentage menu, **−/+** buttons, trackpad pinch, or
  Ctrl/Cmd + scroll. Both PDF readers remember custom zoom levels (25–400%).
- Choose **Reading status** or **Favorite** below the paper title. Status is
  explicitly controlled by you; it does not automatically change on opening a PDF.
- Project rows also have a yellow-star favorite toggle, independent of paper
  favorites. Starring a project does not change the current selection or order.
- **+ Add tags / Edit tags** accepts comma-separated labels. Click a tag to find
  matching papers across projects, or use `tag:robotics` / `tag:"latent actions"`
  in library search. Tags are case-insensitive and copied to new paper copies;
  editing tags afterward affects only that copy.
- **Search library** in the header (Cmd/Ctrl+F) searches saved papers across all
  projects, including notes and page comments. It works offline and never sends
  your query or paper text to an online service. PDF text search covers the bounded
  text extracted during import, not OCR of scanned pages.
- **Project notes…** beneath the Projects list opens an autosaved plain-text
  workspace for cross-paper findings, open questions, baselines, and next steps.
- Independent paper copies have separate reading positions and can have different
  reading statuses. Copying initially preserves the status, favorite, and notes;
  moving out of ScrapBook retains the original record's reading state.

See [TODO.md](TODO.md) for completed priorities and the living feature backlog.

## Library layout

```text
your-library/
├── corpus_cabinet.db
└── projects/
    └── Project Name/
        └── paper.pdf
```

The workspace registry is stored in the platform application-config directory.
The library itself remains an ordinary folder that can be backed up or moved.

## Architecture

```text
PySide6 desktop UI
        │
        ▼
Local application engine
├── storage.py       SQLite and library operations
├── pdfs.py          PDF metadata and bounded text extraction
├── search.py        Crossref, arXiv, and OpenAlex adapters
└── assistant.py     AI-provider boundary and paper context preparation
```

The UI talks directly to the application engine. There is no FastAPI process,
browser tab, or localhost port in the desktop path.

## Tests

```bash
uv run pytest -q
```

The tests use temporary libraries and an offscreen Qt platform. They do not
call the internet, SerpAPI, or an AI provider; provider responses are mocked.

## Packaging direction

The production packaging path is `pyside6-deploy`/Nuitka, with builds made on
native macOS, Windows, and Linux runners. The intended release artifacts are a
signed/notarized macOS app, a signed Windows installer, and a Linux AppImage.
