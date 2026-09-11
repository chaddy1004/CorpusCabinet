"""Verify the native Qt window can display a paper from a local library.

The test uses Qt's offscreen platform and a temporary PDF/library, so it does
not open a real window or contact external services.
"""

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtWidgets import QApplication

from corpus_cabinet.desktop import MainWindow, SearchResultsDialog
from corpus_cabinet.storage import Library, WorkspaceManager


def create_test_pdf(path):
    """Create a one-page PDF fixture at path."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Desktop MVP Paper\nA short abstract")
    document.save(path)
    document.close()


def accept_search_result(result):
    """Accept a dialog callback in the UI test."""
    return True


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
    window.paper_list.setCurrentRow(0)
    application.processEvents()

    assert window.project_list.count() == 1
    assert window.paper_list.count() == 1
    assert window.detail_title.text() == "Desktop MVP Paper"
    assert window.pdf_document.pageCount() == 1
    assert window.offline_mode_checkbox.isChecked() is False
    assert window.search_online_button.isEnabled() is True

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
    }
    assert window.apply_online_metadata(online_result) is True
    assert window.detail_title.text() == "Online Metadata Title"
    assert window.abstract_text.toPlainText() == "An online abstract."
    assert window.open_source_button.isEnabled() is True
    assert window.scholar_button.isEnabled() is True

    dialog = SearchResultsDialog(
        window,
        [online_result],
        project["id"],
        library.list_papers(project["id"])[0]["id"],
        False,
        accept_search_result,
        accept_search_result,
        accept_search_result,
    )
    assert dialog.result_list.count() == 1
    assert dialog.apply_button.isEnabled() is True
    assert dialog.add_button.isEnabled() is True
    assert dialog.download_button.isEnabled() is True
    dialog.close()

    offline_dialog = SearchResultsDialog(
        window,
        [online_result],
        project["id"],
        paper["id"],
        True,
        accept_search_result,
        accept_search_result,
        accept_search_result,
    )
    assert offline_dialog.open_source_button.isEnabled() is False
    assert offline_dialog.scholar_button.isEnabled() is False
    assert offline_dialog.download_button.isEnabled() is False
    offline_dialog.close()

    window.offline_mode_checkbox.setChecked(True)
    application.processEvents()
    assert window.network_status_label.text().startswith("Offline Mode")
    assert window.search_online_button.isEnabled() is False
    assert window.open_source_button.isEnabled() is False
    assert window.scholar_button.isEnabled() is False
    assert manager.is_offline_mode() is True

    window.offline_mode_checkbox.setChecked(False)
    application.processEvents()
    assert window.search_online_button.isEnabled() is True
    assert window.open_source_button.isEnabled() is True
    assert window.scholar_button.isEnabled() is True
    assert manager.is_offline_mode() is False

    window.start_online_search = Mock()
    window.search_input.setText("A paper entered in the search bar")
    window.search_input.returnPressed.emit()
    assert window.start_online_search.call_args.args == (
        "A paper entered in the search bar",
    )

    window.close()
