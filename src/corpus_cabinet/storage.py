"""Store libraries, projects, papers, and workspace preferences locally.

The module reads and writes a workspace registry JSON file and one SQLite
database at <library>/corpus_cabinet.db. Uploaded PDFs are copied below
<library>/projects/<project>/, and extracted text is stored in the papers table.
"""

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone

from corpus_cabinet.pdfs import extract_pdf_metadata, extract_pdf_text


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
                search_context TEXT DEFAULT '',
                folder_path TEXT NOT NULL,
                position INTEGER,
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
            """
        )
        self.ensure_column(connection, "projects", "position", "INTEGER")
        self.ensure_column(connection, "projects", "search_context", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "extracted_text", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "position", "INTEGER")
        self.ensure_column(connection, "papers", "abstract", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "doi", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "external_id", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "external_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "project_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "pdf_url", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "metadata_source", "TEXT DEFAULT ''")
        self.ensure_column(connection, "papers", "citation_count", "INTEGER DEFAULT 0")
        connection.commit()
        connection.close()

    def ensure_column(self, connection, table_name, column_name, definition):
        columns = connection.execute("PRAGMA table_info(" + table_name + ")").fetchall()
        names = [column[1] for column in columns]
        if column_name not in names:
            connection.execute(
                "ALTER TABLE " + table_name + " ADD COLUMN " + column_name + " " + definition
            )

    def list_projects(self):
        connection = self.connect()
        rows = connection.execute(
            """
            SELECT projects.*, COUNT(papers.id) AS paper_count
            FROM projects
            LEFT JOIN papers ON papers.project_id = projects.id
            GROUP BY projects.id
            ORDER BY projects.position IS NULL, projects.position, projects.created_at
            """
        ).fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_project(self, project_id):
        connection = self.connect()
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        connection.close()
        if row:
            return dict(row)
        return None

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

        connection = self.connect()
        connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        connection.commit()
        connection.close()

        folder_path = project.get("folder_path", "")
        if folder_path and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)

    def list_papers(self, project_id=None, query_text=""):
        connection = self.connect()
        sql = """
            SELECT papers.*, projects.name AS project_name
            FROM papers
            JOIN projects ON projects.id = papers.project_id
            WHERE 1 = 1
            """
        values = []

        if project_id is not None:
            sql += " AND papers.project_id = ?"
            values.append(project_id)

        if query_text:
            sql += " AND (papers.title LIKE ? OR papers.authors LIKE ?)"
            search = "%" + query_text + "%"
            values.extend([search, search])

        sql += " ORDER BY papers.position IS NULL, papers.position, papers.created_at DESC"
        rows = connection.execute(sql, values).fetchall()
        connection.close()
        return [dict(row) for row in rows]

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
            return dict(row)
        return None

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
                    project_id, title, authors, extracted_text, file_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    metadata["title"],
                    metadata["authors"],
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
                    extracted_text = ?, file_path = ?
                WHERE id = ?
                """,
                (
                    metadata.get("authors", ""),
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
