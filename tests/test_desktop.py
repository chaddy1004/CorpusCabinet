"""Verify the native Qt window can display a paper from a local library.

The test uses Qt's offscreen platform and a temporary PDF/library, so it does
not open a real window or contact external services.
"""

import os
import shutil
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import (
    QDragEnterEvent, QDragMoveEvent, QDropEvent, QFontMetricsF, QKeySequence,
    QNativeGestureEvent, QPalette, QPointingDevice, QWheelEvent,
)
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QListWidgetItem, QPushButton, QScrollBar, QStyle,
    QStyleOptionSlider, QStyleOptionViewItem,
)

from corpus_cabinet.desktop import (
    ArchivedProjectsDialog,
    BibtexDialog,
    DiscoveryDialog,
    DownloadTask,
    FullscreenPdfDialog,
    ImportTask,
    MainWindow,
    ModernComboBox,
    PdfDropListWidget,
    PdfDropPanel,
    PROJECT_DRAG_MIME,
    ProjectListWidget,
    ProjectSelectionDialog,
    SourceCaptureDialog,
    application_logo_path,
    apply_light_theme,
    comfortable_abstract_html,
    experimental_reader_enabled,
    latex_to_html,
    latex_to_plain_text,
    local_source_paths_from_mime_data,
    pdf_paths_from_mime_data,
    render_pdf_to_printer,
)
from corpus_cabinet.local_documents import capture_local_document
from corpus_cabinet.storage import Library, WorkspaceManager
from corpus_cabinet.research_ui import (
    LibrarySearchDialog,
    LibrarySearchTask,
    ProjectNotesDialog,
)


DOWNLOAD_FIXTURE_PATH = ""
DROP_RESULTS = []


def create_test_pdf(path):
    """Create a one-page PDF fixture at path."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Desktop MVP Paper\nA short abstract")
    document.save(path)
    document.close()


def create_multi_page_test_pdf(path, page_count=3):
    """Create a small multi-page PDF fixture at path."""
    document = pymupdf.open()
    for page_number in range(page_count):
        page = document.new_page()
        page.insert_text(
            (72, 72),
            "Multi-page paper\nPage " + str(page_number + 1),
        )
    document.save(path)
    document.close()


def create_standalone_abstract_pdf(path):
    """Create a PDF whose abstract follows a standalone heading."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Legacy PDF Paper", fontsize=18)
    page.insert_text((72, 110), "Abstract", fontsize=14)
    page.insert_text(
        (72, 140),
        "A recovered abstract from an existing local PDF.",
        fontsize=10,
    )
    page.insert_text((72, 180), "1 Introduction", fontsize=14)
    document.save(path)
    document.close()


def create_article_capture():
    return {
        "source_type": "article",
        "title": "Readable Engineering Notes",
        "authors": "Ada Engineer",
        "venue": "Example Engineering",
        "year": 2026,
        "abstract": "A saved implementation guide.",
        "canonical_url": "https://example.com/engineering-notes",
        "external_url": "https://example.com/engineering-notes",
        "source": "Web article",
        "extracted_text": "A detailed implementation guide with testable advice.",
        "source_metadata": {"published": "2026-09-25"},
        "source_html": b"<article>Implementation guide</article>",
        "assets": [],
        "document": {
            "version": 1,
            "engine": "Web article",
            "title": "Readable Engineering Notes",
            "source_url": "https://example.com/engineering-notes",
            "partial": False,
            "warnings": [],
            "blocks": [
                {
                    "id": 1,
                    "kind": "paragraph",
                    "role": "body",
                    "text": "A detailed implementation guide with testable advice.",
                    "links": [],
                }
            ],
        },
    }


def test_experimental_reader_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("CORPUS_CABINET_READER", raising=False)
    assert experimental_reader_enabled() is False
    monkeypatch.setenv("CORPUS_CABINET_READER", "true")
    assert experimental_reader_enabled() is True


def copy_download_fixture(url, destination, config):
    """Stand in for a network download with the configured PDF fixture."""
    shutil.copy2(DOWNLOAD_FIXTURE_PATH, destination)


def accept_search_result(result, project_id=None):
    """Accept a dialog callback in the UI test."""
    return True


def create_search_project(name):
    """Return a dialog-created project in the UI test."""
    return {"id": 99, "name": name}


def search_context(project_id):
    """Return deterministic visible search preferences in the UI test."""
    return "robotics, manipulation"


def save_search_context(project_id, context):
    """Accept a persisted search preference in the UI test."""
    return None


def save_bibtex(paper_id, bibtex):
    """Accept a reviewed BibTeX entry in the UI test."""
    return bool(paper_id and bibtex)


def capture_pdf_drop(paths):
    """Record a paper-pane PDF drop for the widget integration test."""
    DROP_RESULTS.append(paths)


def project_drag_mime(project_id):
    """Build the app-owned project drag payload used by the sidebar."""
    mime_data = QMimeData()
    mime_data.setData(PROJECT_DRAG_MIME, str(project_id).encode("ascii"))
    return mime_data


def project_item_by_id(window, project_id):
    """Return the sidebar item for one project in its current group order."""
    for index in range(window.project_list.count()):
        item = window.project_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == project_id:
            return item
    return None


def test_lightweight_latex_rendering_is_safe_and_readable():
    assert latex_to_plain_text("LaPA$^2$") == "LaPA²"
    assert latex_to_plain_text(r"Policy $\pi_t$") == "Policy πₜ"

    rendered = latex_to_html("<unsafe>LaPA$^2$")
    assert rendered.startswith("&lt;unsafe&gt;LaPA")
    assert "<sup>2</sup>" in rendered

    dyslexic_abstract = comfortable_abstract_html(
        "First paragraph.\n\nSecond paragraph.",
        "OpenDyslexic",
    )
    assert "font-family: 'OpenDyslexic', sans-serif" in dyslexic_abstract
    assert "line-height: 175%" in dyslexic_abstract
    assert dyslexic_abstract.count("<p>") == 2


