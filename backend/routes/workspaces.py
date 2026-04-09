import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from backend.workspace_config import get_workspace_config
from backend.database import init_workspace, get_workspace_dir

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    path: str
    color: str = "#7F77DD"


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


@router.get("")
def list_workspaces():
    """List all workspaces"""
    config = get_workspace_config()
    active_path = get_workspace_dir()

    return {
        "workspaces": [
            {
                "name": w.name,
                "path": w.path,
                "color": w.color,
                "last_opened": w.last_opened,
                "is_active": w.path == active_path,
                "exists": os.path.exists(w.path),
            }
            for w in config.workspaces
        ],
        "active_workspace": active_path,
    }


@router.post("/check-path")
def check_workspace_path(body: WorkspaceCreate):
    """Check if a workspace path exists before creating"""
    path = os.path.abspath(os.path.expanduser(body.path))

    return {
        "path": path,
        "exists": os.path.exists(path),
        "is_directory": os.path.isdir(path) if os.path.exists(path) else None,
        "needs_creation": not os.path.exists(path),
    }


@router.post("")
def create_workspace(body: WorkspaceCreate):
    """Create a new workspace"""
    config = get_workspace_config()

    try:
        ws = config.add_workspace(body.name, body.path, body.color)
        return {
            "name": ws.name,
            "path": ws.path,
            "color": ws.color,
            "last_opened": ws.last_opened,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to create workspace: {str(e)}")


@router.post("/{path:path}/activate")
def activate_workspace(path: str):
    """
    Switch to a different workspace.

    IMPORTANT: This requires a page reload for the UI to reflect changes.
    The backend will reconnect to the new workspace's database.
    """
    config = get_workspace_config()

    try:
        # Decode path (FastAPI passes it URL-encoded)
        import urllib.parse
        path = urllib.parse.unquote(path)
        path = os.path.abspath(os.path.expanduser(path))

        # Update config
        config.set_active(path)

        # Reinitialize database connection
        init_workspace(path)

        return {
            "success": True,
            "active_workspace": path,
            "requires_reload": True,
            "message": "Workspace switched. Please reload the page.",
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to switch workspace: {str(e)}")


@router.put("/{path:path}")
def update_workspace(path: str, body: WorkspaceUpdate):
    """Update workspace metadata (name, color)"""
    config = get_workspace_config()

    try:
        import urllib.parse
        path = urllib.parse.unquote(path)
        path = os.path.abspath(os.path.expanduser(path))

        config.update_workspace(path, name=body.name, color=body.color)

        ws = next((w for w in config.workspaces if w.path == path), None)
        return {
            "name": ws.name,
            "path": ws.path,
            "color": ws.color,
            "last_opened": ws.last_opened,
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to update workspace: {str(e)}")


@router.delete("/{path:path}")
def delete_workspace(path: str):
    """
    Remove a workspace from the list.

    NOTE: This does NOT delete any files — only removes it from your workspace list.
    Your PDFs and database remain on disk at the original location.
    """
    config = get_workspace_config()

    try:
        import urllib.parse
        path = urllib.parse.unquote(path)
        path = os.path.abspath(os.path.expanduser(path))

        # Can't delete the only workspace
        if len(config.workspaces) == 1:
            raise HTTPException(400, "Cannot delete the only workspace")

        # Can't delete the active workspace without switching first
        if path == get_workspace_dir():
            raise HTTPException(400, "Cannot delete the active workspace. Switch to another workspace first.")

        config.remove_workspace(path)
        return {"success": True, "message": "Workspace removed from list (files not deleted)"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to delete workspace: {str(e)}")
