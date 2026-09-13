"""Verify the native Qt window can display a paper from a local library.

The test uses Qt's offscreen platform and a temporary PDF/library, so it does
not open a real window or contact external services.
"""

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtWidgets import QApplication

from corpus_cabinet.desktop import (
    BibtexDialog,
    DiscoveryDialog,
    MainWindow,
    comfortable_abstract_html,
    latex_to_html,
    latex_to_plain_text,
)
from corpus_cabinet.storage import Library, WorkspaceManager


def create_test_pdf(path):
    """Create a one-page PDF fixture at path."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Desktop MVP Paper\nA short abstract")
    document.save(path)
    document.close()


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
    window.paper_list.setCurrentRow(0)
    application.processEvents()

    assert window.project_list.count() == 1
    assert window.paper_list.count() == 1
    assert window.detail_title.text() == "Desktop MVP Paper"
    assert window.pdf_document.pageCount() == 1
    assert window.offline_mode_checkbox.isChecked() is False
    assert window.discover_button.isEnabled() is True
    assert window.discover_button.text() == "Add online"
    assert window.search_online_action.text() == "Search by title…"
    assert window.add_link_action.text() == "Add from link…"
    assert window.page_stack.currentWidget() == window.home_page
    assert window.open_library_button.text() == "Library folder…"
    assert "Current library:" in window.open_library_button.toolTip()
    assert window.home_project_count.text() == "1"
    assert window.home_paper_count.text() == "1"
    assert hasattr(window, "home_pdf_count") is False
    assert window.abstract_font_toggle.isEnabled() is True
    assert window.abstract_font_toggle.isChecked() is False

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
    assert manager.is_dyslexic_font_enabled() is True

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
    assert window.discover_button.isEnabled() is False
    assert manager.is_offline_mode() is False

    window.network_reachability_changed(
        QNetworkInformation.Reachability.Online
    )
    application.processEvents()
    assert window.network_status_label.text() == "Online"
    assert window.discover_button.isEnabled() is True

    window.offline_mode_checkbox.setChecked(True)
    application.processEvents()
    assert window.network_status_label.text() == "Working offline"
    assert window.discover_button.isEnabled() is False
    assert window.open_source_button.isEnabled() is False
    assert window.scholar_button.isEnabled() is False
    assert manager.is_offline_mode() is True

    window.offline_mode_checkbox.setChecked(False)
    application.processEvents()
    assert window.discover_button.isEnabled() is True
    assert window.open_source_button.isEnabled() is True
    assert window.scholar_button.isEnabled() is True
    assert manager.is_offline_mode() is False

    window.close()


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
    window.select_paper_by_id(paper["id"])
    application.processEvents()

    assert window.pdf_stack.currentWidget() == window.pdf_empty_panel
    assert window.pdf_status_label.text() == "PDF not downloaded"
    assert window.pdf_primary_button.text() == "Download PDF"
    assert window.pdf_choose_button.isVisible() is False
    window.show_library()
    window.detail_tabs.setCurrentIndex(1)
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
