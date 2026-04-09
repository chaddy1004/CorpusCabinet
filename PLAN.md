# Corpus Cabinet — Project Plan

A personal reference manager built as a Python webapp. Think Zotero, but with proper
conference BibTeX (not arXiv links), AI summaries, and a clean three-panel UI.

**Name:** Corpus Cabinet — a cabinet of curated academic works. Style as "Corpus Cabinet" (two words) in the UI.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | FastAPI | Async, fast, great for file uploads |
| Database | SQLite + SQLAlchemy | Simple, file-based, no setup |
| PDF parsing | PyMuPDF (fitz) | Fast title + text extraction |
| Scholar search | SerpAPI | Google Scholar API for BibTeX |
| AI summaries | Anthropic Claude API | Task + methodology extraction |
| Frontend | Vanilla JS | No build step, served by FastAPI |

---

## Directory Structure

```
corpus-cabinet/
├── PLAN.md                  ← you are here
├── requirements.txt
├── .env.example
├── .env                     ← you create this, never commit
├── backend/
│   ├── main.py              ← FastAPI app entry point
│   ├── database.py          ← SQLAlchemy engine + session
│   ├── models.py            ← ORM models
│   ├── pipeline.py          ← PDF → title → SerpAPI → Claude → DB
│   ├── serpapi.py           ← SerpAPI Scholar search + BibTeX fetch
│   └── routes/
│       ├── projects.py      ← CRUD for projects
│       ├── papers.py        ← upload, list, get, delete papers
│       └── tags.py          ← add/remove tags, search by tag
├── frontend/
│   ├── index.html           ← single page app shell
│   └── app.js               ← all UI logic (three-panel layout)
└── data/
    └── projects/            ← PDFs stored here, one folder per project
        └── {project_name}/
            └── paper.pdf
```

---

## Database Schema

### Table: projects
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String | unique |
| color | String | hex color for sidebar dot |
| folder_path | String | absolute path to PDF folder |
| created_at | DateTime | |

### Table: papers
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| project_id | Integer FK | → projects.id |
| title | String | extracted from PDF or Scholar |
| authors | String | |
| conference | String | e.g. "CVPR 2023" |
| year | Integer | |
| bibtex | Text | full BibTeX string |
| summary | Text | AI-generated (task + methodology) |
| file_path | String | absolute path to PDF |
| scholar_id | String | SerpAPI result_id, for re-fetching |
| created_at | DateTime | |

### Table: tags
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String | unique, lowercase, hyphenated |
| color | String | hex background color |
| text_color | String | hex text color |

### Table: paper_tags (junction)
| Column | Type | Notes |
|---|---|---|
| paper_id | Integer PK+FK | → papers.id |
| tag_id | Integer PK+FK | → tags.id |

---

## API Endpoints

### Projects
- `GET  /projects` — list all projects
- `POST /projects` — create project (name, color)
- `DELETE /projects/{id}` — delete project + all papers

### Papers
- `GET  /papers?project_id=&tag=&q=&scope=local|global` — search/filter
- `POST /papers/upload` — upload PDF (multipart, project_id in form)
- `GET  /papers/{id}` — get single paper with all metadata
- `DELETE /papers/{id}` — delete paper (DB + file)

### Tags
- `GET  /tags` — all tags with usage counts
- `POST /papers/{id}/tags` — add tag to paper `{ "name": "transformer" }`
- `DELETE /papers/{id}/tags/{tag_name}` — remove tag from paper

---

## PDF Upload Pipeline

When a PDF is dropped on the UI:

1. **Frontend** sends multipart POST to `/papers/upload` with `file` + `project_id`
2. **FastAPI** saves PDF to `data/projects/{project_name}/{filename}`
3. **PyMuPDF** extracts title from first page (first large bold text or first line)
4. **SerpAPI** searches Google Scholar for the title → gets `result_id`
5. **SerpAPI** calls `google_scholar_cite` engine with `result_id` → gets BibTeX URL
6. **requests.get(bibtex_url)** fetches raw `.bib` text
7. **Claude API** receives extracted PDF text → returns JSON summary:
   ```json
   { "task": "...", "methodology": "...", "datasets": ["..."], "metrics": ["..."] }
   ```
8. All metadata saved to SQLite `papers` table
9. Response returned to frontend with full paper object

---

## Search Logic

- **Local search**: filter by `project_id` + optional `tag` + optional text `q`
- **Global search**: same but no `project_id` filter
- **Tag filter**: JOIN through `paper_tags` → `tags` on `name`
- **Text filter**: `Paper.title.ilike(f"%{q}%")`

---

## Claude API Summary Prompt

```
You are a research assistant. Given the text of an academic paper, extract:
1. Task: what problem/task does this paper address? (1-2 sentences)
2. Methodology: what is the core technical approach? (2-3 sentences)
3. Datasets: list dataset names used for evaluation
4. Metrics: list evaluation metrics used

Respond ONLY with a JSON object:
{
  "task": "...",
  "methodology": "...",
  "datasets": ["..."],
  "metrics": ["..."]
}
```

---

## Environment Variables (.env)

```
SERPAPI_KEY=your_serpapi_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

Workspace locations are managed via the UI (stored in `~/.corpus-cabinet/workspaces.json`).

---

## UI Layout

Three-panel layout (see mockup sessions in Claude.ai):

```
┌──────────────┬─────────────────────┬──────────────────────────┐
│   Sidebar    │    Paper list        │      Detail panel        │
│              │                      │                          │
│ - Projects   │ - Search bar         │ - Title + authors        │
│ - Tag filter │ - Scope toggle       │ - Conference badge       │
│   sidebar    │   (local/global)     │ - AI summary             │
│              │ - Active tag chip    │ - Tag editor             │
│              │ - Drop zone          │   (add/remove tags)      │
│              │ - Paper cards        │ - BibTeX (copy button)   │
│              │   with tags          │ - Datasets + metrics     │
└──────────────┴─────────────────────┴──────────────────────────┘
```

---

## Features Planned (Not Yet Built)

These are noted so Claude Code doesn't implement them prematurely:

- **Cluster view**: group papers by topic using embeddings (e.g. sentence-transformers)
- **Citation graph**: visualize how papers connect via citations
- **Evaluation table**: sort papers by shared dataset/metric/value across a project

---

## Version 2: Desktop App (Electron)

v1 is a webapp (current). v2 will be an Electron desktop app with:
- Native OS folder picker dialogs (no manual path typing)
- Packaged installers for Mac/Windows/Linux
- Same backend (FastAPI) and database (SQLite)
- 100% compatible workspace format (can switch between v1 and v2)

See CLAUDE.md for full v2 architecture and migration plan.

---

## Build Order (for Claude Code)

1. `models.py` + `database.py` — get schema right first
2. `serpapi.py` — test BibTeX fetching independently
3. `pipeline.py` — full upload pipeline with mocked Claude first
4. `routes/` — FastAPI routes one file at a time
5. `main.py` — wire everything together, serve frontend
6. `frontend/index.html` + `app.js` — three-panel UI

---

## Known Gotchas

- SerpAPI `google_scholar_cite` is NOT listed in their sidebar UI — call it directly via `engine=google_scholar_cite` parameter
- BibTeX from Scholar is a direct GET request to the URL in `links[name=="BibTeX"]` — no auth needed
- PyMuPDF title extraction is heuristic — first large text block on page 0. May need fallback to first line if that fails
- SQLite has no migration tool by default — use Alembic if schema changes are needed later
- `paper_tags` is a pure junction table, no ORM model needed (use SQLAlchemy `Table`)
