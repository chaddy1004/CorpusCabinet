import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Paper, Project, Tag
from backend.pipeline import save_pdf_to_project, process_pdf

router = APIRouter(prefix="/papers", tags=["papers"])


def paper_to_dict(p: Paper) -> dict:
    return {
        "id":          p.id,
        "project_id":  p.project_id,
        "project":     p.project.name if p.project else "",
        "title":       p.title,
        "authors":     p.authors,
        "conference":  p.conference,
        "year":        p.year,
        "bibtex":      p.bibtex,
        "task":        p.task,
        "methodology": p.methodology,
        "datasets":    p.datasets.split(", ") if p.datasets else [],
        "metrics":     p.metrics.split(", ") if p.metrics else [],
        "file_path":   p.file_path,
        "scholar_id":  p.scholar_id,
        "created_at":  p.created_at,
        "tags": [
            {"name": t.name, "color": t.color, "text_color": t.text_color}
            for t in p.tags
        ],
    }


@router.get("")
def list_papers(
    project_id: int | None = None,
    tag: str | None = None,
    q: str | None = None,
    scope: str = "local",        # "local" | "global"
    db: Session = Depends(get_db)
):
    query = db.query(Paper)

    if scope == "local" and project_id:
        query = query.filter(Paper.project_id == project_id)

    if tag:
        query = query.join(Paper.tags).filter(Tag.name == tag)

    if q:
        query = query.filter(Paper.title.ilike(f"%{q}%"))

    papers = query.order_by(Paper.created_at.desc()).all()
    return [paper_to_dict(p) for p in papers]


@router.post("/upload")
async def upload_paper(
    background_tasks: BackgroundTasks,
    project_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    project = db.query(Project).get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    content = await file.read()
    pdf_path = save_pdf_to_project(content, file.filename, project)

    # Run the full pipeline (Scholar + Claude) — returns paper immediately
    # For production consider making this async/background and polling
    paper = process_pdf(pdf_path, project, db, file.filename)
    return paper_to_dict(paper)


@router.get("/{paper_id}")
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    return paper_to_dict(paper)


@router.delete("/{paper_id}")
def delete_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")

    # Delete the PDF file from disk
    if os.path.exists(paper.file_path):
        os.remove(paper.file_path)

    db.delete(paper)
    db.commit()
    return {"ok": True}
