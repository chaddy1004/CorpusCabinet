"""Run the native Corpus Cabinet desktop application.

The application reads a workspace registry and SQLite library, reads selected
PDFs, and writes project metadata, paper metadata, extracted text, and copied
PDFs into the active library folder. It uses Qt Widgets and Qt PDF directly;
there is no browser or local HTTP server.
"""

import os
import sys
import tempfile

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QStandardPaths,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from dotenv import load_dotenv

from corpus_cabinet.downloads import download_pdf
from corpus_cabinet.search import OnlineSearchService, google_scholar_url
from corpus_cabinet.storage import Library, WorkspaceManager


class ImportSignals(QObject):
    """Signals emitted by a background PDF import task."""

    finished = Signal(list)
    failed = Signal(str)


class ImportTask(QRunnable):
    """Copy and index a list of PDFs without blocking the Qt event loop."""

    def __init__(self, library_path, project_id, paths):
        super().__init__()
        self.library_path = library_path
        self.project_id = project_id
        self.paths = paths
        self.signals = ImportSignals()

    def run(self):
        try:
            library = Library(self.library_path)
            papers = []
            for path in self.paths:
                papers.append(library.import_pdf(self.project_id, path))
            self.signals.finished.emit(papers)
        except Exception as error:
            self.signals.failed.emit(str(error))


class SearchSignals(QObject):
    """Signals emitted by a background online search task."""

    finished = Signal(list)
    failed = Signal(str)


class SearchTask(QRunnable):
    """Search online providers without blocking the Qt event loop."""

    def __init__(self, title, config, offline):
        super().__init__()
        self.title = title
        self.config = config
        self.offline = offline
        self.signals = SearchSignals()

    def run(self):
        try:
            service = OnlineSearchService(self.config)
            service.set_offline(self.offline)
            results = service.search(self.title)
            self.signals.finished.emit(results)
        except Exception as error:
            self.signals.failed.emit(str(error))


class DownloadSignals(QObject):
    """Signals emitted by a background PDF download and import task."""

    finished = Signal(dict)
    failed = Signal(str)


class DownloadTask(QRunnable):
    """Download one direct PDF, import it, and apply its online metadata."""

    def __init__(self, library_path, project_id, result, config):
        super().__init__()
        self.library_path = library_path
        self.project_id = project_id
        self.result = result
        self.config = config
        self.signals = DownloadSignals()

    def run(self):
        temporary_path = ""
        try:
            pdf_url = self.result.get("pdf_url", "")
            if not pdf_url:
                raise ValueError(
                    "This result does not expose a direct open-access PDF"
                )

            file_descriptor, temporary_path = tempfile.mkstemp(
                prefix="corpus-cabinet-",
                suffix=".pdf",
            )
            os.close(file_descriptor)
            download_pdf(pdf_url, temporary_path, self.config)

            library = Library(self.library_path)
            paper = library.import_pdf(self.project_id, temporary_path)
            paper = library.update_paper_metadata(paper["id"], self.result)
            self.signals.finished.emit(paper)
        except Exception as error:
            self.signals.failed.emit(str(error))
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)


