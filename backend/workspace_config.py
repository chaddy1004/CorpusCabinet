import os
import json
from datetime import datetime, timezone
from typing import List, Optional

# Config file location - in user's home directory for persistence
CONFIG_DIR = os.path.expanduser("~/.corpus-cabinet")
CONFIG_FILE = os.path.join(CONFIG_DIR, "workspaces.json")


class Workspace:
    def __init__(self, name: str, path: str, color: str = "#7F77DD"):
        self.name = name
        self.path = os.path.abspath(os.path.expanduser(path))
        self.color = color
        self.last_opened = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {
            "name": self.name,
            "path": self.path,
            "color": self.color,
            "last_opened": self.last_opened,
        }

    @classmethod
    def from_dict(cls, data: dict):
        ws = cls(data["name"], data["path"], data.get("color", "#7F77DD"))
        ws.last_opened = data.get("last_opened", datetime.now(timezone.utc).isoformat())
        return ws


class WorkspaceConfig:
    def __init__(self):
        self.workspaces: List[Workspace] = []
        self.active_path: Optional[str] = None
        self.load()

    def load(self):
        """Load config from JSON file, or create default if missing"""
        os.makedirs(CONFIG_DIR, exist_ok=True)

        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                self.workspaces = [Workspace.from_dict(w) for w in data.get("workspaces", [])]
                self.active_path = data.get("active_workspace")
        else:
            # First run - migrate from .env if exists, or create default workspace
            from dotenv import load_dotenv
            load_dotenv()

            default_path = os.getenv("WORKSPACE_DIR", "./workspace")
            default_path = os.path.abspath(os.path.expanduser(default_path))

            default_ws = Workspace("My Papers", default_path)
            self.workspaces = [default_ws]
            self.active_path = default_path
            self.save()

    def save(self):
        """Persist config to JSON file"""
        data = {
            "workspaces": [w.to_dict() for w in self.workspaces],
            "active_workspace": self.active_path,
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def get_active_workspace(self) -> Optional[Workspace]:
        """Get the currently active workspace"""
        if not self.active_path:
            return self.workspaces[0] if self.workspaces else None
        return next((w for w in self.workspaces if w.path == self.active_path), None)

    def add_workspace(self, name: str, path: str, color: str = "#7F77DD") -> Workspace:
        """Add a new workspace"""
        path = os.path.abspath(os.path.expanduser(path))

        # Check if workspace with this path already exists
        existing = next((w for w in self.workspaces if w.path == path), None)
        if existing:
            raise ValueError(f"Workspace at {path} already exists")

        ws = Workspace(name, path, color)
        self.workspaces.append(ws)

        # Create the workspace directory
        os.makedirs(ws.path, exist_ok=True)
        os.makedirs(os.path.join(ws.path, "projects"), exist_ok=True)

        self.save()
        return ws

    def remove_workspace(self, path: str):
        """Remove a workspace from the list (does NOT delete files)"""
        path = os.path.abspath(os.path.expanduser(path))
        self.workspaces = [w for w in self.workspaces if w.path != path]

        # If we removed the active workspace, switch to first available
        if self.active_path == path:
            self.active_path = self.workspaces[0].path if self.workspaces else None

        self.save()

    def set_active(self, path: str):
        """Switch to a different workspace"""
        path = os.path.abspath(os.path.expanduser(path))
        ws = next((w for w in self.workspaces if w.path == path), None)
        if not ws:
            raise ValueError(f"Workspace {path} not found")

        self.active_path = path
        ws.last_opened = datetime.now(timezone.utc).isoformat()
        self.save()

    def update_workspace(self, path: str, name: Optional[str] = None, color: Optional[str] = None):
        """Update workspace metadata"""
        path = os.path.abspath(os.path.expanduser(path))
        ws = next((w for w in self.workspaces if w.path == path), None)
        if not ws:
            raise ValueError(f"Workspace {path} not found")

        if name:
            ws.name = name
        if color:
            ws.color = color

        self.save()


# Global config instance
_config = None

def get_workspace_config() -> WorkspaceConfig:
    """Get the singleton workspace config"""
    global _config
    if _config is None:
        _config = WorkspaceConfig()
    return _config
