"""Store libraries, projects, papers, and workspace preferences locally.

The module reads and writes a workspace registry JSON file and one SQLite
database at <library>/corpus_cabinet.db. Uploaded PDFs are copied below
<library>/projects/<project>/, and extracted text is stored in the papers table.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone

from corpus_cabinet.pdfs import extract_pdf_metadata, extract_pdf_text
from corpus_cabinet.web_sources import capture_digest, safe_asset_name


def normalize_paper_identifier(value):
    """Normalize a DOI or provider identifier for duplicate comparison."""
    value = str(value or "").strip().casefold()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip("/.")


def normalize_paper_title(value):
    """Normalize a paper title for conservative exact-title matching."""
    value = str(value or "").casefold()
    return "".join(re.findall(r"[a-z0-9]+", value))


def normalize_tags(value):
    """Use comma-separated, case-insensitive labels without duplicate tags."""
    if isinstance(value, str):
        value = value.split(",")
    tags = []
    for name in value or []:
        name = " ".join(str(name).split()).casefold()
        if "," in name:
            raise ValueError("Use commas to separate tags, not inside a tag")
        if name and name not in tags:
            tags.append(name)
    return sorted(tags)


def calculate_file_sha256(path):
    """Stream one file into a SHA-256 digest without loading it in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def stored_capture_paths(content_path, capture):
    """Return archived original and interactive paths for one capture."""
    cache_path = os.path.dirname(content_path)
    extension = str(capture.get("source_extension") or ".html").casefold()
    if extension not in (".html", ".htm", ".md", ".markdown"):
        extension = ".html"
    original_path = os.path.join(cache_path, "source" + extension)
    interactive_path = ""
    if capture.get("interactive_html"):
        interactive_path = os.path.join(cache_path, "interactive.html")
    return original_path, interactive_path


def library_match_excerpt(text, terms):
    """Return a short readable excerpt around the first matched search term."""
    text = " ".join(str(text or "").split())
    position = 0
    for term in terms:
        match = re.search(re.escape(term), text, re.IGNORECASE)
        if match:
            position = match.start()
            break
    start = max(position - 55, 0)
    end = min(start + 220, len(text))
    excerpt = text[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt += "…"
    return excerpt


class WorkspaceManager:
    """Maintain the list of local libraries and the active library path."""

    def __init__(self, config_path, default_path):
        self.config_path = os.path.abspath(os.path.expanduser(config_path))
        self.default_path = os.path.abspath(os.path.expanduser(default_path))
        self.workspaces = []
        self.active_path = None
        self.offline_mode = False
        self.dyslexic_font = False
        self.load()

    def load(self):
        config_dir = os.path.dirname(self.config_path)
        os.makedirs(config_dir, exist_ok=True)

        if not os.path.exists(self.config_path):
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}

        records = data.get("workspaces", [])
        for record in records:
            path = record.get("path")
            if not path:
                continue
            self.workspaces.append({
                "name": record.get("name") or os.path.basename(path),
                "path": os.path.abspath(os.path.expanduser(path)),
            })

        active_path = data.get("active_workspace")
        if active_path:
            self.active_path = os.path.abspath(os.path.expanduser(active_path))

        self.offline_mode = bool(data.get("offline_mode", False))
        self.dyslexic_font = bool(data.get("dyslexic_font", False))

        if self.active_path and not self.find(self.active_path):
            self.active_path = None

    def save(self):
        config_dir = os.path.dirname(self.config_path)
        os.makedirs(config_dir, exist_ok=True)
        data = {
            "workspaces": self.workspaces,
            "active_workspace": self.active_path,
            "offline_mode": self.offline_mode,
            "dyslexic_font": self.dyslexic_font,
        }
        temporary_path = self.config_path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary_path, self.config_path)

    def find(self, path):
        normalized_path = os.path.abspath(os.path.expanduser(path))
        for workspace in self.workspaces:
            if workspace["path"] == normalized_path:
                return workspace
        return None

    def ensure_default(self):
        if not self.workspaces:
            self.add(self.default_path, "My Library")

        if not self.active_path:
            self.active_path = self.workspaces[0]["path"]
            self.save()

    def add(self, path, name=None):
        normalized_path = os.path.abspath(os.path.expanduser(path))
        existing = self.find(normalized_path)
        if existing:
            return existing

        os.makedirs(normalized_path, exist_ok=True)
        os.makedirs(os.path.join(normalized_path, "projects"), exist_ok=True)

        if not name:
            name = os.path.basename(normalized_path) or "My Library"

        workspace = {"name": name, "path": normalized_path}
        self.workspaces.append(workspace)
        if not self.active_path:
            self.active_path = normalized_path
        self.save()
        return workspace

    def activate(self, path, name=None):
        normalized_path = os.path.abspath(os.path.expanduser(path))
        workspace = self.find(normalized_path)
        if not workspace:
            workspace = self.add(normalized_path, name)

        os.makedirs(normalized_path, exist_ok=True)
        os.makedirs(os.path.join(normalized_path, "projects"), exist_ok=True)
        self.active_path = normalized_path
        self.save()
        return workspace

    def current(self):
        self.ensure_default()
        return self.active_path

    def set_offline_mode(self, enabled):
        """Persist the user's explicit network preference."""
        self.offline_mode = bool(enabled)
        self.save()

    def is_offline_mode(self):
        """Return whether online features are explicitly disabled."""
        return self.offline_mode

    def set_dyslexic_font(self, enabled):
        """Persist the abstract reading-font preference."""
        self.dyslexic_font = bool(enabled)
        self.save()

    def is_dyslexic_font_enabled(self):
        """Return whether abstracts should use OpenDyslexic."""
        return self.dyslexic_font