class SearchResultsDialog(QDialog):
    """Let the user review and apply normalized online search results."""

    def __init__(
        self,
        parent,
        results,
        project_id,
        paper_id,
        offline,
        apply_callback,
        add_callback,
        download_callback=None,
    ):
        super().__init__(parent)
        self.results = results
        self.project_id = project_id
        self.paper_id = paper_id
        self.offline = offline
        self.apply_callback = apply_callback
        self.add_callback = add_callback
        self.download_callback = download_callback

        self.setWindowTitle("Online search results")
        self.resize(900, 560)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        heading = QLabel("Explore paper matches")
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(heading)
        result_hint = QLabel(
            str(len(self.results)) +
            " candidate(s) · select one to inspect before saving"
        )
        result_hint.setStyleSheet("color: #666666;")
        layout.addWidget(result_hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.result_list = QListWidget()
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setMinimumWidth(340)
        self.result_list.currentItemChanged.connect(self.select_result)
        splitter.addWidget(self.result_list)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        self.result_title = QLabel("Select a result")
        self.result_title.setWordWrap(True)
        self.result_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        detail_layout.addWidget(self.result_title)
        self.result_meta = QLabel()
        self.result_meta.setWordWrap(True)
        self.result_meta.setStyleSheet("color: #666666;")
        detail_layout.addWidget(self.result_meta)
        self.result_abstract = QPlainTextEdit()
        self.result_abstract.setReadOnly(True)
        self.result_abstract.setPlaceholderText("No abstract available.")
        self.result_abstract.setMinimumHeight(240)
        detail_layout.addWidget(self.result_abstract)
        splitter.addWidget(detail_panel)
        splitter.setSizes([360, 540])
        layout.addWidget(splitter)

        action_layout = QHBoxLayout()
        self.apply_button = QPushButton("Use metadata")
        self.apply_button.setToolTip(
            "Apply this result to the paper currently selected in your library."
        )
        self.apply_button.clicked.connect(self.apply_selected)
        action_layout.addWidget(self.apply_button)
        self.add_button = QPushButton("Save to project")
        self.add_button.setToolTip(
            "Save this result as a citation in the current project."
        )
        self.add_button.clicked.connect(self.add_selected)
        action_layout.addWidget(self.add_button)
        self.download_button = QPushButton("Download PDF")
        self.download_button.setToolTip(
            "Download and import the direct open-access PDF for this result."
        )
        self.download_button.clicked.connect(self.download_selected)
        action_layout.addWidget(self.download_button)
        self.open_source_button = QPushButton("Open source")
        self.open_source_button.clicked.connect(self.open_source)
        action_layout.addWidget(self.open_source_button)
        self.scholar_button = QPushButton("Open in Google Scholar")
        self.scholar_button.clicked.connect(self.open_google_scholar)
        action_layout.addWidget(self.scholar_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        action_layout.addWidget(close_button)
        layout.addLayout(action_layout)

        for result in self.results:
            label = result.get("title", "Untitled paper")
            details = []
            if result.get("source"):
                details.append(result["source"])
            if result.get("year"):
                details.append(str(result["year"]))
            if result.get("venue"):
                details.append(result["venue"])
            if details:
                label += "\n" + " · ".join(details)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.result_list.addItem(item)

        if self.result_list.count() > 0:
            self.result_list.setCurrentRow(0)
        else:
            self.set_actions_enabled(False)

    def selected_result(self):
        item = self.result_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def select_result(self, item, previous_item=None):
        result = self.selected_result()
        if not result:
            self.result_title.setText("Select a result")
            self.result_meta.setText("")
            self.result_abstract.setPlainText("")
            self.set_actions_enabled(False)
            return

        self.result_title.setText(result.get("title", "Untitled paper"))
        metadata = []
        if result.get("authors"):
            metadata.append(result["authors"])
        if result.get("venue"):
            metadata.append(result["venue"])
        if result.get("year"):
            metadata.append(str(result["year"]))
        if result.get("doi"):
            metadata.append("DOI: " + result["doi"])
        if result.get("source"):
            metadata.append("Source: " + result["source"])
        if result.get("is_open_access"):
            metadata.append("Open access")
        self.result_meta.setText(" · ".join(metadata))
        self.result_abstract.setPlainText(result.get("abstract", ""))
        self.set_actions_enabled(True)

    def set_actions_enabled(self, enabled):
        self.apply_button.setEnabled(enabled and self.paper_id is not None)
        self.add_button.setEnabled(enabled and self.project_id is not None)
        result = self.selected_result()
        source_available = False
        title_available = False
        if result:
            if result.get("external_url") or result.get("pdf_url"):
                source_available = True
            if result.get("title"):
                title_available = True
        self.open_source_button.setEnabled(
            enabled and source_available and not self.offline
        )
        self.scholar_button.setEnabled(
            enabled and title_available and not self.offline
        )
        pdf_available = False
        if result and result.get("pdf_url"):
            pdf_available = True
        self.download_button.setEnabled(
            enabled
            and self.project_id is not None
            and pdf_available
            and not self.offline
            and self.download_callback is not None
        )

    def apply_selected(self):
        result = self.selected_result()
        if result and self.apply_callback:
            if self.apply_callback(result):
                self.accept()

    def add_selected(self):
        result = self.selected_result()
        if result and self.add_callback:
            if self.add_callback(result):
                self.accept()

    def download_selected(self):
        result = self.selected_result()
        if result and self.download_callback:
            if self.download_callback(result):
                self.accept()

    def open_source(self):
        result = self.selected_result()
        if not result or self.offline:
            return
        url = result.get("external_url", "")
        if not url:
            url = result.get("pdf_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def open_google_scholar(self):
        result = self.selected_result()
        if not result or self.offline:
            return
        url = google_scholar_url(result.get("title", ""))
        QDesktopServices.openUrl(QUrl(url))


class MainWindow(QMainWindow):
    """Main three-panel window for the desktop MVP."""

    def __init__(self, workspace_manager):
        super().__init__()
        self.workspace_manager = workspace_manager
        self.library = Library(self.workspace_manager.current())
        self.search_config = {
            "crossref_mailto": os.getenv("CROSSREF_MAILTO", ""),
        }
        self.search_service = OnlineSearchService(self.search_config)
        self.offline_mode = self.workspace_manager.is_offline_mode()
        self.search_service.set_offline(self.offline_mode)
        self.thread_pool = QThreadPool.globalInstance()
        self.current_project_id = None
        self.current_paper_id = None
        self.pdf_document = QPdfDocument(self)

        self.setWindowTitle("Corpus Cabinet")
        self.resize(1280, 800)
        self.build_ui()
        self.update_network_status()
        self.update_search_controls()
        self.refresh_library()

    def build_ui(self):
        central = QWidget()
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(12, 12, 12, 12)

        header_layout = QHBoxLayout()
        title = QLabel("Corpus Cabinet")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.network_status_label = QLabel()
        header_layout.addWidget(self.network_status_label)
        self.offline_mode_checkbox = QCheckBox("Offline mode")
        self.offline_mode_checkbox.setChecked(self.offline_mode)
        self.offline_mode_checkbox.toggled.connect(self.toggle_offline_mode)
        header_layout.addWidget(self.offline_mode_checkbox)
        self.library_label = QLabel()
        self.library_label.setStyleSheet("color: #666666;")
        header_layout.addWidget(self.library_label)
        self.open_library_button = QPushButton("Open Library")
        self.open_library_button.clicked.connect(self.open_library)
        header_layout.addWidget(self.open_library_button)
        outer_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.build_project_panel())
        splitter.addWidget(self.build_paper_panel())
        splitter.addWidget(self.build_detail_panel())
        splitter.setSizes([230, 360, 690])
        outer_layout.addWidget(splitter)
        self.setCentralWidget(central)

    def build_project_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        heading = QLabel("Projects")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        self.project_list = QListWidget()
        self.project_list.currentItemChanged.connect(self.select_project)
        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self.project_context_menu)
        layout.addWidget(self.project_list)

        self.new_project_button = QPushButton("+ New project")
        self.new_project_button.clicked.connect(self.create_project)
        layout.addWidget(self.new_project_button)
        return panel

    def build_paper_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        heading_layout = QHBoxLayout()
        heading = QLabel("Papers")
        heading.setStyleSheet("font-weight: 600;")
        heading_layout.addWidget(heading)
        heading_layout.addStretch()
        self.paper_count_label = QLabel("0 papers")
        self.paper_count_label.setStyleSheet("color: #666666;")
        heading_layout.addWidget(self.paper_count_label)
        layout.addLayout(heading_layout)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Filter library · press Enter to search online"
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setToolTip(
            "Type to filter your library. Press Enter to search Crossref, arXiv, and OpenAlex."
        )
        self.search_input.textChanged.connect(self.refresh_papers)
        self.search_input.returnPressed.connect(self.search_online)
        layout.addWidget(self.search_input)

        self.search_online_button = QPushButton("Search online  ↵")
        self.search_online_button.setToolTip(
            "Search Crossref, arXiv, and OpenAlex. You can also press Enter."
        )
        self.search_online_button.clicked.connect(self.search_online)
        layout.addWidget(self.search_online_button)

        self.import_button = QPushButton("Import PDFs")
        self.import_button.clicked.connect(self.choose_pdfs)
        self.import_button.setEnabled(False)
        layout.addWidget(self.import_button)

        self.paper_list = QListWidget()
        self.paper_list.currentItemChanged.connect(self.select_paper)
        layout.addWidget(self.paper_list)
        return panel

    def build_detail_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.detail_title = QLabel("Select a paper")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(self.detail_title)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.build_detail_tab(), "Details")
        pdf_panel = QWidget()
        pdf_layout = QVBoxLayout(pdf_panel)
        self.pdf_status_label = QLabel("Select a paper")
        self.pdf_status_label.setStyleSheet("color: #666666;")
        pdf_layout.addWidget(self.pdf_status_label)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        pdf_layout.addWidget(self.pdf_view)
        self.detail_tabs.addTab(pdf_panel, "PDF")
        layout.addWidget(self.detail_tabs)

        self.delete_paper_button = QPushButton("Delete paper")
        self.delete_paper_button.clicked.connect(self.delete_current_paper)
        self.delete_paper_button.setEnabled(False)
        layout.addWidget(self.delete_paper_button)
        return panel

    def build_detail_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.detail_meta = QLabel()
        self.detail_meta.setWordWrap(True)
        self.detail_meta.setStyleSheet("color: #666666;")
        layout.addWidget(self.detail_meta)

        link_layout = QHBoxLayout()
        self.open_source_button = QPushButton("Open source")
        self.open_source_button.setToolTip(
            "Open the saved publisher, repository, or PDF link for this paper."
        )
        self.open_source_button.clicked.connect(self.open_current_source)
        link_layout.addWidget(self.open_source_button)
        self.scholar_button = QPushButton("Google Scholar")
        self.scholar_button.setToolTip(
            "Search this saved paper title in Google Scholar."
        )
        self.scholar_button.clicked.connect(self.open_current_google_scholar)
        link_layout.addWidget(self.scholar_button)
        link_layout.addStretch()
        layout.addLayout(link_layout)

        form = QFormLayout()
        self.abstract_text = QPlainTextEdit()
        self.abstract_text.setReadOnly(True)
        self.abstract_text.setPlaceholderText("Online abstract will appear here.")
        self.abstract_text.setMinimumHeight(120)
        form.addRow("Abstract", self.abstract_text)

        self.task_text = QPlainTextEdit()
        self.task_text.setReadOnly(True)
        self.task_text.setPlaceholderText("AI task summary will appear here.")
        self.task_text.setMinimumHeight(90)
        form.addRow("Task", self.task_text)

        self.methodology_text = QPlainTextEdit()
        self.methodology_text.setReadOnly(True)
        self.methodology_text.setPlaceholderText("AI methodology summary will appear here.")
        self.methodology_text.setMinimumHeight(120)
        form.addRow("Methodology", self.methodology_text)
        layout.addLayout(form)

        self.assistant_status = QLabel()
        self.assistant_status.setWordWrap(True)
        self.assistant_status.setStyleSheet("color: #666666;")
        layout.addWidget(self.assistant_status)
        layout.addStretch()
        return tab

    def refresh_library(self):
        current_path = self.workspace_manager.current()
        self.library = Library(current_path)
        self.offline_mode = self.workspace_manager.is_offline_mode()
        self.search_service.set_offline(self.offline_mode)
        self.offline_mode_checkbox.blockSignals(True)
        self.offline_mode_checkbox.setChecked(self.offline_mode)
        self.offline_mode_checkbox.blockSignals(False)
        self.library_label.setText(current_path)
        self.update_network_status()
        self.update_search_controls()
        self.refresh_projects()
        self.import_button.setEnabled(self.current_project_id is not None)

    def update_network_status(self):
        if self.offline_mode:
            self.network_status_label.setText("Offline Mode · local library only")
            self.network_status_label.setStyleSheet("color: #9A5B13;")
        else:
            self.network_status_label.setText("Online search enabled")
            self.network_status_label.setStyleSheet("color: #39704A;")

    def update_search_controls(self):
        if not hasattr(self, "search_online_button"):
            return
        self.search_online_button.setEnabled(not self.offline_mode)
        self.update_paper_link_controls()

    def toggle_offline_mode(self, enabled):
        self.offline_mode = bool(enabled)
        self.search_service.set_offline(self.offline_mode)
        self.workspace_manager.set_offline_mode(self.offline_mode)
        self.update_network_status()
        self.update_search_controls()
        if self.offline_mode:
            self.statusBar().showMessage(
                "Offline Mode enabled. Local library features remain available.",
                5000,
            )
        else:
            self.statusBar().showMessage(
                "Online search enabled.",
                5000,
            )

    def refresh_projects(self, selected_id=None):
        projects = self.library.list_projects()
        if selected_id is None:
            selected_id = self.current_project_id

        self.project_list.blockSignals(True)
        self.project_list.clear()
        target_row = -1
        for index, project in enumerate(projects):
            label = project["name"] + " (" + str(project["paper_count"]) + ")"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            self.project_list.addItem(item)
            if project["id"] == selected_id:
                target_row = index
        self.project_list.blockSignals(False)

        if target_row < 0 and projects:
            target_row = 0

        if target_row >= 0:
            self.project_list.setCurrentRow(target_row)
        else:
            self.current_project_id = None
            self.import_button.setEnabled(False)
            self.refresh_papers()

    def refresh_papers(self):
        query = self.search_input.text().strip()
        papers = self.library.list_papers(self.current_project_id, query)
        paper_suffix = ""
        if len(papers) != 1:
            paper_suffix = "s"
        self.paper_count_label.setText(str(len(papers)) + " paper" + paper_suffix)

        self.paper_list.blockSignals(True)
        self.paper_list.clear()
        target_row = -1
        for index, paper in enumerate(papers):
            item = QListWidgetItem(paper["title"])
            item.setData(Qt.ItemDataRole.UserRole, paper["id"])
            self.paper_list.addItem(item)
            if paper["id"] == self.current_paper_id:
                target_row = index
        self.paper_list.blockSignals(False)

        if target_row >= 0:
            self.paper_list.setCurrentRow(target_row)
        else:
            self.current_paper_id = None
            self.render_empty_detail()

    def select_project(self, item, previous_item=None):
        if not item:
            return
        self.current_project_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_paper_id = None
        self.import_button.setEnabled(True)
        self.refresh_papers()

    def select_paper(self, item, previous_item=None):
        if not item:
            self.current_paper_id = None
            self.render_empty_detail()
            return

        paper_id = item.data(Qt.ItemDataRole.UserRole)
        paper = self.library.get_paper(paper_id)
        if not paper:
            self.render_empty_detail()
            return

        self.current_paper_id = paper_id
        self.render_paper_detail(paper)

    def render_empty_detail(self):
        self.detail_title.setText("Select a paper")
        self.detail_meta.setText("")
        self.abstract_text.setPlainText("")
        self.task_text.setPlainText("")
        self.methodology_text.setPlainText("")
        self.assistant_status.setText("")
        self.delete_paper_button.setEnabled(False)
        self.update_paper_link_controls(None)
        self.pdf_status_label.setText("Select a paper")
        self.pdf_document.close()

    def render_paper_detail(self, paper):
        self.detail_title.setText(paper.get("title", "Untitled paper"))
        metadata = []
        if paper.get("authors"):
            metadata.append(paper["authors"])
        if paper.get("conference"):
            metadata.append(paper["conference"])
        if paper.get("year"):
            metadata.append(str(paper["year"]))
        if paper.get("doi"):
            metadata.append("DOI: " + paper["doi"])
        if paper.get("metadata_source"):
            metadata.append("Source: " + paper["metadata_source"])
        self.detail_meta.setText(" · ".join(metadata))
        self.abstract_text.setPlainText(paper.get("abstract", ""))
        self.task_text.setPlainText(paper.get("task", ""))
        self.methodology_text.setPlainText(paper.get("methodology", ""))

        extracted_text = paper.get("extracted_text", "")
        if extracted_text:
            text_length = len(extracted_text)
            self.assistant_status.setText(
                "AI-ready full text indexed: " + str(text_length) +
                " characters."
            )
        else:
            abstract = paper.get("abstract", "")
            if abstract:
                self.assistant_status.setText(
                    "AI-ready abstract indexed: " + str(len(abstract)) +
                    " characters."
                )
            else:
                self.assistant_status.setText(
                    "No extracted text or abstract is available yet."
                )
        self.delete_paper_button.setEnabled(True)

        self.pdf_document.close()
        file_path = paper.get("file_path", "")
        if file_path and os.path.exists(file_path):
            self.pdf_status_label.setText("Local PDF")
            self.pdf_document.load(file_path)
        else:
            if paper.get("pdf_url"):
                self.pdf_status_label.setText(
                    "No local PDF. An open-access or source link is available in the online result."
                )
            else:
                self.pdf_status_label.setText("No local PDF")
        self.update_paper_link_controls(paper)

    def update_paper_link_controls(self, paper=None):
        if not hasattr(self, "open_source_button"):
            return

        source_available = False
        title_available = False
        if paper is None:
            if self.current_paper_id is not None:
                paper = self.library.get_paper(self.current_paper_id)
        if paper:
            if paper.get("external_url") or paper.get("pdf_url"):
                source_available = True
            if paper.get("title"):
                title_available = True

        self.open_source_button.setEnabled(
            source_available and not self.offline_mode
        )
        self.scholar_button.setEnabled(
            title_available and not self.offline_mode
        )

    def open_current_source(self):
        if self.offline_mode or self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        url = paper.get("external_url", "")
        if not url:
            url = paper.get("pdf_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def open_current_google_scholar(self):
        if self.offline_mode or self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        title = paper.get("title", "")
        if title:
            QDesktopServices.openUrl(QUrl(google_scholar_url(title)))

    def create_project(self):
        name, accepted = QInputDialog.getText(self, "New project", "Project name:")
        if not accepted:
            return
        try:
            project = self.library.create_project(name)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot create project", str(error))
            return
        self.refresh_projects(project["id"])

    def rename_project(self):
        if self.current_project_id is None:
            return
        project = self.library.get_project(self.current_project_id)
        if not project:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Rename project",
            "Project name:",
            text=project["name"],
        )
        if not accepted:
            return
        try:
            self.library.rename_project(self.current_project_id, name)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot rename project", str(error))
            return
        self.refresh_projects(self.current_project_id)

    def project_context_menu(self, position):
        item = self.project_list.itemAt(position)
        if not item:
            return
        self.project_list.setCurrentItem(item)
        menu = QMenu(self)
        rename_action = menu.addAction("Rename project")
        delete_action = menu.addAction("Delete project")
        selected_action = menu.exec(self.project_list.mapToGlobal(position))
        if selected_action == rename_action:
            self.rename_project()
        if selected_action == delete_action:
            self.delete_current_project()

    def delete_current_project(self):
        if self.current_project_id is None:
            return
        project = self.library.get_project(self.current_project_id)
        if not project:
            return
        answer = QMessageBox.question(
            self,
            "Delete project",
            "Delete project and all copied PDFs from this library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_project(self.current_project_id)
        self.current_project_id = None
        self.current_paper_id = None
        self.refresh_projects()

    def choose_pdfs(self):
        if self.current_project_id is None:
            return
        paths, accepted = QFileDialog.getOpenFileNames(
            self,
            "Import PDFs",
            "",
            "PDF files (*.pdf)",
        )
        if not accepted or not paths:
            return
        self.start_import(paths)

    def search_online(self):
        if self.offline_mode:
            QMessageBox.information(
                self,
                "Offline Mode",
                "Online search is disabled while Offline Mode is enabled.",
            )
            return

        title = self.search_input.text().strip()
        if not title and self.current_paper_id is not None:
            paper = self.library.get_paper(self.current_paper_id)
            if paper:
                title = paper.get("title", "").strip()

        if not title:
            QMessageBox.information(
                self,
                "Search online",
                "Enter a paper title or select a paper first.",
            )
            return

        self.start_online_search(title)

    def start_online_search(self, title):
        self.search_online_button.setEnabled(False)
        self.offline_mode_checkbox.setEnabled(False)
        self.import_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        self.statusBar().showMessage(
            "Searching Crossref, arXiv, and OpenAlex…"
        )
        task = SearchTask(title, self.search_config, self.offline_mode)
        task.signals.finished.connect(self.search_finished)
        task.signals.failed.connect(self.search_failed)
        self.thread_pool.start(task)

    def search_finished(self, results):
        self.offline_mode_checkbox.setEnabled(True)
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.import_button.setEnabled(self.current_project_id is not None)
        self.update_search_controls()

        if not results:
            self.statusBar().showMessage("No online results found", 5000)
            QMessageBox.information(
                self,
                "Search online",
                "No matching papers were returned.",
            )
            return

        self.statusBar().showMessage(
            "Found " + str(len(results)) + " online result(s)",
            5000,
        )
        dialog = SearchResultsDialog(
            self,
            results,
            self.current_project_id,
            self.current_paper_id,
            self.offline_mode,
            self.apply_online_metadata,
            self.add_online_paper,
            self.download_online_paper,
        )
        dialog.exec()

    def search_failed(self, message):
        self.offline_mode_checkbox.setEnabled(True)
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.import_button.setEnabled(self.current_project_id is not None)
        self.update_search_controls()
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Online search failed", message)

    def apply_online_metadata(self, result):
        if self.current_paper_id is None:
            return False
        try:
            paper = self.library.update_paper_metadata(
                self.current_paper_id,
                result,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Cannot apply metadata", str(error))
            return False

        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        self.select_paper_by_id(paper["id"])
        self.statusBar().showMessage("Online metadata applied", 5000)
        return True

    def add_online_paper(self, result):
        if self.current_project_id is None:
            return False
        try:
            paper = self.library.create_paper_from_search(
                self.current_project_id,
                result,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Cannot add paper", str(error))
            return False

        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        self.select_paper_by_id(paper["id"])
        self.statusBar().showMessage("Paper added from online metadata", 5000)
        return True

    def download_online_paper(self, result):
        if self.offline_mode:
            return False
        if self.current_project_id is None:
            return False
        if not result.get("pdf_url"):
            return False

        self.search_input.setEnabled(False)
        self.search_online_button.setEnabled(False)
        self.offline_mode_checkbox.setEnabled(False)
        self.project_list.setEnabled(False)
        self.paper_list.setEnabled(False)
        self.import_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        self.statusBar().showMessage("Downloading and importing PDF…")

        task = DownloadTask(
            self.library.path,
            self.current_project_id,
            result,
            self.search_config,
        )
        task.signals.finished.connect(self.download_finished)
        task.signals.failed.connect(self.download_failed)
        self.thread_pool.start(task)
        return True

    def restore_download_controls(self):
        self.search_input.setEnabled(True)
        self.offline_mode_checkbox.setEnabled(True)
        self.project_list.setEnabled(True)
        self.paper_list.setEnabled(True)
        self.import_button.setEnabled(self.current_project_id is not None)
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.update_search_controls()

    def download_finished(self, paper):
        self.restore_download_controls()
        self.statusBar().showMessage("Downloaded and imported PDF", 5000)
        self.refresh_projects(paper.get("project_id"))
        self.refresh_papers()
        self.select_paper_by_id(paper["id"])

    def download_failed(self, message):
        self.restore_download_controls()
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "PDF download failed", message)

    def start_import(self, paths):
        self.import_button.setEnabled(False)
        self.search_online_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        self.statusBar().showMessage("Importing " + str(len(paths)) + " PDF(s)…")
        task = ImportTask(self.library.path, self.current_project_id, paths)
        task.signals.finished.connect(self.import_finished)
        task.signals.failed.connect(self.import_failed)
        self.thread_pool.start(task)

    def import_finished(self, papers):
        self.import_button.setEnabled(True)
        self.update_search_controls()
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.statusBar().showMessage("Imported " + str(len(papers)) + " paper(s)", 5000)
        selected_id = None
        if papers:
            selected_id = papers[-1]["id"]
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        if selected_id is not None:
            self.select_paper_by_id(selected_id)

    def import_failed(self, message):
        self.import_button.setEnabled(True)
        self.update_search_controls()
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "Import failed", message)

    def select_paper_by_id(self, paper_id):
        for index in range(self.paper_list.count()):
            item = self.paper_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == paper_id:
                self.paper_list.setCurrentItem(item)
                return

    def delete_current_paper(self):
        if self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        answer = QMessageBox.question(
            self,
            "Delete paper",
            "Delete this paper and its copied PDF?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_paper(self.current_paper_id)
        self.current_paper_id = None
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()

    def open_library(self):
        selected_path = QFileDialog.getExistingDirectory(
            self,
            "Open library folder",
            self.library.path,
        )
        if not selected_path:
            return
        self.workspace_manager.activate(selected_path)
        self.current_project_id = None
        self.current_paper_id = None
        self.refresh_library()


def default_paths():
    """Return the config and default library paths for this installation."""
    data_dir = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    configured_config_dir = os.getenv("CORPUS_CABINET_CONFIG_DIR")
    if configured_config_dir:
        config_dir = os.path.abspath(os.path.expanduser(configured_config_dir))
    else:
        config_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )

    configured_path = os.getenv("WORKSPACE_DIR")
    if configured_path:
        default_library = os.path.abspath(os.path.expanduser(configured_path))
    else:
        default_library = os.path.join(data_dir, "library")

    try:
        os.makedirs(config_dir, exist_ok=True)
    except OSError:
        config_dir = os.path.join(default_library, ".corpus-cabinet")
        os.makedirs(config_dir, exist_ok=True)

    config_path = os.path.join(config_dir, "workspaces.json")
    return config_path, default_library


def run_app():
    """Create the Qt application and run its event loop."""
    load_dotenv()
    application = QApplication(sys.argv)
    application.setApplicationName("Corpus Cabinet")
    application.setOrganizationName("Corpus Cabinet")
    config_path, default_library = default_paths()
    manager = WorkspaceManager(config_path, default_library)
    window = MainWindow(manager)
    window.show()
    return application.exec()
