"""Display a local experimental Reader without changing PDF files.

Reads reader_cache/document.json and local PNG crops. Background conversion
writes that cache through an isolated, offline subprocess; this module never
uploads documents. Reader previews do not change the embedded PDF's position.
"""

import html
import json
import os
import re
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QByteArray, QObject, QPointF, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSplitter,
    QTextBrowser, QVBoxLayout, QWidget,
)

from corpus_cabinet.arxiv_reader import html_cache_path
from corpus_cabinet.reader import (
    destination_preview, find_cached_pdf_document, load_reader_config,
    read_cached_document,
)
from corpus_cabinet.search import arxiv_query_id


def linked_text(block):
    """Link only unambiguous visible citation labels, without inserting HTML from PDFs."""
    text = block["text"]
    matches = []
    for index, link in enumerate(block.get("links", [])):
        label = link["label"]
        if label.isdigit():
            for citation in re.finditer(r"\[[^\]\n]{1,80}\]", text):
                for match in re.finditer(r"(?<!\d)" + re.escape(label) + r"(?!\d)", citation.group()):
                    matches.append((citation.start() + match.start(), citation.start() + match.end(), index))
        elif len(label) > 1:
            for match in re.finditer(re.escape(label), text):
                matches.append((match.start(), match.end(), index))
    result = []
    position = 0
    for start, end, index in sorted(set(matches)):
        if start < position:
            continue
        result.append(html.escape(text[position:start]))
        result.append('<a href="preview:' + str(block["id"]) + ":" + str(index) + '">' +
                      html.escape(text[start:end]) + "</a>")
        position = end
    result.append(html.escape(text[position:]))
    return "".join(result)


def reader_html(document, width, font_size, spacious, font_family=""):
    if spacious:
        line_height = "170%"
    else:
        line_height = "140%"
    body_style = "color:#292B32;"
    if font_family:
        body_style += "font-family:'" + html.escape(font_family, quote=True) + "', sans-serif;"
    parts = ['<html><head><style>body {' + body_style + '} a {color:#6350AA; text-decoration:none;} '
             'p {margin-top:8px; margin-bottom:18px;} </style></head><body>']
    for block in document["blocks"]:
        if block.get("role") == "front_matter":
            continue
        kind = block["kind"]
        text = linked_text(block)
        parts.append('<a name="block-' + str(block["id"]) + '"></a>')
        if block.get("asset"):
            path = os.path.join(document["cache_path"], block["asset"])
            image = QImage(path)
            original_width = block.get("image_width", image.width())
            original_height = block.get("image_height", image.height())
            url = QUrl.fromLocalFile(path).toString()
            image_width = min(original_width, max(width - 56, 120))
            image_height = image_width * original_height / max(original_width, 1)
            parts.append('<p><a href="figure:' + str(block["id"]) + '"><img src="' +
                         html.escape(url, quote=True) + '" width="' + str(int(image_width)) +
                         '" height="' + str(int(image_height)) + '"></a></p>')
        elif kind in {"title", "section_header"}:
            parts.append('<p style="font-size:' + str(font_size + 3) + 'px; font-weight:600;">' + text + "</p>")
        elif kind == "code":
            parts.append(
                '<pre style="font-family:Menlo, monospace; font-size:'
                + str(max(font_size - 2, 11))
                + 'px; line-height:145%; white-space:pre-wrap; background:#F5F5F7; '
                'padding:12px; border-radius:7px;">'
                + html.escape(block.get("text", ""))
                + "</pre>"
            )
        elif kind == "blockquote":
            parts.append(
                '<p style="font-size:' + str(font_size) + 'px; color:#555862; '
                'border-left:3px solid #C9C1E1; padding-left:14px; line-height:'
                + line_height + ';">' + text + "</p>"
            )
        elif text:
            content = block.get("html", text)
            color = "#292B32"
            if kind in {"caption", "page_header", "page_footer"}:
                color = "#6F737E"
            parts.append('<p style="font-size:' + str(font_size) + 'px; color:' + color +
                         '; line-height:' + line_height + ';">' + content + "</p>")
        source = ""
        if block.get("page"):
            source = '<a href="source:' + str(block["id"]) + '">Original · p. ' + str(block["page"]) + "</a>"
        for index, link in enumerate(block.get("links", [])):
            if 'href="preview:' + str(block["id"]) + ":" + str(index) + '"' in text:
                continue
            if source:
                source += " &nbsp; · &nbsp; "
            source += '<a href="preview:' + str(block["id"]) + ":" + str(index) + '">Preview ' + html.escape(link["label"]) + "</a>"
        if source:
            parts.append('<p style="font-size:11px; color:#848894; margin-bottom:22px;">' + source + "</p>")
    parts.append("</body></html>")
    return "".join(parts)


