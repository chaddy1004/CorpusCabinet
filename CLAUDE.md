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
  database.py        — SQLAlchemy engine, session, Base, workspace-aware init
  models.py          — Project, Paper, Tag ORM models + paper_tags junction table
  pipeline.py        — full upload pipeline: PDF → title → SerpAPI → Claude → DB
  serpapi.py         — Scholar search + BibTeX fetch
  workspace_config.py — Multi-workspace manager (stores in ~/.corpus-cabinet/)
  routes/
    workspaces.py    — Create, list, switch, delete workspaces
    projects.py      — CRUD /projects
    papers.py        — /papers upload, list, get, delete
    tags.py          — /papers/{id}/tags add/remove, /tags list
frontend/
  index.html         — app shell + all CSS (CSS vars, dark mode, three-panel layout)
  app.js             — all UI logic (state, rendering, API calls, workspace switcher)
main.py              — FastAPI app, mounts frontend, registers routers, init workspace
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
```

On first run, creates a default workspace at `./workspace`. Manage workspaces via the UI (click workspace dropdown in sidebar).

## Key design decisions

- **Multi-workspace model**: Like Obsidian/Logseq — each workspace has its own database + PDFs
  - Workspaces stored in: `~/.corpus-cabinet/workspaces.json`
  - Each workspace structure:
    ```
    {workspace_path}/
    ├── corpus_cabinet.db
    └── projects/
        └── {project_name}/
            └── paper.pdf
    ```
  - Switch between workspaces via UI (no restart needed)
- `paper_tags` is a pure SQLAlchemy `Table` (no ORM class)
- Upload pipeline runs synchronously (Scholar + Claude calls block the request) — good enough for personal use
- Tag colors auto-assigned from an 8-color cycle in `tags.py`
- Title extraction uses largest-font span on page 0; falls back to first text line
- BibTeX fetched via two SerpAPI calls: `google_scholar` → result_id → `google_scholar_cite` → BibTeX URL → raw GET

## Features NOT yet built (don't implement prematurely)

- Cluster view (embeddings-based topic grouping)
- Citation graph
- Evaluation table (sort papers by dataset/metric/value)

---

## Version 2: Electron Desktop App (Future)

### Why Electron?

v1 is a webapp where users manually type workspace paths. v2 will be a native desktop app with OS folder picker dialogs (like Logseq/Zettlr).

### Architecture (v2)

```
Electron App
├── main.js          — Node.js process: spawns Python backend, manages windows
├── preload.js       — IPC bridge: exposes selectFolder() to frontend
├── renderer/        — Same frontend as v1 (HTML/JS)
└── python/          — Same backend as v1 (FastAPI, no changes needed)
```

### Key Changes

| Component | v1 (Webapp) | v2 (Electron) |
|-----------|-------------|---------------|
| **Backend** | FastAPI (unchanged) | FastAPI (unchanged) |
| **Database** | SQLite (unchanged) | SQLite (unchanged) |
| **Frontend** | Browser → localhost:8000 | Electron window → embedded backend |
| **Folder picker** | Type path manually | Native OS dialog (`dialog.showOpenDialog`) |
| **Distribution** | Run locally via uvicorn | Packaged .dmg/.exe/.AppImage |

### Database Compatibility

**100% compatible!** The SQLite database and workspace structure are identical:

```
workspace/
├── corpus_cabinet.db          ← Same schema, works in both versions
└── projects/
    └── {project_name}/
        └── paper.pdf          ← Same file structure
```

Users can:
- Point v2 at an existing v1 workspace (seamless migration)
- Switch between v1 and v2 (same data)

### Implementation Notes (for v2)

1. **Python Bundling Options**
   - **Embedded Python** (recommended): Ship with Python + dependencies baked in (~150MB app)
   - **System Python**: Require user to install Python first (smaller app, more setup)

2. **Electron Setup**
   ```bash
   npm install electron electron-builder
   ```

3. **main.js pseudocode**
   ```javascript
   // Spawn Python backend on app start
   const pythonProcess = spawn('python', ['-m', 'uvicorn', 'main:app', '--port', '8000']);

   // Create Electron window
   const win = new BrowserWindow({ ... });
   win.loadURL('http://localhost:8000');

   // Native folder picker
   ipcMain.handle('select-folder', async () => {
     const result = await dialog.showOpenDialog({ properties: ['openDirectory'] });
     return result.filePaths[0];
   });

   // Kill Python on app quit
   app.on('quit', () => pythonProcess.kill());
   ```

4. **Frontend Changes**
   - Settings page: Replace text input with native folder picker button
   - Add `window.electronAPI.selectFolder()` call (via preload.js)
   - Everything else stays the same

5. **Build Commands**
   ```bash
   # Mac
   npm run build:mac  # → .dmg installer

   # Windows
   npm run build:win  # → .exe installer

   # Linux
   npm run build:linux  # → .AppImage/.deb/.rpm
   ```

6. **Distribution**
   - Mac: Sign with Apple Developer ID (for Gatekeeper)
   - Windows: Optional code signing (avoids SmartScreen warnings)
   - Linux: No signing needed

### Migration Path (v1 → v2)

1. User downloads v2 desktop app
2. On first launch: "Select your workspace folder"
3. Point to existing v1 workspace (or create new one)
4. All papers, projects, tags appear immediately (same database)

### When to Build v2

Wait until v1 is stable and well-tested. v2 is mostly packaging/distribution — the core app logic is identical.
