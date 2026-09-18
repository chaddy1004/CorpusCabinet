"""Run the native Corpus Cabinet desktop application.

The application reads a workspace registry and SQLite library, reads selected
PDFs, and writes project metadata, paper metadata, extracted text, and copied
PDFs into the active library folder. It uses Qt Widgets and Qt PDF directly;
there is no browser or local HTTP server.
"""

import html
import json
import math
import os
import re
import signal
import sys
import tempfile
from urllib.parse import quote, unquote

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QKeySequence,
    QPalette,
    QPainter,
    QPen,
    QShortcut,
    QTextDocument,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from dotenv import load_dotenv

from corpus_cabinet.citations import fetch_doi_bibtex, generate_bibtex
from corpus_cabinet.downloads import download_pdf
from corpus_cabinet.research_ui import LibrarySearchDialog, ProjectNotesDialog
from corpus_cabinet.search import (
    OnlineSearchService,
    google_scholar_url,
    suggest_context_terms,
)
from corpus_cabinet.storage import Library, WorkspaceManager


LATEX_SYMBOLS = {
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\pi": "π",
    r"\rho": "ρ",
    r"\sigma": "σ",
    r"\tau": "τ",
    r"\phi": "φ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Delta": "Δ",
    r"\Lambda": "Λ",
    r"\Phi": "Φ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
    r"\times": "×",
    r"\pm": "±",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\neq": "≠",
    r"\infty": "∞",
}
SUPERSCRIPT_CHARACTERS = str.maketrans(
    "0123456789+-=()in",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ",
)
SUBSCRIPT_CHARACTERS = str.maketrans(
    "0123456789+-=()aehi jklmnoprstuvx".replace(" ", ""),
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
)

READING_STATUS_COLORS = {
    "unread": ("#FDE8E7", "#A83532", "#E5AAA7"),
    "reading": ("#FFF4CB", "#765600", "#E3C86C"),
    "read": ("#E5EFFF", "#2457A5", "#9BBBED"),
}


def replace_latex_symbols(value):
    """Replace common LaTeX symbol commands with Unicode equivalents."""
    for command, symbol in LATEX_SYMBOLS.items():
        value = value.replace(command, symbol)
    return value


def script_to_unicode(value, translation, marker):
    """Translate a simple script or preserve readable fallback notation."""
    for character in value:
        if ord(character) not in translation:
            return marker + "(" + value + ")"
    return value.translate(translation)


def replace_superscript_group(match):
    return script_to_unicode(match.group(1), SUPERSCRIPT_CHARACTERS, "^")


def replace_superscript_character(match):
    return script_to_unicode(match.group(1), SUPERSCRIPT_CHARACTERS, "^")


def replace_subscript_group(match):
    return script_to_unicode(match.group(1), SUBSCRIPT_CHARACTERS, "_")


def replace_subscript_character(match):
    return script_to_unicode(match.group(1), SUBSCRIPT_CHARACTERS, "_")


def replace_latex_text(match):
    return match.group(1)


def latex_math_to_plain_text(match):
    """Render one inline math expression using safe Unicode characters."""
    value = replace_latex_symbols(match.group(1))
    value = re.sub(r"\\text\{([^{}]+)\}", replace_latex_text, value)
    value = re.sub(r"\^\{([^{}]+)\}", replace_superscript_group, value)
    value = re.sub(r"\^([A-Za-z0-9+\-=()])", replace_superscript_character, value)
    value = re.sub(r"_\{([^{}]+)\}", replace_subscript_group, value)
    value = re.sub(r"_([A-Za-z0-9+\-=()])", replace_subscript_character, value)
    return value.replace("{", "").replace("}", "")


def latex_to_plain_text(value):
    """Render lightweight inline LaTeX for plain-text Qt controls."""
    value = str(value or "")
    value = re.sub(r"\$([^$]+)\$", latex_math_to_plain_text, value)
    return value


