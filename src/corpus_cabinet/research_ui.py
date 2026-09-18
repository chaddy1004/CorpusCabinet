"""Show local library search and project research-note dialogs.

Search reads the active library's SQLite paper text and comments without network
access. Project notes are autosaved to the projects.notes column in that same
<library>/corpus_cabinet.db database. No PDF files are modified.
"""

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QTimer, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from corpus_cabinet.storage import Library


SEARCH_FIELD_LABELS = {
    "title": "Title",
    "authors": "Authors",
    "abstract": "Abstract",
    "notes": "Personal notes",
    "extracted_text": "PDF text",
    "pdf_comment": "PDF comment",
    "tags": "Tags",
}


class LibrarySearchSignals(QObject):
    """Deliver local-search results with a revision for stale-result protection."""

    finished = Signal(object)
    failed = Signal(object)


class LibrarySearchTask(QRunnable):
    """Search saved text in the background, never contacting an online service."""

    def __init__(self, library_path, query, revision):
        super().__init__()
        self.library_path = library_path
        self.query = query
        self.revision = revision
        self.signals = LibrarySearchSignals()

    def run(self):
        try:
            results = Library(self.library_path).search_library(self.query)
            self.signals.finished.emit(
                {"revision": self.revision, "results": results}
            )
        except Exception as error:
            self.signals.failed.emit(
                {"revision": self.revision, "message": str(error)}
            )


class LibrarySearchDialog(QDialog):
    """Find papers across projects and return the selected local result."""

    def __init__(self, parent, library):
        super().__init__(parent)
        self.library = library
        self.selected_paper = None
        self.revision = 0
        self.thread_pool = QThreadPool.globalInstance()
        self.setWindowTitle("Search library")
        self.resize(780, 560)

        layout = QVBoxLayout(self)
        heading = QLabel("Find something you saved")
        heading.setObjectName("homeSectionHeading")
        layout.addWidget(heading)
        description = QLabel(
            "Search titles, authors, tags, abstracts, PDF text, notes, and comments "
            "across all projects. This searches only your library and works offline."
        )
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText('Search your library… or tag:robotics / tag:"latent actions"')
        self.query_input.setClearButtonEnabled(True)
        layout.addWidget(self.query_input)
        self.status_label = QLabel("Type to search. Double-click a result to open it.")
        self.status_label.setObjectName("mutedLabel")
        layout.addWidget(self.status_label)
        self.result_list = QListWidget()
        self.result_list.setObjectName("librarySearchResults")
        self.result_list.setSpacing(4)
        self.result_list.setStyleSheet(
            "QListWidget#librarySearchResults { border: 0; background: transparent; outline: 0; }"
            "QListWidget#librarySearchResults::item { padding: 12px; "
            "border: 1px solid #dedfe2; border-radius: 7px; background: #ffffff; }"
            "QListWidget#librarySearchResults::item:selected { background: #eeeaff; "
            "color: #443381; border-color: #a99be0; }"
        )
        self.result_list.setWordWrap(True)
        self.result_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.result_list.itemActivated.connect(self.open_result)
        layout.addWidget(self.result_list, 1)
        self.open_button = QPushButton("Open selected paper")
        self.open_button.clicked.connect(self.open_result)
        self.open_button.setEnabled(False)
        layout.addWidget(self.open_button)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.start_search)
        self.query_input.textChanged.connect(self.schedule_search)
        self.query_input.returnPressed.connect(self.start_search)
        self.query_input.setFocus()

    def schedule_search(self, text=None):
        self.revision += 1
        self.search_timer.stop()
        self.result_list.clear()
        self.open_button.setEnabled(False)
        if not self.query_input.text().strip():
            self.status_label.setText("Type to search your saved papers.")
            return
        self.status_label.setText("Searching your library…")
        self.search_timer.start()

    def start_search(self):
        self.search_timer.stop()
        query = self.query_input.text().strip()
        if not query:
            return
        task = LibrarySearchTask(self.library.path, query, self.revision)
        task.signals.finished.connect(self.search_finished)
        task.signals.failed.connect(self.search_failed)
        self.thread_pool.start(task)

    def search_finished(self, payload):
        if payload["revision"] != self.revision:
            return
        self.result_list.clear()
        results = payload["results"]
        for paper in results:
            field = SEARCH_FIELD_LABELS.get(paper.get("match_field"), "Saved text")
            page = paper.get("match_page_number")
            if page:
                field += " · page " + str(page)
            text = (
                paper["title"] + "\n" + paper["project_name"] + " · " + field
                + "\n" + paper.get("match_excerpt", "")
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, paper)
            item.setToolTip(text)
            item.setSizeHint(QSize(0, 100))
            self.result_list.addItem(item)
        self.open_button.setEnabled(bool(results))
        if results:
            self.result_list.setCurrentRow(0)
        if len(results) >= 100:
            self.status_label.setText("Showing the first 100 matches. Refine your search.")
        elif results:
            self.status_label.setText(str(len(results)) + " paper(s) found across your library.")
        else:
            self.status_label.setText("No saved papers match. Try fewer words.")

    def search_failed(self, payload):
        if payload["revision"] != self.revision:
            return
        self.open_button.setEnabled(False)
        self.status_label.setText("Search could not finish: " + payload["message"])

    def open_result(self, item=None):
        if not isinstance(item, QListWidgetItem):
            item = self.result_list.currentItem()
        if item is None:
            return
        self.selected_paper = item.data(Qt.ItemDataRole.UserRole)
        self.accept()


class ProjectNotesDialog(QDialog):
    """Autosave research synthesis independently from individual paper notes."""

    def __init__(self, parent, library, project):
        super().__init__(parent)
        self.library = library
        self.project_id = project["id"]
        self.dirty = False
        self.setWindowTitle("Project notes · " + project["name"])
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        heading = QLabel(project["name"] + " · Research notes")
        heading.setObjectName("homeSectionHeading")
        layout.addWidget(heading)
        description = QLabel(
            "Connect ideas across papers: shared findings, open questions, "
            "baselines to try, and next steps. These notes belong to the project."
        )
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet("font-size: 14px; padding: 12px;")
        self.editor.setPlaceholderText(
            "What have I learned across these papers?\n\n"
            "Open questions\n\nBaselines and experiments\n\nNext steps"
        )
        self.editor.setPlainText(project.get("notes", ""))
        layout.addWidget(self.editor, 1)
        self.status_label = QLabel("Saved automatically to this project")
        self.status_label.setObjectName("mutedLabel")
        layout.addWidget(self.status_label)
        close_button = QPushButton("Done")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(600)
        self.save_timer.timeout.connect(self.save_pending)
        self.editor.textChanged.connect(self.schedule_save)
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.save_pending)

    def schedule_save(self):
        self.dirty = True
        self.status_label.setText("Saving…")
        self.save_timer.start()

    def save_pending(self):
        self.save_timer.stop()
        if not self.dirty:
            return True
        try:
            self.library.update_project_notes(self.project_id, self.editor.toPlainText())
        except Exception as error:
            self.status_label.setText("Notes could not be saved: " + str(error))
            return False
        self.dirty = False
        self.status_label.setText("Saved")
        return True

    def done(self, result):
        if self.save_pending():
            super().done(result)
