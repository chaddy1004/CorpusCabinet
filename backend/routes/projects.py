import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.database import get_db
from backend.models import Project

load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", "./data/projects")
router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name:  str
    color: str = "#7F77DD"


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at).all()
    return [
        {
            "id":           p.id,
            "name":         p.name,
            "color":        p.color,
            "folder_path":  p.folder_path,
            "paper_count":  len(p.papers),
            "created_at":   p.created_at,
        }
        for p in projects
    ]


@router.post("")
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    existing = db.query(Project).filter_by(name=body.name).first()
    if existing:
        raise HTTPException(400, f"Project '{body.name}' already exists")

    folder_path = os.path.abspath(os.path.join(DATA_DIR, body.name))
    os.makedirs(folder_path, exist_ok=True)

    project = Project(name=body.name, color=body.color, folder_path=folder_path)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    db.delete(project)
    db.commit()
    return {"ok": True}
