from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_workspace
from backend.workspace_config import get_workspace_config
from backend.routes import projects, papers, tags, workspaces

app = FastAPI(title="Corpus Cabinet")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(workspaces.router)
app.include_router(projects.router)
app.include_router(papers.router)
app.include_router(tags.router)

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
def root():
    return FileResponse("frontend/index.html")

@app.on_event("startup")
def startup():
    # Load workspace config and initialize the active workspace
    config = get_workspace_config()
    active_ws = config.get_active_workspace()

    if active_ws:
        init_workspace(active_ws.path)
        print(f"Corpus Cabinet started")
        print(f"Active workspace: {active_ws.name} ({active_ws.path})")
    else:
        print("WARNING: No active workspace configured")
