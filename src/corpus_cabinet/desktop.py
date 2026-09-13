"""Run the native Corpus Cabinet desktop application.

The application reads a workspace registry and SQLite library, reads selected
PDFs, and writes project metadata, paper metadata, extracted text, and copied
PDFs into the active library folder. It uses Qt Widgets and Qt PDF directly;
there is no browser or local HTTP server.
"""

import html
import os
import re
import signal
import sys
import tempfile

from PySide6.QtCore import (
    QObject,
    QRunnable,
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
    QFontDatabase,
    QKeySequence,
    QPalette,
    QShortcut,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from dotenv import load_dotenv

from corpus_cabinet.citations import fetch_doi_bibtex, generate_bibtex
from corpus_cabinet.downloads import download_pdf
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

    finished = Signal(dict)
    failed = Signal(str)


class DownloadTask(QRunnable):
    """Download one direct PDF, import it, and apply its online metadata."""

    def __init__(self, library_path, project_id, result, config, paper_id=None):
        super().__init__()
        self.library_path = library_path
        self.project_id = project_id
        self.result = result
        self.config = config
        self.paper_id = paper_id
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
            if self.paper_id is None:
                paper = library.import_pdf(self.project_id, temporary_path)
            else:
                paper = library.attach_pdf(self.paper_id, temporary_path)
            paper = library.update_paper_metadata(paper["id"], self.result)
            self.signals.finished.emit(paper)
        except Exception as error:
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
            self.setWindowTitle("Search online")
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
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.start_search)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        destination_layout = QHBoxLayout()
        if self.pdf_target:
            destination_layout.addWidget(QLabel("Selected paper is in"))
        else:
            destination_layout.addWidget(QLabel("Add to"))
        self.project_combo = QComboBox()
        selected_index = 0
        for index, project in enumerate(projects):
            self.project_combo.addItem(project["name"], project["id"])
            if project["id"] == project_id:
                selected_index = index
        self.project_combo.setCurrentIndex(selected_index)
        self.project_combo.setEnabled(not self.pdf_target)
        destination_layout.addWidget(self.project_combo)
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
            "Search online sources, then review a result before adding it."
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
            self.status_label.setText("Enter a paper title or identifier.")
            return

        context = self.context_input.text().strip()
        project_id = self.project_combo.currentData()
        if self.save_context_callback and project_id is not None:
            self.save_context_callback(project_id, context)
        self.search_button.setEnabled(False)
        self.query_input.setEnabled(False)
        self.project_combo.setEnabled(False)
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
        self.new_project_button.setEnabled(True)
        self.context_input.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Online search failed: " + message)

    def project_changed(self, index=None):
        project_id = self.project_combo.currentData()
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
        self.project_combo.setCurrentIndex(self.project_combo.count() - 1)
        self.status_label.setText("Created project " + project["name"] + ".")

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
            project_id = self.project_combo.currentData()
            if self.add_callback(result, project_id):
                self.status_label.setText(
                    "Citation saved to " + self.project_combo.currentText()
                )

    def download_selected(self):
        result = self.selected_result()
        if result and self.download_callback:
            project_id = self.project_combo.currentData()
            if self.download_callback(result, project_id):
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
        header_layout.addStretch()
        self.network_status_label = QLabel()
        self.network_status_label.setObjectName("networkStatus")
        header_layout.addWidget(self.network_status_label)
        self.discover_button = QPushButton("Add online")
        self.discover_button.setObjectName("discoverButton")
        self.discover_button.setToolTip(
            "Search scholarly sources or add a paper from a link (Cmd/Ctrl+K)"
        )
        self.online_add_menu = QMenu(self.discover_button)
        self.search_online_action = self.online_add_menu.addAction(
            "Search by title…"
        )
        self.search_online_action.triggered.connect(self.discover_papers)
        self.add_link_action = self.online_add_menu.addAction(
            "Add from link…"
        )
        self.add_link_action.triggered.connect(self.add_from_link)
        self.discover_button.setMenu(self.online_add_menu)
        header_layout.addWidget(self.discover_button)
        self.discover_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.discover_shortcut.activated.connect(self.discover_papers)
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
        splitter.addWidget(self.build_project_panel())
        splitter.addWidget(self.build_paper_panel())
        splitter.addWidget(self.build_detail_panel())
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

        recent_heading = QLabel("Continue a project")
        recent_heading.setObjectName("homeSectionHeading")
        layout.addWidget(recent_heading)
        self.home_recent_list = QListWidget()
        self.home_recent_list.setObjectName("homeRecentList")
        self.home_recent_list.setMaximumHeight(190)
        self.home_recent_list.itemClicked.connect(self.open_home_project)
        layout.addWidget(self.home_recent_list)
        layout.addStretch()
        return page

    def show_home(self):
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
        papers = self.library.list_papers()
        self.home_project_count.setText(str(len(projects)))
        self.home_paper_count.setText(str(len(papers)))

        self.home_recent_list.clear()
        for project in projects[:5]:
            paper_count = project.get("paper_count", 0)
            suffix = " papers"
            if paper_count == 1:
                suffix = " paper"
            label = project["name"] + "\n" + str(paper_count) + suffix
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            self.home_recent_list.addItem(item)
        if not projects:
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
            "QPushButton#discoverButton { min-height: 30px; border: 0; "
            "background: #6350aa; color: #ffffff; font-weight: 600; }"
            "QPushButton#discoverButton:hover { background: #574697; }"
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
            "QWidget#detailPanel { background: #ffffff; }"
            "QLabel#paneHeading { font-size: 13px; font-weight: 700; "
            "color: #202124; }"
            "QSplitter::handle { background: #dedfe2; width: 1px; }"
            "QListWidget#projectList, QListWidget#paperList { border: 0; "
            "outline: 0; background: transparent; }"
            "QListWidget#projectList::item { padding: 9px; margin-bottom: 3px; "
            "border-radius: 7px; color: #202124; }"
            "QListWidget#projectList::item:hover { background: #e9e9ec; }"
            "QListWidget#projectList::item:selected { background: #e8e3ff; "
            "color: #443381; }"
            "QListWidget#paperList::item { padding: 10px; margin-bottom: 5px; "
            "border: 1px solid #e0e1e5; border-radius: 7px; "
            "background: #ffffff; color: #202124; }"
            "QListWidget#paperList::item:hover { border-color: #bdbfc5; }"
            "QListWidget#paperList::item:selected { background: #eeeaff; "
            "color: #443381; border-color: #a99be0; }"
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

        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setSpacing(2)
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
        panel.setObjectName("paperPanel")
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
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Recently added", "created")
        self.sort_combo.addItem("Title A–Z", "title")
        self.sort_combo.addItem("Publication date", "publication")
        self.sort_combo.addItem("Author", "author")
        self.sort_combo.currentIndexChanged.connect(self.refresh_papers)
        heading_layout.addWidget(self.sort_combo)
        layout.addLayout(heading_layout)

        self.import_button = QPushButton("Import PDFs")
        self.import_button.setText("+ Import PDFs")
        self.import_button.clicked.connect(self.choose_pdfs)
        self.import_button.setEnabled(False)
        layout.addWidget(self.import_button)

        self.paper_list = QListWidget()
        self.paper_list.setObjectName("paperList")
        self.paper_list.setSpacing(2)
        self.paper_list.setWordWrap(True)
        self.paper_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.paper_list.currentItemChanged.connect(self.select_paper)
        layout.addWidget(self.paper_list)
        return panel

    def build_detail_panel(self):
        panel = QWidget()
        panel.setObjectName("detailPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 22, 24, 18)

        self.detail_title = QLabel("Select a paper")
        self.detail_title.setWordWrap(True)
        self.detail_title.setTextFormat(Qt.TextFormat.RichText)
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(self.detail_title)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.build_detail_tab(), "Details")
        pdf_panel = QWidget()
        pdf_layout = QVBoxLayout(pdf_panel)
        pdf_layout.setContentsMargins(14, 14, 14, 14)
        self.pdf_stack = QStackedWidget()
        self.pdf_empty_panel = self.build_pdf_empty_state()
        self.pdf_stack.addWidget(self.pdf_empty_panel)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_stack.addWidget(self.pdf_view)
        pdf_layout.addWidget(self.pdf_stack)
        self.detail_tabs.addTab(pdf_panel, "PDF")
        layout.addWidget(self.detail_tabs)

        self.delete_paper_button = QPushButton("Delete paper")
        self.delete_paper_button.setObjectName("deleteButton")
        self.delete_paper_button.clicked.connect(self.delete_current_paper)
        self.delete_paper_button.setEnabled(False)
        layout.addWidget(self.delete_paper_button)
        return panel

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
        self.detail_meta = QLabel()
        self.detail_meta.setWordWrap(True)
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
        link_layout.addStretch()
        layout.addLayout(link_layout)

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
        self.import_button.setEnabled(self.current_project_id is not None)

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
        if not hasattr(self, "discover_button"):
            return
        online_enabled = (
            not self.offline_mode and not self.online_controls_busy
        )
        self.discover_button.setEnabled(online_enabled)
        self.discover_shortcut.setEnabled(online_enabled)
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
            label = (
                project["name"] + "\n" + str(paper_count) + paper_suffix
            )
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
        self.refresh_home()

    def refresh_papers(self):
        papers = self.library.list_papers(self.current_project_id)
        sort_mode = self.sort_combo.currentData()
        if sort_mode == "title":
            papers.sort(key=title_sort_key)
        elif sort_mode == "publication":
            papers.sort(key=publication_sort_key)
        elif sort_mode == "author":
            papers.sort(key=author_sort_key)
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
            label = latex_to_plain_text(paper["title"])
            details = []
            if paper.get("authors"):
                details.append(first_author_label(paper["authors"]))
            if paper.get("year"):
                details.append(str(paper["year"]))
            if details:
                label += "\n" + " · ".join(details)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, paper["id"])
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
        self.task_label.setVisible(False)
        self.task_text.setVisible(False)
        self.methodology_label.setVisible(False)
        self.methodology_text.setVisible(False)
        self.assistant_status.setText("")
        self.delete_paper_button.setEnabled(False)
        self.update_paper_link_controls(None)
        self.render_pdf_state()

    def render_paper_detail(self, paper):
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

    def render_pdf_state(self, paper=None):
        self.pdf_document.close()
        self.pdf_action_status.setText("")
        if paper is None:
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

        file_path = paper.get("file_path", "")
        if file_path and os.path.exists(file_path):
            self.pdf_document.load(file_path)
            self.pdf_stack.setCurrentWidget(self.pdf_view)
            return

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

    def discover_papers(self):
        if self.offline_mode:
            QMessageBox.information(
                self,
                "Working offline",
                "Online paper search is unavailable while working offline.",
            )
            return

        projects = self.library.list_projects()
        dialog = DiscoveryDialog(
            self,
            projects,
            self.current_project_id,
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

    def add_from_link(self):
        if self.offline_mode:
            return
        url, accepted = QInputDialog.getText(
            self,
            "Add paper from link",
            "arXiv, DOI, or public project-page URL:",
        )
        url = url.strip()
        parsed_url = QUrl(url)
        if not accepted or not url:
            return
        if not parsed_url.isValid() or parsed_url.scheme() not in ("http", "https"):
            QMessageBox.warning(
                self,
                "Invalid paper link",
                "Enter a complete public http:// or https:// URL.",
            )
            return

        projects = self.library.list_projects()
        dialog = DiscoveryDialog(
            self,
            projects,
            self.current_project_id,
            self.current_paper_id,
            self.search_config,
            self.apply_online_metadata,
            self.add_online_paper,
            self.download_online_paper,
            self.create_project_from_discovery,
            self.discovery_context,
            self.save_discovery_context,
            initial_query=url,
        )
        dialog.setWindowTitle("Add paper from link")
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

    def add_online_paper(self, result, project_id):
        if project_id is None:
            return False
        try:
            paper = self.library.create_paper_from_search(
                project_id,
                result,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Cannot add paper", str(error))
            return False

        self.refresh_projects(self.current_project_id)
        if project_id == self.current_project_id:
            self.refresh_papers()
            self.select_paper_by_id(paper["id"])
        self.refresh_home()
        project = self.library.get_project(project_id)
        project_name = project["name"]
        self.statusBar().showMessage(
            "Citation saved to " + project_name,
            5000,
        )
        return True

    def download_online_paper(self, result, project_id):
        if self.offline_mode:
            return False
        if project_id is None:
            return False
        if not result.get("pdf_url"):
            return False

        return self.start_pdf_download(result, project_id)

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

    def start_pdf_download(self, result, project_id, paper_id=None):
        if self.offline_mode:
            return False
        if project_id is None or not result.get("pdf_url"):
            return False

        self.online_controls_busy = True
        self.update_search_controls()
        self.offline_mode_checkbox.setEnabled(False)
        self.project_list.setEnabled(False)
        self.paper_list.setEnabled(False)
        self.import_button.setEnabled(False)
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        if paper_id is not None:
            self.pdf_primary_button.setEnabled(False)
            self.pdf_choose_button.setEnabled(False)
            self.pdf_action_status.setText("Downloading and validating PDF…")
        self.statusBar().showMessage("Downloading and importing PDF…")

        task = DownloadTask(
            self.library.path,
            project_id,
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
        self.import_button.setEnabled(False)
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
        self.import_button.setEnabled(self.current_project_id is not None)
        self.new_project_button.setEnabled(True)
        self.open_library_button.setEnabled(True)
        self.pdf_primary_button.setEnabled(True)
        self.pdf_choose_button.setEnabled(True)
        self.update_search_controls()

    def download_finished(self, paper):
        self.restore_download_controls()
        self.statusBar().showMessage("PDF downloaded and added", 5000)
        project_id = paper.get("project_id")
        self.refresh_projects(self.current_project_id)
        if project_id == self.current_project_id:
            self.refresh_papers()
            self.select_paper_by_id(paper["id"])
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

    def start_import(self, paths):
        self.online_controls_busy = True
        self.import_button.setEnabled(False)
        self.update_search_controls()
        self.new_project_button.setEnabled(False)
        self.open_library_button.setEnabled(False)
        self.statusBar().showMessage("Importing " + str(len(paths)) + " PDF(s)…")
        task = ImportTask(self.library.path, self.current_project_id, paths)
        task.signals.finished.connect(self.import_finished)
        task.signals.failed.connect(self.import_failed)
        self.thread_pool.start(task)

    def import_finished(self, papers):
        self.online_controls_busy = False
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
        self.refresh_home()

    def import_failed(self, message):
        self.online_controls_busy = False
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
            "Switch Corpus Cabinet library folder",
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
    window.show()
    return application.exec()