class ReaderSignals(QObject):
    finished = Signal(object)
    failed = Signal(object)


class ReaderTask(QRunnable):
    """Run expensive conversion outside Qt with bounded time and CPU threads."""

    def __init__(self, library_path, paper, full, revision, source="pdf"):
        super().__init__()
        self.library_path = library_path
        self.paper = paper
        self.full = full
        self.revision = revision
        self.source = source
        self.signals = ReaderSignals()
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            config = load_reader_config()
            environment = os.environ.copy()
            if self.source == "arxiv":
                command = "import json, sys; from corpus_cabinet.arxiv_reader import convert_arxiv_html; "
                command += "result = convert_arxiv_html(*json.loads(sys.argv[1])); print(json.dumps(result))"
                args = json.dumps([self.library_path, self.paper, config])
            else:
                environment["HF_HUB_OFFLINE"] = "1"
                environment["TRANSFORMERS_OFFLINE"] = "1"
                environment["OMP_NUM_THREADS"] = str(config["cpu_threads"])
                command = "import json, sys; from corpus_cabinet.reader import convert_pdf; "
                command += "result = convert_pdf(*json.loads(sys.argv[1])); print(json.dumps(result))"
                args = json.dumps([self.library_path, self.paper["file_path"], config, self.full])
            if self.cancelled.is_set():
                raise RuntimeError("Conversion cancelled. The original PDF is unchanged.")
            process = subprocess.Popen([sys.executable, "-c", command, args], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + config["timeout_seconds"]
            while True:
                if self.cancelled.is_set() or time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    if self.cancelled.is_set():
                        raise RuntimeError("Conversion cancelled. The original PDF is unchanged.")
                    raise subprocess.TimeoutExpired(process.args, config["timeout_seconds"])
                try:
                    output, errors = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, process.args, output, errors)
            document = json.loads(output.strip().splitlines()[-1])
            self.signals.finished.emit({"revision": self.revision, "document": document,
                                        "source": self.source, "full": self.full})
        except subprocess.TimeoutExpired:
            self.signals.failed.emit({"revision": self.revision, "source": self.source,
                                      "message": "Reader source preparation timed out."})
        except subprocess.CalledProcessError as error:
            lines = error.stderr.strip().splitlines()
            message = "Reader source preparation failed."
            if lines:
                message += " " + lines[-1]
            self.signals.failed.emit({"revision": self.revision, "source": self.source,
                                      "message": message})
        except Exception as error:
            self.signals.failed.emit({"revision": self.revision, "source": self.source,
                                      "message": str(error)})


