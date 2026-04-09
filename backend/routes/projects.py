import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import nullslast
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.database import get_db, get_workspace_dir
from backend.models import Project

router = APIRouter(prefix="/projects", tags=["projects"])


def get_projects_dir() -> str:
    """Get the projects directory for the current workspace"""
    return os.path.join(get_workspace_dir(), "projects")


class ProjectCreate(BaseModel):
    name:  str
    color: str = "#7F77DD"


class ProjectUpdate(BaseModel):
    name:  str | None = None
    color: str | None = None


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(nullslast(Project.position.asc()), Project.created_at).all()
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

    folder_path = os.path.abspath(os.path.join(get_projects_dir(), body.name))
    os.makedirs(folder_path, exist_ok=True)

    project = Project(name=body.name, color=body.color, folder_path=folder_path)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


class ReorderBody(BaseModel):
    project_ids: list[int]

@router.put("/reorder")
def reorder_projects(body: ReorderBody, db: Session = Depends(get_db)):
    for i, pid in enumerate(body.project_ids):
        project = db.query(Project).get(pid)
        if project:
            project.position = i
    db.commit()
    return {"ok": True}


@router.put("/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db)):
    project = db.query(Project).get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if body.name:
        conflict = db.query(Project).filter_by(name=body.name).first()
        if conflict and conflict.id != project_id:
            raise HTTPException(400, f"Project '{body.name}' already exists")
        project.name = body.name
    if body.color:
        project.color = body.color
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "color": project.color, "folder_path": project.folder_path}


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    db.delete(project)
    db.commit()
    return {"ok": True}
