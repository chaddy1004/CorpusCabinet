from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from backend.database import get_db
from backend.models import Tag, Paper, paper_tags

router = APIRouter(tags=["tags"])

# Default colors for auto-created tags (cycles through these)
TAG_COLORS = [
    ("#EEEDFE", "#3C3489"),
    ("#E1F5EE", "#085041"),
    ("#FAECE7", "#712B13"),
    ("#E6F1FB", "#0C447C"),
    ("#FAEEDA", "#633806"),
    ("#EAF3DE", "#27500A"),
    ("#FBEAF0", "#72243E"),
    ("#F1EFE8", "#444441"),
]


class TagAdd(BaseModel):
    name: str


@router.get("/tags")
def list_tags(db: Session = Depends(get_db)):
    """All tags with how many papers use each."""
    tags = db.query(Tag).all()
    return [
        {
            "id":         t.id,
            "name":       t.name,
            "color":      t.color,
            "text_color": t.text_color,
            "count":      len(t.papers),
        }
        for t in tags
    ]


@router.post("/papers/{paper_id}/tags")
def add_tag(paper_id: int, body: TagAdd, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")

    tag_name = body.name.strip().lower().replace(" ", "-")

    tag = db.query(Tag).filter_by(name=tag_name).first()
    if not tag:
        # Auto-assign a color based on how many tags exist
        idx = db.query(Tag).count() % len(TAG_COLORS)
        bg, fg = TAG_COLORS[idx]
        tag = Tag(name=tag_name, color=bg, text_color=fg)
        db.add(tag)
        db.flush()

    if tag not in paper.tags:
        paper.tags.append(tag)
        db.commit()

    return {"name": tag.name, "color": tag.color, "text_color": tag.text_color}


@router.delete("/papers/{paper_id}/tags/{tag_name}")
def remove_tag(paper_id: int, tag_name: str, db: Session = Depends(get_db)):
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")

    tag = db.query(Tag).filter_by(name=tag_name).first()
    if not tag:
        raise HTTPException(404, "Tag not found")

    if tag in paper.tags:
        paper.tags.remove(tag)
        db.commit()

    return {"ok": True}