class ReaderPreviewDialog(QDialog):
    """Show readable destination text beside a focused original source crop."""

    def __init__(self, parent, image, title, page, text=""):
        super().__init__(parent)
        self.page = page
        self.setWindowTitle(title)
        self.resize(780, 560)
        layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("homeSectionHeading")
        layout.addWidget(heading)
        note = QLabel("Readable reference text from the official arXiv HTML source.")
        if page:
            note.setText(
                "Destination on PDF page " + str(page) +
                ". The readable text is extracted from the focused original crop below."
            )
        if title.startswith("Original "):
            note.setText("Original PDF visual crop · page " + str(page) + ".")
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)
        if text:
            text_label = QLabel("Readable text")
            text_label.setObjectName("mutedLabel")
            layout.addWidget(text_label)
            text_view = QTextBrowser()
            text_view.setOpenLinks(False)
            text_view.setMaximumHeight(170)
            text_view.setStyleSheet(
                "QTextBrowser { border: 1px solid #D9DAE1; border-radius: 10px; "
                "background: #FFFFFF; padding: 12px; font-size: 17px; }"
            )
            text_view.setPlainText(text)
            layout.addWidget(text_view)
        if image is not None:
            crop_label = QLabel("Focused original crop")
            crop_label.setObjectName("mutedLabel")
            layout.addWidget(crop_label)
            image_view = QTextBrowser()
            image_view.setOpenLinks(False)
            image_view.document().addResource(
                QTextDocument.ResourceType.ImageResource, QUrl("preview-image"), image
            )
            width = min(image.width(), 680)
            height = width * image.height() / max(image.width(), 1)
            image_view.setHtml(
                '<div style="text-align:center;"><img src="preview-image" width="' +
                str(width) + '" height="' + str(int(height)) + '"></div>'
            )
            layout.addWidget(image_view, 1)
        actions = QHBoxLayout()
        self.original_button = QPushButton("Go to original page")
        self.original_button.setVisible(bool(page))
        actions.addWidget(self.original_button)
        actions.addStretch()
        close = QPushButton("Back to reading")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)