class Library:
    """Provide direct, thread-safe-by-connection access to one library."""

    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.projects_path = os.path.join(self.path, "projects")
        self.db_path = os.path.join(self.path, "corpus_cabinet.db")
        os.makedirs(self.projects_path, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self):
        connection = self.connect()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                color TEXT DEFAULT '#7F77DD',
                kind TEXT DEFAULT 'standard',
                search_context TEXT DEFAULT '',
                folder_path TEXT NOT NULL,
                position INTEGER,
                favorite INTEGER DEFAULT 0,
                favorite_position INTEGER,
                archived INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                authors TEXT DEFAULT '',
                conference TEXT DEFAULT '',
                year INTEGER,
                bibtex TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                task TEXT DEFAULT '',
                methodology TEXT DEFAULT '',
                datasets TEXT DEFAULT '',
                metrics TEXT DEFAULT '',
                abstract TEXT DEFAULT '',
                doi TEXT DEFAULT '',
                external_id TEXT DEFAULT '',
                external_url TEXT DEFAULT '',
                project_url TEXT DEFAULT '',
                pdf_url TEXT DEFAULT '',
                metadata_source TEXT DEFAULT '',
                citation_count INTEGER DEFAULT 0,
                extracted_text TEXT DEFAULT '',
                file_path TEXT NOT NULL,
                scholar_id TEXT DEFAULT '',
                reader_source TEXT DEFAULT '',
                source_type TEXT DEFAULT 'paper',
                canonical_url TEXT DEFAULT '',
                source_metadata TEXT DEFAULT '{}',
                content_path TEXT DEFAULT '',
                current_version_id INTEGER,
                position INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                color TEXT DEFAULT '#F1EFE8',
                text_color TEXT DEFAULT '#444441'
            );
            CREATE TABLE IF NOT EXISTS paper_tags (
                paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (paper_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS paper_comments (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS source_versions (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                abstract TEXT DEFAULT '',
                source_format TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                original_path TEXT NOT NULL,
                interactive_path TEXT DEFAULT '',
                document_path TEXT NOT NULL,
                extracted_text TEXT DEFAULT '',
                source_metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (paper_id, version_number),
                UNIQUE (paper_id, content_hash)
            );
            """
        )
        self.ensure_column(connection, "projects", "position", "INTEGER")
        self.ensure_column(connection, "projects", "kind", "TEXT DEFAULT 'standard'")
        self.ensure_column(connection, "projects", "search_context", "TEXT DEFAULT ''")
        self.ensure_column(connection, "projects", "notes", "TEXT DEFAULT ''")
        self.ensure_column(connection, "projects", "favorite", "INTEGER DEFAULT 0")
        self.ensure_column(connection, "projects", "favorite_position", "INTEGER")
        self.ensure_column(connection, "projects", "archived", "INTEGER DEFAULT 0")
        self.ensure_column(connection, "papers", "extracted_text", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "position", "INTEGER")
        self.ensure_column(connection, "papers", "notes", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "abstract", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "doi", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "external_id", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "external_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "project_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "pdf_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "metadata_source", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "citation_count", "INTEGER DEFAULT 0")
        self.ensure_column(connection, "papers", "last_page", "INTEGER DEFAULT 0")
        self.ensure_column(connection, "papers", "reader_zoom", "TEXT DEFAULT 'fit_width'")
        self.ensure_column(connection, "papers", "last_opened", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "reading_status", "TEXT DEFAULT 'unread'")
        self.ensure_column(connection, "papers", "favorite", "INTEGER DEFAULT 0")
        self.ensure_column(connection, "papers", "reader_source", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "source_type", "TEXT DEFAULT 'paper'")
        self.ensure_column(connection, "papers", "canonical_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "source_metadata", "TEXT DEFAULT '{}'")
        self.ensure_column(connection, "papers", "content_path", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "current_version_id", "INTEGER")
        connection.execute(
            "UPDATE papers SET source_type = 'paper' "
            "WHERE source_type IS NULL OR source_type = ''"
        )
        connection.execute(
            "UPDATE projects SET favorite_position = position "
            "WHERE favorite = 1 AND favorite_position IS NULL"
        )
        self.ensure_scrapbook(connection)
        connection.commit()
        connection.close()

    def ensure_column(self, connection, table_name, column_name, definition):
        columns = connection.execute("PRAGMA table_info(" + table_name + ")").fetchall()
        names = [column[1] for column in columns]
        if column_name not in names:
            connection.execute(
                "ALTER TABLE " + table_name + " ADD COLUMN " + column_name + " " + definition
            )

    def ensure_scrapbook(self, connection):
        """Create or recover the one built-in temporary ScrapBook project."""
        scrapbook = connection.execute(
            "SELECT * FROM projects WHERE kind = 'scrapbook' LIMIT 1"
        ).fetchone()
        if scrapbook:
            connection.execute(
                "UPDATE projects SET name = 'ScrapBook', color = '#4F7FD8', "
                "position = -1, favorite = 0, favorite_position = NULL, "
                "archived = 0 "
                "WHERE id = ?",
                (scrapbook["id"],),
            )
            return

        named_project = connection.execute(
            "SELECT * FROM projects WHERE lower(name) = 'scrapbook' LIMIT 1"
        ).fetchone()
        if named_project:
            connection.execute(
                "UPDATE projects SET name = 'ScrapBook', kind = 'scrapbook', "
                "color = '#4F7FD8', position = -1, favorite = 0, "
                "favorite_position = NULL, archived = 0 WHERE id = ?",
                (named_project["id"],),
            )
            return

        folder_path = os.path.join(self.projects_path, "ScrapBook")
        counter = 1
        while os.path.exists(folder_path):
            folder_path = os.path.join(
                self.projects_path,
                "ScrapBook_" + str(counter),
            )
            counter += 1
        os.makedirs(folder_path, exist_ok=True)
        created_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO projects (
                name, color, kind, folder_path, position, created_at
            ) VALUES ('ScrapBook', '#4F7FD8', 'scrapbook', ?, -1, ?)
            """,
            (folder_path, created_at),
        )

    def list_projects(self, archived=False):
        connection = self.connect()
        rows = connection.execute(
            """
            SELECT projects.*, COUNT(papers.id) AS paper_count
            FROM projects
            LEFT JOIN papers ON papers.project_id = projects.id
            WHERE projects.archived = ?
            GROUP BY projects.id
            ORDER BY CASE WHEN projects.kind = 'scrapbook' THEN 0 ELSE 1 END,
                     projects.favorite DESC,
                     CASE WHEN projects.favorite = 1
                          THEN projects.favorite_position
                          ELSE projects.position END IS NULL,
                     CASE WHEN projects.favorite = 1
                          THEN projects.favorite_position
                          ELSE projects.position END,
                     projects.created_at
            """,
            (int(bool(archived)),),
        ).fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def list_archived_projects(self):
        """Return projects hidden from the active sidebar."""
        return self.list_projects(archived=True)

    def get_project(self, project_id):
        connection = self.connect()
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        connection.close()
        if row:
            return dict(row)
        return None

    def update_project_favorite(self, project_id, favorite):
        """Move a project between ordered favorite and regular groups."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if project.get("kind") == "scrapbook":
            raise ValueError("ScrapBook cannot be favorited")
        favorite = int(bool(favorite))
        connection = self.connect()
        if favorite and not project.get("favorite"):
            favorite_position = project.get("favorite_position")
            if favorite_position is None:
                if project.get("kind") == "scrapbook":
                    favorite_position = -1
                else:
                    row = connection.execute(
                        "SELECT COALESCE(MAX(favorite_position), -1) + 1 "
                        "FROM projects WHERE kind != 'scrapbook' AND archived = 0"
                    ).fetchone()
                    favorite_position = row[0]
            connection.execute(
                "UPDATE projects SET favorite = 1, favorite_position = ? "
                "WHERE id = ?",
                (favorite_position, project_id),
            )
        else:
            connection.execute(
                "UPDATE projects SET favorite = ? WHERE id = ?",
                (favorite, project_id),
            )
        connection.commit()
        connection.close()

    def reorder_projects(self, project_ids, favorite):
        """Persist the standard-project order inside one sidebar group."""
        favorite = int(bool(favorite))
        connection = self.connect()
        rows = connection.execute(
            "SELECT id, favorite, position, favorite_position FROM projects "
            "WHERE kind != 'scrapbook' AND archived = 0",
        ).fetchall()
        expected_ids = {
            row["id"] for row in rows if int(bool(row["favorite"])) == favorite
        }
        ordered_ids = list(project_ids)
        if len(ordered_ids) != len(set(ordered_ids)):
            connection.close()
            raise ValueError("Project order contains duplicates")
        if set(ordered_ids) != expected_ids:
            connection.close()
            raise ValueError("Project order does not match the sidebar group")
        column = "favorite_position"
        if not favorite:
            column = "position"
        positions = sorted(
            row[column]
            for row in rows
            if int(bool(row["favorite"])) == favorite
            and row[column] is not None
        )
        if len(positions) != len(ordered_ids):
            positions = list(range(len(ordered_ids)))
        for project_id, position in zip(ordered_ids, positions):
            connection.execute(
                "UPDATE projects SET " + column + " = ? WHERE id = ?",
                (position, project_id),
            )
        connection.commit()
        connection.close()

    def get_scrapbook(self):
        """Return the built-in temporary ScrapBook project."""
        connection = self.connect()
        row = connection.execute(
            "SELECT * FROM projects WHERE kind = 'scrapbook' LIMIT 1"
        ).fetchone()
        connection.close()
        if row:
            return dict(row)
        return None

    def validate_destination_projects(self, project_ids):
        """Require valid destinations and keep ScrapBook exclusive."""
        validated = []
        scrapbook_selected = False
        for project_id in project_ids:
            if project_id in validated:
                continue
            project = self.get_project(project_id)
            if not project:
                raise ValueError("Project not found")
            if project.get("archived"):
                raise ValueError(
                    "Restore the archived project before adding sources to it"
                )
            validated.append(project_id)
            if project.get("kind") == "scrapbook":
                scrapbook_selected = True
        if scrapbook_selected and len(validated) > 1:
            raise ValueError(
                "ScrapBook must be the only destination for a source"
            )
        return validated

    def update_project_search_context(self, project_id, context):
        context = " ".join(str(context or "").split())
        connection = self.connect()
        connection.execute(
            "UPDATE projects SET search_context = ? WHERE id = ?",
            (context, project_id),
        )
        connection.commit()
        connection.close()
        return self.get_project(project_id)

    def create_project(self, name, color="#7F77DD"):
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty")
        if name.casefold() == "scrapbook":
            raise ValueError("ScrapBook is the built-in temporary folder")

        connection = self.connect()
        folder_name = safe_component(name)
        folder_path = os.path.join(self.projects_path, folder_name)
        counter = 1
        while os.path.exists(folder_path):
            folder_path = os.path.join(self.projects_path, folder_name + "_" + str(counter))
            counter += 1

        os.makedirs(folder_path, exist_ok=True)
        position_row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM projects"
        ).fetchone()
        position = position_row[0]
        created_at = datetime.now(timezone.utc).isoformat()

        try:
            cursor = connection.execute(
                """
                INSERT INTO projects (name, color, folder_path, position, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, color, folder_path, position, created_at),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            connection.close()
            raise ValueError("A project with that name already exists")

        project_id = cursor.lastrowid
        connection.close()
        return self.get_project(project_id)

    def rename_project(self, project_id, name):
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty")
        project = self.get_project(project_id)
        if project and project.get("kind") == "scrapbook":
            raise ValueError("The built-in ScrapBook cannot be renamed")
        if name.casefold() == "scrapbook":
            raise ValueError("ScrapBook is reserved for the temporary folder")

        connection = self.connect()
        try:
            connection.execute(
                "UPDATE projects SET name = ? WHERE id = ?", (name, project_id)
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            connection.close()
            raise ValueError("A project with that name already exists")
        connection.close()

    def delete_project(self, project_id):
        project = self.get_project(project_id)
        if not project:
            return
        if project.get("kind") == "scrapbook":
            raise ValueError("The built-in ScrapBook cannot be deleted")

        connection = self.connect()
        connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        connection.commit()
        connection.close()

        folder_path = project.get("folder_path", "")
        if folder_path and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)

    def archive_project(self, project_id):
        """Hide a standard project without changing any of its contents."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if project.get("kind") == "scrapbook":
            raise ValueError("The built-in ScrapBook cannot be archived")
        connection = self.connect()
        connection.execute(
            "UPDATE projects SET archived = 1 WHERE id = ?",
            (project_id,),
        )
        connection.commit()
        connection.close()

    def restore_project(self, project_id):
        """Return an archived standard project to the active sidebar."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if not project.get("archived"):
            return
        connection = self.connect()
        connection.execute(
            "UPDATE projects SET archived = 0 WHERE id = ?",
            (project_id,),
        )
        connection.commit()
        connection.close()

    def list_papers(self, project_id=None, query_text="", source_type=""):
        connection = self.connect()
        sql = """
            SELECT papers.*, projects.name AS project_name
            FROM papers
            JOIN projects ON projects.id = papers.project_id
            WHERE projects.archived = 0
            """
        values = []

        if project_id is not None:
            sql += " AND papers.project_id = ?"
            values.append(project_id)

        if query_text:
            sql += " AND (papers.title LIKE ? OR papers.authors LIKE ?)"
            search = "%" + query_text + "%"
            values.extend([search, search])

        if source_type:
            sql += " AND papers.source_type = ?"
            values.append(source_type)

        sql += " ORDER BY papers.position IS NULL, papers.position, papers.created_at DESC"
        rows = connection.execute(sql, values).fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def list_sources(self, project_id=None, query_text="", source_type=""):
        """Return all library source types through the backwards-compatible table."""
        return self.list_papers(project_id, query_text, source_type)

    def get_paper(self, paper_id):
        connection = self.connect()
        row = connection.execute(
            """
            SELECT papers.*, projects.name AS project_name
            FROM papers
            JOIN projects ON projects.id = papers.project_id
            WHERE papers.id = ?
            """,
            (paper_id,),
        ).fetchone()
        connection.close()
        if row:
            paper = dict(row)
            paper["tags"] = self.list_paper_tags(paper_id)
            try:
                paper["source_metadata_data"] = json.loads(
                    paper.get("source_metadata") or "{}"
                )
            except (TypeError, ValueError):
                paper["source_metadata_data"] = {}
            return paper
        return None

    def get_source(self, source_id):
        return self.get_paper(source_id)

    def save_reader_state(self, paper_id, page, zoom):
        """Remember a zero-based PDF page, zoom choice, and last reading time."""
        if not self.get_paper(paper_id):
            raise ValueError("Paper not found")
        zoom = str(zoom)
        if zoom not in ("fit_width", "fit_page"):
            try:
                factor = float(zoom)
            except ValueError:
                raise ValueError("Unsupported reader zoom") from None
            if not 0.25 <= factor <= 4.0:
                raise ValueError("Unsupported reader zoom")
        connection = self.connect()
        connection.execute(
            "UPDATE papers SET last_page = ?, reader_zoom = ?, last_opened = ? "
            "WHERE id = ?",
            (max(int(page), 0), zoom, datetime.now(timezone.utc).isoformat(), paper_id),
        )
        connection.commit()
        connection.close()

    def update_reader_source(self, paper_id, source):
        """Remember which successful generated Reader representation to reopen."""
        if not self.get_paper(paper_id):
            raise ValueError("Paper not found")
        source = str(source or "")
        if source not in ("", "pdf_preview", "pdf_full", "captured") and not source.startswith("arxiv:"):
            raise ValueError("Unsupported Reader source")
        connection = self.connect()
        connection.execute(
            "UPDATE papers SET reader_source = ? WHERE id = ?",
            (source, paper_id),
        )
        connection.commit()
        connection.close()

    def recent_reading_papers(self, limit=5):
        """Return recently read local PDFs whose copied files still exist."""
        connection = self.connect()
        rows = connection.execute(
            "SELECT papers.id, papers.project_id, papers.title, papers.last_page, "
            "papers.reader_zoom, papers.last_opened, papers.reading_status, "
            "papers.favorite, papers.file_path, projects.name AS project_name FROM papers "
            "JOIN projects ON projects.id = papers.project_id "
            "WHERE projects.archived = 0 AND papers.last_opened != '' "
            "AND papers.file_path != '' "
            "ORDER BY papers.last_opened DESC, papers.id DESC"
        ).fetchall()
        connection.close()
        papers = []
        for row in rows:
            if os.path.isfile(row["file_path"]):
                papers.append(dict(row))
                if len(papers) >= limit:
                    break
        return papers

    def update_reading_status(self, paper_id, status):
        """Set an explicit reading status without changing the PDF or notes."""
        if status not in ("unread", "reading", "read"):
            raise ValueError("Choose Unread, Reading, or Read")
        if not self.get_paper(paper_id):
            raise ValueError("Paper not found")
        connection = self.connect()
        connection.execute(
            "UPDATE papers SET reading_status = ? WHERE id = ?",
            (status, paper_id),
        )
        connection.commit()
        connection.close()

    def update_paper_favorite(self, paper_id, favorite):
        """Persist the user's optional favorite flag for one independent copy."""
        if not self.get_paper(paper_id):
            raise ValueError("Paper not found")
        connection = self.connect()
        connection.execute(
            "UPDATE papers SET favorite = ? WHERE id = ?",
            (int(bool(favorite)), paper_id),
        )
        connection.commit()
        connection.close()

    def update_project_notes(self, project_id, notes):
        """Autosave plain-text research synthesis for one project."""
        if not self.get_project(project_id):
            raise ValueError("Project not found")
        connection = self.connect()
        connection.execute(
            "UPDATE projects SET notes = ? WHERE id = ?",
            (str(notes or ""), project_id),
        )
        connection.commit()
        connection.close()

    def list_paper_tags(self, paper_id):
        connection = self.connect()
        rows = connection.execute(
            "SELECT tags.name FROM tags JOIN paper_tags ON tags.id = paper_tags.tag_id "
            "WHERE paper_tags.paper_id = ? ORDER BY tags.name",
            (paper_id,),
        ).fetchall()
        connection.close()
        return [row["name"] for row in rows]

    def update_paper_tags(self, paper_id, tags):
        """Replace one copy's tag links; shared tag names remain unchanged."""
        if not self.get_paper(paper_id):
            raise ValueError("Paper not found")
        tags = normalize_tags(tags)
        connection = self.connect()
        try:
            with connection:
                connection.execute("DELETE FROM paper_tags WHERE paper_id = ?", (paper_id,))
                for name in tags:
                    connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                    connection.execute(
                        "INSERT INTO paper_tags (paper_id, tag_id) "
                        "SELECT ?, id FROM tags WHERE name = ?",
                        (paper_id, name),
                    )
        finally:
            connection.close()
        return tags

    def search_library(self, query, limit=100):
        """Search local paper text and comments across projects, with excerpts.

        All query words must occur somewhere in the paper or its comments.
        PDF search uses the bounded text already extracted during import.
        Percent and underscore characters are literal, not SQL wildcards.
        """
        terms = re.findall(r'tag:"(?:\\.|[^"\\])*"|\S+', str(query or ""), re.IGNORECASE)
        if not terms:
            return []
        columns = (
            "title", "authors", "abstract", "notes", "extracted_text",
            "canonical_url", "source_metadata",
        )
        clauses = []
        values = []
        match_terms = []
        tag_query = False
        for term in terms:
            if term.casefold().startswith("tag:"):
                name = term[4:]
                if name.startswith('"'):
                    try:
                        name = json.loads(name)
                    except ValueError:
                        return []
                try:
                    names = normalize_tags([name])
                except ValueError:
                    return []
                if not names:
                    return []
                clauses.append(
                    "EXISTS (SELECT 1 FROM paper_tags JOIN tags ON tags.id = paper_tags.tag_id "
                    "WHERE paper_tags.paper_id = papers.id AND tags.name = ?)"
                )
                values.append(names[0])
                match_terms.append(names[0])
                tag_query = True
                continue
            match_terms.append(term)
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = "%" + escaped + "%"
            matches = []
            for column in columns:
                matches.append("papers." + column + " LIKE ? ESCAPE '\\'")
                values.append(pattern)
            matches.append(
                "EXISTS (SELECT 1 FROM paper_tags JOIN tags ON tags.id = paper_tags.tag_id "
                "WHERE paper_tags.paper_id = papers.id AND tags.name LIKE ? ESCAPE '\\')"
            )
            values.append(pattern.casefold())
            matches.append(
                "EXISTS (SELECT 1 FROM paper_comments WHERE "
                "paper_comments.paper_id = papers.id AND "
                "paper_comments.body LIKE ? ESCAPE '\\')"
            )
            values.append(pattern)
            clauses.append("(" + " OR ".join(matches) + ")")
        connection = self.connect()
        rows = connection.execute(
            "SELECT papers.*, projects.name AS project_name FROM papers "
            "JOIN projects ON projects.id = papers.project_id "
            "WHERE projects.archived = 0 AND "
            + " AND ".join(clauses)
            + " ORDER BY papers.last_opened DESC, papers.title LIMIT ?",
            values + [limit],
        ).fetchall()
        connection.close()
        results = []
        for row in rows:
            paper = dict(row)
            paper["tags"] = self.list_paper_tags(paper["id"])
            fields = []
            fields.append(("tags", ", ".join(paper["tags"]), None))
            for column in columns:
                fields.append((column, paper.get(column, ""), None))
            for comment in self.list_paper_comments(paper["id"]):
                fields.append(("pdf_comment", comment["body"], comment["page_number"]))
            best_score = 0
            for field, text, page_number in fields:
                text = str(text or "")
                score = sum(term.casefold() in text.casefold() for term in match_terms)
                if score > best_score:
                    best_score = score
                    paper["match_field"] = field
                    paper["match_excerpt"] = library_match_excerpt(text, match_terms)
                    paper["match_page_number"] = page_number
            if tag_query:
                paper["match_field"] = "tags"
                paper["match_excerpt"] = ", ".join(paper["tags"])
                paper["match_page_number"] = None
            results.append(paper)
        return results

    def find_duplicate_papers(
        self,
        project_ids,
        candidate,
        exclude_paper_id=None,
    ):
        """Find likely duplicate records inside the chosen destinations."""
        project_ids = self.validate_destination_projects(project_ids)
        candidate_doi = normalize_paper_identifier(candidate.get("doi", ""))
        candidate_external_id = normalize_paper_identifier(
            candidate.get("external_id", "")
        )
        candidate_url = str(candidate.get("canonical_url") or "").strip().casefold().rstrip("/")
        candidate_title = normalize_paper_title(candidate.get("title", ""))
        duplicates = []
        for project_id in project_ids:
            for paper in self.list_papers(project_id):
                if paper["id"] == exclude_paper_id:
                    continue
                reason = ""
                paper_doi = normalize_paper_identifier(paper.get("doi", ""))
                paper_external_id = normalize_paper_identifier(
                    paper.get("external_id", "")
                )
                paper_title = normalize_paper_title(paper.get("title", ""))
                paper_url = str(paper.get("canonical_url") or "").strip().casefold().rstrip("/")
                if candidate_url and candidate_url == paper_url:
                    reason = "same source URL"
                elif candidate_doi and candidate_doi == paper_doi:
                    reason = "same DOI"
                elif (
                    candidate_external_id
                    and candidate_external_id == paper_external_id
                ):
                    reason = "same paper identifier"
                elif (
                    len(candidate_title) >= 12
                    and candidate_title == paper_title
                ):
                    reason = "same title"
                if reason:
                    duplicate = dict(paper)
                    duplicate["match_reason"] = reason
                    duplicates.append(duplicate)
        return duplicates

    def store_source_capture(self, capture):
        """Persist one immutable source capture and return its Reader path."""
        identity = (
            capture.get("canonical_url")
            or capture.get("external_id")
            or capture.get("title")
            or "source"
        )
        url_digest = hashlib.sha256(
            str(identity).encode("utf-8")
        ).hexdigest()[:20]
        cache_path = os.path.join(
            self.path,
            "source_cache",
            url_digest,
            capture_digest(capture),
        )
        os.makedirs(cache_path, exist_ok=True)
        document_path = os.path.join(cache_path, "document.json")
        if not os.path.isfile(document_path):
            extension = str(capture.get("source_extension") or ".html").casefold()
            if extension not in (".html", ".htm", ".md", ".markdown"):
                extension = ".html"
            source_path = os.path.join(cache_path, "source" + extension)
            temporary_source = source_path + ".tmp"
            with open(temporary_source, "wb") as handle:
                handle.write(capture.get("source_html", b""))
            os.replace(temporary_source, source_path)
            interactive_html = capture.get("interactive_html", b"")
            if interactive_html:
                interactive_path = os.path.join(cache_path, "interactive.html")
                temporary_interactive = interactive_path + ".tmp"
                with open(temporary_interactive, "wb") as handle:
                    handle.write(interactive_html)
                os.replace(temporary_interactive, interactive_path)
            for asset in capture.get("assets", []):
                filename = safe_asset_name(asset.get("filename"))
                if not filename:
                    continue
                asset_path = os.path.join(cache_path, filename)
                temporary_asset = asset_path + ".tmp"
                with open(temporary_asset, "wb") as handle:
                    handle.write(asset.get("content", b""))
                os.replace(temporary_asset, asset_path)
            temporary_document = document_path + ".tmp"
            with open(temporary_document, "w", encoding="utf-8") as handle:
                json.dump(capture["document"], handle, ensure_ascii=False, indent=2)
            os.replace(temporary_document, document_path)
        return document_path

    def create_source_from_capture(self, project_id, capture, content_path=""):
        """Save a confirmed article, repository, or local document."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        source_type = str(capture.get("source_type") or "")
        if source_type not in ("article", "github_repository", "document"):
            raise ValueError("Unsupported captured source type")
        title = str(capture.get("title") or "").strip()
        if not title:
            raise ValueError("Captured source has no title")
        if not content_path:
            content_path = self.store_source_capture(capture)
        connection = self.connect()
        position_row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM papers WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """
            INSERT INTO papers (
                project_id, title, authors, conference, year, abstract,
                external_id, external_url, metadata_source, extracted_text,
                file_path, reader_source, source_type, canonical_url,
                source_metadata, content_path, position, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'captured', ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                title,
                str(capture.get("authors") or ""),
                str(capture.get("venue") or ""),
                capture.get("year"),
                str(capture.get("abstract") or ""),
                str(capture.get("external_id") or ""),
                str(capture.get("external_url") or capture.get("canonical_url") or ""),
                str(capture.get("source") or ""),
                str(capture.get("extracted_text") or ""),
                source_type,
                str(capture.get("canonical_url") or ""),
                json.dumps(capture.get("source_metadata") or {}, ensure_ascii=False),
                content_path,
                position_row[0],
                created_at,
            ),
        )
        connection.commit()
        source_id = cursor.lastrowid
        connection.close()
        if source_type == "document":
            try:
                self.create_initial_document_version(
                    source_id, capture, content_path
                )
            except Exception:
                self.delete_paper(source_id)
                raise
        return self.get_source(source_id)

    def create_source_copies(self, project_ids, capture):
        """Create independent project records that share one immutable web snapshot."""
        project_ids = self.validate_destination_projects(project_ids)
        content_path = self.store_source_capture(capture)
        sources = []
        try:
            for project_id in project_ids:
                sources.append(
                    self.create_source_from_capture(project_id, capture, content_path)
                )
        except Exception:
            for source in sources:
                self.delete_paper(source["id"])
            raise
        return sources

    def create_initial_document_version(self, paper_id, capture, content_path):
        """Attach version one to a newly created local document record."""
        original_path, interactive_path = stored_capture_paths(
            content_path, capture
        )
        metadata = capture.get("source_metadata") or {}
        connection = self.connect()
        cursor = connection.execute(
            """
            INSERT INTO source_versions (
                paper_id, version_number, title, abstract, source_format,
                original_filename, content_hash, original_path,
                interactive_path, document_path, extracted_text,
                source_metadata, created_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                str(capture.get("title") or "Untitled document"),
                str(capture.get("abstract") or ""),
                str(metadata.get("source_format") or "html"),
                str(metadata.get("original_filename") or os.path.basename(original_path)),
                str(capture.get("content_hash") or capture_digest(capture)),
                original_path,
                interactive_path,
                content_path,
                str(capture.get("extracted_text") or ""),
                json.dumps(metadata, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE papers SET current_version_id = ? WHERE id = ?",
            (cursor.lastrowid, paper_id),
        )
        connection.commit()
        connection.close()

    def list_document_versions(self, paper_id):
        """Return every immutable revision, newest first."""
        connection = self.connect()
        rows = connection.execute(
            "SELECT source_versions.*, "
            "CASE WHEN papers.current_version_id = source_versions.id "
            "THEN 1 ELSE 0 END AS is_current "
            "FROM source_versions JOIN papers ON papers.id = source_versions.paper_id "
            "WHERE source_versions.paper_id = ? "
            "ORDER BY source_versions.version_number DESC",
            (paper_id,),
        ).fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_document_version(self, paper_id, version_id=None):
        """Return one revision or the document's active revision."""
        connection = self.connect()
        if version_id is None:
            row = connection.execute(
                "SELECT source_versions.* FROM source_versions "
                "JOIN papers ON papers.current_version_id = source_versions.id "
                "WHERE papers.id = ?",
                (paper_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM source_versions WHERE paper_id = ? AND id = ?",
                (paper_id, version_id),
            ).fetchone()
        connection.close()
        if row:
            return dict(row)
        return None

    def add_document_revision(self, paper_id, capture):
        """Archive a new immutable revision and make it the active version."""
        paper = self.get_paper(paper_id)
        if not paper or paper.get("source_type") != "document":
            raise ValueError("Select a local document before adding a revision")
        if capture.get("source_type") != "document":
            raise ValueError("The revision must be an HTML or Markdown document")
        content_hash = str(capture.get("content_hash") or capture_digest(capture))
        connection = self.connect()
        duplicate = connection.execute(
            "SELECT version_number FROM source_versions "
            "WHERE paper_id = ? AND content_hash = ?",
            (paper_id, content_hash),
        ).fetchone()
        if duplicate:
            connection.close()
            raise ValueError(
                "This exact content is already saved as version "
                + str(duplicate["version_number"])
            )
        connection.close()

        content_path = self.store_source_capture(capture)
        original_path, interactive_path = stored_capture_paths(
            content_path, capture
        )
        metadata = capture.get("source_metadata") or {}
        connection = self.connect()
        row = connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 "
            "FROM source_versions WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        version_number = row[0]
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """
            INSERT INTO source_versions (
                paper_id, version_number, title, abstract, source_format,
                original_filename, content_hash, original_path,
                interactive_path, document_path, extracted_text,
                source_metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                version_number,
                str(capture.get("title") or paper.get("title") or "Untitled document"),
                str(capture.get("abstract") or ""),
                str(metadata.get("source_format") or "html"),
                str(metadata.get("original_filename") or os.path.basename(original_path)),
                content_hash,
                original_path,
                interactive_path,
                content_path,
                str(capture.get("extracted_text") or ""),
                json.dumps(metadata, ensure_ascii=False),
                created_at,
            ),
        )
        version_id = cursor.lastrowid
        connection.execute(
            """
            UPDATE papers SET title = ?, abstract = ?, conference = ?,
                external_id = ?, metadata_source = ?, extracted_text = ?,
                source_metadata = ?, content_path = ?, current_version_id = ?
            WHERE id = ?
            """,
            (
                str(capture.get("title") or paper.get("title") or "Untitled document"),
                str(capture.get("abstract") or ""),
                str(capture.get("venue") or "Local document"),
                str(capture.get("external_id") or ""),
                str(capture.get("source") or "Local document"),
                str(capture.get("extracted_text") or ""),
                json.dumps(metadata, ensure_ascii=False),
                content_path,
                version_id,
                paper_id,
            ),
        )
        connection.commit()
        connection.close()
        return self.get_document_version(paper_id, version_id)

    def activate_document_version(self, paper_id, version_id):
        """Make one archived revision active without deleting newer history."""
        paper = self.get_paper(paper_id)
        version = self.get_document_version(paper_id, version_id)
        if not paper or paper.get("source_type") != "document" or not version:
            raise ValueError("Document version not found")
        connection = self.connect()
        connection.execute(
            """
            UPDATE papers SET title = ?, abstract = ?, conference = ?,
                external_id = ?, metadata_source = ?, extracted_text = ?,
                source_metadata = ?, content_path = ?, current_version_id = ?
            WHERE id = ?
            """,
            (
                version["title"],
                version["abstract"],
                "Local " + version["source_format"].title(),
                version["content_hash"],
                "Local " + version["source_format"].title(),
                version["extracted_text"],
                version["source_metadata"],
                version["document_path"],
                version["id"],
                paper_id,
            ),
        )
        connection.commit()
        connection.close()
        return self.get_paper(paper_id)

    def find_pdf_duplicates(self, project_ids, source_path):
        """Extract lightweight PDF metadata and find destination duplicates."""
        metadata = extract_pdf_metadata(source_path)
        duplicates = self.find_duplicate_papers(project_ids, metadata)
        matched_ids = set()
        for duplicate in duplicates:
            duplicate["source_file"] = os.path.basename(source_path)
            matched_ids.add(duplicate["id"])

        source_size = os.path.getsize(source_path)
        source_digest = ""
        for project_id in project_ids:
            for paper in self.list_papers(project_id):
                if paper["id"] in matched_ids:
                    continue
                paper_path = paper.get("file_path", "")
                if not paper_path or not os.path.isfile(paper_path):
                    continue
                if os.path.getsize(paper_path) != source_size:
                    continue
                if not source_digest:
                    source_digest = calculate_file_sha256(source_path)
                if calculate_file_sha256(paper_path) != source_digest:
                    continue
                duplicate = dict(paper)
                duplicate["match_reason"] = "same PDF file"
                duplicate["source_file"] = os.path.basename(source_path)
                duplicates.append(duplicate)
                matched_ids.add(paper["id"])
        return duplicates

    def import_pdf(self, project_id, source_path):
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")

        if not source_path.lower().endswith(".pdf"):
            raise ValueError("Only PDF files are supported")

        destination = unique_destination(project["folder_path"], os.path.basename(source_path))
        shutil.copy2(source_path, destination)

        try:
            metadata = extract_pdf_metadata(destination)
            extracted_text = extract_pdf_text(destination)
            connection = self.connect()
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute(
                """
                INSERT INTO papers (
                    project_id, title, authors, abstract, external_id,
                    external_url, pdf_url, metadata_source, extracted_text,
                    file_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    metadata["title"],
                    metadata["authors"],
                    metadata.get("abstract", ""),
                    metadata.get("external_id", ""),
                    metadata.get("external_url", ""),
                    metadata.get("pdf_url", ""),
                    metadata.get("metadata_source", ""),
                    extracted_text,
                    destination,
                    created_at,
                ),
            )
            connection.commit()
            paper_id = cursor.lastrowid
            connection.close()
        except Exception:
            if os.path.exists(destination):
                os.remove(destination)
            raise

        return self.get_paper(paper_id)

    def attach_pdf(self, paper_id, source_path):
        """Copy and index a PDF for an existing citation-only paper."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        if paper.get("file_path"):
            raise ValueError("This paper already has a local PDF")
        if not source_path.lower().endswith(".pdf"):
            raise ValueError("Only PDF files are supported")

        project = self.get_project(paper["project_id"])
        if not project:
            raise ValueError("Project not found")

        destination = unique_destination(
            project["folder_path"],
            os.path.basename(source_path),
        )
        shutil.copy2(source_path, destination)

        try:
            metadata = extract_pdf_metadata(destination)
            extracted_text = extract_pdf_text(destination)
            connection = self.connect()
            connection.execute(
                """
                UPDATE papers
                SET authors = CASE WHEN authors = '' THEN ? ELSE authors END,
                    abstract = CASE WHEN abstract = '' THEN ? ELSE abstract END,
                    external_id = CASE WHEN external_id = '' THEN ? ELSE external_id END,
                    external_url = CASE WHEN external_url = '' THEN ? ELSE external_url END,
                    pdf_url = CASE WHEN pdf_url = '' THEN ? ELSE pdf_url END,
                    metadata_source = CASE
                        WHEN metadata_source = '' THEN ? ELSE metadata_source
                    END,
                    extracted_text = ?, file_path = ?
                WHERE id = ?
                """,
                (
                    metadata.get("authors", ""),
                    metadata.get("abstract", ""),
                    metadata.get("external_id", ""),
                    metadata.get("external_url", ""),
                    metadata.get("pdf_url", ""),
                    metadata.get("metadata_source", ""),
                    extracted_text,
                    destination,
                    paper_id,
                ),
            )
            connection.commit()
            connection.close()
        except Exception:
            if os.path.exists(destination):
                os.remove(destination)
            raise

        return self.get_paper(paper_id)

    def refresh_pdf_metadata(self, paper_id):
        """Recover missing metadata from an already attached local PDF."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        file_path = paper.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            raise ValueError("The paper's local PDF could not be found")

        metadata = extract_pdf_metadata(file_path)
        title = paper.get("title", "")
        extracted_title = metadata.get("title", "")
        can_improve_local_title = paper.get("metadata_source", "") in (
            "",
            "Local PDF",
        )
        if can_improve_local_title and extracted_title:
            if not title or title == "Untitled paper":
                title = extracted_title
            elif (
                len(extracted_title.split()) > len(title.split())
                and extracted_title.lower().startswith(title.lower())
            ):
                title = extracted_title

        connection = self.connect()
        connection.execute(
            """
            UPDATE papers
            SET title = ?,
                authors = CASE WHEN authors = '' THEN ? ELSE authors END,
                abstract = CASE WHEN abstract = '' THEN ? ELSE abstract END,
                external_id = CASE WHEN external_id = '' THEN ? ELSE external_id END,
                external_url = CASE WHEN external_url = '' THEN ? ELSE external_url END,
                pdf_url = CASE WHEN pdf_url = '' THEN ? ELSE pdf_url END,
                metadata_source = CASE
                    WHEN metadata_source = '' THEN ? ELSE metadata_source
                END
            WHERE id = ?
            """,
            (
                title,
                metadata.get("authors", ""),
                metadata.get("abstract", ""),
                metadata.get("external_id", ""),
                metadata.get("external_url", ""),
                metadata.get("pdf_url", ""),
                metadata.get("metadata_source", ""),
                paper_id,
            ),
        )
        connection.commit()
        connection.close()
        return self.get_paper(paper_id)

    def create_paper_from_search(self, project_id, result):
        """Save a confirmed online result as a paper without a local PDF."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")

        title = str(result.get("title") or "").strip()
        if not title:
            raise ValueError("Search result has no title")

        connection = self.connect()
        position_row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM papers WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        position = position_row[0]
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """
            INSERT INTO papers (
                project_id, title, authors, conference, year, abstract, doi,
                external_id, external_url, project_url, pdf_url, metadata_source,
                citation_count, file_path, position, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                title,
                str(result.get("authors") or ""),
                str(result.get("venue") or ""),
                result.get("year"),
                str(result.get("abstract") or ""),
                str(result.get("doi") or ""),
                str(result.get("external_id") or ""),
                str(result.get("external_url") or ""),
                str(result.get("project_url") or ""),
                str(result.get("pdf_url") or ""),
                str(result.get("source") or ""),
                max(int(result.get("citation_count") or 0), 0),
                "",
                position,
                created_at,
            ),
        )
        connection.commit()
        paper_id = cursor.lastrowid
        connection.close()
        return self.get_paper(paper_id)

    def copy_paper(self, paper_id, project_id):
        """Create an independent paper record and PDF copy in another project."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        if paper["project_id"] == project_id:
            raise ValueError("Paper is already in this project")
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if project.get("kind") == "scrapbook":
            raise ValueError("Sources cannot be copied into ScrapBook")

        destination = ""
        source_path = paper.get("file_path", "")
        if source_path:
            if not os.path.exists(source_path):
                raise ValueError("The paper's local PDF could not be found")
            destination = unique_destination(
                project["folder_path"],
                os.path.basename(source_path),
            )
            shutil.copy2(source_path, destination)

        connection = self.connect()
        try:
            position_row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM papers "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute(
                """
                INSERT INTO papers (
                    project_id, title, authors, conference, year, bibtex, notes,
                    summary, task, methodology, datasets, metrics, abstract,
                    doi, external_id, external_url, project_url, pdf_url,
                    metadata_source, citation_count, extracted_text, file_path,
                    scholar_id, reader_source, position, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    project_id,
                    paper.get("title", ""),
                    paper.get("authors", ""),
                    paper.get("conference", ""),
                    paper.get("year"),
                    paper.get("bibtex", ""),
                    paper.get("notes", ""),
                    paper.get("summary", ""),
                    paper.get("task", ""),
                    paper.get("methodology", ""),
                    paper.get("datasets", ""),
                    paper.get("metrics", ""),
                    paper.get("abstract", ""),
                    paper.get("doi", ""),
                    paper.get("external_id", ""),
                    paper.get("external_url", ""),
                    paper.get("project_url", ""),
                    paper.get("pdf_url", ""),
                    paper.get("metadata_source", ""),
                    paper.get("citation_count", 0),
                    paper.get("extracted_text", ""),
                    destination,
                    paper.get("scholar_id", ""),
                    paper.get("reader_source", ""),
                    position_row[0],
                    created_at,
                ),
            )
            copied_paper_id = cursor.lastrowid
            connection.execute(
                "UPDATE papers SET reading_status = ?, favorite = ?, "
                "source_type = ?, canonical_url = ?, source_metadata = ?, "
                "content_path = ? WHERE id = ?",
                (
                    paper.get("reading_status", "unread"),
                    paper.get("favorite", 0),
                    paper.get("source_type", "paper"),
                    paper.get("canonical_url", ""),
                    paper.get("source_metadata", "{}"),
                    paper.get("content_path", ""),
                    copied_paper_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO paper_tags (paper_id, tag_id)
                SELECT ?, tag_id FROM paper_tags WHERE paper_id = ?
                """,
                (copied_paper_id, paper_id),
            )
            connection.execute(
                """
                INSERT INTO paper_comments (
                    paper_id, page_number, body, created_at
                )
                SELECT ?, page_number, body, created_at
                FROM paper_comments
                WHERE paper_id = ?
                """,
                (copied_paper_id, paper_id),
            )
            if paper.get("source_type") == "document":
                versions = connection.execute(
                    "SELECT * FROM source_versions WHERE paper_id = ? "
                    "ORDER BY version_number",
                    (paper_id,),
                ).fetchall()
                copied_current_version_id = None
                for version in versions:
                    version_cursor = connection.execute(
                        """
                        INSERT INTO source_versions (
                            paper_id, version_number, title, abstract,
                            source_format, original_filename, content_hash,
                            original_path, interactive_path, document_path,
                            extracted_text, source_metadata, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            copied_paper_id,
                            version["version_number"],
                            version["title"],
                            version["abstract"],
                            version["source_format"],
                            version["original_filename"],
                            version["content_hash"],
                            version["original_path"],
                            version["interactive_path"],
                            version["document_path"],
                            version["extracted_text"],
                            version["source_metadata"],
                            version["created_at"],
                        ),
                    )
                    if version["id"] == paper.get("current_version_id"):
                        copied_current_version_id = version_cursor.lastrowid
                if copied_current_version_id is not None:
                    connection.execute(
                        "UPDATE papers SET current_version_id = ? WHERE id = ?",
                        (copied_current_version_id, copied_paper_id),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            if destination and os.path.exists(destination):
                os.remove(destination)
            raise
        connection.close()
        return self.get_paper(copied_paper_id)

    def copy_paper_to_projects(self, paper_id, project_ids):
        """Copy one paper independently to each distinct destination project."""
        copied = []
        seen = set()
        try:
            for project_id in project_ids:
                if project_id in seen:
                    continue
                seen.add(project_id)
                copied.append(self.copy_paper(paper_id, project_id))
        except Exception:
            for paper in copied:
                self.delete_paper(paper["id"])
            raise
        return copied

    def move_scrapbook_paper(self, paper_id, project_id):
        """Move one ScrapBook record and its PDF into a standard project."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        source_project = self.get_project(paper["project_id"])
        if not source_project or source_project.get("kind") != "scrapbook":
            raise ValueError("Only ScrapBook papers can use this move action")
        target_project = self.get_project(project_id)
        if not target_project:
            raise ValueError("Project not found")
        if target_project.get("kind") == "scrapbook":
            raise ValueError("Choose a project outside ScrapBook")

        source_path = paper.get("file_path", "")
        destination = ""
        if source_path:
            if not os.path.exists(source_path):
                raise ValueError("The paper's local PDF could not be found")
            destination = unique_destination(
                target_project["folder_path"],
                os.path.basename(source_path),
            )

        connection = self.connect()
        moved_file = False
        try:
            if source_path:
                shutil.move(source_path, destination)
                moved_file = True
            position_row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM papers "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            created_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                UPDATE papers
                SET project_id = ?, file_path = ?, position = ?, created_at = ?
                WHERE id = ?
                """,
                (
                    project_id,
                    destination,
                    position_row[0],
                    created_at,
                    paper_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            if moved_file and os.path.exists(destination):
                shutil.move(destination, source_path)
            connection.close()
            raise
        connection.close()
        return self.get_paper(paper_id)

    def update_paper_metadata(self, paper_id, result):
        """Apply a confirmed online result without changing local PDF text."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")

        title = str(result.get("title") or "").strip()
        if not title:
            raise ValueError("Search result has no title")

        connection = self.connect()
        connection.execute(
            """
            UPDATE papers
            SET title = ?, authors = ?, conference = ?, year = ?,
                abstract = ?, doi = ?, external_id = ?, external_url = ?,
                project_url = ?, pdf_url = ?, metadata_source = ?,
                citation_count = ?
            WHERE id = ?
            """,
            (
                title,
                str(result.get("authors") or ""),
                str(result.get("venue") or ""),
                result.get("year"),
                str(result.get("abstract") or ""),
                str(result.get("doi") or ""),
                str(result.get("external_id") or ""),
                str(result.get("external_url") or ""),
                str(
                    result.get("project_url")
                    or paper.get("project_url")
                    or ""
                ),
                str(result.get("pdf_url") or ""),
                str(result.get("source") or ""),
                max(int(result.get("citation_count") or 0), 0),
                paper_id,
            ),
        )
        connection.commit()
        connection.close()
        return self.get_paper(paper_id)

    def update_paper_bibtex(self, paper_id, bibtex):
        """Persist a reviewed BibTeX entry without changing paper metadata."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        connection = self.connect()
        connection.execute(
            "UPDATE papers SET bibtex = ? WHERE id = ?",
            (str(bibtex or "").strip(), paper_id),
        )
        connection.commit()
        connection.close()
        return self.get_paper(paper_id)

    def update_paper_notes(self, paper_id, notes):
        """Persist the user's private manual notes for one paper."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        connection = self.connect()
        connection.execute(
            "UPDATE papers SET notes = ? WHERE id = ?",
            (str(notes or ""), paper_id),
        )
        connection.commit()
        connection.close()
        return self.get_paper(paper_id)

    def list_paper_comments(self, paper_id):
        """Return page-specific comments for one paper in reading order."""
        connection = self.connect()
        rows = connection.execute(
            """
            SELECT id, paper_id, page_number, body, created_at
            FROM paper_comments
            WHERE paper_id = ?
            ORDER BY page_number, created_at, id
            """,
            (paper_id,),
        ).fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def create_paper_comment(self, paper_id, page_number, body):
        """Create an app-side comment anchored to one PDF page."""
        paper = self.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")
        page_number = int(page_number)
        if page_number < 1:
            raise ValueError("Page number must be at least 1")
        body = str(body or "").strip()
        if not body:
            raise ValueError("Comment cannot be empty")

        created_at = datetime.now(timezone.utc).isoformat()
        connection = self.connect()
        cursor = connection.execute(
            """
            INSERT INTO paper_comments (
                paper_id, page_number, body, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (paper_id, page_number, body, created_at),
        )
        connection.commit()
        comment_id = cursor.lastrowid
        row = connection.execute(
            """
            SELECT id, paper_id, page_number, body, created_at
            FROM paper_comments
            WHERE id = ?
            """,
            (comment_id,),
        ).fetchone()
        connection.close()
        return dict(row)

    def delete_paper_comment(self, comment_id):
        """Delete one app-side PDF comment."""
        connection = self.connect()
        connection.execute(
            "DELETE FROM paper_comments WHERE id = ?",
            (comment_id,),
        )
        connection.commit()
        connection.close()

    def delete_paper(self, paper_id):
        paper = self.get_paper(paper_id)
        if not paper:
            return

        file_path = paper.get("file_path", "")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        connection = self.connect()
        connection.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        connection.commit()
        connection.close()


def safe_component(value):
    """Make a user name safe for use as one directory component."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        return "Project"
    return value


def unique_destination(folder_path, filename):
    """Return a non-existing PDF path below a project folder."""
    base_name, extension = os.path.splitext(filename)
    base_name = safe_component(base_name)
    destination = os.path.join(folder_path, base_name + extension)
    counter = 1

    while os.path.exists(destination):
        destination = os.path.join(
            folder_path,
            base_name + "_" + str(counter) + extension,
        )
        counter += 1

    return destination
