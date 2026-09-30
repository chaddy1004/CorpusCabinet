# Corpus Cabinet

Corpus Cabinet is a native desktop academic reference manager for macOS,
Windows, and Linux. It is designed around a local library of papers: choose a
library folder, organize papers into projects, and read PDFs without a browser
or local web server.

## Current MVP

- Native PySide6/Qt desktop window
- Local library and workspace persistence
- Project creation, renaming, deletion, favorite grouping, persistent drag
  ordering, and research-source counts
- A blue temporary ScrapBook that stays above projects and disappears from the
  sidebar whenever its staging area is empty
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
- General research sources alongside papers, with project-level type filtering
- Reviewed public article capture with readable text, bounded images, original
  HTML, canonical URL duplicate checks, and persistent offline Reader snapshots
- Reviewed GitHub repository capture with an offline, searchable README Reader
  view and a link back to the full repository
- Local HTML and Markdown archival with exact originals, offline Reader copies,
  isolated interactive HTML, and immutable revision history with text comparison
- A provider-independent `AssistantEngine` foundation for future paper chat

The AI reading assistant, citation management, synchronization, YouTube
transcripts, and full repository code indexing are planned follow-up milestones.
The old FastAPI/browser
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

Use **+ Add source** in the currently selected project's Sources pane to upload
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

## Articles and GitHub repositories

Use **+ Add source → Save a website or GitHub repository…** in the selected
project. Paste a public HTTP(S) URL, inspect the extracted title, creator, source
type, and readable content, then choose the destination projects. Nothing is
saved before this confirmation. ScrapBook remains an exclusive temporary
destination, while standard projects receive independent records.

Ordinary articles keep an immutable original HTML snapshot, readable ordered
blocks, canonical URL, bounded offline images, and searchable text. GitHub
repository roots keep a searchable offline snapshot of the README and a link
back to the original repository. Detailed repository information stays on
GitHub instead of being duplicated in Corpus Cabinet. Both reopen in the
comfortable Reader without internet access. **Open
original article** or **Open repository** returns to the live source when online.
Corpus Cabinet does not bypass authentication, paywalls, or access controls;
JavaScript-only pages may not expose enough readable content to capture.

Use the source-type menu above a project's list to show all sources, papers,
articles, or GitHub repositories. Library search indexes article text, README
content, source metadata, notes, and tags alongside existing paper fields.

## Local HTML and Markdown documents

Use **+ Add source → Add HTML or Markdown from computer…** to archive `.html`,
`.htm`, `.md`, or `.markdown` files. Corpus Cabinet keeps the exact original,
creates a searchable Reader version, and packages local images, stylesheets, and
scripts referenced with relative paths. Self-contained HTML widgets remain
interactive in **Original**, but the archived page cannot use the network or
read files outside its saved bundle. Markdown opens as its exact source text in
that tab.

You can also drag PDFs, HTML files, and Markdown files directly onto the current
project's Sources pane. The open project is selected by default, and the same
confirmation lets you add independent copies to other projects. Mixed drops are
supported.

Use **Versions → Add revision…** when an agent or collaborator produces an
updated document. Every revision is immutable. The newest import becomes the
current Reader version, any prior revision can be made current again, and any
two revisions can be compared as a readable text diff. Document notes, tags,
project membership, and version history remain attached to the same library
record. Importing identical content twice is rejected.

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

## Experimental comfortable Reader

The **Reader** tab first checks for an exact-title official arXiv record and uses
arXiv HTML when available. Known arXiv identifiers are used directly; other
papers require an exact normalized title match before Corpus Cabinet accepts the
record. This prevents a similar paper from being silently substituted. If HTML
is unavailable—or while offline—Reader can generate a three-page local PDF
preview before deciding whether to convert the full paper. Reader
reflows text into one column with adjustable text size and spacing, and preserves
detected figures, tables, and equations as original PDF crops. Click a visual to
expand it, or **Original · p. …** to check a block in the PDF. Internal citation
links open large selectable destination text with a focused original crop, without
changing your PDF reading position. These previews show extracted source context,
not automatically verified reference entries. Some citation labels cannot be
linked inline; use their Preview action. **Compare with PDF** opens an adjustable
side-by-side debugging view; source links then navigate that comparison PDF while
leaving Reader open. **Use OpenDyslexic** applies the same saved accessibility
preference to both abstracts and Reader body text while keeping mathematical
symbols in a dedicated serif face. Official arXiv MathML is converted into
readable inline notation and standalone equations instead of exposed TeX commands.

This is opt-in and **not a guaranteed lossless conversion**. Reading order,
captions, inline mathematics, missed visual regions, and missing content need
checking against the original. Scanned PDFs are not supported in this trial
(OCR is disabled). The original PDF is never modified, and conversion does not
upload PDFs, download models, or require internet access.

Optional setup (installs dependencies and downloads a model, so obtain permission
before running it on someone else's machine):

```bash
uv sync --extra reader
uv run --extra reader docling-tools models download layout -o .reader_models
uv run --extra reader run_desktop.py
```

`./run.sh` preserves the dependencies in an existing virtual environment.
Use `--extra reader` when launching directly through uv; plain `uv run` may
remove optional dependencies. `CORPUS_READER_MODELS` can point to a prefetched
model directory instead. Model files are ignored by Git. Settings live in
`src/corpus_cabinet/configs/config.yaml`; conversion is CPU-only with two threads,
a time limit, and file/page limits. Cancel stops the conversion subprocess.

The canonical Reader cache is a versioned `document.json`: ordered blocks such
as headings, paragraphs, captions, figures, tables, formulas, and references,
plus provenance, internal destinations, warnings, and its source engine. It is
not a flattened Markdown file. PDF conversions keep raster crops beside that
JSON. arXiv conversions also keep the exact downloaded `source.html` and cached
raster figure files. Caches live under `<library>/reader_cache/`; PDF cache keys
include the PDF SHA-256 and conversion settings, while arXiv cache keys include
the arXiv ID and HTML converter version. Preview and full PDF conversions have
separate caches. The successful source key is saved on the paper, so switching
papers or restarting the app reopens the generated view without reconverting;
existing compatible caches are also detected. Original PDF page/zoom history is independent
of Reader typography. This experimental Reader does not yet save its own scroll
position or import publisher HTML outside arXiv.

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

The current Apple-silicon beta can be built locally with:

```bash
uv sync --extra reader --group dev
./scripts/build_macos.sh
```

This creates `dist/CorpusCabinet-<version>-macos-arm64.dmg`. The DMG includes
the Qt runtime, PDF and WebEngine support, Docling, and the prefetched offline
Reader model. It is ad-hoc signed for beta sharing but is not Apple-notarized;
testers should follow `BETA_INSTALL.md`. Libraries remain outside the app bundle,
so replacing the application during an update does not remove user data.
