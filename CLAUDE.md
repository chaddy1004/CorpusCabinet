# Corpus Cabinet

A personal academic reference manager. Upload a PDF → auto-extracts title, fetches Scholar metadata + BibTeX via SerpAPI, generates AI summary via Claude API, stores everything in SQLite.

## Stack

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **PDF parsing**: PyMuPDF (`fitz`)
- **Scholar metadata**: SerpAPI (`google_scholar` + `google_scholar_cite` engines)
- **AI summaries**: Anthropic Claude API (`claude-sonnet-4-20250514`)
- **Frontend**: Vanilla JS, single-page, no build step

## Structure

```
backend/
  database.py       — SQLAlchemy engine, session, Base, init_db()
  models.py         — Project, Paper, Tag ORM models + paper_tags junction table
  pipeline.py       — full upload pipeline: PDF → title → SerpAPI → Claude → DB
  serpapi.py        — Scholar search + BibTeX fetch
  routes/
    projects.py     — CRUD /projects
    papers.py       — /papers upload, list, get, delete
    tags.py         — /papers/{id}/tags add/remove, /tags list
frontend/
  index.html        — app shell + all CSS (CSS vars, dark mode, three-panel layout)
  app.js            — all UI logic (state, rendering, API calls, drop zone)
main.py             — FastAPI app, mounts frontend, registers routers, calls init_db()
```

## Running

```bash
# local
uvicorn main:app --reload

# docker
docker compose up --build
```

Requires a `.env` file (copy `.env.example`):
```
SERPAPI_KEY=...
ANTHROPIC_API_KEY=...
DATABASE_URL=sqlite:///./corpus_cabinet.db
DATA_DIR=./data/projects
```

## Key design decisions

- Files are stored on disk at `data/projects/{project_name}/{filename}.pdf`; path saved in DB
- `paper_tags` is a pure SQLAlchemy `Table` (no ORM class)
- Upload pipeline runs synchronously (Scholar + Claude calls block the request) — good enough for personal use
- Tag colors auto-assigned from an 8-color cycle in `tags.py`
- Title extraction uses largest-font span on page 0; falls back to first text line
- BibTeX fetched via two SerpAPI calls: `google_scholar` → result_id → `google_scholar_cite` → BibTeX URL → raw GET

## Features NOT yet built (don't implement prematurely)

- Cluster view (embeddings-based topic grouping)
- Citation graph
- Evaluation table (sort papers by dataset/metric/value)
