"""Display archived local documents and manage immutable revisions.

Reads document snapshots and version rows from the active Library. It writes no
files directly; revision imports are captured by local_documents and persisted
through Library below `<library>/source_cache/` and `corpus_cabinet.db`.
"""

import difflib
import html
import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from corpus_cabinet.local_documents import capture_local_document


class LocalDocumentInterceptor(QWebEngineUrlRequestInterceptor):
    """Allow archived bundle files and data URLs while blocking the network."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.allowed_root = ""

    def set_allowed_root(self, path):
        self.allowed_root = os.path.realpath(path) if path else ""

    def interceptRequest(self, info):
        url = info.requestUrl()
        scheme = url.scheme().casefold()
        if scheme in ("about", "data", "blob"):
            return
        if scheme == "file" and self.allowed_root:
            candidate = os.path.realpath(url.toLocalFile())
            try:
                allowed = os.path.commonpath(
                    [self.allowed_root, candidate]
                ) == self.allowed_root
            except ValueError:
                allowed = False
            if allowed:
                return
        info.block(True)


class LocalDocumentPage(QWebEnginePage):
    """Keep navigation inside the bundle and hand clicked web links outward."""

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        clicked = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        if navigation_type == clicked and url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class InteractiveDocumentPanel(QWidget):
    """Open exact Markdown or sandboxed interactive HTML from the active version."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = None
        self.paper = None
        self.web_view = None
        self.profile = None
        self.interceptor = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        self.status = QLabel(
            "Select a local document to open its archived original."
        )
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.stack = QStackedWidget()
        self.empty = QLabel("No local document selected.")
        self.empty.setObjectName("mutedLabel")
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.raw_text)
        layout.addWidget(self.stack, 1)

    def ensure_web_view(self):
        if self.web_view is not None:
            return
        self.web_view = QWebEngineView()
        self.profile = QWebEngineProfile(self.web_view)
        self.profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.MemoryHttpCache
        )
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self.interceptor = LocalDocumentInterceptor(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        page = LocalDocumentPage(self.profile, self.web_view)
        self.web_view.setPage(page)
        settings = self.web_view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        self.stack.addWidget(self.web_view)

    def set_context(self, library, paper):
        self.library = library
        self.paper = paper
        if not paper or paper.get("source_type") != "document":
            self.empty.setText("No local document selected.")
            self.stack.setCurrentWidget(self.empty)
            self.status.setText(
                "Select a local document to open its archived original."
            )
            return
        version = library.get_document_version(paper["id"])
        if not version:
            self.empty.setText("The archived original could not be found.")
            self.stack.setCurrentWidget(self.empty)
            return
        version_label = "Version " + str(version["version_number"])
        if version["source_format"] == "html":
            source_path = version.get("interactive_path") or version["original_path"]
            if not os.path.isfile(source_path):
                self.empty.setText("The archived HTML file is missing.")
                self.stack.setCurrentWidget(self.empty)
                return
            self.ensure_web_view()
            self.interceptor.set_allowed_root(os.path.dirname(source_path))
            self.web_view.load(QUrl.fromLocalFile(source_path))
            self.stack.setCurrentWidget(self.web_view)
            self.status.setText(
                version_label
                + " · JavaScript enabled inside the archived bundle · network and outside-file access blocked"
            )
            return
        try:
            with open(version["original_path"], encoding="utf-8-sig") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            self.empty.setText("The archived Markdown file is missing or unreadable.")
            self.stack.setCurrentWidget(self.empty)
            return
        self.raw_text.setPlainText(text)
        self.stack.setCurrentWidget(self.raw_text)
        self.status.setText(version_label + " · exact archived Markdown")


def version_diff_html(older, newer):
    """Return a bounded, readable unified text diff for two revisions."""
    older_lines = str(older.get("extracted_text") or "").splitlines()
    newer_lines = str(newer.get("extracted_text") or "").splitlines()
    lines = list(
        difflib.unified_diff(
            older_lines,
            newer_lines,
            fromfile="Version " + str(older["version_number"]),
            tofile="Version " + str(newer["version_number"]),
            lineterm="",
        )
    )
    if not lines:
        return "<p>No normalized text changes between these versions.</p>"
    truncated = len(lines) > 2000
    lines = lines[:2000]
    rendered = []
    for line in lines:
        color = "#303238"
        background = "transparent"
        if line.startswith("+") and not line.startswith("+++"):
            color = "#245C3A"
            background = "#E8F5EC"
        elif line.startswith("-") and not line.startswith("---"):
            color = "#943737"
            background = "#FCEBEC"
        elif line.startswith("@@"):
            color = "#574697"
            background = "#F1EEFA"
        rendered.append(
            '<div style="color:' + color + ';background:' + background
            + ';padding:1px 6px;white-space:pre-wrap;">'
            + html.escape(line) + "</div>"
        )
    if truncated:
        rendered.append(
            '<p style="color:#74777F;">Diff truncated after 2,000 lines.</p>'
        )
    return '<div style="font-family:monospace;font-size:12px;">' + "".join(rendered) + "</div>"


class DocumentVersionsPanel(QWidget):
    """Import, activate, and compare immutable document revisions."""

    documentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = None
        self.paper = None
        self.versions = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)
        heading_layout = QHBoxLayout()
        heading = QLabel("Document history")
        heading.setObjectName("homeSectionHeading")
        heading_layout.addWidget(heading)
        heading_layout.addStretch()
        self.add_button = QPushButton("Add revision…")
        self.add_button.clicked.connect(self.add_revision)
        heading_layout.addWidget(self.add_button)
        layout.addLayout(heading_layout)
        self.status = QLabel(
            "Revisions preserve the original file and normalized Reader content."
        )
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.version_list = QListWidget()
        self.version_list.currentItemChanged.connect(self.version_selected)
        layout.addWidget(self.version_list)
        action_layout = QHBoxLayout()
        self.activate_button = QPushButton("Make selected version current")
        self.activate_button.clicked.connect(self.activate_selected)
        action_layout.addWidget(self.activate_button)
        action_layout.addStretch()
        layout.addLayout(action_layout)
        compare_layout = QHBoxLayout()
        compare_layout.addWidget(QLabel("Compare"))
        self.older_combo = QComboBox()
        compare_layout.addWidget(self.older_combo)
        compare_layout.addWidget(QLabel("with"))
        self.newer_combo = QComboBox()
        compare_layout.addWidget(self.newer_combo)
        self.compare_button = QPushButton("Show changes")
        self.compare_button.clicked.connect(self.compare_versions)
        compare_layout.addWidget(self.compare_button)
        compare_layout.addStretch()
        layout.addLayout(compare_layout)
        self.diff_view = QTextBrowser()
        self.diff_view.setOpenExternalLinks(False)
        layout.addWidget(self.diff_view, 1)
        self.set_context(None, None)

    def set_context(self, library, paper):
        self.library = library
        self.paper = paper
        is_document = bool(paper and paper.get("source_type") == "document")
        self.add_button.setEnabled(is_document)
        self.version_list.clear()
        self.older_combo.clear()
        self.newer_combo.clear()
        self.diff_view.clear()
        self.versions = []
        if not is_document:
            self.status.setText("Select a local document to inspect its history.")
            self.activate_button.setEnabled(False)
            self.compare_button.setEnabled(False)
            return
        self.versions = library.list_document_versions(paper["id"])
        for version in self.versions:
            label = "Version " + str(version["version_number"])
            if version.get("is_current"):
                label += " · current"
            label += "\n" + version["original_filename"] + " · " + version["created_at"][:10]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, version["id"])
            self.version_list.addItem(item)
        chronological = list(reversed(self.versions))
        for version in chronological:
            label = "Version " + str(version["version_number"])
            self.older_combo.addItem(label, version["id"])
            self.newer_combo.addItem(label, version["id"])
        if len(chronological) > 1:
            self.older_combo.setCurrentIndex(len(chronological) - 2)
            self.newer_combo.setCurrentIndex(len(chronological) - 1)
        self.compare_button.setEnabled(len(self.versions) > 1)
        if self.version_list.count():
            self.version_list.setCurrentRow(0)
        self.status.setText(
            str(len(self.versions)) + " immutable version"
            + ("" if len(self.versions) == 1 else "s") + " saved."
        )

    def version_selected(self, current, previous=None):
        if current is None:
            self.activate_button.setEnabled(False)
            return
        version_id = current.data(Qt.ItemDataRole.UserRole)
        version = self.version_by_id(version_id)
        self.activate_button.setEnabled(bool(version and not version.get("is_current")))

    def version_by_id(self, version_id):
        for version in self.versions:
            if version["id"] == version_id:
                return version
        return None

    def add_revision(self):
        if not self.paper or not self.library:
            return
        path, ignored_filter = QFileDialog.getOpenFileName(
            self,
            "Add document revision",
            "",
            "Documents (*.html *.htm *.md *.markdown)",
        )
        del ignored_filter
        if not path:
            return
        try:
            capture = capture_local_document(path)
            self.library.add_document_revision(self.paper["id"], capture)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "Revision could not be added", str(error))
            return
        paper_id = self.paper["id"]
        self.paper = self.library.get_paper(paper_id)
        self.set_context(self.library, self.paper)
        self.documentChanged.emit(paper_id)

    def activate_selected(self):
        item = self.version_list.currentItem()
        if not item or not self.paper or not self.library:
            return
        try:
            self.library.activate_document_version(
                self.paper["id"], item.data(Qt.ItemDataRole.UserRole)
            )
        except ValueError as error:
            QMessageBox.warning(self, "Version could not be opened", str(error))
            return
        paper_id = self.paper["id"]
        self.paper = self.library.get_paper(paper_id)
        self.set_context(self.library, self.paper)
        self.documentChanged.emit(paper_id)

    def compare_versions(self):
        older = self.version_by_id(self.older_combo.currentData())
        newer = self.version_by_id(self.newer_combo.currentData())
        if not older or not newer:
            return
        self.diff_view.setHtml(version_diff_html(older, newer))