class ReaderPanel(QWidget):
    """An opt-in conversion preview with source links and adjustable typography."""

    sourceRequested = Signal(int, object)
    readerSourceChanged = Signal(int, str)
    dyslexicChanged = Signal(bool)

    def __init__(self, font_family="", dyslexic_enabled=False, parent=None):
        super().__init__(parent)
        self.revision = 0
        self.paper = None
        self.library_path = None
        self.online = True
        self.document_data = None
        self.active_task = None
        self.font_size = 16
        self.spacious = True
        self.font_family = font_family
        self.use_dyslexic = bool(dyslexic_enabled and font_family)
        self.preview_dialog = None
        self.thread_pool = QThreadPool.globalInstance()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        self.status = QLabel("Experimental Reader · select a paper with a local PDF.")
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedLabel")
        layout.addWidget(self.status)
        toolbar = QHBoxLayout()
        self.convert_button = QPushButton("Find best Reader source")
        self.convert_button.clicked.connect(self.generate_preview)
        toolbar.addWidget(self.convert_button)
        self.pdf_button = QPushButton("Use PDF conversion")
        self.pdf_button.clicked.connect(self.generate_pdf_preview)
        toolbar.addWidget(self.pdf_button)
        self.full_button = QPushButton("Convert full PDF")
        self.full_button.clicked.connect(self.generate_full)
        toolbar.addWidget(self.full_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_conversion)
        toolbar.addWidget(self.cancel_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        typography = QHBoxLayout()
        self.smaller_button = QPushButton("A−")
        self.smaller_button.setToolTip("Smaller text")
        self.smaller_button.clicked.connect(self.smaller_text)
        typography.addWidget(self.smaller_button)
        self.larger_button = QPushButton("A+")
        self.larger_button.setToolTip("Larger text")
        self.larger_button.clicked.connect(self.larger_text)
        typography.addWidget(self.larger_button)
        self.spacing_button = QPushButton("Spacious")
        self.spacing_button.setCheckable(True)
        self.spacing_button.setChecked(True)
        self.spacing_button.toggled.connect(self.change_spacing)
        typography.addWidget(self.spacing_button)
        self.dyslexic_toggle = QCheckBox("Use OpenDyslexic")
        self.dyslexic_toggle.setToolTip(
            "Use the bundled OpenDyslexic font for Reader body text"
        )
        self.dyslexic_toggle.setChecked(self.use_dyslexic)
        self.dyslexic_toggle.setEnabled(bool(self.font_family))
        self.dyslexic_toggle.toggled.connect(self.change_reader_font)
        typography.addWidget(self.dyslexic_toggle)
        self.compare_button = QPushButton("Compare with PDF")
        self.compare_button.setCheckable(True)
        self.compare_button.setToolTip(
            "Show the generated Reader and original PDF side by side"
        )
        self.compare_button.toggled.connect(self.toggle_comparison)
        typography.addWidget(self.compare_button)
        typography.addStretch()
        layout.addLayout(typography)
        self.browser = QTextBrowser()
        self.browser.setMaximumWidth(820)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setStyleSheet("QTextBrowser { border: 0; background: #FFFFFF; padding: 16px 20px; }")
        self.browser.anchorClicked.connect(self.activate_link)
        self.reader_container = QWidget()
        reader_layout = QHBoxLayout()
        self.reader_container.setLayout(reader_layout)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        reader_layout.addStretch()
        reader_layout.addWidget(self.browser, 1)
        reader_layout.addStretch()
        self.compare_document = QPdfDocument(self)
        self.compare_view = QPdfView()
        self.compare_view.setDocument(self.compare_document)
        self.compare_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.compare_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.compare_view.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.compare_container = QWidget()
        compare_layout = QVBoxLayout(self.compare_container)
        compare_layout.setContentsMargins(10, 0, 0, 0)
        compare_heading = QLabel("Original PDF · comparison")
        compare_heading.setObjectName("mutedLabel")
        compare_layout.addWidget(compare_heading)
        compare_layout.addWidget(self.compare_view, 1)
        self.compare_container.setVisible(False)
        self.comparison_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.comparison_splitter.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.comparison_splitter.addWidget(self.reader_container)
        self.comparison_splitter.addWidget(self.compare_container)
        self.comparison_splitter.setStretchFactor(0, 1)
        self.comparison_splitter.setStretchFactor(1, 1)
        layout.addWidget(self.comparison_splitter, 1)
        self.set_context(None, None)

    def set_context(self, library_path, paper, online=True):
        if (self.revision > 0 and self.paper == paper and self.library_path == library_path
                and self.online == online):
            return
        self.revision += 1
        self.library_path = library_path
        self.paper = paper
        self.online = online
        self.document_data = None
        if self.preview_dialog:
            self.preview_dialog.close()
        self.browser.clear()
        self.status.setToolTip("")
        pdf_available = bool(paper and paper.get("file_path") and os.path.isfile(paper["file_path"]))
        self.compare_document.close()
        self.compare_button.setEnabled(pdf_available)
        if pdf_available:
            self.compare_document.load(paper["file_path"])
        self.compare_container.setVisible(
            pdf_available and self.compare_button.isChecked()
        )
        self.update_reader_width()
        source_type = "paper"
        if paper:
            source_type = paper.get("source_type") or "paper"
        is_paper = source_type == "paper"
        self.convert_button.setEnabled(bool(paper) and is_paper and (online or pdf_available)
                                       and self.active_task is None)
        self.pdf_button.setEnabled(pdf_available and self.active_task is None)
        self.full_button.setEnabled(False)
        self.status.setText("Experimental Reader · arXiv HTML is preferred when available; local PDF conversion is the fallback.")
        if not paper:
            self.status.setText("Select a source to open Reader.")
        elif not is_paper:
            self.status.setText("Opening the saved source snapshot…")
        elif not online and not pdf_available:
            self.status.setText("No local PDF is available while working offline.")
        self.restore_cached_document()

    def restore_cached_document(self):
        if not self.paper or not self.library_path:
            return
        if (self.paper.get("source_type") or "paper") != "paper":
            content_path = str(self.paper.get("content_path") or "")
            if content_path:
                document = read_cached_document(os.path.dirname(content_path))
                if document:
                    self.document_data = document
                    self.update_available_buttons()
                    self.show_document_status()
                    self.render_document()
                    return
            self.status.setText(
                "The saved source snapshot is unavailable. Add the source again to rebuild it."
            )
            return
        source = str(self.paper.get("reader_source") or "")
        sources = []
        if source:
            sources.append(source)
        else:
            for key in ("external_id", "external_url", "pdf_url"):
                identifier = arxiv_query_id(self.paper.get(key, ""))
                if identifier:
                    sources.append("arxiv:" + identifier)
                    break
            if os.path.isfile(self.paper.get("file_path", "")):
                sources.extend(["pdf_full", "pdf_preview"])
        document = None
        restored_source = ""
        try:
            config = load_reader_config()
            for candidate in sources:
                if candidate.startswith("arxiv:"):
                    identifier = candidate.split(":", 1)[1]
                    cache_path = html_cache_path(
                        self.library_path, identifier, config
                    )
                else:
                    pdf_path = self.paper.get("file_path", "")
                    if not os.path.isfile(pdf_path):
                        continue
                    full = candidate == "pdf_full"
                    document = find_cached_pdf_document(
                        self.library_path, pdf_path, config, full
                    )
                    if document:
                        restored_source = candidate
                        break
                    continue
                document = read_cached_document(cache_path)
                if document:
                    restored_source = candidate
                    break
        except (OSError, RuntimeError, ValueError):
            document = None
        if not document:
            if (source.startswith("arxiv:") and self.online
                    and self.active_task is None):
                self.status.setText(
                    "Updating the saved arXiv Reader to the improved format…"
                )
                self.start_conversion(False, "arxiv")
                return
            if source:
                self.status.setText(
                    "The saved Reader cache is unavailable. Generate it again to rebuild it."
                )
            return
        self.document_data = document
        if restored_source != source:
            self.paper["reader_source"] = restored_source
            self.readerSourceChanged.emit(self.paper["id"], restored_source)
        self.update_available_buttons()
        self.show_document_status()
        self.render_document()

    def generate_preview(self):
        if self.online:
            self.start_conversion(False, "arxiv")
        else:
            self.start_conversion(False, "pdf")

    def generate_pdf_preview(self):
        self.start_conversion(False, "pdf")

    def generate_full(self):
        self.start_conversion(True, "pdf")

    def start_conversion(self, full, source):
        if self.active_task is not None or not self.paper:
            return
        if source == "arxiv":
            self.status.setText("Checking for an exact-title official arXiv HTML version…")
        else:
            self.status.setText("Converting the PDF locally… you can keep using the library.")
        self.convert_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.full_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        task = ReaderTask(self.library_path, self.paper, full, self.revision, source)
        task.signals.finished.connect(self.conversion_finished)
        task.signals.failed.connect(self.conversion_failed)
        self.active_task = task
        self.thread_pool.start(task)

    def conversion_finished(self, result):
        self.active_task = None
        if result["revision"] != self.revision:
            self.update_available_buttons()
            return
        self.document_data = result["document"]
        self.update_available_buttons()
        self.show_document_status()
        document = self.document_data
        source = ""
        if document["engine"] == "arXiv HTML":
            identifier = document.get("arxiv_id", "")
            if identifier:
                source = "arxiv:" + identifier
        elif result.get("full"):
            source = "pdf_full"
        else:
            source = "pdf_preview"
        if source and self.paper:
            self.paper["reader_source"] = source
            self.readerSourceChanged.emit(self.paper["id"], source)
        self.render_document()

    def show_document_status(self):
        document = self.document_data
        if not document:
            return
        engine = str(document.get("engine") or "Reader document")
        if engine == "arXiv HTML":
            description = "Preferred source · official arXiv HTML"
        elif engine in ("Web article", "GitHub README"):
            description = "Saved offline snapshot · " + engine
        elif engine.startswith("Local "):
            description = "Archived local document · " + engine
        else:
            description = "Converted " + str(document.get("pages_converted", 0)) + " of " + str(document.get("page_count", 0)) + " pages"
            if document.get("partial"):
                description = "Preview only · " + description
        self.status.setText(description + " · experimental, not verified against the original.")
        self.status.setToolTip("\n".join(document.get("warnings") or []))

    def conversion_failed(self, result):
        self.active_task = None
        if result["revision"] == self.revision:
            if result.get("source") == "arxiv" and "Unavailable" in result["message"]:
                if self.paper and os.path.isfile(self.paper.get("file_path", "")):
                    self.status.setText("No official arXiv HTML found. Falling back to a local PDF preview…")
                    self.active_task = None
                    self.start_conversion(False, "pdf")
                    return
            self.update_available_buttons()
            self.status.setText(result["message"])
        else:
            self.update_available_buttons()

    def update_available_buttons(self):
        pdf_available = bool(self.paper and os.path.isfile(self.paper.get("file_path", "")))
        is_paper = bool(
            self.paper and (self.paper.get("source_type") or "paper") == "paper"
        )
        self.convert_button.setEnabled(is_paper and (self.online or pdf_available))
        self.pdf_button.setEnabled(pdf_available)
        self.full_button.setEnabled(pdf_available and bool(
            self.document_data and self.document_data["partial"]
            and self.document_data["engine"] not in (
                "arXiv HTML", "Web article", "GitHub README"
            )))
        self.cancel_button.setEnabled(False)

    def cancel_conversion(self):
        if self.active_task:
            self.active_task.cancel()

    def render_document(self):
        if not self.document_data:
            return
        scroll = self.browser.verticalScrollBar().value()
        font_family = ""
        if self.use_dyslexic:
            font_family = self.font_family
        self.browser.setHtml(
            reader_html(
                self.document_data,
                self.browser.viewport().width(),
                self.font_size,
                self.spacious,
                font_family,
            )
        )
        self.browser.verticalScrollBar().setValue(scroll)

    def smaller_text(self):
        self.font_size = max(12, self.font_size - 1)
        self.render_document()

    def larger_text(self):
        self.font_size = min(26, self.font_size + 1)
        self.render_document()

    def change_spacing(self, enabled):
        self.spacious = enabled
        self.render_document()

    def change_reader_font(self, enabled):
        self.use_dyslexic = bool(enabled and self.font_family)
        self.render_document()
        self.dyslexicChanged.emit(self.use_dyslexic)

    def set_dyslexic_font(self, enabled):
        self.use_dyslexic = bool(enabled and self.font_family)
        self.dyslexic_toggle.blockSignals(True)
        self.dyslexic_toggle.setChecked(self.use_dyslexic)
        self.dyslexic_toggle.blockSignals(False)
        self.render_document()

    def toggle_comparison(self, enabled):
        pdf_available = bool(
            self.paper and os.path.isfile(self.paper.get("file_path", ""))
        )
        self.compare_container.setVisible(enabled and pdf_available)
        self.update_reader_width()
        if enabled and pdf_available:
            width = max(self.comparison_splitter.width(), 800)
            self.comparison_splitter.setSizes([width // 2, width // 2])
        self.render_document()

    def update_reader_width(self):
        if self.compare_container.isVisible():
            self.browser.setMaximumWidth(16777215)
        else:
            self.browser.setMaximumWidth(820)

    def show_source_in_compare(self, page_number, bounds):
        if (not self.compare_container.isVisible()
                or self.compare_document.pageCount() <= 0):
            return False
        page = max(
            min(page_number - 1, self.compare_document.pageCount() - 1), 0
        )
        point = QPointF()
        if bounds:
            point = QPointF(bounds[0], bounds[1])
        self.compare_view.pageNavigator().jump(page, point)
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render_document()

    def activate_link(self, url):
        if not self.document_data or not self.paper:
            return
        fields = url.toString().split(":")
        try:
            block = self.document_data["blocks"][int(fields[1])]
            if fields[0] == "source":
                self.sourceRequested.emit(block["page"], block["bounds"])
                return
            if fields[0] == "preview":
                link = block["links"][int(fields[2])]
                reference_id = link.get("reference_id")
                if reference_id:
                    text = self.document_data.get("references", {}).get(reference_id, "")
                    if not text:
                        raise RuntimeError("This HTML reference target is unavailable.")
                    dialog = ReaderPreviewDialog(self, None, "Reference preview", None, text)
                    self.preview_dialog = dialog
                    dialog.show()
                    return
                data = destination_preview(self.paper["file_path"], link["page"], link["point"])
                image = QImage.fromData(QByteArray(data["image"]), "PNG")
                page = link["page"]
                title = "Reference / destination preview"
                text = data["text"]
            elif fields[0] == "figure":
                image = QImage(os.path.join(self.document_data["cache_path"], block["asset"]))
                page = block.get("page")
                text = ""
                if page:
                    title = "Original " + block["kind"]
                else:
                    title = "arXiv HTML " + block["kind"]
            else:
                return
            dialog = ReaderPreviewDialog(self, image, title, page, text)
            dialog.original_button.clicked.connect(self.preview_original)
            self.preview_dialog = dialog
            dialog.show()
        except (ValueError, IndexError, KeyError, RuntimeError) as error:
            self.status.setText("Preview unavailable: " + str(error))

    def preview_original(self):
        if self.preview_dialog:
            self.sourceRequested.emit(self.preview_dialog.page, None)
            self.preview_dialog.close()
