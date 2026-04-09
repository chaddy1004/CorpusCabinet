import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import nullslast
from sqlalchemy.orm import Session
from pydantic import BaseModel

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

    papers = query.order_by(nullslast(Paper.position.asc()), Paper.created_at.desc()).all()
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


class ReorderBody(BaseModel):
    paper_ids: list[int]

@router.put("/reorder")
def reorder_papers(body: ReorderBody, db: Session = Depends(get_db)):
    for i, pid in enumerate(body.paper_ids):
        paper = db.query(Paper).get(pid)
        if paper:
            paper.position = i
    db.commit()
    return {"ok": True}


@router.get("/{paper_id}")
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    return paper_to_dict(paper)


@router.post("/{paper_id}/reprocess")
def reprocess_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    if not os.path.exists(paper.file_path):
        raise HTTPException(404, "PDF file not found on disk")

    from backend.pipeline import extract_metadata_with_haiku, _extract_title_fallback, extract_text, summarize_paper
    from backend.serpapi import get_paper_metadata

    pdf_meta    = extract_metadata_with_haiku(paper.file_path)
    pdf_title   = pdf_meta.get("title", "").strip() or _extract_title_fallback(paper.file_path)
    pdf_authors = pdf_meta.get("authors", "").strip()

    meta          = get_paper_metadata(pdf_title, pdf_authors=pdf_authors)
    final_title   = meta.get("title") or pdf_title
    final_authors = pdf_authors or meta.get("authors", "")

    text         = extract_text(paper.file_path)
    summary_data = summarize_paper(text)

    paper.title       = final_title
    paper.authors     = final_authors
    paper.conference  = meta["conference"]
    paper.year        = meta["year"]
    paper.bibtex      = meta["bibtex"]
    paper.scholar_id  = meta["scholar_id"]
    paper.task        = summary_data.get("task", "")
    paper.methodology = summary_data.get("methodology", "")
    paper.datasets    = ", ".join(summary_data.get("datasets", []))
    paper.metrics     = ", ".join(summary_data.get("metrics", []))

    db.commit()
    db.refresh(paper)
    return paper_to_dict(paper)


@router.get("/{paper_id}/pdf")
def get_paper_pdf(paper_id: int, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    if not os.path.exists(paper.file_path):
        raise HTTPException(404, "PDF file not found on disk")
    return FileResponse(paper.file_path, media_type="application/pdf")


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
