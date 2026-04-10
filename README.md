# Corpus Cabinet

A personal academic reference manager. Drop a PDF — it automatically extracts the title, fetches metadata and BibTeX from Google Scholar, generates an AI summary, and stores everything in a local SQLite database.

Think Zotero, but with proper conference BibTeX, AI-generated summaries, and a clean three-panel UI.

---

## Features

- **PDF upload** — drag-and-drop or click to upload. Supports multiple files at once.
- **Auto metadata** — title extracted from the PDF, then cross-referenced with Google Scholar to get authors, conference/venue, year, and BibTeX.
- **AI summaries** — Claude extracts the task, methodology, datasets, and evaluation metrics from each paper.
- **BibTeX** — one-click copy for any paper.
- **Projects** — organise papers into projects (e.g. by research topic or reading list).
- **Tags** — add freeform tags to papers; filter the paper list by tag.
- **Search** — search by title within a project or across all projects.
- **Dark mode** — follows your system preference.

---

## Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI |
| Server | Uvicorn |
| Database | SQLite via SQLAlchemy |
| PDF parsing | PyMuPDF |
| Scholar metadata | SerpAPI |
| AI summaries | Anthropic Claude API |
| Frontend | Vanilla JS + HTML/CSS (no build step) |

---

## Setup

### Prerequisites

- Docker + Docker Compose, **or** Python 3.11+
- A [SerpAPI](https://serpapi.com) key (free tier: 100 searches/month)
- An [Anthropic](https://console.anthropic.com) API key

### 1. Clone and configure

```bash
git clone https://github.com/your-username/CorpusCabinet.git
cd CorpusCabinet
cp .env.example .env
```

Edit `.env` and fill in your API keys:

```env
SERPAPI_KEY=your_serpapi_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

### 2. Run with Docker (recommended)

```bash
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000).

Your database and PDFs are stored in `./workspace/` on your host machine and persist across container restarts.

### 3. Run locally (without Docker)

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

---

## How to use

### Creating a project

Click **+ New project** in the sidebar and enter a name. Projects are colour-coded for easy identification.

### Adding a paper

1. Select a project in the sidebar.
2. Drag a PDF onto the drop zone in the middle panel, or click it to open a file picker.
3. The upload pipeline runs automatically:
   - Title extracted from the PDF
   - Google Scholar queried for metadata + BibTeX
   - Claude generates a structured summary
4. The paper appears in the list when processing is complete (typically 5–15 seconds).

### Viewing a paper

Click any paper card to open the detail panel on the right, which shows:

- Title, authors, conference, year
- AI summary (task + methodology)
- Tags
- BibTeX (with copy button)
- Datasets and evaluation metrics extracted by AI

### Tagging

In the detail panel, click **+ Add tag** to add a tag to a paper. Type a new tag name or select an existing one from the suggestions. Tags are auto-coloured and appear in the sidebar for filtering.

To filter by tag, click any tag in the **Filter by tag** section of the sidebar. Click again to clear the filter.

### Searching

Use the search bar at the top of the middle panel to filter by title. Use the **This project / All projects** toggle to scope the search.

### Copying BibTeX

Open a paper, scroll to the BibTeX section, and click **Copy**. The full BibTeX entry is copied to your clipboard.

---

## Data storage

```
workspace/
├── corpus_cabinet.db      ← all metadata, tags, and paper records
└── projects/
    └── {project_name}/
        └── paper.pdf      ← original uploaded PDFs
```

The `workspace/` directory is excluded from version control. Back it up separately if you care about your data.

---

## Limitations

- Upload pipeline is synchronous — the request blocks while Scholar + Claude run. For personal use this is fine; large batches will be slow.
- Scholar metadata quality depends on SerpAPI finding the paper. Niche or very new papers may not match.
- Title extraction is heuristic (largest font on page 1) and occasionally picks up a section header instead of the actual title.