def latex_math_to_html(match):
    """Render one escaped inline math expression as Qt-supported HTML."""
    value = html.escape(match.group(1))
    value = replace_latex_symbols(value)
    value = re.sub(r"\\text\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"\^\{([^{}]+)\}", r"<sup>\1</sup>", value)
    value = re.sub(r"\^([A-Za-z0-9+\-=()])", r"<sup>\1</sup>", value)
    value = re.sub(r"_\{([^{}]+)\}", r"<sub>\1</sub>", value)
    value = re.sub(r"_([A-Za-z0-9+\-=()])", r"<sub>\1</sub>", value)
    value = value.replace("{", "").replace("}", "")
    return value


def latex_to_html(value):
    """Escape provider text and render its lightweight inline LaTeX."""
    value = str(value or "")
    parts = []
    position = 0
    for match in re.finditer(r"\$([^$]+)\$", value):
        parts.append(html.escape(value[position:match.start()]))
        parts.append(latex_math_to_html(match))
        position = match.end()
    parts.append(html.escape(value[position:]))
    return "".join(parts)


def load_opendyslexic_font():
    """Register the bundled OpenDyslexic font and return its family name."""
    font_path = os.path.join(
        os.path.dirname(__file__),
        "assets",
        "fonts",
        "OpenDyslexic-Regular.otf",
    )
    font_id = QFontDatabase.addApplicationFont(font_path)
    if font_id < 0:
        return ""
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        return ""
    return families[0]


def comfortable_abstract_html(value, font_family=""):
    """Render provider paragraphs with comfortable reading typography."""
    value = str(value or "").strip()
    if not value:
        return ""
    paragraphs = re.split(r"\n\s*\n", value)
    output = []
    for paragraph in paragraphs:
        paragraph = " ".join(paragraph.split())
        if paragraph:
            output.append("<p>" + latex_to_html(paragraph) + "</p>")
    if font_family:
        family_css = "'" + html.escape(font_family, quote=True) + "', sans-serif"
        line_height = "175%"
    else:
        family_css = "Georgia, 'Times New Roman', serif"
        line_height = "165%"
    return (
        "<div style=\"font-family: " + family_css + "; "
        "font-size: 15px; line-height: " + line_height + "; color: #2d2f33;\">"
        + "".join(output)
        + "</div>"
    )


def title_sort_key(paper):
    """Sort papers by normalized title."""
    return str(paper.get("title") or "").casefold()


def author_sort_key(paper):
    """Sort papers by normalized author text, with missing authors last."""
    authors = str(paper.get("authors") or "").casefold()
    if authors:
        return (0, authors)
    return (1, "")


def publication_sort_key(paper):
    """Sort papers newest-first, with missing publication years last."""
    year = paper.get("year")
    if year is None:
        return (1, 0)
    return (0, -int(year))


def created_sort_key(paper):
    """Sort papers by their ISO-formatted creation timestamp."""
    return str(paper.get("created_at") or "")


def first_author_label(authors):
    """Return a compact author label for a discovery result row."""
    names = [name.strip() for name in str(authors or "").split(",")]
    names = [name for name in names if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return names[0] + " et al."


def project_search_context(library, project_id):
    """Suggest visible preference terms from a project's saved papers."""
    if project_id is None:
        return ""
    project = library.get_project(project_id)
    if not project:
        return ""
    if project.get("kind") == "scrapbook":
        return ""
    if project.get("search_context"):
        return project["search_context"]

    parts = [project.get("name", "")]
    papers = library.list_papers(project_id)
    for paper in papers[:50]:
        parts.append(paper.get("title", ""))
        parts.append(paper.get("abstract", ""))
        parts.append(paper.get("task", ""))
    return suggest_context_terms(" ".join(parts))


def stored_paper_result(paper, pdf_url=None):
    """Return a saved paper in the provider-neutral metadata shape."""
    if pdf_url is None:
        pdf_url = paper.get("pdf_url", "")
    return {
        "title": paper.get("title", ""),
        "authors": paper.get("authors", ""),
        "venue": paper.get("conference", ""),
        "year": paper.get("year"),
        "doi": paper.get("doi", ""),
        "abstract": paper.get("abstract", ""),
        "external_id": paper.get("external_id", ""),
        "external_url": paper.get("external_url", ""),
        "project_url": paper.get("project_url", ""),
        "pdf_url": pdf_url,
        "source": paper.get("metadata_source", ""),
        "citation_count": paper.get("citation_count", 0),
    }


def normalize_project_ids(value):
    """Return distinct project IDs from one ID or a sequence of IDs."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = [value]
    project_ids = []
    for project_id in candidates:
        if project_id is not None and project_id not in project_ids:
            project_ids.append(project_id)
    return project_ids


def reading_status_sort_key(paper):
    """Put active reading first, followed by unread and completed papers."""
    order = {"reading": 0, "unread": 1, "read": 2}
    return (order.get(paper.get("reading_status"), 1), title_sort_key(paper))


def paper_card_label(paper):
    """Keep paper cards consistent after status and favorite changes."""
    label = latex_to_plain_text(paper["title"])
    if paper.get("favorite"):
        label = "★ " + label
    details = []
    if paper.get("authors"):
        details.append(first_author_label(paper["authors"]))
    if paper.get("year"):
        details.append(str(paper["year"]))
    details.append(str(paper.get("reading_status") or "unread").capitalize())
    return label + "\n" + " · ".join(details)


def restore_zoom_choice(combo, value):
    """Restore a saved fit mode or numeric zoom, falling back to fit width."""
    target_index = -1
    for index in range(combo.count()):
        if str(combo.itemData(index)) == str(value):
            target_index = index
            break
    if target_index < 0:
        try:
            zoom = float(value)
        except (TypeError, ValueError):
            zoom = 0
        if 0.25 <= zoom <= 4.0:
            target_index = combo.findData("custom_zoom", Qt.ItemDataRole.UserRole + 1)
            if target_index < 0:
                combo.addItem("", zoom)
                target_index = combo.count() - 1
                combo.setItemData(target_index, "custom_zoom", Qt.ItemDataRole.UserRole + 1)
            combo.setItemText(target_index, f"{zoom * 100:.0f}%")
            combo.setItemData(target_index, zoom)
        else:
            target_index = 0
    combo.setCurrentIndex(target_index)


def pdf_paths_from_mime_data(mime_data, require_exists=True):
    """Return local PDF paths carried by a drag operation."""
    paths = []
    if not mime_data.hasUrls():
        return paths
    for url in mime_data.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        if not path.lower().endswith(".pdf"):
            continue
        if require_exists and not os.path.isfile(path):
            continue
        paths.append(path)
    return paths


def stop_application(signum=None, frame=None):
    """Quit the Qt application when the terminal sends Ctrl+C."""
    application = QApplication.instance()
    if application is not None:
        application.quit()


def allow_python_signals():
    """Give Python regular turns while Qt owns the main event loop."""
    return None


def apply_light_theme(application):
    """Use one deliberate light palette instead of the host dark appearance."""
    application.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f7f7f8"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f2f2f4"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#6350aa"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a8d94"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#d7d8dc"))
    palette.setColor(QPalette.ColorRole.Midlight, QColor("#ececef"))
    palette.setColor(QPalette.ColorRole.Dark, QColor("#b7bac2"))
    palette.setColor(QPalette.ColorRole.Light, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#4d3b8d"))
    application.setPalette(palette)
    application.setStyleSheet(
        "QScrollBar:vertical { background: transparent; width: 10px; margin: 0; border: 0; }"
        "QScrollBar::handle:vertical { background: #BFC3CF; border-radius: 3px; "
        "min-height: 28px; margin: 0 2px; }"
        "QScrollBar::handle:vertical:hover { background: #9C94B2; }"
        "QScrollBar::handle:vertical:pressed { background: #7660BD; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        "QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; border: 0; }"
        "QScrollBar::handle:horizontal { background: #BFC3CF; border-radius: 3px; "
        "min-width: 28px; margin: 2px 0; }"
        "QScrollBar::handle:horizontal:hover { background: #9C94B2; }"
        "QScrollBar::handle:horizontal:pressed { background: #7660BD; }"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: 0; }"
        "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
    )


class ImportSignals(QObject):
    """Signals emitted by a background PDF import task."""

    finished = Signal(list)
    failed = Signal(str)
    duplicatesFound = Signal(object)


class ImportTask(QRunnable):
    """Copy and index a list of PDFs without blocking the Qt event loop."""

    def __init__(
        self,
        library_path,
        project_ids,
        paths,
        allow_duplicates=False,
    ):
        super().__init__()
        self.library_path = library_path
        self.project_ids = normalize_project_ids(project_ids)
        self.paths = paths
        self.allow_duplicates = allow_duplicates
        self.signals = ImportSignals()

    def run(self):
        library = None
        papers = []
        try:
            library = Library(self.library_path)
            self.project_ids = library.validate_destination_projects(
                self.project_ids
            )
            if not self.allow_duplicates:
                duplicates = []
                for path in self.paths:
                    duplicates.extend(
                        library.find_pdf_duplicates(self.project_ids, path)
                    )
                if duplicates:
                    self.signals.duplicatesFound.emit(
                        {
                            "duplicates": duplicates,
                            "paths": self.paths,
                            "project_ids": self.project_ids,
                        }
                    )
                    return
            for path in self.paths:
                for project_id in self.project_ids:
                    papers.append(library.import_pdf(project_id, path))
            self.signals.finished.emit(papers)
        except Exception as error:
            if library is not None:
                for paper in papers:
                    library.delete_paper(paper["id"])
            self.signals.failed.emit(str(error))


class AttachTask(QRunnable):
    """Copy and index one user-selected PDF for an existing paper."""

    def __init__(self, library_path, paper_id, path):
        super().__init__()
        self.library_path = library_path
        self.paper_id = paper_id
        self.path = path
        self.signals = DownloadSignals()

    def run(self):
        try:
            library = Library(self.library_path)
            paper = library.attach_pdf(self.paper_id, self.path)
            self.signals.finished.emit(paper)
        except Exception as error:
            self.signals.failed.emit(str(error))


class SearchSignals(QObject):
    """Signals emitted by a background online search task."""

    finished = Signal(list)
    failed = Signal(str)


class SearchTask(QRunnable):
    """Search online providers without blocking the Qt event loop."""

    def __init__(self, title, context, config, offline):
        super().__init__()
        self.title = title
        self.context = context
        self.config = config
        self.offline = offline
        self.signals = SearchSignals()

    def run(self):
        try:
            service = OnlineSearchService(self.config)
            service.set_offline(self.offline)
            results = service.search(self.title, self.context)
            self.signals.finished.emit(results)
        except Exception as error:
            self.signals.failed.emit(str(error))


class BibtexSignals(QObject):
    """Signals emitted by a DOI BibTeX lookup task."""

    finished = Signal(str)
    failed = Signal(str)


class BibtexTask(QRunnable):
    """Retrieve DOI-registered BibTeX without blocking the interface."""

    def __init__(self, doi, config):
        super().__init__()
        self.doi = doi
        self.config = config
        self.signals = BibtexSignals()

    def run(self):
        try:
            bibtex = fetch_doi_bibtex(self.doi, self.config)
            self.signals.finished.emit(bibtex)
        except Exception as error:
            self.signals.failed.emit(str(error))


class DownloadSignals(QObject):
    """Signals emitted by a background PDF download and import task."""

    finished = Signal(object)
    failed = Signal(str)


class DownloadTask(QRunnable):
    """Download one direct PDF, import it, and apply its online metadata."""

    def __init__(self, library_path, project_ids, result, config, paper_id=None):
        super().__init__()
        self.library_path = library_path
        self.project_ids = normalize_project_ids(project_ids)
        self.result = result
        self.config = config
        self.paper_id = paper_id
        self.signals = DownloadSignals()

    def run(self):
        temporary_path = ""
        papers = []
        library = None
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
            if self.paper_id is None:
                self.project_ids = library.validate_destination_projects(
                    self.project_ids
                )
                for project_id in self.project_ids:
                    paper = library.import_pdf(project_id, temporary_path)
                    papers.append(paper)
                    paper = library.update_paper_metadata(
                        paper["id"],
                        self.result,
                    )
                    papers[-1] = paper
            else:
                paper = library.attach_pdf(self.paper_id, temporary_path)
                paper = library.update_paper_metadata(
                    paper["id"],
                    self.result,
                )
                papers.append(paper)
            self.signals.finished.emit(papers)
        except Exception as error:
            if self.paper_id is None and library is not None:
                for paper in papers:
                    library.delete_paper(paper["id"])
            self.signals.failed.emit(str(error))
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)


class BibtexDialog(QDialog):
    """Review, copy, and save DOI-backed or locally generated BibTeX."""

    def __init__(self, parent, paper, config, offline, save_callback):
        super().__init__(parent)
        self.paper = paper
        self.config = config
        self.offline = offline
        self.save_callback = save_callback
        self.modified = False
        self.thread_pool = QThreadPool.globalInstance()

        self.setWindowTitle("BibTeX citation")
        self.resize(700, 500)
        self.build_ui()
        self.prepare_citation()

    def build_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel(latex_to_html(self.paper.get("title", "Untitled paper")))
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        layout.addWidget(title)

        self.source_label = QLabel()
        self.source_label.setObjectName("mutedLabel")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("BibTeX will appear here.")
        self.editor.setStyleSheet("font-family: Menlo, Monaco, monospace;")
        layout.addWidget(self.editor)

        self.warning_label = QLabel(
            "For the safest option, obtain BibTeX directly from Google "
            "Scholar manually and verify it against the published paper."
        )
        self.warning_label.setObjectName("citationWarning")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        action_layout = QHBoxLayout()
        self.scholar_button = QPushButton("Open Google Scholar")
        self.scholar_button.setEnabled(not self.offline)
        self.scholar_button.clicked.connect(self.open_google_scholar)
        action_layout.addWidget(self.scholar_button)
        action_layout.addStretch()
        self.save_button = QPushButton("Save changes")
        self.save_button.clicked.connect(self.save_changes)
        action_layout.addWidget(self.save_button)
        self.copy_button = QPushButton("Copy BibTeX")
        self.copy_button.setObjectName("citationPrimaryButton")
        self.copy_button.clicked.connect(self.copy_bibtex)
        action_layout.addWidget(self.copy_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        action_layout.addWidget(close_button)
        layout.addLayout(action_layout)

        self.editor.textChanged.connect(self.mark_modified)

    def prepare_citation(self):
        saved_bibtex = str(self.paper.get("bibtex") or "").strip()
        if saved_bibtex:
            self.set_editor_text(saved_bibtex)
            self.source_label.setText(
                "Saved reviewed citation · your edits will not be overwritten."
            )
            return

        self.set_editor_text(generate_bibtex(self.paper))
        doi = self.paper.get("doi", "")
        if doi and not self.offline:
            self.source_label.setText(
                "Checking the DOI registry for deposited BibTeX…"
            )
            self.progress_bar.setVisible(True)
            task = BibtexTask(doi, self.config)
            task.signals.finished.connect(self.lookup_finished)
            task.signals.failed.connect(self.lookup_failed)
            self.thread_pool.start(task)
        elif doi:
            self.source_label.setText(
                "Draft generated locally · DOI lookup is unavailable offline."
            )
        else:
            self.source_label.setText(
                "Draft generated from available metadata · review before using."
            )

    def set_editor_text(self, value):
        self.editor.blockSignals(True)
        self.editor.setPlainText(value)
        self.editor.blockSignals(False)
        self.modified = False

    def mark_modified(self):
        self.modified = True

    def lookup_finished(self, bibtex):
        self.progress_bar.setVisible(False)
        if self.modified:
            self.source_label.setText(
                "DOI BibTeX was found, but your in-progress edits were kept."
            )
        else:
            self.set_editor_text(bibtex)
            self.source_label.setText(
                "Retrieved from DOI-registered metadata · review before using."
            )

    def lookup_failed(self, message):
        self.progress_bar.setVisible(False)
        self.source_label.setText(
            "DOI lookup was unavailable · showing a locally generated draft."
        )

    def copy_bibtex(self):
        bibtex = self.editor.toPlainText().strip()
        if not bibtex:
            return
        QApplication.clipboard().setText(bibtex)
        self.source_label.setText("BibTeX copied · verify it before publication.")

    def save_changes(self):
        bibtex = self.editor.toPlainText().strip()
        if not bibtex:
            return
        if self.save_callback(self.paper["id"], bibtex):
            self.modified = False
            self.source_label.setText("Reviewed BibTeX saved to this paper.")

    def open_google_scholar(self):
        title = self.paper.get("title", "")
        if title:
            QDesktopServices.openUrl(QUrl(google_scholar_url(title)))


class ComboPopupStyle(QProxyStyle):
    """Use a styled list popup instead of the platform's menu-like combo popup."""

    def styleHint(self, hint, option=None, widget=None, return_data=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        if hint == QStyle.StyleHint.SH_ComboBox_PopupFrameStyle:
            return int(QFrame.Shape.NoFrame)
        return super().styleHint(hint, option, widget, return_data)


class ModernComboBox(QComboBox):
    """Keep native combo interaction with a light control and soft list menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup_style = ComboPopupStyle("Fusion")
        self.popup_style.setParent(self)
        self.setStyle(self.popup_style)
        self.setView(QListView())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        arrow = os.path.join(os.path.dirname(__file__), "assets", "chevron-down.svg")
        self.setStyleSheet(
            "QComboBox { background: #FFFFFF; color: #242528; border: 1px solid #DCDEE5; "
            "border-radius: 8px; padding: 5px 30px 5px 10px; min-height: 20px; }"
            "QComboBox:hover { border-color: #B8B0CE; }"
            "QComboBox:focus { border-color: #927DCB; }"
            "QComboBox:disabled { background: #F3F3F5; color: #A4A6AC; }"
            "QComboBox::drop-down { border: 0; width: 24px; margin-right: 4px; }"
            'QComboBox::down-arrow { image: url("' + arrow + '"); width: 12px; height: 12px; }'
        )
        self.view().setStyleSheet(
            "QListView { background: transparent; color: #242528; border: 0; "
            "padding: 5px; outline: 0; font-size: 13px; font-weight: 400; }"
            "QListView::item { min-height: 24px; padding: 5px 10px; "
            "border: 0; border-radius: 5px; color: #242528; }"
            "QListView::item:hover { background: #F5F3FA; }"
            "QListView::item:selected { background: #EFEBFA; color: #574697; }"
        )
        popup = self.view().window()
        popup.setObjectName("comboPopup")
        popup.setFrameShape(QFrame.Shape.NoFrame)
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched.objectName() == "comboPopup" and event.type() == QEvent.Type.Paint:
            painter = QPainter(watched)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(watched.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QColor("#E0DDE8"))
            painter.drawRoundedRect(QRectF(watched.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
            return True
        return super().eventFilter(watched, event)


class ProjectCardDelegate(QStyledItemDelegate):
    """Paint selection backgrounds while the card widgets draw their labels."""

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        option.text = ""
        widget = option.widget
        widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, widget)


class ProjectListWidget(QListWidget):
    """Keep project labels fully wrapped beside their favorite controls."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for index in range(self.count()):
            item = self.item(index)
            card = self.itemWidget(item)
            if card is not None and card.objectName() == "projectCard":
                height = max(card.sizeHint().height(), card.heightForWidth(self.viewport().width()))
                item.setSizeHint(QSize(0, height))


class ReadingStatusCombo(ModernComboBox):
    """Keep the native status menu, with a centered label and visible chevron."""

    def content_geometry(self):
        """Center the visible text ink and chevron as one balanced group."""
        font = self.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        ink = QFontMetricsF(font).tightBoundingRect(self.currentText())
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        gap = 8
        arrow_width = 7.25
        group_width = ink.width() + gap + arrow_width
        left = rect.center().x() - group_width / 2
        label_position = QPointF(left - ink.left(), rect.center().y() - ink.center().y())
        arrow_center = QPointF(left + ink.width() + gap + arrow_width / 2, rect.center().y())
        return font, label_position, arrow_center

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background, foreground, border = READING_STATUS_COLORS.get(
            self.currentData(), READING_STATUS_COLORS["unread"]
        )
        if not self.isEnabled():
            background, foreground, border = "#F3F3F5", "#848691", "#DCDEE3"
        painter.setBrush(QColor(background))
        if self.hasFocus() or self.underMouse():
            painter.setPen(QPen(QColor(border), 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setPen(QColor(foreground))
        font, label_position, arrow_center = self.content_geometry()
        painter.setFont(font)
        painter.drawText(label_position, self.currentText())
        x = arrow_center.x()
        y = arrow_center.y()
        pen = QPen(QColor(foreground), 1.25)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5))
        painter.drawLine(QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5))


class ZoomablePdfView(QPdfView):
    """Add trackpad pinch and modified-wheel zoom without changing plain scroll."""

    customZoomChanged = Signal(float)

    def effective_zoom(self):
        if self.zoomMode() == QPdfView.ZoomMode.Custom:
            return self.zoomFactor()
        page = max(0, self.pageNavigator().currentPage())
        size = self.document().pagePointSize(page)
        margins = self.documentMargins()
        width = max(1, self.viewport().width() - margins.left() - margins.right())
        height = max(1, self.viewport().height() - margins.top() - margins.bottom())
        zoom = width / max(1, size.width())
        if self.zoomMode() == QPdfView.ZoomMode.FitInView:
            zoom = min(zoom, height / max(1, size.height()))
        return zoom

    def scale_zoom(self, multiplier):
        if self.document() is None or self.document().pageCount() <= 0:
            return
        if multiplier <= 0:
            return
        navigator = self.pageNavigator()
        page = max(0, navigator.currentPage())
        location = navigator.currentLocation()
        zoom = round(max(0.25, min(4.0, self.effective_zoom() * multiplier)), 4)
        self.setZoomMode(QPdfView.ZoomMode.Custom)
        self.setZoomFactor(zoom)
        navigator.jump(page, location)
        self.customZoomChanged.emit(zoom)

    def zoom_in(self):
        self.scale_zoom(1.2)

    def zoom_out(self):
        self.scale_zoom(1 / 1.2)

    def wheelEvent(self, event):
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        if event.modifiers() & modifiers:
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.pixelDelta().y()
            self.scale_zoom(1.2 ** (max(-240, min(240, delta)) / 120))
            event.accept()
            return
        super().wheelEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.scale_zoom(1 + event.value())
                event.accept()
                return True
        return super().viewportEvent(event)


class PaperCardDelegate(QStyledItemDelegate):
    """Measure and draw wrapped paper cards with separate status pills."""

    def card_document(self, option, index, width):
        paper = index.data(Qt.ItemDataRole.UserRole + 1) or {}
        title = latex_to_plain_text(paper.get("title", ""))
        title_html = html.escape(title)
        if paper.get("favorite"):
            title_html = '<span style="color:#D9A000;">★</span> ' + title_html
        details = []
        if paper.get("authors"):
            details.append(first_author_label(paper["authors"]))
        if paper.get("year"):
            details.append(str(paper["year"]))
        color = "#202124"
        if option.state & QStyle.StateFlag.State_Selected:
            color = "#443381"
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.parent().font())
        document.setHtml(
            '<div style="color:' + color + '; font-weight:600;">'
            + title_html + '</div>'
            + '<div style="color:#656871;">'
            + html.escape(" · ".join(details)) + '</div>'
        )
        document.setTextWidth(max(40, width - 28))
        return document

    def sizeHint(self, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        width = max(68, self.parent().viewport().width() - 4)
        document = self.card_document(option, index, width - 4)
        return QSize(width, math.ceil(document.size().height()) + 62)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -7)
        background = "#FFFFFF"
        border = "#E0E1E5"
        if option.state & QStyle.StateFlag.State_Selected:
            background = "#EEEAFF"
            border = "#A99BE0"
        elif option.state & QStyle.StateFlag.State_MouseOver:
            border = "#BDBFC5"
        painter.setBrush(QColor(background))
        painter.setPen(QColor(border))
        painter.drawRoundedRect(rect, 8, 8)
        document = self.card_document(option, index, rect.width())
        painter.save()
        painter.translate(rect.left() + 14, rect.top() + 12)
        document.drawContents(painter)
        painter.restore()
        paper = index.data(Qt.ItemDataRole.UserRole + 1) or {}
        status = paper.get("reading_status") or "unread"
        background, foreground, border = READING_STATUS_COLORS.get(
            status, READING_STATUS_COLORS["unread"]
        )
        label = status.capitalize()
        font = self.parent().font()
        font.setBold(True)
        painter.setFont(font)
        pill = QRectF(
            rect.left() + 14, rect.top() + 20 + document.size().height(),
            painter.fontMetrics().horizontalAdvance(label) + 24, 24,
        )
        painter.setBrush(QColor(background))
        painter.setPen(QColor(border))
        painter.drawRoundedRect(pill, 12, 12)
        painter.setPen(QColor(foreground))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


class PdfDropListWidget(QListWidget):
    """Accept local PDF drops across the current project's paper list."""

    pdfsDropped = Signal(object)
    dragActiveChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_enabled = False
        self.drag_active = False
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.doItemsLayout()

    def set_drop_enabled(self, enabled):
        self.drop_enabled = bool(enabled)
        if not self.drop_enabled:
            self.set_drag_active(False)

    def set_drag_active(self, active):
        active = bool(active)
        if active == self.drag_active:
            return
        self.drag_active = active
        self.setProperty("dragActive", bool(active))
        if active:
            self.setStyleSheet(
                "QListWidget#paperList { background: #f4f1ff; "
                "border: 2px dashed #806cc8; border-radius: 9px; }"
            )
        else:
            self.setStyleSheet("")
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()
        self.dragActiveChanged.emit(active)

    def dragEnterEvent(self, event):
        paths = pdf_paths_from_mime_data(event.mimeData(), False)
        if self.drop_enabled and paths:
            self.set_drag_active(True)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        paths = pdf_paths_from_mime_data(event.mimeData(), False)
        if self.drop_enabled and paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self.set_drag_active(False)
            event.ignore()

    def dragLeaveEvent(self, event):
        self.set_drag_active(False)
        event.accept()

    def dropEvent(self, event):
        paths = pdf_paths_from_mime_data(event.mimeData())
        self.set_drag_active(False)
        if not self.drop_enabled or not paths:
            event.ignore()
            return
        self.pdfsDropped.emit(paths)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class PdfDropPanel(QWidget):
    """Accept local PDF drops on the surrounding Papers pane."""

    pdfsDropped = Signal(object)
    dragActiveChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_enabled = False
        self.drag_active = False
        self.setAcceptDrops(True)

    def set_drop_enabled(self, enabled):
        self.drop_enabled = bool(enabled)
        if not self.drop_enabled:
            self.set_drag_active(False)

    def set_drag_active(self, active):
        active = bool(active)
        if active == self.drag_active:
            return
        self.drag_active = active
        self.setProperty("dragActive", bool(active))
        if active:
            self.setStyleSheet(
                "QWidget#paperPanel { background: #f4f1ff; "
                "border: 2px dashed #806cc8; }"
            )
        else:
            self.setStyleSheet("")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        self.dragActiveChanged.emit(active)

    def dragEnterEvent(self, event):
        paths = pdf_paths_from_mime_data(event.mimeData(), False)
        if self.drop_enabled and paths:
            self.set_drag_active(True)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        paths = pdf_paths_from_mime_data(event.mimeData(), False)
        if self.drop_enabled and paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self.set_drag_active(False)
            event.ignore()

    def dragLeaveEvent(self, event):
        self.set_drag_active(False)
        event.accept()

    def dropEvent(self, event):
        paths = pdf_paths_from_mime_data(event.mimeData())
        self.set_drag_active(False)
        if not self.drop_enabled or not paths:
            event.ignore()
            return
        self.pdfsDropped.emit(paths)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class AddPaperDialog(QDialog):
    """Offer the two clear ways to add a paper to one project."""

    def __init__(self, parent, project, online_enabled):
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Add paper")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        heading = QLabel("Add a paper to " + project["name"])
        heading.setObjectName("homeSectionHeading")
        layout.addWidget(heading)
        description = QLabel(
            "Choose a PDF from your computer, or find a paper online by "
            "title, DOI, arXiv URL, or project-page link."
        )
        description.setWordWrap(True)
        description.setObjectName("mutedLabel")
        layout.addWidget(description)

        self.upload_button = QPushButton("Add PDF from computer…")
        self.upload_button.setMinimumHeight(48)
        self.upload_button.clicked.connect(self.choose_upload)
        layout.addWidget(self.upload_button)

        self.online_button = QPushButton("Search or paste a link…")
        self.online_button.setMinimumHeight(48)
        self.online_button.setEnabled(online_enabled)
        self.online_button.clicked.connect(self.choose_online)
        layout.addWidget(self.online_button)

        if not online_enabled:
            offline_label = QLabel(
                "Online search is unavailable right now. You can still add "
                "a local PDF."
            )
            offline_label.setObjectName("mutedLabel")
            offline_label.setWordWrap(True)
            layout.addWidget(offline_label)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(cancel_button, alignment=Qt.AlignmentFlag.AlignRight)

    def choose_upload(self):
        self.choice = "upload"
        self.accept()

    def choose_online(self):
        self.choice = "online"
        self.accept()


class ProjectSelectionDialog(QDialog):
    """Choose one or more destination projects with visible locked entries."""

    def __init__(
        self,
        parent,
        projects,
        selected_ids=None,
        locked_ids=None,
        exclusive_ids=None,
        require_unlocked_selection=True,
        title="Choose projects",
        prompt="Choose where independent copies should be created.",
    ):
        super().__init__(parent)
        if selected_ids is None:
            selected_ids = []
        if locked_ids is None:
            locked_ids = []
        if exclusive_ids is None:
            exclusive_ids = []
        self.locked_ids = set(locked_ids)
        self.exclusive_ids = set(exclusive_ids)
        self.changing_checks = False
        self.require_unlocked_selection = require_unlocked_selection
        self.setWindowTitle(title)
        self.resize(430, 360)

        layout = QVBoxLayout(self)
        prompt_label = QLabel(prompt)
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        self.project_list = QListWidget()
        for project in projects:
            item = QListWidgetItem(project["name"])
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if project["id"] in selected_ids:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            if project["id"] in self.locked_ids:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.project_list.addItem(item)
        layout.addWidget(self.project_list)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        self.project_list.itemChanged.connect(self.project_check_changed)
        self.update_accept_button()

    def selected_project_ids(self):
        project_ids = []
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                project_ids.append(item.data(Qt.ItemDataRole.UserRole))
        return project_ids

    def update_accept_button(self, item=None):
        enabled = True
        if self.require_unlocked_selection:
            enabled = False
            for project_id in self.selected_project_ids():
                if project_id not in self.locked_ids:
                    enabled = True
                    break
        button = self.button_box.button(
            QDialogButtonBox.StandardButton.Ok
        )
        button.setEnabled(enabled)

    def project_check_changed(self, changed_item):
        if self.changing_checks:
            return
        if changed_item.checkState() != Qt.CheckState.Checked:
            self.update_accept_button()
            return

        changed_id = changed_item.data(Qt.ItemDataRole.UserRole)
        self.changing_checks = True
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            project_id = item.data(Qt.ItemDataRole.UserRole)
            if changed_id in self.exclusive_ids:
                if project_id != changed_id:
                    item.setCheckState(Qt.CheckState.Unchecked)
            elif project_id in self.exclusive_ids:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.changing_checks = False
        self.update_accept_button()


class DiscoveryDialog(QDialog):
    """Search online sources and add a confirmed result to any project."""

    def __init__(
        self,
        parent,
        projects,
        project_id,
        paper_id,
        config,
        apply_callback,
        add_callback,
        download_callback,
        create_project_callback,
        context_callback,
        save_context_callback,
        pdf_target=False,
        initial_query="",
    ):
        super().__init__(parent)
        self.results = []
        self.projects = list(projects)
        self.additional_project_ids = []
        self.paper_id = paper_id
        self.config = config
        self.apply_callback = apply_callback
        self.add_callback = add_callback
        self.download_callback = download_callback
        self.create_project_callback = create_project_callback
        self.context_callback = context_callback
        self.save_context_callback = save_context_callback
        self.pdf_target = pdf_target
        self.initial_query = initial_query
        self.thread_pool = QThreadPool.globalInstance()

        if self.pdf_target:
            self.setWindowTitle("Find an online PDF")
        else:
            self.setWindowTitle("Add paper online")
        self.resize(860, 560)
        self.build_ui(projects, project_id)

    def build_ui(self, projects, project_id):
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText(
            "Search by title, acronym, DOI, arXiv, or project-page link"
        )
        self.query_input.setClearButtonEnabled(True)
        self.query_input.setText(self.initial_query)
        self.query_input.returnPressed.connect(self.start_search)
        search_layout.addWidget(self.query_input)
        self.search_button = QPushButton("Find paper")
        self.search_button.clicked.connect(self.start_search)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        destination_layout = QHBoxLayout()
        if self.pdf_target:
            destination_layout.addWidget(QLabel("Selected paper is in"))
        else:
            destination_layout.addWidget(QLabel("Add to"))
        self.project_combo = ModernComboBox()
        selected_index = 0
        for index, project in enumerate(projects):
            project_name = project["name"]
            if project.get("kind") == "scrapbook":
                project_name += " · temporary"
            self.project_combo.addItem(project_name, project["id"])
            if project["id"] == project_id:
                selected_index = index
        self.project_combo.setCurrentIndex(selected_index)
        self.project_combo.setEnabled(not self.pdf_target)
        destination_layout.addWidget(self.project_combo)
        self.more_projects_button = QPushButton("Add to more projects…")
        self.more_projects_button.clicked.connect(self.choose_more_projects)
        self.more_projects_button.setVisible(not self.pdf_target)
        destination_layout.addWidget(self.more_projects_button)
        self.new_project_button = QPushButton("+ New project")
        self.new_project_button.clicked.connect(self.create_project)
        destination_layout.addWidget(self.new_project_button)
        self.new_project_button.setVisible(not self.pdf_target)
        destination_layout.addStretch()
        providers = QLabel("Crossref · arXiv · OpenAlex")
        providers.setStyleSheet("color: #666666;")
        destination_layout.addWidget(providers)
        layout.addLayout(destination_layout)

        context_layout = QHBoxLayout()
        context_layout.addWidget(QLabel("Prioritize"))
        self.context_input = QLineEdit()
        self.context_input.setPlaceholderText(
            "Optional project context, such as robotics or climate"
        )
        self.context_input.setToolTip(
            "Editable preference terms inferred from the destination project. "
            "They rerank results but do not filter them out. Edited terms are "
            "saved to the project when you search."
        )
        context_layout.addWidget(self.context_input)
        layout.addLayout(context_layout)
        self.project_combo.currentIndexChanged.connect(self.project_changed)
        self.project_changed()

        self.status_label = QLabel(
            "Search by title or paste an arXiv, DOI, or project-page link."
        )
        self.status_label.setStyleSheet("color: #666666;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.result_list = QListWidget()
        self.result_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.result_list.setWordWrap(True)
        self.result_list.setSpacing(4)
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setStyleSheet(
            "QListWidget { outline: 0; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #e0e1e5; "
            "background: #ffffff; color: #202124; }"
            "QListWidget::item:alternate { background: #f7f7f8; }"
            "QListWidget::item:selected { background: #eeeaff; "
            "color: #443381; }"
        )
        self.result_list.currentItemChanged.connect(self.select_result)
        self.result_list.itemDoubleClicked.connect(self.perform_primary_action)
        layout.addWidget(self.result_list)

        self.result_title_label = QLabel("Select a result")
        self.result_title_label.setWordWrap(True)
        self.result_title_label.setTextFormat(Qt.TextFormat.RichText)
        self.result_title_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.result_title_label)
        self.result_reason_label = QLabel()
        self.result_reason_label.setWordWrap(True)
        self.result_reason_label.setStyleSheet("color: #666666;")
        layout.addWidget(self.result_reason_label)

        self.result_abstract = QPlainTextEdit()
        self.result_abstract.setReadOnly(True)
        self.result_abstract.setPlaceholderText(
            "Select a result to inspect its abstract."
        )
        self.result_abstract.setMaximumHeight(130)
        layout.addWidget(self.result_abstract)

        action_layout = QHBoxLayout()
        action_layout.addStretch()
        self.primary_button = QPushButton("Save citation")
        self.primary_button.clicked.connect(self.perform_primary_action)
        action_layout.addWidget(self.primary_button)
        self.more_button = QPushButton("More")
        self.more_menu = QMenu(self.more_button)
        self.save_action = self.more_menu.addAction("Save citation")
        self.save_action.triggered.connect(self.add_selected)
        self.save_action.setVisible(not self.pdf_target)
        self.apply_action = self.more_menu.addAction(
            "Apply metadata to selected library paper"
        )
        self.apply_action.triggered.connect(self.apply_selected)
        self.more_menu.addSeparator()
        self.open_source_action = self.more_menu.addAction("Open source")
        self.open_source_action.triggered.connect(self.open_source)
        self.open_project_action = self.more_menu.addAction("Open project page")
        self.open_project_action.triggered.connect(self.open_project_page)
        self.scholar_action = self.more_menu.addAction("Google Scholar")
        self.scholar_action.triggered.connect(self.open_google_scholar)
        self.more_button.setMenu(self.more_menu)
        action_layout.addWidget(self.more_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        action_layout.addWidget(close_button)
        layout.addLayout(action_layout)

        self.primary_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.primary_shortcut.activated.connect(self.perform_primary_action)
        self.set_actions_enabled(False)
        self.query_input.setFocus()
        if self.initial_query:
            QTimer.singleShot(0, self.start_search)

    def start_search(self):
        title = self.query_input.text().strip()
        if not title:
            self.status_label.setText(
                "Enter a paper title, DOI, arXiv ID, or link."
            )
            return

        context = self.context_input.text().strip()
        project_id = self.project_combo.currentData()
        if self.save_context_callback and project_id is not None:
            self.save_context_callback(project_id, context)
        self.search_button.setEnabled(False)
        self.query_input.setEnabled(False)
        self.project_combo.setEnabled(False)
        self.more_projects_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.context_input.setEnabled(False)
        self.result_list.clear()
        self.result_abstract.clear()
        self.set_actions_enabled(False)
        self.progress_bar.setVisible(True)
        parsed_query = QUrl(title)
        if (
            parsed_query.scheme() in ("http", "https")
            and "arxiv.org" not in title
            and "doi.org" not in title
        ):
            self.status_label.setText(
                "Inspecting the project page and scholarly sources…"
            )
        else:
            self.status_label.setText(
                "Searching Crossref, arXiv, and OpenAlex…"
            )

        task = SearchTask(title, context, self.config, False)
        task.signals.finished.connect(self.search_finished)
        task.signals.failed.connect(self.search_failed)
        self.thread_pool.start(task)

    def search_finished(self, results):
        self.search_button.setEnabled(True)
        self.query_input.setEnabled(True)
        self.project_combo.setEnabled(not self.pdf_target)
        self.update_more_projects_button()
        self.new_project_button.setEnabled(True)
        self.context_input.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.results = results

        if not results:
            self.status_label.setText("No matching papers were returned.")
            return

        self.status_label.setText(
            str(len(results)) + " result(s) · select one to review"
        )
        for result in results:
            label = latex_to_plain_text(
                result.get("title", "Untitled paper")
            )
            details = []
            if result.get("authors"):
                details.append(first_author_label(result["authors"]))
            if result.get("year"):
                details.append(str(result["year"]))
            if result.get("venue"):
                details.append(result["venue"])
            elif result.get("doi"):
                details.append("Published record")
            elif result.get("source") == "arXiv":
                details.append("Preprint")
            if result.get("source"):
                details.append(result["source"])
            citation_count = result.get("citation_count", 0) or 0
            if citation_count == 1:
                details.append("1 citation")
            else:
                details.append(str(citation_count) + " citations")
            if self.context_input.text().strip():
                match_percent = round(result.get("context_score", 0) * 100)
                details.append(str(match_percent) + "% project match")
            if result.get("pdf_url"):
                details.append("Open PDF")
            else:
                details.append("Metadata only")
            label += "\n" + " · ".join(details)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.result_list.addItem(item)
        self.result_list.setCurrentRow(0)
        self.result_list.setFocus()

    def search_failed(self, message):
        self.search_button.setEnabled(True)
        self.query_input.setEnabled(True)
        self.project_combo.setEnabled(not self.pdf_target)
        self.update_more_projects_button()
        self.new_project_button.setEnabled(True)
        self.context_input.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Online search failed: " + message)

    def project_changed(self, index=None):
        project_id = self.project_combo.currentData()
        project = self.primary_project()
        if project and project.get("kind") == "scrapbook":
            self.additional_project_ids = []
        if project_id in self.additional_project_ids:
            self.additional_project_ids.remove(project_id)
        self.update_more_projects_button()
        context = ""
        if self.context_callback:
            context = self.context_callback(project_id)
        self.context_input.setText(context)

    def create_project(self):
        name, accepted = QInputDialog.getText(
            self,
            "New project",
            "Project name:",
        )
        if not accepted or not name.strip():
            return
        project = self.create_project_callback(name)
        if not project:
            return
        self.project_combo.addItem(project["name"], project["id"])
        self.projects.append(project)
        self.project_combo.setCurrentIndex(self.project_combo.count() - 1)
        self.status_label.setText("Created project " + project["name"] + ".")

    def destination_project_ids(self):
        project_ids = []
        primary_project_id = self.project_combo.currentData()
        if primary_project_id is not None:
            project_ids.append(primary_project_id)
        primary_project = self.primary_project()
        if primary_project and primary_project.get("kind") == "scrapbook":
            return project_ids
        for project_id in self.additional_project_ids:
            if project_id not in project_ids:
                project_ids.append(project_id)
        return project_ids

    def choose_more_projects(self):
        primary_project_id = self.project_combo.currentData()
        selected_ids = self.destination_project_ids()
        standard_projects = []
        for project in self.projects:
            if project.get("kind") != "scrapbook":
                standard_projects.append(project)
        dialog = ProjectSelectionDialog(
            self,
            standard_projects,
            selected_ids=selected_ids,
            locked_ids=[primary_project_id],
            require_unlocked_selection=False,
            title="Add to more projects",
            prompt=(
                "The primary project is locked because it supplies search "
                "preferences. Select any additional projects that should "
                "receive independent copies."
            ),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_ids = dialog.selected_project_ids()
        self.additional_project_ids = []
        for project_id in selected_ids:
            if project_id != primary_project_id:
                self.additional_project_ids.append(project_id)
        self.update_more_projects_button()
        self.set_actions_enabled(self.selected_result() is not None)

    def update_more_projects_button(self):
        primary_project = self.primary_project()
        if primary_project and primary_project.get("kind") == "scrapbook":
            self.more_projects_button.setText("ScrapBook is exclusive")
            self.more_projects_button.setEnabled(False)
            return
        self.more_projects_button.setEnabled(not self.pdf_target)
        count = len(self.additional_project_ids)
        if count == 0:
            self.more_projects_button.setText("Add to more projects…")
        elif count == 1:
            self.more_projects_button.setText("+ 1 more project")
        else:
            self.more_projects_button.setText("+ " + str(count) + " more projects")

    def primary_project(self):
        project_id = self.project_combo.currentData()
        for project in self.projects:
            if project["id"] == project_id:
                return project
        return None

    def selected_result(self):
        item = self.result_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def select_result(self, item, previous_item=None):
        result = self.selected_result()
        if not result:
            self.result_abstract.setPlainText("")
            self.result_title_label.setText("Select a result")
            self.result_reason_label.setText("")
            self.primary_button.setText("Save citation")
            self.set_actions_enabled(False)
            return
        self.result_title_label.setText(
            latex_to_html(result.get("title", "Untitled paper"))
        )
        reasons = []
        query_percent = round(result.get("query_score", 0) * 100)
        reasons.append(str(query_percent) + "% query match")
        if self.context_input.text().strip():
            context_percent = round(result.get("context_score", 0) * 100)
            reasons.append(str(context_percent) + "% project match")
        citation_count = result.get("citation_count", 0) or 0
        if citation_count == 1:
            reasons.append("1 citation")
        else:
            reasons.append(str(citation_count) + " citations")
        self.result_reason_label.setText(" · ".join(reasons))
        self.result_abstract.setPlainText(
            latex_to_plain_text(result.get("abstract", ""))
        )
        if result.get("pdf_url"):
            if self.pdf_target:
                self.primary_button.setText("Download PDF")
            else:
                self.primary_button.setText("Download + add")
        else:
            if self.pdf_target:
                self.primary_button.setText("No PDF available")
            else:
                self.primary_button.setText("Save citation")
        self.set_actions_enabled(True)

    def set_actions_enabled(self, enabled):
        project_id = self.project_combo.currentData()
        self.apply_action.setEnabled(enabled and self.paper_id is not None)
        self.save_action.setEnabled(enabled and project_id is not None)
        result = self.selected_result()
        source_available = False
        project_page_available = False
        title_available = False
        if result:
            if result.get("external_url") or result.get("pdf_url"):
                source_available = True
            if result.get("project_url"):
                project_page_available = True
            if result.get("title"):
                title_available = True
        self.open_source_action.setEnabled(enabled and source_available)
        self.open_project_action.setEnabled(
            enabled and project_page_available
        )
        self.scholar_action.setEnabled(enabled and title_available)
        pdf_available = False
        if result and result.get("pdf_url"):
            pdf_available = True
        primary_available = enabled and project_id is not None
        if self.pdf_target:
            primary_available = (
                enabled
                and pdf_available
                and self.download_callback is not None
            )
        elif pdf_available:
            primary_available = (
                primary_available and self.download_callback is not None
            )
        else:
            primary_available = primary_available and self.add_callback is not None
        self.primary_button.setEnabled(primary_available)
        self.more_button.setEnabled(
            enabled and (source_available or title_available or project_id is not None)
        )

    def perform_primary_action(self, item=None):
        result = self.selected_result()
        if not result:
            return
        if self.pdf_target:
            if result.get("pdf_url"):
                self.download_selected()
        elif result.get("pdf_url"):
            self.download_selected()
        else:
            self.add_selected()

    def apply_selected(self):
        result = self.selected_result()
        if result and self.apply_callback:
            if self.apply_callback(result):
                self.accept()

    def add_selected(self):
        result = self.selected_result()
        if result and self.add_callback:
            project_ids = self.destination_project_ids()
            if self.add_callback(result, project_ids):
                count = len(project_ids)
                self.status_label.setText(
                    "Citation copied to " + str(count) + " project(s)"
                )

    def download_selected(self):
        result = self.selected_result()
        if result and self.download_callback:
            project_ids = self.destination_project_ids()
            if self.download_callback(result, project_ids):
                self.accept()

    def open_source(self):
        result = self.selected_result()
        if not result:
            return
        url = result.get("external_url", "")
        if not url:
            url = result.get("pdf_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def open_google_scholar(self):
        result = self.selected_result()
        if not result:
            return
        title = result.get("title", "")
        if title:
            QDesktopServices.openUrl(QUrl(google_scholar_url(title)))

    def open_project_page(self):
        result = self.selected_result()
        if not result:
            return
        url = result.get("project_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))


class PdfCommentsPanel(QWidget):
    """Create and navigate app-side comments anchored to PDF pages."""

    def __init__(self, library, paper_id, navigator, parent=None):
        super().__init__(parent)
        self.library = library
        self.paper_id = paper_id
        self.navigator = navigator
        self.setObjectName("pdfCommentsPanel")
        self.setMinimumWidth(240)
        self.setMaximumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        heading = QLabel("PDF comments")
        heading.setObjectName("paneHeading")
        layout.addWidget(heading)
        self.page_label = QLabel("Commenting on page 1")
        self.page_label.setObjectName("mutedLabel")
        layout.addWidget(self.page_label)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "Write a comment about the page you are viewing…"
        )
        self.editor.setMaximumHeight(100)
        layout.addWidget(self.editor)
        self.add_button = QPushButton("Add comment")
        self.add_button.clicked.connect(self.add_comment)
        layout.addWidget(self.add_button)

        self.comment_list = QListWidget()
        self.comment_list.setObjectName("pdfCommentList")
        self.comment_list.setWordWrap(True)
        self.comment_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.comment_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.comment_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.comment_list.itemClicked.connect(self.jump_to_comment)
        self.comment_list.currentItemChanged.connect(
            self.comment_selection_changed
        )
        layout.addWidget(self.comment_list, 1)

        self.delete_button = QPushButton("Delete selected comment")
        self.delete_button.setObjectName("deleteButton")
        self.delete_button.clicked.connect(self.delete_selected_comment)
        layout.addWidget(self.delete_button)
        note = QLabel(
            "Comments stay in Corpus Cabinet; the original PDF is unchanged."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.navigator.currentPageChanged.connect(self.current_page_changed)
        self.refresh()

    def set_context(self, library, paper_id):
        self.library = library
        self.paper_id = paper_id
        self.editor.clear()
        self.refresh()

    def current_page_number(self):
        try:
            current_page = self.navigator.currentPage()
        except RuntimeError:
            return 1
        if current_page < 0:
            current_page = 0
        return current_page + 1

    def current_page_changed(self, page=None):
        self.page_label.setText(
            "Commenting on page " + str(self.current_page_number())
        )

    def refresh(self):
        self.comment_list.clear()
        enabled = self.library is not None and self.paper_id is not None
        self.editor.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self.delete_button.setEnabled(False)
        self.current_page_changed()
        if not enabled:
            return
        for comment in self.library.list_paper_comments(self.paper_id):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, comment["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, comment["page_number"])
            item.setToolTip(comment["body"])
            approximate_lines = (len(comment["body"]) + 27) // 28
            if approximate_lines < 1:
                approximate_lines = 1
            if approximate_lines > 5:
                approximate_lines = 5
            item.setSizeHint(QSize(0, 42 + approximate_lines * 20))
            self.comment_list.addItem(item)
            self.comment_list.setItemWidget(
                item,
                self.create_comment_card(comment),
            )

    def create_comment_card(self, comment):
        """Build a readable, wrapping card for one PDF comment."""
        card = QWidget()
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        page_label = QLabel("Page " + str(comment["page_number"]))
        page_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(page_label)
        body_label = QLabel(comment["body"])
        body_label.setWordWrap(True)
        layout.addWidget(body_label)
        return card

    def comment_selection_changed(self, current, previous=None):
        self.delete_button.setEnabled(current is not None)

    def add_comment(self):
        if self.library is None or self.paper_id is None:
            return
        body = self.editor.toPlainText().strip()
        if not body:
            return
        try:
            self.library.create_paper_comment(
                self.paper_id,
                self.current_page_number(),
                body,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Cannot add comment", str(error))
            return
        self.editor.clear()
        self.refresh()

    def delete_selected_comment(self):
        item = self.comment_list.currentItem()
        if item is None or self.library is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete comment",
            "Delete this PDF comment?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_paper_comment(
            item.data(Qt.ItemDataRole.UserRole)
        )
        self.refresh()

    def jump_to_comment(self, item):
        page_number = item.data(Qt.ItemDataRole.UserRole + 1)
        if page_number is None:
            return
        self.navigator.jump(int(page_number) - 1, QPointF())


class FullscreenPdfDialog(QDialog):
    """Distraction-free multi-page PDF reader with explicit exit controls."""

    def __init__(
        self,
        parent,
        file_path,
        title,
        initial_page=0,
        library=None,
        paper_id=None,
        initial_zoom="fit_width",
    ):
        super().__init__(parent)
        self.library = library
        self.paper_id = paper_id
        self.loading = True
        self.setWindowTitle(title)
        self.document = QPdfDocument(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        toolbar_layout = QHBoxLayout()
        reader_title = QLabel(latex_to_plain_text(title))
        reader_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        reader_title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        toolbar_layout.addWidget(reader_title)
        toolbar_layout.addStretch()
        self.previous_button = QPushButton("Previous")
        self.previous_button.clicked.connect(self.go_to_previous_page)
        toolbar_layout.addWidget(self.previous_button)
        self.page_label = QLabel("Page - of -")
        self.page_label.setObjectName("mutedLabel")
        toolbar_layout.addWidget(self.page_label)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.go_to_next_page)
        toolbar_layout.addWidget(self.next_button)
        self.comments_button = QPushButton("Comments")
        self.comments_button.setCheckable(True)
        self.comments_button.setChecked(True)
        toolbar_layout.addWidget(self.comments_button)
        toolbar_layout.addWidget(QLabel("Zoom"))
        self.zoom_combo = ModernComboBox()
        self.zoom_combo.addItem("Fit width", "fit_width")
        self.zoom_combo.addItem("Fit page", "fit_page")
        self.zoom_combo.addItem("100%", 1.0)
        self.zoom_combo.addItem("125%", 1.25)
        self.zoom_combo.addItem("150%", 1.5)
        self.zoom_combo.currentIndexChanged.connect(self.change_zoom)
        toolbar_layout.addWidget(self.zoom_combo)
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setToolTip("Zoom out")
        toolbar_layout.addWidget(self.zoom_out_button)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setToolTip("Zoom in · trackpad pinch or Ctrl/Cmd + scroll also works")
        toolbar_layout.addWidget(self.zoom_in_button)
        self.exit_button = QPushButton("Exit full screen")
        self.exit_button.clicked.connect(self.accept)
        toolbar_layout.addWidget(self.exit_button)
        layout.addLayout(toolbar_layout)

        self.view = ZoomablePdfView()
        self.zoom_out_button.clicked.connect(self.view.zoom_out)
        self.zoom_in_button.clicked.connect(self.view.zoom_in)
        self.view.customZoomChanged.connect(self.sync_custom_zoom)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        reader_splitter = QSplitter(Qt.Orientation.Horizontal)
        reader_splitter.addWidget(self.view)
        self.comments_panel = PdfCommentsPanel(
            library,
            paper_id,
            self.view.pageNavigator(),
        )
        reader_splitter.addWidget(self.comments_panel)
        reader_splitter.setStretchFactor(0, 1)
        reader_splitter.setSizes([900, 300])
        self.comments_button.toggled.connect(
            self.comments_panel.setVisible
        )
        self.comments_panel.setVisible(
            library is not None and paper_id is not None
        )
        self.comments_button.setVisible(
            library is not None and paper_id is not None
        )
        layout.addWidget(reader_splitter, 1)

        self.document.pageCountChanged.connect(self.update_page_controls)
        self.view.pageNavigator().currentPageChanged.connect(
            self.update_page_controls
        )
        self.view.pageNavigator().currentPageChanged.connect(self.save_reader_state)
        self.escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.escape_shortcut.activated.connect(self.reject)

        self.document.load(file_path)
        restore_zoom_choice(self.zoom_combo, initial_zoom)
        self.change_zoom()
        if initial_page > 0 and initial_page < self.document.pageCount():
            self.view.pageNavigator().jump(initial_page, QPointF())
        self.update_page_controls()
        self.loading = False
        self.save_reader_state()

    def update_page_controls(self, value=None):
        """Update the full-screen reader's page counter and buttons."""
        page_count = self.document.pageCount()
        current_page = self.view.pageNavigator().currentPage()
        if page_count <= 0:
            self.page_label.setText("Page - of -")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.zoom_combo.setEnabled(False)
            self.zoom_out_button.setEnabled(False)
            self.zoom_in_button.setEnabled(False)
            return
        if current_page < 0:
            current_page = 0
        self.page_label.setText(
            "Page " + str(current_page + 1) + " of " + str(page_count)
        )
        self.previous_button.setEnabled(current_page > 0)
        self.next_button.setEnabled(current_page < page_count - 1)
        self.zoom_combo.setEnabled(True)
        self.zoom_out_button.setEnabled(True)
        self.zoom_in_button.setEnabled(True)

    def go_to_previous_page(self):
        """Move the full-screen reader to the preceding page."""
        navigator = self.view.pageNavigator()
        target_page = navigator.currentPage() - 1
        if target_page >= 0:
            navigator.jump(target_page, QPointF())

    def go_to_next_page(self):
        """Move the full-screen reader to the following page."""
        navigator = self.view.pageNavigator()
        target_page = navigator.currentPage() + 1
        if target_page < self.document.pageCount():
            navigator.jump(target_page, QPointF())

    def sync_custom_zoom(self, zoom):
        self.zoom_combo.blockSignals(True)
        restore_zoom_choice(self.zoom_combo, zoom)
        self.zoom_combo.blockSignals(False)
        self.save_reader_state()

    def change_zoom(self, index=None):
        """Apply the selected fit or fixed zoom mode."""
        zoom = self.zoom_combo.currentData()
        if zoom == "fit_width":
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        elif zoom == "fit_page":
            self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        elif zoom is not None:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.view.setZoomFactor(float(zoom))
        self.save_reader_state()

    def save_reader_state(self, page=None):
        if self.loading or self.library is None or self.paper_id is None:
            return
        if self.document.pageCount() <= 0:
            return
        try:
            self.library.save_reader_state(
                self.paper_id,
                self.current_page(),
                self.zoom_combo.currentData(),
            )
        except Exception:
            self.page_label.setToolTip("Reading position could not be saved.")

    def done(self, result):
        self.save_reader_state()
        super().done(result)

    def current_page(self):
        """Return the page visible when the reader closes."""
        return self.view.pageNavigator().currentPage()


class MainWindow(QMainWindow):
    """Main three-panel window for the desktop MVP."""

    def __init__(self, workspace_manager):
        super().__init__()
        self.workspace_manager = workspace_manager
        self.library = Library(self.workspace_manager.current())
        self.search_config = {
            "crossref_mailto": os.getenv("CROSSREF_MAILTO", ""),
            "search_result_limit": 10,
            "project_page_max_bytes": 2 * 1024 * 1024,
            "project_page_max_redirects": 5,
        }
        self.user_offline_mode = self.workspace_manager.is_offline_mode()
        self.network_available = True
        self.network_information = None
        self.online_controls_busy = False
        self.dyslexic_font_enabled = (
            self.workspace_manager.is_dyslexic_font_enabled()
        )
        self.opendyslexic_family = load_opendyslexic_font()
        self.initialize_network_monitor()
        self.offline_mode = (
            self.user_offline_mode or not self.network_available
        )
        self.thread_pool = QThreadPool.globalInstance()
        self.current_project_id = None
        self.current_paper_id = None
        self.pdf_metadata_attempted = set()
        self.notes_paper_id = None
        self.notes_dirty = False
        self.notes_save_timer = QTimer(self)
        self.notes_save_timer.setSingleShot(True)
        self.notes_save_timer.setInterval(600)
        self.notes_save_timer.timeout.connect(self.save_pending_notes)
        self.reader_paper_id = None
        self.loading_pdf = False
        self.reader_dirty = False
        self.reader_save_timer = QTimer(self)
        self.reader_save_timer.setSingleShot(True)
        self.reader_save_timer.setInterval(500)
        self.reader_save_timer.timeout.connect(self.save_pending_reader_state)
        self.pdf_document = QPdfDocument(self)

        self.setWindowTitle("Corpus Cabinet")
        self.resize(1280, 800)
        self.build_ui()
        self.update_network_status()
        self.update_search_controls()
        self.refresh_library()

    def build_ui(self):
        central = QWidget()
        central.setObjectName("appShell")
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("appHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        title = QLabel("Corpus Cabinet")
        title.setObjectName("appTitle")
        header_layout.addWidget(title)
        self.home_button = QPushButton("Home")
        self.home_button.setObjectName("navigationButton")
        self.home_button.setCheckable(True)
        self.home_button.clicked.connect(self.show_home)
        header_layout.addWidget(self.home_button)
        self.library_view_button = QPushButton("Library")
        self.library_view_button.setObjectName("navigationButton")
        self.library_view_button.setCheckable(True)
        self.library_view_button.clicked.connect(self.show_library)
        header_layout.addWidget(self.library_view_button)
        self.library_search_button = QPushButton("Search library")
        self.library_search_button.setObjectName("quietButton")
        self.library_search_button.setToolTip(
            "Search your saved papers, notes, and PDF comments (Cmd/Ctrl+F)."
        )
        self.library_search_button.clicked.connect(self.open_library_search)
        header_layout.addWidget(self.library_search_button)
        self.library_search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.library_search_shortcut.activated.connect(self.open_library_search)
        header_layout.addStretch()
        self.network_status_label = QLabel()
        self.network_status_label.setObjectName("networkStatus")
        header_layout.addWidget(self.network_status_label)
        self.discover_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.discover_shortcut.activated.connect(self.open_add_paper)
        self.offline_mode_checkbox = QCheckBox("Work offline")
        self.offline_mode_checkbox.setChecked(self.user_offline_mode)
        self.offline_mode_checkbox.toggled.connect(self.toggle_offline_mode)
        header_layout.addWidget(self.offline_mode_checkbox)
        self.open_library_button = QPushButton("Library folder…")
        self.open_library_button.setObjectName("quietButton")
        self.open_library_button.setToolTip(
            "Choose the folder that stores the Corpus Cabinet database and PDFs."
        )
        self.open_library_button.clicked.connect(self.open_library)
        header_layout.addWidget(self.open_library_button)
        outer_layout.addWidget(header)

        self.page_stack = QStackedWidget()
        self.home_page = self.build_home_page()
        self.library_page = QWidget()
        library_layout = QVBoxLayout(self.library_page)
        library_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.library_splitter = splitter
        splitter.addWidget(self.build_project_panel())
        splitter.addWidget(self.build_paper_panel())
        splitter.addWidget(self.build_detail_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([230, 360, 690])
        library_layout.addWidget(splitter)
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.library_page)
        outer_layout.addWidget(self.page_stack, 1)
        self.setCentralWidget(central)
        self.apply_product_styles()
        self.show_home()

    def build_home_page(self):
        page = QWidget()
        page.setObjectName("homePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(54, 42, 54, 42)
        layout.setSpacing(18)

        eyebrow = QLabel("YOUR RESEARCH HOME")
        eyebrow.setObjectName("homeEyebrow")
        layout.addWidget(eyebrow)

        headline = QLabel("Make room for the next idea.")
        headline.setObjectName("homeHeadline")
        layout.addWidget(headline)

        introduction = QLabel(
            "Continue building the projects already taking shape, or make "
            "room for a new direction."
        )
        introduction.setObjectName("homeIntroduction")
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        action_layout = QHBoxLayout()
        browse_button = QPushButton("Browse library")
        browse_button.clicked.connect(self.show_library)
        action_layout.addWidget(browse_button)
        new_project_button = QPushButton("New project")
        new_project_button.clicked.connect(self.create_project_from_home)
        action_layout.addWidget(new_project_button)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        overview_heading = QLabel("At a glance")
        overview_heading.setObjectName("homeSectionHeading")
        layout.addWidget(overview_heading)

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)
        project_card = QWidget()
        project_card.setObjectName("homeStatCard")
        project_card_layout = QVBoxLayout(project_card)
        project_card_layout.addWidget(QLabel("Projects"))
        self.home_project_count = QLabel("0")
        self.home_project_count.setObjectName("homeStatValue")
        project_card_layout.addWidget(self.home_project_count)
        stats_layout.addWidget(project_card)

        paper_card = QWidget()
        paper_card.setObjectName("homeStatCard")
        paper_card_layout = QVBoxLayout(paper_card)
        paper_card_layout.addWidget(QLabel("Saved papers"))
        self.home_paper_count = QLabel("0")
        self.home_paper_count.setObjectName("homeStatValue")
        paper_card_layout.addWidget(self.home_paper_count)
        stats_layout.addWidget(paper_card)

        layout.addLayout(stats_layout)

        reading_heading = QLabel("Recently opened")
        reading_heading.setObjectName("homeSectionHeading")
        layout.addWidget(reading_heading)
        self.home_reading_list = QListWidget()
        self.home_reading_list.setObjectName("homeRecentList")
        self.home_reading_list.setMaximumHeight(150)
        self.home_reading_list.itemClicked.connect(self.resume_home_paper)
        layout.addWidget(self.home_reading_list)

        recent_heading = QLabel("Continue a project")
        recent_heading.setObjectName("homeSectionHeading")
        layout.addWidget(recent_heading)
        self.home_recent_list = QListWidget()
        self.home_recent_list.setObjectName("homeRecentList")
        self.home_recent_list.setMaximumHeight(130)
        self.home_recent_list.itemClicked.connect(self.open_home_project)
        layout.addWidget(self.home_recent_list)
        layout.addStretch()
        return page

    def show_home(self):
        self.save_pending_reader_state()
        self.page_stack.setCurrentWidget(self.home_page)
        self.home_button.setChecked(True)
        self.library_view_button.setChecked(False)
        self.refresh_home()

    def show_library(self):
        self.page_stack.setCurrentWidget(self.library_page)
        self.home_button.setChecked(False)
        self.library_view_button.setChecked(True)

    def refresh_home(self):
        if not hasattr(self, "home_recent_list"):
            return
        projects = self.library.list_projects()
        standard_projects = []
        for project in projects:
            if project.get("kind") != "scrapbook":
                standard_projects.append(project)
        papers = self.library.list_papers()
        self.home_project_count.setText(str(len(standard_projects)))
        self.home_paper_count.setText(str(len(papers)))

        self.home_reading_list.clear()
        for paper in self.library.recent_reading_papers():
            label = (
                latex_to_plain_text(paper["title"]) + "\n"
                + paper["project_name"] + " · Page "
                + str(paper.get("last_page", 0) + 1) + " · "
                + str(paper.get("reading_status") or "unread").capitalize()
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, paper["id"])
            self.home_reading_list.addItem(item)
        if not self.home_reading_list.count():
            item = QListWidgetItem("Open a PDF to start your reading history.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.home_reading_list.addItem(item)

        self.home_recent_list.clear()
        for project in standard_projects[:5]:
            paper_count = project.get("paper_count", 0)
            suffix = " papers"
            if paper_count == 1:
                suffix = " paper"
            label = project["name"] + "\n" + str(paper_count) + suffix
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            self.home_recent_list.addItem(item)
        if not standard_projects:
            item = QListWidgetItem(
                "No projects yet — create one when you have a direction to follow."
            )
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.home_recent_list.addItem(item)

    def open_home_project(self, item):
        project_id = item.data(Qt.ItemDataRole.UserRole)
        if project_id is None:
            return
        self.show_library()
        for index in range(self.project_list.count()):
            project_item = self.project_list.item(index)
            if project_item.data(Qt.ItemDataRole.UserRole) == project_id:
                self.project_list.setCurrentItem(project_item)
                return

    def navigate_to_paper(self, paper_id):
        """Reveal one independent paper copy in its owning project."""
        paper = self.library.get_paper(paper_id)
        if not paper:
            return False
        self.save_pending_notes()
        self.save_pending_reader_state()
        self.show_library()
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == paper["project_id"]:
                self.project_list.setCurrentItem(item)
                break
        self.select_paper_by_id(paper_id)
        return self.current_paper_id == paper_id

    def resume_home_paper(self, item):
        paper_id = item.data(Qt.ItemDataRole.UserRole)
        if paper_id is not None and self.navigate_to_paper(paper_id):
            self.detail_tabs.setCurrentWidget(self.pdf_tab)
            self.detail_tab_changed(self.detail_tabs.indexOf(self.pdf_tab))

    def open_library_search(self, checked=False, query=""):
        """Keep local retrieval separate from online paper acquisition."""
        self.save_pending_notes()
        self.save_pending_reader_state()
        dialog = LibrarySearchDialog(self, self.library)
        if query:
            dialog.query_input.setText(query)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paper = dialog.selected_paper
        if not paper or not self.navigate_to_paper(paper["id"]):
            return
        field = paper.get("match_field")
        if field == "notes":
            self.detail_tabs.setCurrentWidget(self.notes_tab)
        elif field in ("extracted_text", "pdf_comment"):
            self.detail_tabs.setCurrentWidget(self.pdf_tab)
            page_number = paper.get("match_page_number")
            if page_number and self.pdf_document.pageCount() > 0:
                page = min(page_number - 1, self.pdf_document.pageCount() - 1)
                self.pdf_view.pageNavigator().jump(page, QPointF())
        else:
            self.detail_tabs.setCurrentIndex(0)

    def open_project_notes(self):
        if self.current_project_id is None:
            return
        project = self.library.get_project(self.current_project_id)
        if project:
            dialog = ProjectNotesDialog(self, self.library, project)
            dialog.exec()

    def edit_paper_tags(self):
        if self.current_paper_id is None:
            return
        tags = self.library.list_paper_tags(self.current_paper_id)
        text, accepted = QInputDialog.getText(
            self, "Paper tags", "Separate tags with commas, e.g. robotics, latent actions.\n"
            "Remove a tag here to unlink it from this paper.",
            QLineEdit.EchoMode.Normal, ", ".join(tags),
        )
        if not accepted:
            return
        try:
            tags = self.library.update_paper_tags(self.current_paper_id, text)
        except Exception as error:
            QMessageBox.warning(self, "Tags could not be saved", str(error))
            return
        self.render_paper_tags(tags)
        self.statusBar().showMessage("Tags saved", 3000)

    def render_paper_tags(self, tags=None):
        self.tags_button.setEnabled(self.current_paper_id is not None)
        tags = tags or []
        if tags:
            self.tags_button.setText("Edit tags")
        else:
            self.tags_button.setText("+ Add tags")
        links = []
        for tag in tags:
            links.append(
                '<a style="color:#6350AA; text-decoration:none;" href="tag:'
                + quote(tag, safe="") + '">#' + html.escape(tag) + '</a>'
            )
        self.paper_tags_label.setText(" &nbsp; · &nbsp; ".join(links))
        self.paper_tags_label.setVisible(bool(tags))

    def search_paper_tag(self, link):
        tag = unquote(link.removeprefix("tag:"))
        self.open_library_search(query="tag:" + json.dumps(tag, ensure_ascii=False))

    def render_reading_controls(self, paper=None):
        self.reading_status_combo.blockSignals(True)
        self.favorite_button.blockSignals(True)
        self.reading_status_combo.setEnabled(paper is not None)
        self.favorite_button.setEnabled(paper is not None)
        status = "unread"
        favorite = False
        if paper:
            status = paper.get("reading_status") or "unread"
            favorite = bool(paper.get("favorite"))
        index = self.reading_status_combo.findData(status)
        self.reading_status_combo.setCurrentIndex(max(index, 0))
        self.style_reading_status(status)
        self.favorite_button.setChecked(favorite)
        if favorite:
            self.favorite_button.setText("★")
            self.favorite_button.setToolTip("Remove from favorites")
            self.favorite_button.setAccessibleName("Remove from favorites")
        else:
            self.favorite_button.setText("☆")
            self.favorite_button.setToolTip("Add to favorites")
            self.favorite_button.setAccessibleName("Add to favorites")
        self.reading_status_combo.blockSignals(False)
        self.favorite_button.blockSignals(False)

    def style_reading_status(self, status):
        background, _, border = READING_STATUS_COLORS.get(
            status, READING_STATUS_COLORS["unread"]
        )
        self.reading_status_combo.setStyleSheet(
            f"""
            QComboBox#readingStatusPill {{
                background: {background}; color: #242528;
                border: 1px solid {border}; border-radius: 16px;
                padding: 0px 28px 0px 14px; font-weight: 600;
            }}
            QComboBox#readingStatusPill::drop-down {{
                border: none; width: 24px;
            }}
            QComboBox#readingStatusPill:disabled {{
                background: #F3F3F5; color: #848691; border-color: #DCDEE3;
            }}
            QComboBox#readingStatusPill QAbstractItemView {{
                background: white; color: #242528;
                selection-background-color: #EFEBFF;
                selection-color: #443381;
            }}
            """
        )

    def change_reading_status(self, index=None):
        if self.current_paper_id is None:
            return
        self.library.update_reading_status(
            self.current_paper_id,
            self.reading_status_combo.currentData(),
        )
        self.style_reading_status(self.reading_status_combo.currentData())
        self.refresh_reading_labels()
        if self.sort_combo.currentData() == "reading_status":
            self.refresh_papers()

    def change_paper_favorite(self, favorite):
        if self.current_paper_id is None:
            return
        self.library.update_paper_favorite(self.current_paper_id, favorite)
        if favorite:
            self.favorite_button.setText("★")
            self.favorite_button.setToolTip("Remove from favorites")
            self.favorite_button.setAccessibleName("Remove from favorites")
        else:
            self.favorite_button.setText("☆")
            self.favorite_button.setToolTip("Add to favorites")
            self.favorite_button.setAccessibleName("Add to favorites")
        self.refresh_reading_labels()

    def refresh_reading_labels(self):
        for index in range(self.paper_list.count()):
            item = self.paper_list.item(index)
            paper = self.library.get_paper(item.data(Qt.ItemDataRole.UserRole))
            if paper:
                item.setText(paper_card_label(paper))
                item.setData(Qt.ItemDataRole.UserRole + 1, paper)
        self.refresh_home()

    def apply_product_styles(self):
        """Apply the intentionally light product shell."""
        self.setStyleSheet(
            "QWidget { color: #202124; font-size: 13px; }"
            "QWidget#appShell { background: #f7f7f8; }"
            "QWidget#appHeader { background: #ffffff; "
            "border-bottom: 1px solid #dedfe2; }"
            "QLabel#appTitle { font-size: 20px; font-weight: 700; color: #202124; }"
            "QLabel#libraryName { color: #9a9da4; }"
            "QLabel#networkStatus { color: #35704b; }"
            "QLabel#mutedLabel { color: #74777f; }"
            "QPushButton#navigationButton { border: 0; background: transparent; "
            "color: #6b6e76; }"
            "QPushButton#navigationButton:checked { color: #443381; "
            "background: #eeeaff; }"
            "QPushButton { min-height: 28px; padding: 2px 10px; "
            "border: 1px solid #ced0d5; border-radius: 6px; "
            "background: #ffffff; color: #202124; }"
            "QPushButton:hover { background: #f1f1f3; }"
            "QPushButton:disabled { color: #a4a6ac; background: #f3f3f4; }"
            "QPushButton#quietButton { background: transparent; }"
            "QWidget#homePage { background: #fbfbfc; }"
            "QLabel#homeEyebrow { color: #6350aa; font-weight: 700; }"
            "QLabel#homeHeadline { color: #202124; font-size: 32px; "
            "font-weight: 700; }"
            "QLabel#homeIntroduction { color: #656871; font-size: 16px; }"
            "QPushButton#homePrimaryButton { min-height: 34px; border: 0; "
            "background: #6350aa; color: #ffffff; font-weight: 600; }"
            "QPushButton#homePrimaryButton:hover { background: #574697; }"
            "QPushButton#citationPrimaryButton { min-height: 30px; border: 0; "
            "background: #6350aa; color: #ffffff; font-weight: 600; }"
            "QPushButton#citationPrimaryButton:hover { background: #574697; }"
            "QLabel#citationWarning { padding: 9px; border-radius: 6px; "
            "background: #fff6df; color: #76531b; }"
            "QLabel#homeSectionHeading { margin-top: 8px; font-size: 15px; "
            "font-weight: 700; }"
            "QWidget#homeStatCard { background: #ffffff; "
            "border: 1px solid #e0e1e5; border-radius: 9px; }"
            "QLabel#homeStatValue { color: #443381; font-size: 26px; "
            "font-weight: 700; }"
            "QListWidget#homeRecentList { background: #ffffff; "
            "border: 1px solid #e0e1e5; border-radius: 9px; outline: 0; }"
            "QListWidget#homeRecentList::item { padding: 11px; "
            "border-bottom: 1px solid #ececef; }"
            "QListWidget#homeRecentList::item:hover { background: #f4f2ff; }"
            "QWidget#projectPanel, QWidget#paperPanel { "
            "background: #f2f2f4; }"
            "QWidget#projectPanel { border-right: 1px solid #dedfe2; }"
            "QWidget#paperPanel { border-right: 1px solid #dedfe2; }"
            "QWidget#paperPanel[dragActive=\"true\"] { "
            "background: #f4f1ff; border: 2px dashed #806cc8; }"
            "QLabel#paperDropHint { padding: 10px; border-radius: 7px; "
            "background: #6350aa; color: #ffffff; font-weight: 700; }"
            "QWidget#detailPanel { background: #ffffff; }"
            "QLabel#paneHeading { font-size: 13px; font-weight: 700; "
            "color: #202124; }"
            "QSplitter::handle { background: #dedfe2; width: 1px; }"
            "QListWidget#projectList, QListWidget#paperList { border: 0; "
            "outline: 0; background: transparent; }"
            "QListWidget#projectList::item { padding: 0; margin-bottom: 3px; "
            "border-radius: 7px; color: #202124; }"
            "QListWidget#projectList::item:hover { background: #e9e9ec; }"
            "QListWidget#projectList::item:selected { background: #e8e3ff; "
            "color: #443381; }"
            "QListWidget#paperList[dragActive=\"true\"] { "
            "background: #f4f1ff; border: 2px dashed #806cc8; "
            "border-radius: 9px; }"
            "QComboBox, QLineEdit, QPlainTextEdit { border: 1px solid #ced0d5; "
            "border-radius: 6px; padding: 5px; background: #ffffff; "
            "color: #202124; selection-background-color: #6350aa; "
            "selection-color: #ffffff; }"
            "QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover { "
            "border-color: #aaadb4; }"
            "QTextBrowser#abstractReader { border: 0; padding: 10px; "
            "background: #ffffff; color: #2d2f33; }"
            "QTabWidget::pane { border: 1px solid #dedfe2; "
            "border-radius: 7px; background: #ffffff; }"
            "QTabBar::tab { padding: 7px 12px; color: #6b6e76; "
            "background: #f2f2f4; border: 1px solid #dedfe2; }"
            "QTabBar::tab:selected { color: #443381; background: #ffffff; "
            "border-bottom-color: #ffffff; }"
            "QWidget#pdfEmptyPanel { background: #f7f7f8; border-radius: 9px; }"
            "QWidget#pdfCommentsPanel { background: #f7f7f8; "
            "border-left: 1px solid #dedfe2; }"
            "QListWidget#pdfCommentList { border: 1px solid #dedfe2; "
            "border-radius: 7px; background: #ffffff; outline: 0; }"
            "QListWidget#pdfCommentList::item { padding: 9px; "
            "border-bottom: 1px solid #ececef; }"
            "QListWidget#pdfCommentList::item:selected { "
            "background: #eeeaff; color: #443381; }"
            "QLabel#pdfEmptyMark { min-width: 46px; min-height: 46px; "
            "max-width: 46px; max-height: 46px; border-radius: 23px; "
            "background: #e8e3ff; color: #443381; font-weight: 700; }"
            "QLabel#pdfEmptyHeading { margin-top: 8px; color: #202124; "
            "font-size: 18px; font-weight: 700; }"
            "QLabel#pdfEmptyCopy { color: #6b6e76; }"
            "QLabel#pdfSourceNote { color: #35704b; font-weight: 600; }"
            "QPushButton#pdfPrimaryButton { min-height: 32px; border: 0; "
            "background: #6350aa; color: #ffffff; font-weight: 600; }"
            "QPushButton#pdfPrimaryButton:hover { background: #574697; }"
            "QPushButton#pdfTextButton { border: 0; background: transparent; "
            "color: #686b73; text-decoration: underline; }"
            "QPushButton#deleteButton { color: #c85b5b; background: transparent; }"
        )

    def build_project_panel(self):
        panel = QWidget()
        panel.setObjectName("projectPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        heading = QLabel("Projects")
        heading.setObjectName("paneHeading")
        layout.addWidget(heading)

        self.project_list = ProjectListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setItemDelegate(ProjectCardDelegate(self.project_list))
        self.project_list.setSpacing(2)
        self.project_list.currentItemChanged.connect(self.select_project)
        self.project_list.currentItemChanged.connect(
            self.update_project_card_selection
        )
        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self.project_context_menu)
        layout.addWidget(self.project_list)

        self.project_notes_button = QPushButton("Project notes…")
        self.project_notes_button.setEnabled(False)
        self.project_notes_button.clicked.connect(self.open_project_notes)
        layout.addWidget(self.project_notes_button)

        self.new_project_button = QPushButton("+ New project")
        self.new_project_button.clicked.connect(self.create_project)
        layout.addWidget(self.new_project_button)
        return panel

    def build_paper_panel(self):
        panel = PdfDropPanel()
        self.paper_panel = panel
        panel.setObjectName("paperPanel")
        panel.pdfsDropped.connect(self.confirm_dropped_pdfs)
        panel.dragActiveChanged.connect(self.show_paper_drop_hint)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        heading_layout = QHBoxLayout()
        heading = QLabel("Papers")
        heading.setObjectName("paneHeading")
        heading_layout.addWidget(heading)
        heading_layout.addStretch()
        self.paper_count_label = QLabel("0 papers")
        self.paper_count_label.setObjectName("mutedLabel")
        heading_layout.addWidget(self.paper_count_label)
        self.sort_combo = ModernComboBox()
        self.sort_combo.addItem("Recently added", "created")
        self.sort_combo.addItem("Title A–Z", "title")
        self.sort_combo.addItem("Publication date", "publication")
        self.sort_combo.addItem("Author", "author")
        self.sort_combo.addItem("Reading status", "reading_status")
        self.sort_combo.currentIndexChanged.connect(self.refresh_papers)
        heading_layout.addWidget(self.sort_combo)
        layout.addLayout(heading_layout)

        self.add_paper_button = QPushButton("+ Add paper")
        self.add_paper_button.setToolTip(
            "Add a PDF, search by title, or paste a paper link (Cmd/Ctrl+K)"
        )
        self.add_paper_button.clicked.connect(self.open_add_paper)
        self.add_paper_button.setEnabled(False)
        layout.addWidget(self.add_paper_button)

        self.paper_drop_hint = QLabel("Drop PDF to add to this project")
        self.paper_drop_hint.setObjectName("paperDropHint")
        self.paper_drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.paper_drop_hint.setVisible(False)
        layout.addWidget(self.paper_drop_hint)

        self.paper_list = PdfDropListWidget()
        self.paper_list.setObjectName("paperList")
        self.paper_list.setItemDelegate(PaperCardDelegate(self.paper_list))
        self.paper_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.paper_list.setSpacing(2)
        self.paper_list.pdfsDropped.connect(self.confirm_dropped_pdfs)
        self.paper_list.dragActiveChanged.connect(self.show_paper_drop_hint)
        self.paper_list.setWordWrap(True)
        self.paper_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.paper_list.currentItemChanged.connect(self.select_paper)
        layout.addWidget(self.paper_list)
        return panel

    def show_paper_drop_hint(self, active):
        """Show the current destination while a PDF is over the Papers pane."""
        if not active or self.current_project_id is None:
            self.paper_drop_hint.setVisible(False)
            return
        project = self.library.get_project(self.current_project_id)
        if not project:
            self.paper_drop_hint.setVisible(False)
            return
        self.paper_drop_hint.setText("Drop PDF to add to " + project["name"])
        self.paper_drop_hint.setVisible(True)

    def build_detail_panel(self):
        panel = QWidget()
        panel.setObjectName("detailPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 22, 24, 18)

        self.detail_title = QLabel("Select a paper")
        self.detail_title.setWordWrap(True)
        self.detail_title.setTextFormat(Qt.TextFormat.RichText)
        self.detail_title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(self.detail_title)

        reading_layout = QHBoxLayout()
        reading_layout.setSpacing(8)
        self.reading_status_combo = ReadingStatusCombo()
        self.reading_status_combo.setObjectName("readingStatusPill")
        self.reading_status_combo.setFixedSize(108, 28)
        self.reading_status_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reading_status_combo.setToolTip("Click to change reading status")
        self.reading_status_combo.setAccessibleName("Change reading status")
        self.reading_status_combo.addItem("Unread", "unread")
        self.reading_status_combo.addItem("Reading", "reading")
        self.reading_status_combo.addItem("Read", "read")
        self.reading_status_combo.setEnabled(False)
        self.reading_status_combo.currentIndexChanged.connect(self.change_reading_status)
        reading_layout.addWidget(self.reading_status_combo)
        self.favorite_button = QPushButton("☆")
        self.favorite_button.setObjectName("paperFavoriteButton")
        self.favorite_button.setFixedSize(28, 28)
        self.favorite_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.favorite_button.setToolTip("Add to favorites")
        self.favorite_button.setAccessibleName("Add to favorites")
        self.favorite_button.setStyleSheet(
            "QPushButton#paperFavoriteButton { min-height: 0; border: 0; "
            "border-radius: 14px; padding: 0; background: transparent; "
            "color: #848691; font-size: 20px; }"
            "QPushButton#paperFavoriteButton:hover { background: #FFF7DB; color: #B88400; }"
            "QPushButton#paperFavoriteButton:checked { color: #D9A000; }"
            "QPushButton#paperFavoriteButton:focus { border: 1px solid #D9A000; }"
            "QPushButton#paperFavoriteButton:disabled { color: #A4A6AC; }"
        )
        self.favorite_button.setCheckable(True)
        self.favorite_button.setEnabled(False)
        self.favorite_button.toggled.connect(self.change_paper_favorite)
        reading_layout.addWidget(self.favorite_button)
        self.tags_button = QPushButton("+ Add tags")
        self.tags_button.setObjectName("paperReferenceButton")
        self.tags_button.setFixedHeight(28)
        self.tags_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tags_button.setToolTip("Organize this paper with searchable tags")
        self.tags_button.clicked.connect(self.edit_paper_tags)
        self.tags_button.setStyleSheet(
            "QPushButton#paperReferenceButton { min-height: 0; border: 0; "
            "border-radius: 5px; padding: 0 8px; background: transparent; color: #6350AA; font-size: 12px; }"
            "QPushButton#paperReferenceButton:hover { background: #F3F1FA; }"
            "QPushButton#paperReferenceButton:focus { border: 1px solid #A99BE0; }"
            "QPushButton#paperReferenceButton:disabled { color: #A4A6AC; }"
        )
        reading_layout.addWidget(self.tags_button)
        reading_layout.addStretch()
        layout.addLayout(reading_layout)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName("paperDetailTabs")
        self.detail_tabs.setStyleSheet(
            "QTabWidget#paperDetailTabs::pane { border: 0; "
            "border-top: 1px solid #E8E9ED; border-radius: 0; background: #FFFFFF; }"
            "QTabWidget#paperDetailTabs QTabBar::tab { border: 0; "
            "border-bottom: 2px solid transparent; background: transparent; "
            "padding: 10px 14px; margin-right: 8px; color: #74777F; }"
            "QTabWidget#paperDetailTabs QTabBar::tab:hover { color: #443381; }"
            "QTabWidget#paperDetailTabs QTabBar::tab:selected { "
            "border-bottom: 2px solid #7660BD; color: #574697; }"
        )
        self.detail_tabs.addTab(self.build_detail_tab(), "Details")
        self.notes_tab = self.build_notes_tab()
        pdf_panel = QWidget()
        pdf_layout = QVBoxLayout(pdf_panel)
        pdf_layout.setContentsMargins(14, 14, 14, 14)
        self.pdf_toolbar = QWidget()
        pdf_toolbar_layout = QVBoxLayout(self.pdf_toolbar)
        pdf_toolbar_layout.setContentsMargins(0, 0, 0, 6)
        pdf_navigation_layout = QHBoxLayout()
        pdf_toolbar_layout.addLayout(pdf_navigation_layout)
        self.pdf_previous_button = QPushButton("Previous")
        self.pdf_previous_button.clicked.connect(self.go_to_previous_pdf_page)
        pdf_navigation_layout.addWidget(self.pdf_previous_button)
        self.pdf_page_label = QLabel("Page - of -")
        self.pdf_page_label.setObjectName("mutedLabel")
        pdf_navigation_layout.addWidget(self.pdf_page_label)
        self.pdf_next_button = QPushButton("Next")
        self.pdf_next_button.clicked.connect(self.go_to_next_pdf_page)
        pdf_navigation_layout.addWidget(self.pdf_next_button)
        self.pdf_comments_button = QPushButton("Comments")
        self.pdf_comments_button.setCheckable(True)
        self.pdf_comments_button.setChecked(True)
        pdf_navigation_layout.addWidget(self.pdf_comments_button)
        self.pdf_fullscreen_button = QPushButton("Full screen")
        self.pdf_fullscreen_button.setToolTip(
            "Open a distraction-free PDF reader. Press Esc to exit."
        )
        self.pdf_fullscreen_button.clicked.connect(self.open_fullscreen_pdf)
        pdf_navigation_layout.addWidget(self.pdf_fullscreen_button)
        pdf_navigation_layout.addStretch()
        pdf_zoom_layout = QHBoxLayout()
        pdf_toolbar_layout.addLayout(pdf_zoom_layout)
        pdf_zoom_layout.addWidget(QLabel("Zoom"))
        self.pdf_zoom_combo = ModernComboBox()
        self.pdf_zoom_combo.addItem("Fit width", "fit_width")
        self.pdf_zoom_combo.addItem("Fit page", "fit_page")
        self.pdf_zoom_combo.addItem("100%", 1.0)
        self.pdf_zoom_combo.addItem("125%", 1.25)
        self.pdf_zoom_combo.addItem("150%", 1.5)
        self.pdf_zoom_combo.currentIndexChanged.connect(self.change_pdf_zoom)
        pdf_zoom_layout.addWidget(self.pdf_zoom_combo)
        self.pdf_zoom_out_button = QPushButton("−")
        self.pdf_zoom_out_button.setToolTip("Zoom out")
        pdf_zoom_layout.addWidget(self.pdf_zoom_out_button)
        self.pdf_zoom_in_button = QPushButton("+")
        self.pdf_zoom_in_button.setToolTip("Zoom in · trackpad pinch or Ctrl/Cmd + scroll also works")
        pdf_zoom_layout.addWidget(self.pdf_zoom_in_button)
        pdf_zoom_layout.addStretch()
        self.pdf_toolbar.setVisible(False)
        pdf_layout.addWidget(self.pdf_toolbar)
        self.pdf_stack = QStackedWidget()
        # Fit the reader to its pane instead of letting its size hint move dividers.
        self.pdf_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.pdf_empty_panel = self.build_pdf_empty_state()
        self.pdf_stack.addWidget(self.pdf_empty_panel)
        self.pdf_view = ZoomablePdfView()
        self.pdf_zoom_out_button.clicked.connect(self.pdf_view.zoom_out)
        self.pdf_zoom_in_button.clicked.connect(self.pdf_view.zoom_in)
        self.pdf_view.customZoomChanged.connect(self.sync_custom_pdf_zoom)
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.pdf_document.pageCountChanged.connect(
            self.update_pdf_page_controls
        )
        self.pdf_view.pageNavigator().currentPageChanged.connect(
            self.update_pdf_page_controls
        )
        self.pdf_view.pageNavigator().currentPageChanged.connect(
            self.schedule_reader_save
        )
        self.pdf_stack.addWidget(self.pdf_view)
        self.pdf_reader_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pdf_reader_splitter.addWidget(self.pdf_stack)
        self.pdf_comments_panel = PdfCommentsPanel(
            self.library,
            None,
            self.pdf_view.pageNavigator(),
        )
        self.pdf_reader_splitter.addWidget(self.pdf_comments_panel)
        self.pdf_reader_splitter.setStretchFactor(0, 1)
        self.pdf_reader_splitter.setSizes([720, 260])
        self.pdf_comments_button.toggled.connect(
            self.pdf_comments_panel.setVisible
        )
        self.pdf_comments_panel.setVisible(False)
        pdf_layout.addWidget(self.pdf_reader_splitter, 1)
        self.pdf_tab = pdf_panel
        self.detail_tabs.addTab(self.pdf_tab, "PDF")
        self.detail_tabs.addTab(self.notes_tab, "Notes")
        self.detail_tabs.currentChanged.connect(self.detail_tab_changed)
        layout.addWidget(self.detail_tabs)

        paper_action_layout = QHBoxLayout()
        self.copy_paper_button = QPushButton("Copy to project…")
        self.copy_paper_button.clicked.connect(self.copy_current_paper)
        self.copy_paper_button.setEnabled(False)
        paper_action_layout.addWidget(self.copy_paper_button)
        self.delete_paper_button = QPushButton("Delete paper")
        self.delete_paper_button.setObjectName("deleteButton")
        self.delete_paper_button.clicked.connect(self.delete_current_paper)
        self.delete_paper_button.setEnabled(False)
        paper_action_layout.addWidget(self.delete_paper_button)
        layout.addLayout(paper_action_layout)
        return panel

    def build_notes_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        heading = QLabel("Personal notes")
        heading.setObjectName("homeSectionHeading")
        layout.addWidget(heading)
        description = QLabel(
            "Write observations, questions, or reminders about this paper."
        )
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setObjectName("notesEditor")
        self.notes_editor.setPlaceholderText(
            "Select a paper, then start writing…"
        )
        self.notes_editor.setEnabled(False)
        self.notes_editor.textChanged.connect(self.schedule_notes_save)
        layout.addWidget(self.notes_editor, 1)

        self.notes_status_label = QLabel("Select a paper to add notes.")
        self.notes_status_label.setObjectName("mutedLabel")
        layout.addWidget(self.notes_status_label)
        return tab

    def build_pdf_empty_state(self):
        panel = QWidget()
        panel.setObjectName("pdfEmptyPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.addStretch()

        self.pdf_empty_mark = QLabel("PDF")
        self.pdf_empty_mark.setObjectName("pdfEmptyMark")
        self.pdf_empty_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(
            self.pdf_empty_mark,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )

        self.pdf_status_label = QLabel("Select a paper")
        self.pdf_status_label.setObjectName("pdfEmptyHeading")
        self.pdf_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pdf_status_label)

        self.pdf_empty_copy = QLabel(
            "Choose a paper from the library to view or add its PDF."
        )
        self.pdf_empty_copy.setObjectName("pdfEmptyCopy")
        self.pdf_empty_copy.setWordWrap(True)
        self.pdf_empty_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pdf_empty_copy)

        self.pdf_source_note = QLabel()
        self.pdf_source_note.setObjectName("pdfSourceNote")
        self.pdf_source_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pdf_source_note)

        action_layout = QHBoxLayout()
        action_layout.addStretch()
        self.pdf_primary_button = QPushButton("Find online copy")
        self.pdf_primary_button.setObjectName("pdfPrimaryButton")
        self.pdf_primary_button.clicked.connect(self.perform_pdf_primary_action)
        action_layout.addWidget(self.pdf_primary_button)
        self.pdf_choose_button = QPushButton("Choose file")
        self.pdf_choose_button.clicked.connect(self.choose_pdf_for_current_paper)
        action_layout.addWidget(self.pdf_choose_button)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        secondary_layout = QHBoxLayout()
        secondary_layout.addStretch()
        self.pdf_paste_button = QPushButton("Paste PDF URL")
        self.pdf_paste_button.setObjectName("pdfTextButton")
        self.pdf_paste_button.clicked.connect(self.paste_pdf_url)
        secondary_layout.addWidget(self.pdf_paste_button)
        self.pdf_source_button = QPushButton("Open source page")
        self.pdf_source_button.setObjectName("pdfTextButton")
        self.pdf_source_button.clicked.connect(self.open_current_source)
        secondary_layout.addWidget(self.pdf_source_button)
        secondary_layout.addStretch()
        layout.addLayout(secondary_layout)

        self.pdf_action_status = QLabel()
        self.pdf_action_status.setObjectName("mutedLabel")
        self.pdf_action_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pdf_action_status)
        layout.addStretch()
        return panel

    def build_detail_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        self.detail_meta = QLabel()
        self.detail_meta.setWordWrap(True)
        self.detail_meta.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.detail_meta.setObjectName("mutedLabel")
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
        self.bibtex_button = QPushButton("BibTeX")
        self.bibtex_button.setToolTip(
            "Review DOI-provided or locally generated BibTeX before copying."
        )
        self.bibtex_button.clicked.connect(self.open_bibtex_dialog)
        link_layout.addWidget(self.bibtex_button)
        for button in (self.open_source_button, self.scholar_button, self.bibtex_button):
            button.setObjectName("paperReferenceButton")
            button.setFixedHeight(28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton#paperReferenceButton { min-height: 0; border: 0; "
                "border-radius: 5px; padding: 0 8px; background: transparent; "
                "color: #6350AA; font-size: 12px; }"
                "QPushButton#paperReferenceButton:hover { background: #F3F1FA; }"
                "QPushButton#paperReferenceButton:focus { border: 1px solid #A99BE0; }"
                "QPushButton#paperReferenceButton:disabled { color: #A4A6AC; }"
            )
        link_layout.setSpacing(8)
        link_layout.addStretch()
        layout.addLayout(link_layout)

        self.paper_tags_label = QLabel()
        self.paper_tags_label.setWordWrap(True)
        self.paper_tags_label.setTextFormat(Qt.TextFormat.RichText)
        self.paper_tags_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.paper_tags_label.setToolTip("Click a tag to find matching papers across your library")
        self.paper_tags_label.linkActivated.connect(self.search_paper_tag)
        self.paper_tags_label.setVisible(False)
        layout.addWidget(self.paper_tags_label)

        abstract_heading_layout = QHBoxLayout()
        self.abstract_label = QLabel("Abstract")
        self.abstract_label.setObjectName("paneHeading")
        abstract_heading_layout.addWidget(self.abstract_label)
        abstract_heading_layout.addStretch()
        self.abstract_font_toggle = QCheckBox("Use OpenDyslexic")
        self.abstract_font_toggle.setToolTip(
            "Change abstract body text to the bundled OpenDyslexic font."
        )
        self.abstract_font_toggle.setChecked(self.dyslexic_font_enabled)
        self.abstract_font_toggle.toggled.connect(self.toggle_abstract_font)
        if not self.opendyslexic_family:
            self.abstract_font_toggle.setEnabled(False)
            self.abstract_font_toggle.setToolTip(
                "The bundled OpenDyslexic font could not be loaded."
            )
        abstract_heading_layout.addWidget(self.abstract_font_toggle)
        layout.addLayout(abstract_heading_layout)
        self.abstract_text = QTextBrowser()
        self.abstract_text.setObjectName("abstractReader")
        self.abstract_text.setReadOnly(True)
        self.abstract_text.setPlaceholderText("Online abstract will appear here.")
        self.abstract_text.setMinimumHeight(220)
        layout.addWidget(self.abstract_text, 1)

        form = QFormLayout()
        self.task_label = QLabel("Task")
        self.task_text = QPlainTextEdit()
        self.task_text.setReadOnly(True)
        self.task_text.setPlaceholderText("AI task summary will appear here.")
        self.task_text.setMinimumHeight(90)
        form.addRow(self.task_label, self.task_text)

        self.methodology_label = QLabel("Methodology")
        self.methodology_text = QPlainTextEdit()
        self.methodology_text.setReadOnly(True)
        self.methodology_text.setPlaceholderText("AI methodology summary will appear here.")
        self.methodology_text.setMinimumHeight(120)
        form.addRow(self.methodology_label, self.methodology_text)
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
        self.user_offline_mode = self.workspace_manager.is_offline_mode()
        self.offline_mode = (
            self.user_offline_mode or not self.network_available
        )
        self.offline_mode_checkbox.blockSignals(True)
        self.offline_mode_checkbox.setChecked(self.user_offline_mode)
        self.offline_mode_checkbox.blockSignals(False)
        library_name = os.path.basename(current_path.rstrip(os.sep))
        if not library_name:
            library_name = current_path
        self.open_library_button.setToolTip(
            "Current library: " + library_name + "\n" + current_path
        )
        self.update_network_status()
        self.update_search_controls()
        self.refresh_projects()
        self.add_paper_button.setEnabled(
            self.current_project_id is not None
            and not self.online_controls_busy
        )

    def update_network_status(self):
        if self.user_offline_mode:
            self.network_status_label.setText("Working offline")
            self.network_status_label.setStyleSheet("color: #9A5B13;")
            self.network_status_label.setToolTip(
                "Online features are turned off by your preference."
            )
        elif not self.network_available:
            self.network_status_label.setText("No internet · offline")
            self.network_status_label.setStyleSheet("color: #9A5B13;")
            self.network_status_label.setToolTip(
                "Online features will return automatically when the connection does."
            )
        else:
            self.network_status_label.setText("Online")
            self.network_status_label.setStyleSheet("color: #39704A;")
            self.network_status_label.setToolTip("Internet access is available.")

    def update_search_controls(self):
        add_enabled = (
            self.current_project_id is not None
            and not self.online_controls_busy
        )
        self.add_paper_button.setEnabled(add_enabled)
        self.discover_shortcut.setEnabled(add_enabled)
        self.update_paper_link_controls()
        if self.current_paper_id is not None:
            paper = self.library.get_paper(self.current_paper_id)
            if paper and not paper.get("file_path"):
                self.render_pdf_state(paper)

    def toggle_offline_mode(self, enabled):
        self.user_offline_mode = bool(enabled)
        self.workspace_manager.set_offline_mode(self.user_offline_mode)
        self.offline_mode = (
            self.user_offline_mode or not self.network_available
        )
        self.update_network_status()
        self.update_search_controls()
        if self.user_offline_mode:
            self.statusBar().showMessage(
                "Working offline. Local library features remain available.",
                5000,
            )
        elif not self.network_available:
            self.statusBar().showMessage(
                "Offline preference cleared. Waiting for an internet connection.",
                5000,
            )
        else:
            self.statusBar().showMessage(
                "Online features enabled.",
                5000,
            )

    def initialize_network_monitor(self):
        """Read Qt network reachability and subscribe to future changes."""
        QNetworkInformation.loadDefaultBackend()
        self.network_information = QNetworkInformation.instance()
        if self.network_information is None:
            return
        self.apply_network_reachability(
            self.network_information.reachability()
        )
        self.network_information.reachabilityChanged.connect(
            self.network_reachability_changed
        )

    def apply_network_reachability(self, reachability):
        """Translate conclusive Qt reachability states into availability."""
        if reachability == QNetworkInformation.Reachability.Online:
            self.network_available = True
        elif reachability in (
            QNetworkInformation.Reachability.Disconnected,
            QNetworkInformation.Reachability.Local,
            QNetworkInformation.Reachability.Site,
        ):
            self.network_available = False

    def network_reachability_changed(self, reachability):
        """Temporarily enter or leave offline mode with the connection."""
        previous_available = self.network_available
        self.apply_network_reachability(reachability)
        if previous_available == self.network_available:
            return
        self.offline_mode = (
            self.user_offline_mode or not self.network_available
        )
        if not hasattr(self, "network_status_label"):
            return
        self.update_network_status()
        self.update_search_controls()
        if self.user_offline_mode:
            return
        if self.network_available:
            self.statusBar().showMessage(
                "Internet connection restored. Online features are available.",
                5000,
            )
        else:
            self.statusBar().showMessage(
                "Internet connection lost. Working offline temporarily.",
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
            paper_count = project["paper_count"]
            paper_suffix = " papers"
            if paper_count == 1:
                paper_suffix = " paper"
            label = project["name"] + "\n" + str(paper_count) + paper_suffix
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            if project.get("kind") == "scrapbook":
                item.setData(Qt.ItemDataRole.UserRole + 1, "scrapbook")
                item.setSizeHint(QSize(0, 82))
                item.setToolTip(
                    "Temporary holding area. Papers move out instead of being copied."
                )
            self.project_list.addItem(item)
            if project.get("kind") == "scrapbook":
                scrapbook_card = self.create_scrapbook_card(
                    paper_count,
                    paper_suffix,
                    project,
                )
                self.project_list.setItemWidget(item, scrapbook_card)
            else:
                card = self.create_project_card(project, paper_count, paper_suffix)
                card.ensurePolished()
                item.setSizeHint(QSize(0, max(card.sizeHint().height(), card.heightForWidth(self.project_list.viewport().width()))))
                self.project_list.setItemWidget(item, card)
            if project["id"] == selected_id:
                target_row = index
        self.project_list.blockSignals(False)

        if target_row < 0 and projects:
            target_row = 0

        if target_row >= 0:
            self.project_list.setCurrentRow(target_row)
        else:
            self.current_project_id = None
            self.add_paper_button.setEnabled(False)
            self.project_notes_button.setEnabled(False)
            self.paper_panel.set_drop_enabled(False)
            self.paper_list.set_drop_enabled(False)
            self.refresh_papers()
        self.refresh_home()

    def create_project_favorite_button(self, project):
        button = QPushButton()
        button.setObjectName("projectFavoriteButton")
        button.setProperty("projectId", project["id"])
        button.setCheckable(True)
        button.setFixedSize(28, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QPushButton#projectFavoriteButton { min-height: 0; border: 0; "
            "border-radius: 14px; padding: 0; background: transparent; "
            "color: #848691; font-size: 20px; }"
            "QPushButton#projectFavoriteButton:hover { background: #FFF7DB; color: #B88400; }"
            "QPushButton#projectFavoriteButton:checked { color: #D9A000; }"
            "QPushButton#projectFavoriteButton:focus { border: 1px solid #D9A000; }"
        )
        button.setChecked(bool(project.get("favorite")))
        self.render_project_favorite_button(button)
        button.clicked.connect(self.change_project_favorite)
        return button

    def render_project_favorite_button(self, button):
        if button.isChecked():
            button.setText("★")
            action = "Remove project from favorites"
        else:
            button.setText("☆")
            action = "Add project to favorites"
        button.setToolTip(action)
        button.setAccessibleName(action)

    def change_project_favorite(self, favorite):
        button = self.sender()
        try:
            self.library.update_project_favorite(button.property("projectId"), favorite)
        except Exception as error:
            button.setChecked(not favorite)
            QMessageBox.warning(self, "Favorite could not be saved", str(error))
        self.render_project_favorite_button(button)

    def create_project_card(self, project, paper_count, paper_suffix):
        card = QWidget(self.project_list.viewport())
        card.setObjectName("projectCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(9, 8, 6, 8)
        layout.setSpacing(6)
        labels = QVBoxLayout()
        labels.setSpacing(2)
        title = QLabel(project["name"])
        title.setObjectName("projectCardTitle")
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        labels.addWidget(title)
        count = QLabel(str(paper_count) + paper_suffix)
        count.setObjectName("projectCardCount")
        count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        labels.addWidget(count)
        layout.addLayout(labels, 1)
        layout.addWidget(self.create_project_favorite_button(project))
        self.style_project_card(card, False)
        return card

    def style_project_card(self, card, selected):
        if card.objectName() == "scrapbookCard":
            self.style_scrapbook_card(card, selected)
            return
        color = "#202124"
        if selected:
            color = "#443381"
        card.setStyleSheet(
            "QWidget#projectCard { background: transparent; border: 0; }"
            "QLabel#projectCardTitle, QLabel#projectCardCount { background: transparent; "
            "border: 0; color: " + color + "; }"
        )

    def create_scrapbook_card(self, paper_count, paper_suffix, project):
        """Build the visually distinct temporary-paper card."""
        card = QWidget()
        card.setObjectName("scrapbookCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(3)

        heading_layout = QHBoxLayout()
        heading_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("ScrapBook")
        title.setObjectName("scrapbookTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        heading_layout.addWidget(title)
        heading_layout.addStretch()
        badge = QLabel("TEMP")
        badge.setObjectName("scrapbookBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        heading_layout.addWidget(badge)
        heading_layout.addWidget(self.create_project_favorite_button(project))
        layout.addLayout(heading_layout)

        count_label = QLabel(str(paper_count) + paper_suffix + " · staging area")
        count_label.setObjectName("scrapbookCount")
        count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(count_label)
        self.style_scrapbook_card(card, False)
        return card

    def style_scrapbook_card(self, card, selected):
        """Keep ScrapBook distinct while showing its selection state."""
        if selected:
            border_color = "#6350AA"
            background_color = "#FFF3DC"
        else:
            border_color = "#D89A39"
            background_color = "#FFF9EE"
        card.setStyleSheet(
            "QWidget#scrapbookCard { background: "
            + background_color
            + "; border: 2px solid "
            + border_color
            + "; border-radius: 10px; }"
            "QLabel#scrapbookTitle { border: 0; background: transparent; "
            "color: #4B3420; font-size: 15px; font-weight: 700; }"
            "QLabel#scrapbookBadge { border: 0; border-radius: 7px; "
            "background: #F2C879; color: #694314; padding: 2px 6px; "
            "font-size: 10px; font-weight: 700; }"
            "QLabel#scrapbookCount { border: 0; background: transparent; "
            "color: #875E2D; font-size: 12px; }"
        )

    def update_project_card_selection(self, current, previous=None):
        """Update project card labels and the distinctive ScrapBook border."""
        if previous is not None:
            previous_card = self.project_list.itemWidget(previous)
            if previous_card is not None:
                self.style_project_card(previous_card, False)
        if current is not None:
            current_card = self.project_list.itemWidget(current)
            if current_card is not None:
                self.style_project_card(current_card, True)

    def refresh_papers(self):
        papers = self.library.list_papers(self.current_project_id)
        sort_mode = self.sort_combo.currentData()
        if sort_mode == "title":
            papers.sort(key=title_sort_key)
        elif sort_mode == "publication":
            papers.sort(key=publication_sort_key)
        elif sort_mode == "author":
            papers.sort(key=author_sort_key)
        elif sort_mode == "reading_status":
            papers.sort(key=reading_status_sort_key)
        else:
            papers.sort(key=created_sort_key, reverse=True)
        paper_suffix = ""
        if len(papers) != 1:
            paper_suffix = "s"
        self.paper_count_label.setText(str(len(papers)) + " paper" + paper_suffix)

        self.paper_list.blockSignals(True)
        self.paper_list.clear()
        target_row = -1
        for index, paper in enumerate(papers):
            item = QListWidgetItem(paper_card_label(paper))
            item.setData(Qt.ItemDataRole.UserRole, paper["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, paper)
            self.paper_list.addItem(item)
            if paper["id"] == self.current_paper_id:
                target_row = index
        self.paper_list.blockSignals(False)

        if target_row < 0 and papers:
            target_row = 0

        if target_row >= 0:
            self.paper_list.setCurrentRow(target_row)
        else:
            self.current_paper_id = None
            self.render_empty_detail()

    def select_project(self, item, previous_item=None):
        if not item:
            return
        self.save_pending_notes()
        self.current_project_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_paper_id = None
        self.add_paper_button.setEnabled(True)
        self.project_notes_button.setEnabled(True)
        self.paper_panel.set_drop_enabled(True)
        self.paper_list.set_drop_enabled(True)
        self.refresh_papers()

    def select_paper(self, item, previous_item=None):
        self.save_pending_notes()
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
        paper = self.repair_missing_pdf_metadata(paper)
        self.render_paper_detail(paper)

    def repair_missing_pdf_metadata(self, paper):
        """Repair one legacy local record whose abstract was not extracted."""
        file_path = paper.get("file_path", "")
        if paper.get("abstract") or not file_path:
            return paper
        attempt_key = (self.workspace_manager.current(), paper["id"])
        if attempt_key in self.pdf_metadata_attempted:
            return paper
        self.pdf_metadata_attempted.add(attempt_key)
        try:
            refreshed = self.library.refresh_pdf_metadata(paper["id"])
        except Exception:
            return paper
        if refreshed.get("abstract"):
            self.statusBar().showMessage(
                "Recovered abstract from the local PDF",
                5000,
            )
        return refreshed

    def render_empty_detail(self):
        self.detail_title.setText("Select a paper")
        self.render_reading_controls()
        self.tags_button.setEnabled(False)
        self.tags_button.setText("+ Add tags")
        self.paper_tags_label.clear()
        self.paper_tags_label.setVisible(False)
        self.detail_meta.setText("")
        self.abstract_text.setPlainText("")
        self.task_text.setPlainText("")
        self.methodology_text.setPlainText("")
        self.notes_save_timer.stop()
        self.notes_paper_id = None
        self.notes_dirty = False
        self.notes_editor.blockSignals(True)
        self.notes_editor.setPlainText("")
        self.notes_editor.blockSignals(False)
        self.notes_editor.setEnabled(False)
        self.notes_status_label.setText("Select a paper to add notes.")
        self.task_label.setVisible(False)
        self.task_text.setVisible(False)
        self.methodology_label.setVisible(False)
        self.methodology_text.setVisible(False)
        self.assistant_status.setText("")
        self.copy_paper_button.setText("Copy to project…")
        self.copy_paper_button.setEnabled(False)
        self.delete_paper_button.setEnabled(False)
        self.update_paper_link_controls(None)
        self.render_pdf_state()

    def render_paper_detail(self, paper):
        self.render_reading_controls(paper)
        self.render_paper_tags(paper.get("tags", []))
        self.detail_title.setText(
            latex_to_html(paper.get("title", "Untitled paper"))
        )
        metadata = []
        if paper.get("authors"):
            metadata.append(paper["authors"])
        if paper.get("conference"):
            metadata.append("Published in " + paper["conference"])
        if paper.get("year"):
            metadata.append(str(paper["year"]))
        if paper.get("doi"):
            metadata.append("DOI: " + paper["doi"])
        citation_count = paper.get("citation_count", 0) or 0
        if citation_count == 1:
            metadata.append("1 citation (estimate)")
        elif citation_count > 1:
            metadata.append(str(citation_count) + " citations (estimate)")
        if paper.get("metadata_source"):
            metadata.append("Metadata: " + paper["metadata_source"])
        self.detail_meta.setText(latex_to_plain_text(" · ".join(metadata)))
        self.render_abstract_text(paper.get("abstract", ""))
        self.load_paper_notes(paper)
        self.task_text.setPlainText(
            latex_to_plain_text(paper.get("task", ""))
        )
        self.methodology_text.setPlainText(
            latex_to_plain_text(paper.get("methodology", ""))
        )
        has_task = bool(paper.get("task"))
        has_methodology = bool(paper.get("methodology"))
        self.task_label.setVisible(has_task)
        self.task_text.setVisible(has_task)
        self.methodology_label.setVisible(has_methodology)
        self.methodology_text.setVisible(has_methodology)

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
        project = self.library.get_project(paper["project_id"])
        if project and project.get("kind") == "scrapbook":
            self.copy_paper_button.setText("Move to project…")
            self.copy_paper_button.setToolTip(
                "Move this paper and its PDF out of temporary ScrapBook."
            )
        else:
            self.copy_paper_button.setText("Copy to project…")
            self.copy_paper_button.setToolTip(
                "Create independent copies in other projects."
            )
        self.copy_paper_button.setEnabled(True)
        self.delete_paper_button.setEnabled(True)

        self.render_pdf_state(paper)
        self.update_paper_link_controls(paper)

    def render_abstract_text(self, value):
        """Render an abstract with the active reading-font preference."""
        font_family = ""
        if self.dyslexic_font_enabled:
            font_family = self.opendyslexic_family
        self.abstract_text.setHtml(
            comfortable_abstract_html(value, font_family)
        )

    def toggle_abstract_font(self, enabled):
        """Persist and apply the OpenDyslexic abstract preference."""
        self.dyslexic_font_enabled = bool(enabled)
        self.workspace_manager.set_dyslexic_font(
            self.dyslexic_font_enabled
        )
        if self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        self.render_abstract_text(paper.get("abstract", ""))

    def load_paper_notes(self, paper):
        """Load one paper's manual notes without scheduling a save."""
        if self.notes_paper_id == paper["id"] and self.notes_dirty:
            return
        self.notes_save_timer.stop()
        self.notes_paper_id = paper["id"]
        self.notes_dirty = False
        self.notes_editor.blockSignals(True)
        self.notes_editor.setPlainText(paper.get("notes", ""))
        self.notes_editor.blockSignals(False)
        self.notes_editor.setEnabled(True)
        self.notes_status_label.setText("Saved automatically")

    def schedule_notes_save(self):
        """Debounce note writes while the user is typing."""
        if self.notes_paper_id is None:
            return
        self.notes_dirty = True
        self.notes_status_label.setText("Saving…")
        self.notes_save_timer.start()

    def save_pending_notes(self):
        """Persist the current note immediately when needed."""
        self.notes_save_timer.stop()
        if self.notes_paper_id is None or not self.notes_dirty:
            return
        try:
            self.library.update_paper_notes(
                self.notes_paper_id,
                self.notes_editor.toPlainText(),
            )
        except ValueError:
            self.notes_status_label.setText("Notes could not be saved")
            return
        self.notes_dirty = False
        self.notes_status_label.setText("Saved")

    def render_pdf_state(self, paper=None):
        self.save_pending_reader_state()
        self.loading_pdf = True
        self.reader_paper_id = None
        self.reader_dirty = False
        self.pdf_document.close()
        self.pdf_toolbar.setVisible(False)
        self.pdf_comments_panel.setVisible(False)
        self.pdf_comments_panel.set_context(self.library, None)
        self.update_pdf_page_controls()
        self.pdf_action_status.setText("")
        if paper is None:
            self.loading_pdf = False
            self.pdf_stack.setCurrentWidget(self.pdf_empty_panel)
            self.pdf_status_label.setText("Select a paper")
            self.pdf_empty_copy.setText(
                "Choose a paper from the library to view or add its PDF."
            )
            self.pdf_source_note.setVisible(False)
            self.pdf_primary_button.setVisible(False)
            self.pdf_choose_button.setVisible(False)
            self.pdf_paste_button.setVisible(False)
            self.pdf_source_button.setVisible(False)
            return

        paper = self.library.get_paper(paper["id"]) or paper
        file_path = paper.get("file_path", "")
        if file_path and os.path.exists(file_path):
            self.pdf_document.load(file_path)
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            restore_zoom_choice(self.pdf_zoom_combo, paper.get("reader_zoom", "fit_width"))
            self.change_pdf_zoom()
            page_count = self.pdf_document.pageCount()
            if page_count > 0:
                page = max(min(paper.get("last_page", 0), page_count - 1), 0)
                self.pdf_view.pageNavigator().jump(page, QPointF())
                self.reader_paper_id = paper["id"]
            self.loading_pdf = False
            self.pdf_toolbar.setVisible(True)
            self.update_pdf_page_controls()
            self.pdf_stack.setCurrentWidget(self.pdf_view)
            self.pdf_comments_panel.set_context(
                self.library,
                paper["id"],
            )
            self.pdf_comments_panel.setVisible(
                self.pdf_comments_button.isChecked()
            )
            if self.detail_tabs.currentWidget() == self.pdf_tab:
                self.detail_tab_changed(self.detail_tabs.indexOf(self.pdf_tab))
            return

        self.loading_pdf = False
        self.pdf_stack.setCurrentWidget(self.pdf_empty_panel)
        self.pdf_choose_button.setVisible(True)
        self.pdf_choose_button.setEnabled(True)
        if self.offline_mode:
            self.pdf_status_label.setText("Add a local PDF")
            self.pdf_empty_copy.setText(
                "Online lookup is unavailable while offline. Choose a PDF "
                "from your computer; online options return when reconnected."
            )
            self.pdf_source_note.setVisible(False)
            self.pdf_primary_button.setVisible(False)
            self.pdf_paste_button.setVisible(False)
            self.pdf_source_button.setVisible(False)
        elif paper.get("pdf_url"):
            self.pdf_status_label.setText("PDF not downloaded")
            self.pdf_empty_copy.setText(
                "An open-access PDF link is available. Download it to read, "
                "search, and annotate it inside Corpus Cabinet."
            )
            source = paper.get("metadata_source") or "an online source"
            self.pdf_source_note.setText(
                "Open-access copy found via " + source
            )
            self.pdf_source_note.setVisible(True)
            self.pdf_primary_button.setText("Download PDF")
            self.pdf_primary_button.setVisible(True)
            self.pdf_paste_button.setVisible(True)
            self.pdf_source_button.setVisible(
                bool(paper.get("external_url") or paper.get("pdf_url"))
            )
        else:
            self.pdf_status_label.setText("Add a PDF to this paper")
            self.pdf_empty_copy.setText(
                "Corpus Cabinet does not have a direct PDF link yet. Search "
                "open-access sources or attach your own copy."
            )
            self.pdf_source_note.setVisible(False)
            self.pdf_primary_button.setText("Find online copy")
            self.pdf_primary_button.setVisible(True)
            self.pdf_paste_button.setVisible(True)
            self.pdf_source_button.setVisible(bool(paper.get("external_url")))

    def detail_tab_changed(self, index):
        """Only count a paper as opened when its local PDF reader is used."""
        reader_visible = self.page_stack.currentWidget() == self.library_page
        if index == self.detail_tabs.indexOf(self.pdf_tab) and reader_visible and self.reader_paper_id is not None and not self.loading_pdf:
            self.reader_dirty = True
        self.save_pending_reader_state()

    def schedule_reader_save(self, page=None):
        if self.loading_pdf or self.reader_paper_id is None:
            return
        if self.detail_tabs.currentWidget() != self.pdf_tab:
            return
        if self.page_stack.currentWidget() != self.library_page:
            return
        self.reader_dirty = True
        self.reader_save_timer.start()

    def save_pending_reader_state(self):
        """Flush the active PDF's position before switching papers or libraries."""
        self.reader_save_timer.stop()
        if self.loading_pdf or self.reader_paper_id is None or not self.reader_dirty:
            return
        if self.pdf_document.pageCount() <= 0:
            return
        try:
            self.library.save_reader_state(
                self.reader_paper_id,
                self.pdf_view.pageNavigator().currentPage(),
                self.pdf_zoom_combo.currentData(),
            )
        except ValueError:
            self.reader_dirty = False
            return
        except Exception:
            self.statusBar().showMessage("Reading position could not be saved", 5000)
            return
        self.reader_dirty = False

    def update_pdf_page_controls(self, value=None):
        """Show the visible page and enable valid navigation actions."""
        page_count = self.pdf_document.pageCount()
        current_page = self.pdf_view.pageNavigator().currentPage()
        if page_count <= 0:
            self.pdf_page_label.setText("Page - of -")
            self.pdf_previous_button.setEnabled(False)
            self.pdf_next_button.setEnabled(False)
            self.pdf_fullscreen_button.setEnabled(False)
            self.pdf_zoom_combo.setEnabled(False)
            self.pdf_zoom_out_button.setEnabled(False)
            self.pdf_zoom_in_button.setEnabled(False)
            return
        if current_page < 0:
            current_page = 0
        self.pdf_page_label.setText(
            "Page " + str(current_page + 1) + " of " + str(page_count)
        )
        self.pdf_previous_button.setEnabled(current_page > 0)
        self.pdf_next_button.setEnabled(current_page < page_count - 1)
        self.pdf_fullscreen_button.setEnabled(True)
        self.pdf_zoom_combo.setEnabled(True)
        self.pdf_zoom_out_button.setEnabled(True)
        self.pdf_zoom_in_button.setEnabled(True)

    def go_to_previous_pdf_page(self):
        """Move the embedded reader to the preceding page."""
        navigator = self.pdf_view.pageNavigator()
        target_page = navigator.currentPage() - 1
        if target_page >= 0:
            navigator.jump(target_page, QPointF())

    def go_to_next_pdf_page(self):
        """Move the embedded reader to the following page."""
        navigator = self.pdf_view.pageNavigator()
        target_page = navigator.currentPage() + 1
        if target_page < self.pdf_document.pageCount():
            navigator.jump(target_page, QPointF())

    def sync_custom_pdf_zoom(self, zoom):
        self.pdf_zoom_combo.blockSignals(True)
        restore_zoom_choice(self.pdf_zoom_combo, zoom)
        self.pdf_zoom_combo.blockSignals(False)
        self.schedule_reader_save()

    def change_pdf_zoom(self, index=None):
        """Apply the selected fit or fixed zoom mode."""
        zoom = self.pdf_zoom_combo.currentData()
        if zoom == "fit_width":
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        elif zoom == "fit_page":
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView)
        elif zoom is not None:
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.pdf_view.setZoomFactor(float(zoom))
        self.schedule_reader_save()

    def open_fullscreen_pdf(self):
        """Open the current local PDF in a distraction-free reader."""
        if self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        file_path = paper.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            return
        current_page = self.pdf_view.pageNavigator().currentPage()
        self.save_pending_reader_state()
        dialog = FullscreenPdfDialog(
            self,
            file_path,
            paper.get("title", "PDF reader"),
            current_page,
            self.library,
            paper["id"],
            self.pdf_zoom_combo.currentData(),
        )
        dialog.showFullScreen()
        dialog.exec()
        current_page = dialog.current_page()
        self.pdf_comments_panel.refresh()
        restore_zoom_choice(self.pdf_zoom_combo, dialog.zoom_combo.currentData())
        if current_page >= 0:
            self.pdf_view.pageNavigator().jump(current_page, QPointF())
        self.refresh_home()

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
        if paper and paper.get("doi"):
            self.open_source_button.setText("Open published version")
        else:
            self.open_source_button.setText("Open source")
        self.scholar_button.setEnabled(
            title_available and not self.offline_mode
        )
        self.bibtex_button.setEnabled(paper is not None and title_available)

    def perform_pdf_primary_action(self):
        if self.current_paper_id is None or self.offline_mode:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        if paper.get("pdf_url"):
            self.download_pdf_for_current_paper()
        else:
            self.find_current_pdf()

    def choose_pdf_for_current_paper(self):
        if self.current_paper_id is None:
            return
        path, accepted = QFileDialog.getOpenFileName(
            self,
            "Choose a PDF for this paper",
            "",
            "PDF files (*.pdf)",
        )
        if not accepted or not path:
            return
        self.start_pdf_attach(path)

    def paste_pdf_url(self):
        if self.current_paper_id is None or self.offline_mode:
            return
        url, accepted = QInputDialog.getText(
            self,
            "Paste PDF URL",
            "Direct PDF URL:",
        )
        url = url.strip()
        parsed_url = QUrl(url)
        if not accepted or not url:
            return
        if not parsed_url.isValid() or parsed_url.scheme() not in ("http", "https"):
            QMessageBox.warning(
                self,
                "Invalid PDF URL",
                "Enter a complete http:// or https:// URL.",
            )
            return
        paper = self.library.get_paper(self.current_paper_id)
        result = stored_paper_result(paper, url)
        self.start_pdf_download(result, paper["project_id"], paper["id"])

    def find_current_pdf(self):
        if self.current_paper_id is None or self.offline_mode:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        projects = self.library.list_projects()
        dialog = DiscoveryDialog(
            self,
            projects,
            paper["project_id"],
            paper["id"],
            self.search_config,
            self.apply_online_metadata,
            self.add_online_paper,
            self.download_pdf_to_current_paper,
            self.create_project_from_discovery,
            self.discovery_context,
            self.save_discovery_context,
            pdf_target=True,
            initial_query=paper.get("title", ""),
        )
        dialog.exec()

    def open_current_source(self):
        if self.offline_mode or self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        url = ""
        if paper.get("doi"):
            url = "https://doi.org/" + paper["doi"]
        if not url:
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

    def open_bibtex_dialog(self):
        if self.current_paper_id is None:
            return
        paper = self.library.get_paper(self.current_paper_id)
        if not paper:
            return
        dialog = BibtexDialog(
            self,
            paper,
            self.search_config,
            self.offline_mode,
            self.save_paper_bibtex,
        )
        dialog.exec()

    def save_paper_bibtex(self, paper_id, bibtex):
        try:
            self.library.update_paper_bibtex(paper_id, bibtex)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot save BibTeX", str(error))
            return False
        self.statusBar().showMessage("Reviewed BibTeX saved", 5000)
        return True

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
        return project

    def create_project_from_home(self):
        project = self.create_project()
        if project:
            self.show_library()

    def rename_project(self):
        if self.current_project_id is None:
            return
        project = self.library.get_project(self.current_project_id)
        if not project:
            return
        if project.get("kind") == "scrapbook":
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
        project_id = item.data(Qt.ItemDataRole.UserRole)
        project = self.library.get_project(project_id)
        menu = QMenu(self)
        if project and project.get("kind") == "scrapbook":
            information_action = menu.addAction("Built-in temporary folder")
            information_action.setEnabled(False)
            menu.exec(self.project_list.mapToGlobal(position))
            return
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
        if project.get("kind") == "scrapbook":
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

    def open_add_paper(self):
        """Open one project-scoped entry point for local and online papers."""
        if self.current_project_id is None:
            return
        project = self.library.get_project(self.current_project_id)
        if not project:
            return
        dialog = AddPaperDialog(
            self,
            project,
            not self.offline_mode,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.choice == "upload":
            self.choose_pdfs(project["id"])
        elif dialog.choice == "online":
            self.discover_papers(project["id"])

    def choose_pdfs(self, project_id=None):
        """Choose local PDFs, then confirm all destination projects."""
        if project_id is None:
            project_id = self.current_project_id
        if project_id is None:
            return
        paths, accepted = QFileDialog.getOpenFileNames(
            self,
            "Add PDFs",
            "",
            "PDF files (*.pdf)",
        )
        if not accepted or not paths:
            return
        self.confirm_pdf_destinations(
            paths,
            project_id,
            "Add PDFs to projects",
            (
                "Choose every project that should receive an independent "
                "record and PDF copy."
            ),
        )

    def confirm_dropped_pdfs(self, paths):
        """Confirm a paper-pane drop and optionally add more destinations."""
        project_id = self.current_project_id
        project = self.library.get_project(project_id)
        if not project or not paths:
            return
        names = []
        for path in paths[:3]:
            names.append(os.path.basename(path))
        file_summary = ", ".join(names)
        if len(paths) > 3:
            file_summary += " and " + str(len(paths) - 3) + " more"
        prompt = (
            "Add " + file_summary + "?\n\n" + project["name"]
            + " is selected because it is open now. Choose any "
            "additional projects that should receive independent copies."
        )
        self.confirm_pdf_destinations(
            paths,
            project_id,
            "Add dropped PDFs",
            prompt,
        )

    def confirm_pdf_destinations(self, paths, project_id, title, prompt):
        """Show the shared multi-project confirmation for local PDFs."""
        projects = self.library.list_projects()
        scrapbook_ids = []
        for project in projects:
            if project.get("kind") == "scrapbook":
                scrapbook_ids.append(project["id"])
        dialog = ProjectSelectionDialog(
            self,
            projects,
            selected_ids=[project_id],
            exclusive_ids=scrapbook_ids,
            title=title,
            prompt=prompt,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        return self.start_import(paths, dialog.selected_project_ids())

    def discover_papers(self, project_id=None):
        """Search online with one project preselected as the destination."""
        if self.offline_mode:
            QMessageBox.information(
                self,
                "Working offline",
                "Online paper search is unavailable while working offline.",
            )
            return
        if project_id is None:
            project_id = self.current_project_id

        projects = self.library.list_projects()
        dialog = DiscoveryDialog(
            self,
            projects,
            project_id,
            self.current_paper_id,
            self.search_config,
            self.apply_online_metadata,
            self.add_online_paper,
            self.download_online_paper,
            self.create_project_from_discovery,
            self.discovery_context,
            self.save_discovery_context,
        )
        dialog.exec()

    def create_project_from_discovery(self, name):
        selected_project_id = self.current_project_id
        selected_paper_id = self.current_paper_id
        try:
            project = self.library.create_project(name)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot create project", str(error))
            return None
        self.refresh_projects(selected_project_id)
        if selected_paper_id is not None:
            self.select_paper_by_id(selected_paper_id)
        return project

    def discovery_context(self, project_id):
        return project_search_context(self.library, project_id)

    def save_discovery_context(self, project_id, context):
        project = self.library.get_project(project_id)
        if project and project.get("kind") == "scrapbook":
            return
        self.library.update_project_search_context(project_id, context)

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

    def add_online_paper(self, result, project_ids):
        project_ids = normalize_project_ids(project_ids)
        if not project_ids:
            return False
        papers = []
        try:
            project_ids = self.library.validate_destination_projects(
                project_ids
            )
            duplicates = self.library.find_duplicate_papers(
                project_ids,
                result,
            )
            if duplicates and not self.confirm_duplicate_papers(duplicates):
                return False
            for project_id in project_ids:
                papers.append(
                    self.library.create_paper_from_search(
                        project_id,
                        result,
                    )
                )
        except ValueError as error:
            for paper in papers:
                self.library.delete_paper(paper["id"])
            QMessageBox.warning(self, "Cannot add paper", str(error))
            return False

        self.refresh_projects(self.current_project_id)
        current_copy = None
        for paper in papers:
            if paper["project_id"] == self.current_project_id:
                current_copy = paper
                break
        if current_copy is not None:
            self.refresh_papers()
            self.select_paper_by_id(current_copy["id"])
        self.refresh_home()
        self.statusBar().showMessage(
            "Citation copied to " + str(len(papers)) + " project(s)",
            5000,
        )
        return True

    def download_online_paper(self, result, project_ids):
        if self.offline_mode:
            return False
        project_ids = normalize_project_ids(project_ids)
        if not project_ids:
            return False
        try:
            project_ids = self.library.validate_destination_projects(
                project_ids
            )
            duplicates = self.library.find_duplicate_papers(
                project_ids,
                result,
            )
            if duplicates and not self.confirm_duplicate_papers(duplicates):
                return False
        except ValueError as error:
            QMessageBox.warning(self, "Cannot add paper", str(error))
            return False
        if not result.get("pdf_url"):
            return False

        return self.start_pdf_download(result, project_ids)

    def confirm_duplicate_papers(self, duplicates):
        """Warn about destination duplicates and allow an intentional copy."""
        lines = []
        for duplicate in duplicates[:8]:
            source_file = duplicate.get("source_file", "")
            prefix = ""
            if source_file:
                prefix = source_file + ": "
            lines.append(
                "• "
                + prefix
                + latex_to_plain_text(duplicate.get("title", "Untitled paper"))
                + " — already in "
                + duplicate.get("project_name", "this project")
                + " ("
                + duplicate.get("match_reason", "possible match")
                + ")"
            )
        if len(duplicates) > 8:
            lines.append("• " + str(len(duplicates) - 8) + " more match(es)")
        message = (
            "Corpus Cabinet found possible duplicate paper records:\n\n"
            + "\n".join(lines)
            + "\n\nAdd another independent copy anyway?"
        )
        answer = QMessageBox.question(
            self,
            "Possible duplicate paper",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def download_pdf_for_current_paper(self):
        if self.current_paper_id is None or self.offline_mode:
            return False
        paper = self.library.get_paper(self.current_paper_id)
        if not paper or not paper.get("pdf_url"):
            return False
        result = stored_paper_result(paper)
        return self.start_pdf_download(
            result,
            paper["project_id"],
            paper["id"],
        )

    def download_pdf_to_current_paper(self, result, project_id):
        if self.current_paper_id is None or self.offline_mode:
            return False
        paper = self.library.get_paper(self.current_paper_id)
        if not paper or not result.get("pdf_url"):
            return False
        return self.start_pdf_download(
            result,
            paper["project_id"],
            paper["id"],
        )

    def start_pdf_download(self, result, project_ids, paper_id=None):
        if self.offline_mode:
            return False
        project_ids = normalize_project_ids(project_ids)
        if not project_ids or not result.get("pdf_url"):
            return False

        self.online_controls_busy = True
        self.update_search_controls()
        self.offline_mode_checkbox.setEnabled(False)
        self.project_list.setEnabled(False)
        self.paper_list.setEnabled(False)
        self.add_paper_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        if paper_id is not None:
            self.pdf_primary_button.setEnabled(False)
            self.pdf_choose_button.setEnabled(False)
            self.pdf_action_status.setText("Downloading and validating PDF…")
        self.statusBar().showMessage("Downloading and importing PDF…")

        task = DownloadTask(
            self.library.path,
            project_ids,
            result,
            self.search_config,
            paper_id,
        )
        task.signals.finished.connect(self.download_finished)
        task.signals.failed.connect(self.download_failed)
        self.thread_pool.start(task)
        return True

    def start_pdf_attach(self, path):
        if self.current_paper_id is None:
            return False
        self.online_controls_busy = True
        self.update_search_controls()
        self.offline_mode_checkbox.setEnabled(False)
        self.project_list.setEnabled(False)
        self.paper_list.setEnabled(False)
        self.add_paper_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        self.pdf_primary_button.setEnabled(False)
        self.pdf_choose_button.setEnabled(False)
        self.pdf_action_status.setText("Adding and indexing PDF…")
        self.statusBar().showMessage("Adding and indexing PDF…")

        task = AttachTask(self.library.path, self.current_paper_id, path)
        task.signals.finished.connect(self.attach_finished)
        task.signals.failed.connect(self.download_failed)
        self.thread_pool.start(task)
        return True

    def restore_download_controls(self):
        self.online_controls_busy = False
        self.offline_mode_checkbox.setEnabled(True)
        self.project_list.setEnabled(True)
        self.paper_list.setEnabled(True)
        self.add_paper_button.setEnabled(
            self.current_project_id is not None
        )
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.pdf_primary_button.setEnabled(True)
        self.pdf_choose_button.setEnabled(True)
        self.update_search_controls()

    def download_finished(self, papers):
        self.restore_download_controls()
        attached_to_existing = False
        for paper in papers:
            if paper.get("id") == self.current_paper_id:
                attached_to_existing = True
                break
        if attached_to_existing:
            message = "PDF downloaded and attached"
        else:
            message = (
                "PDF downloaded and copied to "
                + str(len(papers))
                + " project(s)"
            )
        self.statusBar().showMessage(message, 5000)
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        for paper in papers:
            if paper.get("project_id") == self.current_project_id:
                self.select_paper_by_id(paper["id"])
                break
        self.refresh_home()

    def attach_finished(self, paper):
        self.restore_download_controls()
        self.statusBar().showMessage("PDF added and indexed", 5000)
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        self.select_paper_by_id(paper["id"])
        self.refresh_home()

    def download_failed(self, message):
        self.restore_download_controls()
        self.statusBar().clearMessage()
        self.pdf_action_status.setText("")
        QMessageBox.warning(self, "PDF download failed", message)

    def start_import(
        self,
        paths,
        project_ids=None,
        allow_duplicates=False,
    ):
        if project_ids is None:
            project_ids = [self.current_project_id]
        project_ids = normalize_project_ids(project_ids)
        if not project_ids:
            return False
        self.online_controls_busy = True
        self.add_paper_button.setEnabled(False)
        self.update_search_controls()
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        self.statusBar().showMessage("Importing " + str(len(paths)) + " PDF(s)…")
        task = ImportTask(
            self.library.path,
            project_ids,
            paths,
            allow_duplicates,
        )
        task.signals.finished.connect(self.import_finished)
        task.signals.failed.connect(self.import_failed)
        task.signals.duplicatesFound.connect(self.import_duplicates_found)
        self.thread_pool.start(task)
        return True

    def import_duplicates_found(self, payload):
        """Ask before restarting a local import with duplicates allowed."""
        self.online_controls_busy = False
        self.add_paper_button.setEnabled(True)
        self.update_search_controls()
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.statusBar().clearMessage()
        if not self.confirm_duplicate_papers(payload["duplicates"]):
            return
        self.start_import(
            payload["paths"],
            payload["project_ids"],
            True,
        )

    def import_finished(self, papers):
        self.online_controls_busy = False
        self.add_paper_button.setEnabled(True)
        self.update_search_controls()
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.statusBar().showMessage(
            "Created " + str(len(papers)) + " imported paper copy/copies",
            5000,
        )
        selected_id = None
        for paper in papers:
            if paper.get("project_id") == self.current_project_id:
                selected_id = paper["id"]
                break
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        if selected_id is not None:
            self.select_paper_by_id(selected_id)
        self.refresh_home()

    def import_failed(self, message):
        self.online_controls_busy = False
        self.add_paper_button.setEnabled(True)
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

    def copy_current_paper(self):
        if self.current_paper_id is None:
            return
        self.save_pending_notes()
        source_paper_id = self.current_paper_id
        source_project = self.library.get_project(self.current_project_id)
        if not source_project:
            return
        projects = []
        for project in self.library.list_projects():
            if project.get("kind") != "scrapbook":
                projects.append(project)

        if source_project.get("kind") == "scrapbook":
            if not projects:
                QMessageBox.information(
                    self,
                    "No destination project",
                    "Create a project before moving this ScrapBook paper.",
                )
                return
            project_names = []
            for project in projects:
                project_names.append(project["name"])
            selected_name, accepted = QInputDialog.getItem(
                self,
                "Move paper from ScrapBook",
                "Move to:",
                project_names,
                0,
                False,
            )
            if not accepted:
                return
            target_project = None
            for project in projects:
                if project["name"] == selected_name:
                    target_project = project
                    break
            if target_project is None:
                return
            try:
                source_paper = self.library.get_paper(source_paper_id)
                duplicates = self.library.find_duplicate_papers(
                    [target_project["id"]],
                    source_paper,
                )
                if duplicates and not self.confirm_duplicate_papers(duplicates):
                    return
                moved_paper = self.library.move_scrapbook_paper(
                    source_paper_id,
                    target_project["id"],
                )
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Cannot move paper", str(error))
                return
            self.refresh_projects(target_project["id"])
            self.select_paper_by_id(moved_paper["id"])
            self.refresh_home()
            self.statusBar().showMessage(
                "Paper moved from ScrapBook to " + target_project["name"],
                5000,
            )
            return

        if len(projects) < 2:
            QMessageBox.information(
                self,
                "No destination project",
                "Create another project before copying this paper.",
            )
            return
        dialog = ProjectSelectionDialog(
            self,
            projects,
            selected_ids=[self.current_project_id],
            locked_ids=[self.current_project_id],
            title="Copy paper to projects",
            prompt=(
                "Choose the projects that should receive independent copies. "
                "The current project is shown as already containing the paper."
            ),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        project_ids = []
        for project_id in dialog.selected_project_ids():
            if project_id != self.current_project_id:
                project_ids.append(project_id)
        if not project_ids:
            return
        try:
            source_paper = self.library.get_paper(source_paper_id)
            duplicates = self.library.find_duplicate_papers(
                project_ids,
                source_paper,
            )
            if duplicates and not self.confirm_duplicate_papers(duplicates):
                return
            copies = self.library.copy_paper_to_projects(
                source_paper_id,
                project_ids,
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot copy paper", str(error))
            return
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()
        self.select_paper_by_id(source_paper_id)
        self.refresh_home()
        self.statusBar().showMessage(
            "Paper copied to " + str(len(copies)) + " project(s)",
            5000,
        )

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
        self.notes_save_timer.stop()
        if self.notes_paper_id == self.current_paper_id:
            self.notes_paper_id = None
            self.notes_dirty = False
        self.library.delete_paper(self.current_paper_id)
        self.current_paper_id = None
        self.refresh_projects(self.current_project_id)
        self.refresh_papers()

    def open_library(self):
        selected_path = QFileDialog.getExistingDirectory(
            self,
            "Switch Corpus Cabinet library folder",
            self.library.path,
        )
        if not selected_path:
            return
        self.save_pending_notes()
        self.save_pending_reader_state()
        self.workspace_manager.activate(selected_path)
        self.reader_paper_id = None
        self.current_project_id = None
        self.current_paper_id = None
        self.refresh_library()

    def closeEvent(self, event):
        """Flush pending notes and reading position before the window closes."""
        self.save_pending_notes()
        self.save_pending_reader_state()
        super().closeEvent(event)


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
    apply_light_theme(application)
    application.setApplicationName("Corpus Cabinet")
    application.setOrganizationName("Corpus Cabinet")
    signal.signal(signal.SIGINT, stop_application)
    signal_timer = QTimer(application)
    signal_timer.timeout.connect(allow_python_signals)
    signal_timer.start(250)
    config_path, default_library = default_paths()
    manager = WorkspaceManager(config_path, default_library)
    window = MainWindow(manager)
    application.aboutToQuit.connect(window.save_pending_notes)
    application.aboutToQuit.connect(window.save_pending_reader_state)
    window.show()
    return application.exec()
