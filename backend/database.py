from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session
from contextlib import contextmanager
import os

# Will be set by workspace config on startup
_engine = None
_SessionLocal = None
_current_workspace_dir = None

class Base(DeclarativeBase):
    pass


def get_workspace_dir() -> str:
    """Get the current workspace directory"""
    if _current_workspace_dir is None:
        raise RuntimeError("Workspace not initialized. Call init_workspace() first.")
    return _current_workspace_dir


def init_workspace(workspace_path: str):
    """Initialize database connection for a workspace"""
    global _engine, _SessionLocal, _current_workspace_dir

    workspace_path = os.path.abspath(os.path.expanduser(workspace_path))
    os.makedirs(workspace_path, exist_ok=True)
    os.makedirs(os.path.join(workspace_path, "projects"), exist_ok=True)

    # Database lives in the workspace
    db_path = os.path.join(workspace_path, "corpus_cabinet.db")
    database_url = f"sqlite:///{db_path}"

    # Close existing engine if switching workspaces
    if _engine is not None:
        _engine.dispose()

    # Create new engine and session for this workspace
    _engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False}  # needed for SQLite
    )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    _current_workspace_dir = workspace_path

    # Ensure tables exist
    from backend.models import Project, Paper, Tag  # noqa: F401
    from sqlalchemy import text
    Base.metadata.create_all(bind=_engine)

    # Migrations: add columns that may not exist in older DBs
    with _engine.connect() as conn:
        for stmt in [
            "ALTER TABLE papers ADD COLUMN position INTEGER",
            "ALTER TABLE projects ADD COLUMN position INTEGER",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists

    print(f"[database] Workspace initialized: {workspace_path}")
    print(f"[database] Database: {db_path}")


def get_db():
    """FastAPI dependency for database sessions"""
    if _SessionLocal is None:
        raise RuntimeError("Workspace not initialized")
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_direct() -> Session:
    """Get a database session directly (for use outside FastAPI)"""
    if _SessionLocal is None:
        raise RuntimeError("Workspace not initialized")
    return _SessionLocal()


# Backward compatibility
WORKSPACE_DIR = property(lambda self: get_workspace_dir())


def init_db():
    """Legacy compatibility - does nothing now (init_workspace handles it)"""
    pass
