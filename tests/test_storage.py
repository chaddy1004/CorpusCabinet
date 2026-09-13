"""Verify local library and workspace behavior without external services.

The tests create temporary PDF files and libraries. They write only below
pytest's temporary directory and remove those artifacts when each test ends.
"""

import os

import pymupdf

from corpus_cabinet.assistant import AssistantEngine
from corpus_cabinet.storage import Library, WorkspaceManager


def create_test_pdf(path):
    """Create a small text PDF fixture at path."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Test Paper Title\nJane Doe\nAbstract text")
    document.save(path)
    document.close()


def test_library_import_search_and_delete(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "source.pdf")
    create_test_pdf(source_path)

    library = Library(root)
    project = library.create_project("Vision Papers")
    paper = library.import_pdf(project["id"], source_path)

    assert paper["title"] == "Test Paper Title"
    assert paper["authors"] == ""
    assert "Abstract text" in paper["extracted_text"]
    assert os.path.exists(paper["file_path"])
    assert len(library.list_papers(project["id"], "test paper")) == 1

    library.delete_paper(paper["id"])
    assert library.get_paper(paper["id"]) is None
    assert not os.path.exists(paper["file_path"])


def test_attach_pdf_to_saved_citation(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "attached.pdf")
    create_test_pdf(source_path)

    library = Library(root)
    project = library.create_project("Robotics")
    paper = library.create_paper_from_search(
        project["id"],
        {
            "title": "Published Paper",
            "venue": "Robotics Conference",
            "citation_count": 42,
        },
    )

    attached = library.attach_pdf(paper["id"], source_path)

    assert attached["title"] == "Published Paper"
    assert attached["conference"] == "Robotics Conference"
    assert attached["citation_count"] == 42
    assert os.path.exists(attached["file_path"])
    assert "Abstract text" in attached["extracted_text"]


def test_workspace_manager_persists_active_library(tmp_path):
    config_path = str(tmp_path / "config" / "workspaces.json")
    default_path = str(tmp_path / "default-library")
    manager = WorkspaceManager(config_path, default_path)
    assert manager.current() == os.path.abspath(default_path)

    second_path = str(tmp_path / "second-library")
    manager.activate(second_path, "Second Library")
    manager.set_offline_mode(True)
    manager.set_dyslexic_font(True)

    reloaded = WorkspaceManager(config_path, default_path)
    assert reloaded.current() == os.path.abspath(second_path)
    assert reloaded.find(second_path)["name"] == "Second Library"
    assert reloaded.is_offline_mode() is True
    assert reloaded.is_dyslexic_font_enabled() is True


def test_project_search_context_persists(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Robotics")

    updated = library.update_project_search_context(
        project["id"],
        "mobile manipulation, imitation learning",
    )

    assert updated["search_context"] == (
        "mobile manipulation, imitation learning"
    )
    reloaded = Library(root)
    assert reloaded.get_project(project["id"])["search_context"] == (
        "mobile manipulation, imitation learning"
    )


def test_search_metadata_record_supports_ai_context_and_local_pdf_later(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Online Papers")
    result = {
        "title": "A Searchable Paper",
        "authors": "Jane Doe, John Roe",
        "venue": "Example Conference",
        "year": 2024,
        "doi": "10.1000/searchable",
        "abstract": "This paper has a useful abstract.",
        "external_id": "W123",
        "external_url": "https://example.org/paper",
        "project_url": "https://example.github.io/paper",
        "pdf_url": "https://example.org/paper.pdf",
        "source": "Crossref, OpenAlex",
    }

    paper = library.create_paper_from_search(project["id"], result)

    assert paper["file_path"] == ""
    assert paper["doi"] == "10.1000/searchable"
    assert paper["conference"] == "Example Conference"
    assert paper["project_url"] == "https://example.github.io/paper"
    context = AssistantEngine(library).prepare_context(
        paper["id"],
        "What is the paper about?",
    )
    assert context["text"] == result["abstract"]

    updated_result = dict(result)
    updated_result["title"] = "A Better Searchable Paper"
    updated = library.update_paper_metadata(paper["id"], updated_result)
    assert updated["title"] == "A Better Searchable Paper"

    reviewed = library.update_paper_bibtex(
        paper["id"],
        "@article{reviewed, title={Reviewed}}",
    )
    assert reviewed["bibtex"].startswith("@article{reviewed")