def test_application_logo_is_bundled_and_loadable():
    logo_path = application_logo_path()

    assert os.path.basename(logo_path) == "logo.png"
    assert os.path.isfile(logo_path)
    with open(logo_path, "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_pdf_drag_payload_keeps_only_existing_local_pdfs(tmp_path):
    pdf_path = str(tmp_path / "paper.pdf")
    markdown_path = str(tmp_path / "guide.md")
    html_path = str(tmp_path / "guide.html")
    text_path = str(tmp_path / "notes.txt")
    create_test_pdf(pdf_path)
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write("# Guide\n\nA useful local guide.")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write("<h1>Guide</h1><p>A useful local guide.</p>")
    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write("not a paper")

    mime_data = QMimeData()
    mime_data.setUrls(
        [
            QUrl.fromLocalFile(pdf_path),
            QUrl.fromLocalFile(markdown_path),
            QUrl.fromLocalFile(html_path),
            QUrl.fromLocalFile(text_path),
            QUrl.fromLocalFile(str(tmp_path / "promised.pdf")),
            QUrl("https://example.org/paper.pdf"),
        ]
    )

    assert pdf_paths_from_mime_data(mime_data) == [pdf_path]
    assert pdf_paths_from_mime_data(mime_data, False) == [
        pdf_path,
        str(tmp_path / "promised.pdf"),
    ]
    assert local_source_paths_from_mime_data(mime_data) == [
        pdf_path,
        markdown_path,
        html_path,
    ]


def test_paper_list_accepts_pdf_and_markdown_drop_and_shows_active_state(tmp_path):
    DROP_RESULTS.clear()
    pdf_path = str(tmp_path / "paper.pdf")
    markdown_path = str(tmp_path / "guide.md")
    create_test_pdf(pdf_path)
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write("# Guide\n\nA useful local guide.")
    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    project_list = PdfDropListWidget()
    project_list.set_drop_enabled(True)
    item = QListWidgetItem("Robot Learning")
    item.setData(Qt.ItemDataRole.UserRole, 17)
    project_list.addItem(item)
    project_list.resize(260, 180)
    project_list.show()
    application.processEvents()

    mime_data = QMimeData()
    mime_data.setUrls([
        QUrl.fromLocalFile(pdf_path),
        QUrl.fromLocalFile(markdown_path),
    ])
    position = project_list.visualItemRect(item).center()
    drag_event = QDragEnterEvent(
        position,
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    project_list.sourcesDropped.connect(capture_pdf_drop)

    QApplication.sendEvent(project_list.viewport(), drag_event)
    assert drag_event.isAccepted() is True
    assert project_list.property("dragActive") is True
    drop_event = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(project_list.viewport(), drop_event)

    assert DROP_RESULTS == [[pdf_path, markdown_path]]
    assert drop_event.isAccepted() is True
    assert drop_event.dropAction() == Qt.DropAction.CopyAction
    assert project_list.property("dragActive") is False
    project_list.close()


def test_project_list_drop_emits_order_without_detaching_custom_cards(monkeypatch):
    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    project_list = ProjectListWidget()
    cards = []
    for project_id in (1, 2, 3):
        item = QListWidgetItem("Project " + str(project_id))
        item.setData(Qt.ItemDataRole.UserRole, project_id)
        item.setData(Qt.ItemDataRole.UserRole + 2, False)
        project_list.addItem(item)
        card = QPushButton("Card " + str(project_id))
        cards.append(card)
        project_list.setItemWidget(item, card)
    project_list.resize(280, 240)
    project_list.show()
    application.processEvents()

    drag = Mock()
    drag_class = Mock(return_value=drag)
    monkeypatch.setattr("corpus_cabinet.desktop.QDrag", drag_class)
    project_list.setCurrentRow(0)
    project_list.startDrag(Qt.DropAction.MoveAction)
    drag_class.assert_called_once_with(project_list)
    drag.exec.assert_called_once_with(Qt.DropAction.MoveAction)
    drag_mime = drag.setMimeData.call_args.args[0]
    assert bytes(drag_mime.data(PROJECT_DRAG_MIME)) == b"1"
    assert project_list.count() == 3

    third = project_list.item(2)
    third_rect = project_list.visualItemRect(third)
    position = QPointF(third_rect.center().x(), third_rect.bottom() - 1)
    mime_data = project_drag_mime(1)
    move_event = QDragMoveEvent(
        position.toPoint(),
        Qt.DropAction.MoveAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    project_list.dragMoveEvent(move_event)
    assert move_event.isAccepted() is True
    assert project_list.project_drop_y == third_rect.bottom()
    drop_event = QDropEvent(
        position,
        Qt.DropAction.MoveAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    order_changed = Mock()
    project_list.projectOrderChanged.connect(order_changed)

    project_list.dropEvent(drop_event)

    assert [
        project_list.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(project_list.count())
    ] == [1, 2, 3]
    assert project_list.itemWidget(project_list.item(0)) is cards[0]
    assert drop_event.isAccepted() is True
    assert project_list.project_drop_y is None
    order_changed.assert_called_once_with([], [2, 3, 1])
    project_list.close()


def test_project_drop_persists_then_rebuilds_every_sidebar_card(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    first = library.create_project("First")
    second = library.create_project("Second")
    third = library.create_project("Third")
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    window = MainWindow(manager)
    window.show()
    application.processEvents()

    first_item = project_item_by_id(window, first["id"])
    third_item = project_item_by_id(window, third["id"])
    window.project_list.setCurrentItem(first_item)
    third_rect = window.project_list.visualItemRect(third_item)
    position = QPointF(third_rect.center().x(), third_rect.bottom() - 1)
    mime_data = project_drag_mime(first["id"])
    drop_event = QDropEvent(
        position,
        Qt.DropAction.MoveAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    window.project_list.dropEvent(drop_event)
    application.processEvents()

    expected_ids = [second["id"], third["id"], first["id"]]
    assert [
        window.project_list.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(window.project_list.count())
    ] == expected_ids
    assert [
        project["id"]
        for project in library.list_projects()
        if project.get("kind") != "scrapbook"
    ] == expected_ids
    assert all(
        window.project_list.itemWidget(window.project_list.item(index)) is not None
        for index in range(window.project_list.count())
    )
    window.close()


def test_surrounding_paper_panel_accepts_pdf_drop(tmp_path):
    DROP_RESULTS.clear()
    pdf_path = str(tmp_path / "paper.pdf")
    create_test_pdf(pdf_path)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    panel = PdfDropPanel()
    panel.set_drop_enabled(True)
    panel.resize(320, 240)
    panel.show()
    application.processEvents()
    panel.sourcesDropped.connect(capture_pdf_drop)
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(pdf_path)])
    position = panel.rect().center()
    drag_event = QDragEnterEvent(
        position,
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(panel, drag_event)
    assert drag_event.isAccepted() is True
    assert panel.property("dragActive") is True
    drop_event = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(panel, drop_event)

    assert DROP_RESULTS == [[pdf_path]]
    assert panel.property("dragActive") is False
    panel.close()


def test_dropped_markdown_uses_current_project_and_archives_document(
    tmp_path,
    monkeypatch,
):
    root = str(tmp_path / "library")
    markdown_path = tmp_path / "controls-guide.md"
    markdown_path.write_text(
        "# Controls guide\n\n"
        "This engineering guide documents a repeatable calibration workflow "
        "with enough context to remain useful during offline implementation "
        "and review. The archived copy should preserve this exact source.\n",
        encoding="utf-8",
    )
    library = Library(root)
    project = library.create_project("Documentation")
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    window = MainWindow(manager)
    monkeypatch.setattr(
        ProjectSelectionDialog,
        "exec",
        Mock(return_value=QDialog.DialogCode.Accepted),
    )
    monkeypatch.setattr(
        ProjectSelectionDialog,
        "selected_project_ids",
        Mock(return_value=[project["id"]]),
    )

    window.confirm_dropped_sources([str(markdown_path)])
    application.processEvents()

    sources = library.list_sources(project["id"])
    assert len(sources) == 1
    assert sources[0]["source_type"] == "document"
    assert sources[0]["title"] == "Controls guide"
    assert os.path.isfile(sources[0]["content_path"])
    assert window.current_paper_id == sources[0]["id"]
    window.close()


def test_main_window_displays_imported_paper(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "paper.pdf")
    create_test_pdf(source_path)

    library = Library(root)
    project = library.create_project("Desktop Project")
    paper = library.import_pdf(project["id"], source_path)
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)

    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    window = MainWindow(manager)
    window.network_reachability_changed(
        QNetworkInformation.Reachability.Online
    )
    assert project_item_by_id(window, library.get_scrapbook()["id"]) is None
    for index in range(window.project_list.count()):
        project_item = window.project_list.item(index)
        if project_item.data(Qt.ItemDataRole.UserRole) == project["id"]:
            window.project_list.setCurrentItem(project_item)
            break
    window.paper_list.setCurrentRow(0)
    application.processEvents()

    assert window.project_list.count() == 1
    assert window.paper_list.count() == 1
    assert window.detail_title.text() == "Desktop MVP Paper"
    assert bool(
        window.detail_title.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    ) is True
    window.detail_title.setSelection(0, 7)
    assert window.detail_title.selectedText() == "Desktop"
    assert bool(
        window.detail_meta.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    ) is True
    assert window.pdf_document.pageCount() == 1
    assert window.pdf_view.pageMode() == window.pdf_view.PageMode.MultiPage
    assert window.pdf_view.zoomMode() == window.pdf_view.ZoomMode.FitToWidth
    assert window.pdf_page_label.text() == "Page 1 of 1"
    assert window.pdf_previous_button.isEnabled() is False
    assert window.pdf_next_button.isEnabled() is False
    assert window.offline_mode_checkbox.isChecked() is False
    assert hasattr(window, "discover_button") is False
    assert window.add_paper_button.isEnabled() is True
    assert window.add_paper_button.text() == "+ Add source"
    assert window.page_stack.currentWidget() == window.home_page
    assert window.open_library_button.text() == "Library folder…"
    assert "Current library:" in window.open_library_button.toolTip()
    assert window.home_project_count.text() == "1"
    assert window.home_paper_count.text() == "1"
    assert hasattr(window, "home_pdf_count") is False
    assert window.abstract_font_toggle.isEnabled() is True
    assert window.abstract_font_toggle.isChecked() is False
    assert window.copy_paper_button.text() == "Copy to project…"
    assert window.copy_paper_button.isEnabled() is True
    assert [window.detail_tabs.tabText(index) for index in range(3)] == ["Details", "PDF", "Notes"]
    assert window.detail_tabs.isTabVisible(
        window.detail_tabs.indexOf(window.reader_panel)
    ) is False
    assert window.notes_editor.isEnabled() is True
    assert window.pdf_comments_panel.isVisible() is False

    window.show_library()
    window.detail_tabs.setCurrentWidget(window.pdf_tab)
    window.show()
    application.processEvents()
    assert window.pdf_comments_panel.isVisible() is True
    window.pdf_comments_panel.editor.setPlainText("Compare this diagram.")
    window.pdf_comments_panel.add_button.click()
    assert window.pdf_comments_panel.comment_list.count() == 1
    assert library.list_paper_comments(paper["id"])[0]["body"] == (
        "Compare this diagram."
    )

    window.notes_editor.setPlainText("A manual research note.")
    assert window.notes_status_label.text() == "Saving…"
    window.save_pending_notes()
    assert window.notes_status_label.text() == "Saved"
    assert library.get_paper(paper["id"])["notes"] == (
        "A manual research note."
    )

    online_result = {
        "title": "Online Metadata Title",
        "authors": "Jane Doe",
        "venue": "Example Venue",
        "year": 2024,
        "doi": "10.1000/desktop",
        "abstract": "An online abstract.",
        "external_id": "W123",
        "external_url": "https://example.org/paper",
        "pdf_url": "https://example.org/paper.pdf",
        "source": "Crossref",
        "is_open_access": False,
        "citation_count": 12,
        "context_score": 0.5,
        "query_score": 0.9,
    }
    assert window.apply_online_metadata(online_result) is True
    assert window.detail_title.text() == "Online Metadata Title"
    assert window.abstract_text.toPlainText() == "An online abstract."
    assert "12 citations (estimate)" in window.detail_meta.text()
    assert window.open_source_button.isEnabled() is True
    assert window.open_source_button.text() == "Open published version"
    assert window.scholar_button.isEnabled() is True

    window.abstract_font_toggle.setChecked(True)
    application.processEvents()
    assert window.dyslexic_font_enabled is True
    assert "OpenDyslexic" in window.abstract_text.toHtml()
    assert window.reader_panel.dyslexic_toggle.isChecked()
    assert manager.is_dyslexic_font_enabled() is True

    window.reader_panel.dyslexic_toggle.setChecked(False)
    application.processEvents()
    assert not window.abstract_font_toggle.isChecked()
    assert manager.is_dyslexic_font_enabled() is False

    second_project = library.create_project("Second destination")
    dialog = DiscoveryDialog(
        window,
        library.list_projects(),
        project["id"],
        library.list_papers(project["id"])[0]["id"],
        {},
        accept_search_result,
        accept_search_result,
        accept_search_result,
        create_search_project,
        search_context,
        save_search_context,
    )
    assert dialog.windowTitle() == "Add paper online"
    assert dialog.search_button.text() == "Find paper"
    assert "paste an arXiv" in dialog.status_label.text()
    thread_pool_mock = Mock()
    dialog.thread_pool = thread_pool_mock
    dialog.query_input.setText("HoMeR")
    dialog.start_search()
    assert dialog.progress_bar.isHidden() is False
    assert thread_pool_mock.start.call_count == 1
    dialog.search_finished([online_result])
    assert dialog.result_list.count() == 1
    assert "12 citations" in dialog.result_list.item(0).text()
    assert "50% project match" in dialog.result_list.item(0).text()
    assert dialog.result_title_label.text() == "Online Metadata Title"
    assert "90% query match" in dialog.result_reason_label.text()
    assert dialog.primary_button.text() == "Download + add"
    assert dialog.primary_button.isEnabled() is True
    assert dialog.apply_action.isEnabled() is True
    assert dialog.save_action.isEnabled() is True
    assert dialog.progress_bar.isHidden() is True
    dialog.additional_project_ids = [second_project["id"]]
    dialog.update_more_projects_button()
    assert dialog.destination_project_ids() == [
        project["id"],
        second_project["id"],
    ]
    assert dialog.more_projects_button.text() == "+ 1 more project"
    dialog.add_selected()
    assert dialog.status_label.text() == "Citation copied to 2 project(s)"
    dialog.close()

    spontaneous_project = window.create_project_from_discovery(
        "Spontaneous Research"
    )
    assert spontaneous_project["name"] == "Spontaneous Research"
    assert os.path.isdir(spontaneous_project["folder_path"])

    window.network_reachability_changed(
        QNetworkInformation.Reachability.Disconnected
    )
    application.processEvents()
    assert window.network_status_label.text() == "No internet · offline"
    assert window.offline_mode_checkbox.isChecked() is False
    assert window.add_paper_button.isEnabled() is True
    assert manager.is_offline_mode() is False

    window.network_reachability_changed(
        QNetworkInformation.Reachability.Online
    )
    application.processEvents()
    assert window.network_status_label.text() == "Online"
    assert window.add_paper_button.isEnabled() is True

    window.offline_mode_checkbox.setChecked(True)
    application.processEvents()
    assert window.network_status_label.text() == "Working offline"
    assert window.add_paper_button.isEnabled() is True
    assert window.open_source_button.isEnabled() is False
    assert window.scholar_button.isEnabled() is False
    assert manager.is_offline_mode() is True

    window.offline_mode_checkbox.setChecked(False)
    application.processEvents()
    assert window.add_paper_button.isEnabled() is True
    assert window.open_source_button.isEnabled() is True
    assert window.scholar_button.isEnabled() is True
    assert manager.is_offline_mode() is False

    window.close()


def test_saved_article_uses_reader_hides_paper_actions_and_filters_by_type(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Engineering Sources")
    source = library.create_source_copies(
        [project["id"]],
        create_article_capture(),
    )[0]
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)

    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    window = MainWindow(manager)
    for index in range(window.project_list.count()):
        item = window.project_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == project["id"]:
            window.project_list.setCurrentItem(item)
            break
    window.paper_list.setCurrentRow(0)
    application.processEvents()

    assert window.detail_title.text() == "Readable Engineering Notes"
    assert "Article" in window.detail_meta.text()
    assert window.abstract_label.text() == "Summary"
    assert window.detail_tabs.isTabVisible(
        window.detail_tabs.indexOf(window.pdf_tab)
    ) is False
    assert window.open_source_button.text() == "Open original article"
    assert window.scholar_button.isVisible() is False
    assert window.bibtex_button.isVisible() is False
    assert window.reader_panel.document_data["engine"] == "Web article"
    assert "testable advice" in window.reader_panel.browser.toPlainText()
    assert window.detail_tabs.tabText(
        window.detail_tabs.indexOf(window.reader_panel)
    ) == "Content"
    assert window.detail_tabs.isTabVisible(
        window.detail_tabs.indexOf(window.reader_panel)
    ) is True
    assert window.paper_list.item(0).data(
        Qt.ItemDataRole.UserRole + 1
    )["source_type"] == "article"

    window.source_type_combo.setCurrentIndex(
        window.source_type_combo.findData("paper")
    )
    application.processEvents()
    assert window.paper_list.count() == 0
    window.source_type_combo.setCurrentIndex(
        window.source_type_combo.findData("web")
    )
    application.processEvents()
    assert window.paper_list.count() == 1
    assert library.get_source(source["id"])["content_path"]
    window.close()


def test_saved_markdown_document_shows_reader_original_and_versions(tmp_path):
    root = str(tmp_path / "library")
    source_path = tmp_path / "guide.md"
    source_path.write_text(
        "# Robot setup guide\n\n"
        "This archived procedure explains the calibration workflow in enough "
        "detail to remain useful during offline engineering review.\n",
        encoding="utf-8",
    )
    library = Library(root)
    project = library.create_project("Documentation")
    capture = capture_local_document(str(source_path))
    source = library.create_source_from_capture(project["id"], capture)
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)

    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    window = MainWindow(manager)
    for index in range(window.project_list.count()):
        item = window.project_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == project["id"]:
            window.project_list.setCurrentItem(item)
            break
    window.select_paper_by_id(source["id"])
    application.processEvents()

    assert window.detail_title.text() == "Robot setup guide"
    assert window.source_type_combo.findData("markdown") >= 0
    assert window.detail_tabs.isTabVisible(
        window.detail_tabs.indexOf(window.pdf_tab)
    ) is False
    assert window.detail_tabs.isTabVisible(
        window.detail_tabs.indexOf(window.interactive_document_panel)
    ) is True
    assert window.detail_tabs.isTabVisible(
        window.detail_tabs.indexOf(window.document_versions_panel)
    ) is True
    assert "calibration workflow" in window.reader_panel.browser.toPlainText()
    assert "# Robot setup guide" in (
        window.interactive_document_panel.raw_text.toPlainText()
    )
    assert window.document_versions_panel.version_list.count() == 1
    assert window.detail_tabs.tabText(
        window.detail_tabs.indexOf(window.reader_panel)
    ) == "Content"
    window.close()


def test_source_list_groups_content_types_and_collapses_sections(tmp_path):
    root = str(tmp_path / "library")
    markdown_path = tmp_path / "guide.md"
    html_path = tmp_path / "report.html"
    markdown_path.write_text(
        "# Controls guide\n\n"
        "A repeatable calibration procedure for the complete robot system. "
        "It records setup, safety checks, expected results, and recovery "
        "steps so the document remains useful during offline review.",
        encoding="utf-8",
    )
    html_path.write_text(
        "<html><head><title>Audit report</title></head>"
        "<body><p>A saved engineering audit covering system setup, safety "
        "checks, expected results, and recovery steps for future offline "
        "implementation and review.</p></body></html>",
        encoding="utf-8",
    )
    library = Library(root)
    project = library.create_project("Mixed sources")
    paper = library.create_paper_from_search(
        project["id"],
        {"title": "Research paper"},
    )
    markdown = library.create_source_from_capture(
        project["id"],
        capture_local_document(str(markdown_path)),
    )
    html_source = library.create_source_from_capture(
        project["id"],
        capture_local_document(str(html_path)),
    )
    web = library.create_source_copies(
        [project["id"]],
        create_article_capture(),
    )[0]
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    window = MainWindow(manager)
    application.processEvents()

    headers = {}
    header_rows = []
    visible_source_ids = []
    for row in range(window.paper_list.count()):
        item = window.paper_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole + 3) == "source_group_header":
            button = window.paper_list.itemWidget(item)
            headers[button.property("groupKey")] = button
            header_rows.append(row)
        elif item.data(Qt.ItemDataRole.UserRole) is not None:
            visible_source_ids.append(item.data(Qt.ItemDataRole.UserRole))
    assert set(headers) == {"paper", "markdown", "html", "web"}
    assert set(visible_source_ids) == {
        paper["id"], markdown["id"], html_source["id"], web["id"]
    }
    assert window.paper_list.count() == 8
    delegate = window.paper_list.itemDelegate()
    for row in header_rows:
        index = window.paper_list.model().index(row, 0)
        option = QStyleOptionViewItem()
        assert delegate.sizeHint(option, index).height() == 36
        painter = Mock()
        delegate.paint(painter, option, index)
        painter.save.assert_not_called()

    headers["html"].click()
    application.processEvents()

    visible_source_ids = [
        window.paper_list.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(window.paper_list.count())
        if window.paper_list.item(row).data(Qt.ItemDataRole.UserRole)
        is not None
    ]
    assert html_source["id"] not in visible_source_ids
    assert window.paper_list.count() == 7
    html_header = None
    for row in range(window.paper_list.count()):
        item = window.paper_list.item(row)
        button = window.paper_list.itemWidget(item)
        if button is not None and button.property("groupKey") == "html":
            html_header = button
            break
    assert html_header is not None
    assert html_header.accessibleName() == "Expand HTML documents"

    window.source_type_combo.setCurrentIndex(
        window.source_type_combo.findData("html")
    )
    application.processEvents()
    assert window.paper_list.count() == 1
    assert window.paper_list.item(0).data(
        Qt.ItemDataRole.UserRole
    ) == html_source["id"]
    window.close()


def test_source_capture_dialog_starts_unsaved_and_has_project_confirmation(tmp_path):
    library = Library(str(tmp_path / "library"))
    project = library.create_project("Web")
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    dialog = SourceCaptureDialog(
        None,
        library.list_projects(),
        project["id"],
        lambda capture, project_ids: True,
    )

    assert dialog.windowTitle() == "Save website or repository"
    assert dialog.save_button.isEnabled() is False
    assert "github.com" in dialog.url_input.placeholderText()
    dialog.close()


def test_saved_citation_shows_contextual_pdf_actions(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Reading")
    paper = library.create_paper_from_search(
        project["id"],
        {
            "title": "A Published Paper",
            "venue": "Example Conference",
            "external_url": "https://doi.org/10.1000/example",
            "pdf_url": "https://example.org/paper.pdf",
            "source": "Crossref, arXiv",
            "citation_count": 42,
        },
    )
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)

    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    window = MainWindow(manager)
    for index in range(window.project_list.count()):
        project_item = window.project_list.item(index)
        if project_item.data(Qt.ItemDataRole.UserRole) == project["id"]:
            window.project_list.setCurrentItem(project_item)
            break
    window.select_paper_by_id(paper["id"])
    application.processEvents()

    assert window.pdf_stack.currentWidget() == window.pdf_empty_panel
    assert window.pdf_status_label.text() == "PDF not downloaded"
    assert window.pdf_primary_button.text() == "Download PDF"
    assert window.pdf_choose_button.isVisible() is False
    window.show_library()
    window.detail_tabs.setCurrentWidget(window.pdf_tab)
    window.show()
    application.processEvents()
    assert window.pdf_choose_button.isVisible() is True
    assert "42 citations (estimate)" in window.detail_meta.text()

    window.offline_mode_checkbox.setChecked(True)
    application.processEvents()
    assert window.pdf_status_label.text() == "Add a local PDF"
    assert window.pdf_primary_button.isVisible() is False
    assert window.pdf_choose_button.isVisible() is True

    window.close()


def test_pdf_viewer_navigates_every_loaded_page(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "multi-page.pdf")
    create_multi_page_test_pdf(source_path)
    library = Library(root)
    project = library.create_project("Multi-page papers")
    paper = library.import_pdf(project["id"], source_path)
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)

    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    window = MainWindow(manager)
    for index in range(window.project_list.count()):
        project_item = window.project_list.item(index)
        if project_item.data(Qt.ItemDataRole.UserRole) == project["id"]:
            window.project_list.setCurrentItem(project_item)
            break
    window.select_paper_by_id(paper["id"])
    window.show_library()
    window.detail_tabs.setCurrentWidget(window.pdf_tab)
    window.show()
    application.processEvents()

    assert window.pdf_document.pageCount() == 3
    assert window.pdf_page_label.text() == "Page 1 of 3"
    assert window.pdf_previous_button.isEnabled() is False
    assert window.pdf_next_button.isEnabled() is True
    assert window.pdf_fullscreen_button.isEnabled() is True
    assert window.pdf_print_button.isEnabled() is True

    window.pdf_next_button.click()
    application.processEvents()

    assert window.pdf_view.pageNavigator().currentPage() == 1
    assert window.pdf_page_label.text() == "Page 2 of 3"
    assert window.pdf_previous_button.isEnabled() is True
    assert window.pdf_next_button.isEnabled() is True

    window.pdf_zoom_combo.setCurrentIndex(2)
    application.processEvents()
    assert window.pdf_view.zoomMode() == window.pdf_view.ZoomMode.Custom
    assert window.pdf_view.zoomFactor() == 1.0

    reader = FullscreenPdfDialog(
        window,
        paper["file_path"],
        paper["title"],
        initial_page=1,
        library=library,
        paper_id=paper["id"],
    )
    reader.showFullScreen()
    application.processEvents()
    assert reader.isFullScreen() is True
    assert reader.document.pageCount() == 3
    assert reader.page_label.text() == "Page 2 of 3"
    assert reader.escape_shortcut.key() == QKeySequence("Escape")
    assert reader.comments_button.isVisible() is True
    assert reader.print_button.isEnabled() is True
    reader.comments_panel.editor.setPlainText("Inspect the second page.")
    reader.comments_panel.add_button.click()
    assert library.list_paper_comments(paper["id"])[0]["page_number"] == 2
    reader.next_button.click()
    application.processEvents()
    assert reader.current_page() == 2
    assert reader.page_label.text() == "Page 3 of 3"
    assert library.get_paper(paper["id"])["last_page"] == 2
    reader.close()
    window.close()


def test_pdf_print_renderer_outputs_selected_page_range(tmp_path):
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    source_path = str(tmp_path / "print-source.pdf")
    output_path = str(tmp_path / "printed-pages.pdf")
    create_multi_page_test_pdf(source_path)
    document = QPdfDocument()
    document.load(source_path)
    printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(output_path)
    printer.setFromTo(2, 3)

    assert render_pdf_to_printer(document, printer) is True

    printed = pymupdf.open(output_path)
    assert printed.page_count == 2
    preview = printed[0].get_pixmap()
    assert min(preview.samples) < 200
    printed.close()
    document.close()


def test_home_resumes_page_zoom_status_and_favorite_after_restart(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "reader.pdf")
    create_multi_page_test_pdf(source_path)
    library = Library(root)
    project = library.create_project("Research")
    paper = library.import_pdf(project["id"], source_path)
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    window = MainWindow(manager)
    assert window.navigate_to_paper(paper["id"]) is True
    assert "border-radius: 16px" in window.reading_status_combo.styleSheet()
    assert "#FDE8E7" in window.reading_status_combo.styleSheet()
    assert window.reading_status_combo.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert "Click to change" in window.reading_status_combo.toolTip()
    assert library.get_paper(paper["id"])["last_opened"] == ""
    window.detail_tabs.setCurrentWidget(window.notes_tab)
    assert window.detail_tabs.currentIndex() == 2
    assert library.get_paper(paper["id"])["last_opened"] == ""
    window.detail_tabs.setCurrentWidget(window.pdf_tab)
    window.pdf_view.pageNavigator().jump(2, QPointF())
    window.pdf_zoom_combo.setCurrentIndex(3)
    window.reading_status_combo.setCurrentIndex(1)
    assert "#FFF4CB" in window.reading_status_combo.styleSheet()
    window.favorite_button.setChecked(True)
    assert window.favorite_button.text() == "★"
    assert window.favorite_button.toolTip() == "Remove from favorites"
    assert "#D9A000" in window.favorite_button.styleSheet()
    window.show_home()
    assert window.home_reading_list.count() == 1
    assert "Page 3" in window.home_reading_list.item(0).text()
    window.close()

    reopened = MainWindow(manager)
    reopened.resume_home_paper(reopened.home_reading_list.item(0))
    assert reopened.current_project_id == project["id"]
    assert reopened.current_paper_id == paper["id"]
    assert reopened.detail_tabs.currentWidget() == reopened.pdf_tab
    assert reopened.pdf_view.pageNavigator().currentPage() == 2
    assert reopened.pdf_zoom_combo.currentData() == 1.25
    assert reopened.pdf_view.zoomFactor() == 1.25
    assert reopened.reading_status_combo.currentData() == "reading"
    assert "#FFF4CB" in reopened.reading_status_combo.styleSheet()
    assert reopened.favorite_button.isChecked() is True
    assert reopened.favorite_button.text() == "★"
    assert "★" in reopened.paper_list.currentItem().text()
    assert "Reading" in reopened.paper_list.currentItem().text()
    library.create_paper_from_search(project["id"], {"title": "An Unread Paper"})
    reopened.refresh_papers()
    reopened.sort_combo.setCurrentIndex(reopened.sort_combo.findData("reading_status"))
    assert reopened.current_paper_id == paper["id"]
    assert reopened.paper_list.item(0).data(Qt.ItemDataRole.UserRole) == paper["id"]
    assert reopened.pdf_view.pageNavigator().currentPage() == 2
    reopened.reading_status_combo.setCurrentIndex(2)
    assert "#E5EFFF" in reopened.reading_status_combo.styleSheet()
    assert reopened.paper_list.item(1).data(Qt.ItemDataRole.UserRole) == paper["id"]
    assert reopened.pdf_view.pageNavigator().currentPage() == 2
    reopened.favorite_button.click()
    assert reopened.favorite_button.text() == "☆"
    assert reopened.favorite_button.accessibleName() == "Add to favorites"
    reopened.close()


def test_pdf_zoom_buttons_wheel_pinch_and_custom_zoom_restore(tmp_path):
    root = str(tmp_path / "library")
    source = str(tmp_path / "reader.pdf")
    create_multi_page_test_pdf(source)
    library = Library(root)
    project = library.create_project("Research")
    paper = library.import_pdf(project["id"], source)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    window = MainWindow(manager)
    window.navigate_to_paper(paper["id"])
    window.detail_tabs.setCurrentWidget(window.pdf_tab)
    window.show()
    application.processEvents()
    QTest.mouseClick(window.reading_status_combo, Qt.MouseButton.LeftButton)
    assert window.reading_status_combo.view().isVisible()
    window.reading_status_combo.hidePopup()
    for status_index in range(3):
        window.reading_status_combo.setCurrentIndex(status_index)
        application.processEvents()
        assert window.reading_status_combo.palette().color(QPalette.ColorRole.Text).name() == "#242528"
        assert window.reading_status_combo.view().palette().color(QPalette.ColorRole.Text).name() == "#242528"
        combo = window.reading_status_combo
        font, position, arrow = combo.content_geometry()
        label = QFontMetricsF(font).tightBoundingRect(combo.currentText()).translated(position)
        left_margin = label.left()
        right_margin = combo.width() - (arrow.x() + 3.625)
        assert abs(left_margin - right_margin) < 0.01
        assert abs(label.center().y() - combo.height() / 2) < 0.01
        assert not font.bold()
    reader = FullscreenPdfDialog(
        window, paper["file_path"], paper["title"],
        library=library, paper_id=paper["id"],
    )
    for view, combo, zoom_in, zoom_out in (
        (window.pdf_view, window.pdf_zoom_combo, window.pdf_zoom_in_button, window.pdf_zoom_out_button),
        (reader.view, reader.zoom_combo, reader.zoom_in_button, reader.zoom_out_button),
    ):
        fitted_zoom = view.effective_zoom()
        zoom_in.click()
        assert view.zoomMode() == view.ZoomMode.Custom
        assert abs(view.zoomFactor() - fitted_zoom * 1.2) < 0.0001
        combo.setCurrentIndex(2)
        view.pageNavigator().jump(1, QPointF())
        zoom_in.click()
        assert view.zoomFactor() == 1.2
        assert combo.currentData() == 1.2
        assert view.pageNavigator().currentPage() == 1
        zoom_out.click()
        assert view.zoomFactor() == 1.0
        wheel = QWheelEvent(
            QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        application.sendEvent(view.viewport(), wheel)
        assert view.zoomFactor() == 1.2
        pinch = QNativeGestureEvent(
            Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(),
            2, QPointF(100, 100), QPointF(100, 100), QPointF(100, 100),
            0.25, QPointF(),
        )
        application.sendEvent(view.viewport(), pinch)
        assert view.zoomFactor() == 1.5
        assert combo.currentData() == 1.5
        assert view.pageNavigator().currentPage() == 1
        plain_scroll = QWheelEvent(
            QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        application.sendEvent(view.viewport(), plain_scroll)
        assert view.zoomFactor() == 1.5
        zoom_in.click()
        zoom_in.click()
        assert view.zoomFactor() == 2.16
        assert combo.currentData() == 2.16
        assert combo.count() == 6
        view.scale_zoom(100)
        assert view.zoomFactor() == 4.0
        view.scale_zoom(0.001)
        assert view.zoomFactor() == 0.25
        view.scale_zoom(8.64)
        assert view.zoomFactor() == 2.16
    reader.close()
    window.close()
    assert library.get_paper(paper["id"])["reader_zoom"] == "2.16"
    reopened = MainWindow(manager)
    reopened.navigate_to_paper(paper["id"])
    assert reopened.pdf_zoom_combo.currentData() == 2.16
    assert reopened.pdf_view.zoomFactor() == 2.16
    reopened.close()


def test_modern_dropdown_supports_keyboard_selection_and_escape():
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    combo = ModernComboBox()
    combo.addItem("Recently added", "created")
    combo.addItem("Title A–Z", "title")
    combo.addItem("Author", "author")
    combo.show()
    changes = Mock()
    combo.currentIndexChanged.connect(changes)
    combo.showPopup()
    application.processEvents()
    assert combo.view().isVisible()
    popup_image = combo.view().window().grab().toImage()
    assert popup_image.pixelColor(0, 0).alpha() == 0
    assert popup_image.pixelColor(popup_image.width() // 2, popup_image.height() - 10).name() == "#ffffff"
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentData() == "title"
    changes.assert_called_once_with(1)
    combo.showPopup()
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert combo.currentData() == "title"
    assert not combo.view().isVisible()
    combo.close()


def test_slim_scrollbars_have_no_arrow_buttons_and_still_scroll():
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    apply_light_theme(application)
    for orientation in (Qt.Orientation.Vertical, Qt.Orientation.Horizontal):
        bar = QScrollBar(orientation)
        bar.setRange(0, 100)
        bar.setPageStep(20)
        bar.resize(10, 200)
        if orientation == Qt.Orientation.Horizontal:
            bar.resize(200, 10)
        bar.show()
        application.processEvents()
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        for control in (QStyle.SubControl.SC_ScrollBarAddLine, QStyle.SubControl.SC_ScrollBarSubLine):
            rect = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, control, bar)
            assert rect.isEmpty()
        QTest.keyClick(bar, Qt.Key.Key_End)
        assert bar.value() == 100
        QTest.keyClick(bar, Qt.Key.Key_Home)
        assert bar.value() == 0
        bar.close()


def test_selecting_papers_keeps_pane_widths_and_window_size_stable(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Research")
    short = library.create_paper_from_search(project["id"], {"title": "Short Paper"})
    long = library.create_paper_from_search(project["id"], {
        "title": "Learning Embodiment-Agnostic Robot Manipulation Policies from "
        "Unstructured Human Videos with General-Purpose World Models",
        "authors": "; ".join(["Alexandra Researcher" + str(index) for index in range(40)]),
        "doi": "10.1234/" + "long-publisher-identifier" * 8,
        "abstract": "This paper explores robot learning. " * 80,
    })
    library.update_paper_tags(short["id"], "robotics")
    pdf_path = str(tmp_path / "paper.pdf")
    create_multi_page_test_pdf(pdf_path)
    pdf_paper = library.import_pdf(project["id"], pdf_path)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    window = MainWindow(manager)
    window.navigate_to_paper(short["id"])
    window.resize(1280, 800)
    window.show()
    application.processEvents()
    window.library_splitter.setSizes([230, 360, 688])
    application.processEvents()
    initial_size = window.size()
    initial_panes = window.library_splitter.sizes()
    for paper in (long, pdf_paper, short, long, pdf_paper, short):
        window.select_paper_by_id(paper["id"])
        application.processEvents()
        assert window.size() == initial_size
        assert window.library_splitter.sizes() == initial_panes
    window.library_splitter.setSizes([260, 410, 728])
    application.processEvents()
    manually_resized_panes = window.library_splitter.sizes()
    assert manually_resized_panes != initial_panes
    window.select_paper_by_id(long["id"])
    application.processEvents()
    assert window.library_splitter.sizes() == manually_resized_panes
    window.select_paper_by_id(pdf_paper["id"])
    for tab_index in (1, 2, 0):
        window.detail_tabs.setCurrentIndex(tab_index)
        application.processEvents()
        assert window.size() == initial_size
        assert window.library_splitter.sizes() == manually_resized_panes
    window.resize(1480, 800)
    application.processEvents()
    assert window.library_splitter.sizes()[:2] == manually_resized_panes[:2]
    window.close()


def test_project_archive_hides_sidebar_and_restore_dialog_returns_it(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    first = library.create_project("Current work")
    archived = library.create_project("Older experiment")
    library.create_paper_from_search(
        archived["id"],
        {"title": "Archived source"},
    )
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    window = MainWindow(manager)
    window.show_library()
    window.show()
    application.processEvents()
    archived_item = project_item_by_id(window, archived["id"])
    window.project_list.setCurrentItem(archived_item)
    assert window.current_project_id == archived["id"]

    window.archive_current_project()
    application.processEvents()

    assert project_item_by_id(window, archived["id"]) is None
    assert project_item_by_id(window, first["id"]) is not None
    assert window.archived_projects_button.isVisible() is True
    assert window.archived_projects_button.text() == "Archived projects (1)…"
    assert library.get_project(archived["id"])["archived"] == 1
    dialog = ArchivedProjectsDialog(window, library)
    assert dialog.project_list.count() == 1
    assert "Older experiment" in dialog.project_list.item(0).text()
    assert "1 source" in dialog.project_list.item(0).text()
    assert dialog.restore_button.isEnabled() is True
    dialog.restore_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.restored_project_id == archived["id"]

    window.refresh_projects(selected_id=dialog.restored_project_id)
    application.processEvents()
    assert project_item_by_id(window, archived["id"]) is not None
    assert window.current_project_id == archived["id"]
    assert window.archived_projects_button.isVisible() is False
    window.close()


def test_project_favorite_stars_preserve_selection_and_survive_restart(tmp_path, monkeypatch):
    root = str(tmp_path / "library")
    library = Library(root)
    first = library.create_project("First")
    second = library.create_project("Second")
    paper = library.create_paper_from_search(first["id"], {"title": "One Paper"})
    library.create_paper_from_search(
        library.get_scrapbook()["id"],
        {"title": "Staged source"},
    )
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    window = MainWindow(manager)
    window.navigate_to_paper(paper["id"])
    window.show()
    application.processEvents()
    second_item = project_item_by_id(window, second["id"])
    second_card = window.project_list.itemWidget(second_item)
    star = second_card.findChild(QPushButton, "projectFavoriteButton")
    assert star.text() == "☆"
    QTest.mouseClick(star, Qt.MouseButton.LeftButton)
    assert library.get_project(second["id"])["favorite"] == 1
    second_item = project_item_by_id(window, second["id"])
    assert window.project_list.row(second_item) == 1
    second_card = window.project_list.itemWidget(second_item)
    star = second_card.findChild(QPushButton, "projectFavoriteButton")
    assert star.text() == "★"
    assert "#D9A000" in star.styleSheet()
    assert window.current_project_id == first["id"]
    assert window.current_paper_id == paper["id"]
    QTest.mouseClick(second_card, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
    assert window.current_project_id == second["id"]
    scrapbook_item = project_item_by_id(window, library.get_scrapbook()["id"])
    scrapbook_card = window.project_list.itemWidget(scrapbook_item)
    assert "border: 2px solid #6B93E8" in scrapbook_card.styleSheet()
    assert scrapbook_card.findChild(
        QPushButton, "projectFavoriteButton"
    ) is None
    assert library.get_scrapbook()["favorite"] == 0
    assert window.current_project_id == second["id"]
    scrapbook_item = project_item_by_id(window, library.get_scrapbook()["id"])
    assert window.project_list.row(scrapbook_item) == 0
    scrapbook_card = window.project_list.itemWidget(scrapbook_item)
    QTest.mouseClick(scrapbook_card, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
    assert window.current_project_id == library.get_scrapbook()["id"]
    window.close()
    reopened = MainWindow(manager)
    reopened_item = project_item_by_id(reopened, second["id"])
    reopened_star = reopened.project_list.itemWidget(reopened_item).findChild(
        QPushButton, "projectFavoriteButton"
    )
    assert reopened_star.isChecked()
    assert reopened_star.text() == "★"
    assert "Remove project" in reopened_star.accessibleName()
    reopened_star.click()
    assert library.get_project(second["id"])["favorite"] == 0
    reopened_item = project_item_by_id(reopened, second["id"])
    reopened_star = reopened.project_list.itemWidget(reopened_item).findChild(
        QPushButton, "projectFavoriteButton"
    )
    assert reopened_star.text() == "☆"
    monkeypatch.setattr(reopened.library, "update_project_favorite", Mock(side_effect=ValueError("Save failed")))
    monkeypatch.setattr("corpus_cabinet.desktop.QMessageBox.warning", Mock())
    reopened_star.click()
    assert not reopened_star.isChecked()
    assert reopened_star.text() == "☆"
    reopened.close()


def test_paper_tag_editor_and_click_to_search(tmp_path, monkeypatch):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Research")
    paper = library.create_paper_from_search(project["id"], {"title": "One Paper"})
    other = library.create_paper_from_search(project["id"], {"title": "Other Paper"})
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    window = MainWindow(manager)
    window.navigate_to_paper(paper["id"])
    assert window.tags_button.text() == "+ Add tags"
    editor = Mock(return_value=("Robotics, latent actions, ROBOTICS, <test>", True))
    monkeypatch.setattr("corpus_cabinet.desktop.QInputDialog.getText", editor)
    window.tags_button.click()
    assert library.list_paper_tags(paper["id"]) == ["<test>", "latent actions", "robotics"]
    assert window.tags_button.text() == "Edit tags"
    assert "#latent actions" in window.paper_tags_label.text()
    assert "&lt;test&gt;" in window.paper_tags_label.text()
    assert "tag:latent%20actions" in window.paper_tags_label.text()
    search = Mock()
    monkeypatch.setattr(window, "open_library_search", search)
    window.paper_tags_label.linkActivated.emit("tag:latent%20actions")
    search.assert_called_once_with(query='tag:"latent actions"')
    editor.return_value = ("discarded", False)
    window.tags_button.click()
    assert "discarded" not in library.list_paper_tags(paper["id"])
    dialog = LibrarySearchDialog(window, library)
    dialog.search_finished({"revision": dialog.revision, "results": library.search_library("tag:robotics")})
    assert "Tags" in dialog.result_list.item(0).text()
    assert "latent actions" in dialog.result_list.item(0).text()
    dialog.close()
    window.navigate_to_paper(other["id"])
    assert window.tags_button.text() == "+ Add tags"
    assert window.paper_tags_label.isHidden()
    window.navigate_to_paper(paper["id"])
    assert "#robotics" in window.paper_tags_label.text()
    editor.return_value = ("", True)
    window.tags_button.click()
    assert library.list_paper_tags(paper["id"]) == []
    assert window.paper_tags_label.isHidden()
    window.close()


def test_paper_cards_fit_full_titles_metadata_and_pills_after_resize(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Research")
    titles = [
        "Zero Shot Learning Recent Advances in Robotics",
        "FTP-1: A Generalist Foundation Tactile Policy Across Tactile Sensors "
        "for Contact-Rich Manipulation",
        "LUCID: Learning Embodiment-Agnostic Intent Models from Unstructured "
        "Human Videos for Scalable Dexterous Robot Skill Acquisition",
    ]
    for title in titles:
        paper = library.create_paper_from_search(
            project["id"], {"title": title, "authors": "Chen, A.; Doe, J.", "year": 2026}
        )
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)
    window = MainWindow(manager)
    window.navigate_to_paper(paper["id"])
    window.show_library()
    window.show()
    heights = []
    for width in (220, 430):
        window.paper_list.setFixedWidth(width)
        application.processEvents()
        window.paper_list.doItemsLayout()
        total_height = 0
        for row in range(window.paper_list.count()):
            item = window.paper_list.item(row)
            index = window.paper_list.model().index(row, 0)
            rect = window.paper_list.visualItemRect(item)
            option = QStyleOptionViewItem()
            option.initFrom(window.paper_list)
            document = window.paper_list.itemDelegate().card_document(
                option, index, rect.width() - 4
            )
            paper = item.data(Qt.ItemDataRole.UserRole + 1)
            assert paper["title"] in document.toPlainText()
            assert "2026" in document.toPlainText()
            assert rect.height() >= document.size().height() + 61
            total_height += rect.height()
        heights.append(total_height)
    assert heights[0] > heights[1]
    window.reading_status_combo.setCurrentIndex(2)
    assert window.paper_list.currentItem().data(
        Qt.ItemDataRole.UserRole + 1
    )["reading_status"] == "read"
    window.close()


def test_project_notes_flush_on_close_and_remain_separate(tmp_path):
    library = Library(str(tmp_path / "library"))
    project = library.create_project("Synthesis")
    paper = library.create_paper_from_search(project["id"], {"title": "One Paper"})
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    dialog = ProjectNotesDialog(None, library, project)
    dialog.editor.setPlainText("Shared limitation: no matched-budget baseline.")
    assert dialog.status_label.text() == "Saving…"
    dialog.reject()
    saved_project = library.get_project(project["id"])
    assert saved_project["notes"] == "Shared limitation: no matched-budget baseline."
    assert library.get_paper(paper["id"])["notes"] == ""
    reopened = ProjectNotesDialog(None, library, saved_project)
    assert reopened.editor.toPlainText() == saved_project["notes"]
    reopened.close()


def test_library_search_runs_locally_and_ignores_stale_results(tmp_path):
    library = Library(str(tmp_path / "library"))
    project = library.create_project("Search")
    paper = library.create_paper_from_search(project["id"], {"title": "A Robot Paper"})
    library.update_paper_notes(paper["id"], "Remember the calibration baseline.")
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    dialog = LibrarySearchDialog(None, library)
    dialog.thread_pool = Mock()
    dialog.query_input.setText("calibration")
    dialog.start_search()
    assert dialog.thread_pool.start.call_count == 1
    payloads = []
    task = LibrarySearchTask(library.path, "calibration", dialog.revision)
    task.signals.finished.connect(payloads.append)
    task.run()
    dialog.search_finished({"revision": dialog.revision - 1, "results": payloads[0]["results"]})
    assert dialog.result_list.count() == 0
    dialog.search_finished(payloads[0])
    assert dialog.result_list.count() == 1
    assert "Personal notes" in dialog.result_list.item(0).text()
    dialog.open_result(dialog.result_list.item(0))
    assert dialog.selected_paper["id"] == paper["id"]
    dialog.close()


def test_selecting_legacy_pdf_repairs_its_missing_abstract(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "legacy.pdf")
    create_standalone_abstract_pdf(source_path)
    library = Library(root)
    project = library.create_project("Legacy papers")
    paper = library.import_pdf(project["id"], source_path)
    connection = library.connect()
    connection.execute(
        "UPDATE papers SET abstract = '' WHERE id = ?",
        (paper["id"],),
    )
    connection.commit()
    connection.close()
    manager = WorkspaceManager(str(tmp_path / "config.json"), root)

    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    window = MainWindow(manager)
    for index in range(window.project_list.count()):
        project_item = window.project_list.item(index)
        if project_item.data(Qt.ItemDataRole.UserRole) == project["id"]:
            window.project_list.setCurrentItem(project_item)
            break
    window.select_paper_by_id(paper["id"])
    application.processEvents()

    repaired = library.get_paper(paper["id"])
    assert repaired["abstract"] == (
        "A recovered abstract from an existing local PDF."
    )
    assert window.abstract_text.toPlainText() == repaired["abstract"]
    window.close()


def test_bibtex_dialog_labels_generated_citation_as_reviewable():
    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    dialog = BibtexDialog(
        None,
        {
            "id": 7,
            "title": "A Useful Paper",
            "authors": "Jane Doe, John Roe",
            "conference": "Example Conference",
            "year": 2025,
            "bibtex": "",
            "doi": "",
        },
        {},
        True,
        save_bibtex,
    )

    assert dialog.editor.toPlainText().startswith("@misc{doe2025Useful")
    assert "safest option" in dialog.warning_label.text()
    assert "Google Scholar manually" in dialog.warning_label.text()
    assert dialog.scholar_button.isEnabled() is False
    dialog.copy_bibtex()
    assert QApplication.clipboard().text().startswith("@misc")
    dialog.save_changes()
    assert dialog.source_label.text() == "Reviewed BibTeX saved to this paper."
    dialog.close()


def test_project_picker_and_import_task_support_multiple_projects(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "paper.pdf")
    create_test_pdf(source_path)
    library = Library(root)
    first_project = library.create_project("First")
    second_project = library.create_project("Second")

    application = QApplication.instance()
    if application is None:
        application = QApplication([])

    picker = ProjectSelectionDialog(
        None,
        library.list_projects(),
        selected_ids=[first_project["id"]],
        locked_ids=[first_project["id"]],
        require_unlocked_selection=False,
    )
    first_item = None
    second_item = None
    for index in range(picker.project_list.count()):
        item = picker.project_list.item(index)
        project_id = item.data(Qt.ItemDataRole.UserRole)
        if project_id == first_project["id"]:
            first_item = item
        if project_id == second_project["id"]:
            second_item = item
    assert bool(first_item.flags() & Qt.ItemFlag.ItemIsEnabled) is False
    second_item.setCheckState(Qt.CheckState.Checked)
    assert picker.selected_project_ids() == [
        first_project["id"],
        second_project["id"],
    ]
    picker.close()

    scrapbook = library.get_scrapbook()
    exclusive_picker = ProjectSelectionDialog(
        None,
        library.list_projects(),
        selected_ids=[first_project["id"]],
        exclusive_ids=[scrapbook["id"]],
    )
    scrapbook_item = None
    first_project_item = None
    for index in range(exclusive_picker.project_list.count()):
        item = exclusive_picker.project_list.item(index)
        project_id = item.data(Qt.ItemDataRole.UserRole)
        if project_id == scrapbook["id"]:
            scrapbook_item = item
        if project_id == first_project["id"]:
            first_project_item = item
    scrapbook_item.setCheckState(Qt.CheckState.Checked)
    assert first_project_item.checkState() == Qt.CheckState.Unchecked
    first_project_item.setCheckState(Qt.CheckState.Checked)
    assert scrapbook_item.checkState() == Qt.CheckState.Unchecked
    exclusive_picker.close()

    imported = []
    task = ImportTask(
        root,
        [first_project["id"], second_project["id"]],
        [source_path],
    )
    task.signals.finished.connect(imported.extend)
    task.run()

    assert len(imported) == 2
    assert imported[0]["project_id"] == first_project["id"]
    assert imported[1]["project_id"] == second_project["id"]
    assert imported[0]["file_path"] != imported[1]["file_path"]
    assert os.path.exists(imported[0]["file_path"])
    assert os.path.exists(imported[1]["file_path"])

    duplicate_payloads = []
    duplicate_task = ImportTask(
        root,
        [first_project["id"]],
        [source_path],
    )
    duplicate_task.signals.duplicatesFound.connect(duplicate_payloads.append)
    duplicate_task.run()
    assert len(duplicate_payloads) == 1
    assert duplicate_payloads[0]["duplicates"][0]["match_reason"] == (
        "same title"
    )
    assert len(library.list_papers(first_project["id"])) == 1


def test_online_download_creates_physical_copy_per_project(
    tmp_path,
    monkeypatch,
):
    global DOWNLOAD_FIXTURE_PATH

    root = str(tmp_path / "library")
    DOWNLOAD_FIXTURE_PATH = str(tmp_path / "download.pdf")
    create_test_pdf(DOWNLOAD_FIXTURE_PATH)
    library = Library(root)
    first_project = library.create_project("First")
    second_project = library.create_project("Second")
    monkeypatch.setattr(
        "corpus_cabinet.desktop.download_pdf",
        copy_download_fixture,
    )

    downloaded = []
    task = DownloadTask(
        root,
        [first_project["id"], second_project["id"]],
        {
            "title": "Online Paper",
            "pdf_url": "https://example.org/paper.pdf",
            "source": "Test provider",
        },
        {},
    )
    task.signals.finished.connect(downloaded.extend)
    task.run()

    assert len(downloaded) == 2
    assert downloaded[0]["title"] == "Online Paper"
    assert downloaded[0]["file_path"] != downloaded[1]["file_path"]
    assert os.path.exists(downloaded[0]["file_path"])
    assert os.path.exists(downloaded[1]["file_path"])
