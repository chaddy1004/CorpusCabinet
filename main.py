from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routes import projects, papers, tags

app = FastAPI(title="Corpus Cabinet")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
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
    init_db()
    print("Corpus Cabinet started — DB initialized")
