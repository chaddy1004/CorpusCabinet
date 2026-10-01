"""Verify local library and workspace behavior without external services.

The tests create temporary PDF files and libraries. They write only below
pytest's temporary directory and remove those artifacts when each test ends.
"""

import os
import json

import pymupdf
import pytest

from corpus_cabinet.assistant import AssistantEngine
from corpus_cabinet.storage import Library, WorkspaceManager


def article_capture():
    """Return a small provider-neutral web capture for storage tests."""
    return {
        "source_type": "article",
        "title": "A Practical Engineering Article",
        "authors": "Ada Engineer",
        "venue": "Engineering Notes",
        "year": 2026,
        "abstract": "Implementation details that complement published work.",
        "canonical_url": "https://example.com/practical-engineering",
        "external_url": "https://example.com/practical-engineering",
        "source": "Web article",
        "extracted_text": "A controller implementation with a useful failure checklist.",
        "source_metadata": {"published": "2026-09-25"},
        "source_html": b"<article><p>Controller implementation.</p></article>",
        "assets": [],
        "document": {
            "version": 1,
            "engine": "Web article",
            "title": "A Practical Engineering Article",
            "source_url": "https://example.com/practical-engineering",
            "partial": False,
            "warnings": [],
            "blocks": [
                {
                    "id": 1,
                    "kind": "paragraph",
                    "role": "body",
                    "text": "Controller implementation.",
                    "links": [],
                }
            ],
        },
    }


def create_test_pdf(path):
    """Create a small text PDF fixture at path."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Test Paper Title", fontsize=18)
    page.insert_text((72, 94), "Jane Doe", fontsize=11)
    page.insert_text((72, 110), "Example University", fontsize=9)
    page.insert_text((72, 142), "Abstract: Abstract text", fontsize=10)
    page.insert_text((72, 760), "arXiv:2401.12345v2", fontsize=8)
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
    assert paper["authors"] == "Jane Doe"
    assert paper["abstract"] == "Abstract text"
    assert paper["external_id"] == "2401.12345"
    assert paper["external_url"] == "https://arxiv.org/abs/2401.12345"
    assert paper["metadata_source"] == "Local PDF"
    assert "Abstract text" in paper["extracted_text"]
    assert os.path.exists(paper["file_path"])
    assert len(library.list_papers(project["id"], "test paper")) == 1

    library.delete_paper(paper["id"])
    assert library.get_paper(paper["id"]) is None
    assert not os.path.exists(paper["file_path"])


def test_article_sources_migrate_copy_search_and_reopen_offline(tmp_path):
    library = Library(str(tmp_path / "library"))
    first_project = library.create_project("Engineering")
    second_project = library.create_project("Implementation")

    sources = library.create_source_copies(
        [first_project["id"], second_project["id"]],
        article_capture(),
    )

    assert len(sources) == 2
    assert sources[0]["source_type"] == "article"
    assert sources[0]["canonical_url"] == article_capture()["canonical_url"]
    assert sources[0]["content_path"] == sources[1]["content_path"]
    assert os.path.isfile(sources[0]["content_path"])
    with open(sources[0]["content_path"], encoding="utf-8") as handle:
        document = json.load(handle)
    assert document["engine"] == "Web article"
    assert library.list_sources(
        first_project["id"], source_type="article"
    )[0]["id"] == sources[0]["id"]
    assert library.list_sources(
        first_project["id"], source_type="paper"
    ) == []

    results = library.search_library("failure checklist")
    assert [result["id"] for result in results] == [sources[0]["id"], sources[1]["id"]]
    duplicates = library.find_duplicate_papers(
        [first_project["id"]],
        article_capture(),
    )
    assert duplicates[0]["match_reason"] == "same source URL"

    copied = library.copy_paper(sources[0]["id"], second_project["id"])
    assert copied["source_type"] == "article"
    assert copied["content_path"] == sources[0]["content_path"]


def test_existing_research_project_page_is_backfilled_on_reopen(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    project = library.create_project("Research")
    capture = article_capture()
    capture["canonical_url"] = "https://hanzhic.github.io/vidbot-project/"
    capture["external_url"] = capture["canonical_url"]
    capture["source_html"] = b"""
    <main><h1>VidBot</h1><h2>Abstract</h2>
    <p>A research project about reusable robot policies from video.</p>
    <a href="https://arxiv.org/abs/2503.07135">Paper</a>
    <h2>BibTeX</h2><pre>@inproceedings{vidbot2025}</pre></main>
    """
    source = library.create_source_from_capture(project["id"], capture)
    connection = library.connect()
    connection.execute(
        "UPDATE papers SET source_metadata = '{}' WHERE id = ?",
        (source["id"],),
    )
    connection.commit()
    connection.close()

    reopened = Library(root)
    migrated = reopened.get_source(source["id"])

    assert migrated["source_metadata_data"]["content_category"] == "research"


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
    assert attached["authors"] == "Jane Doe"
    assert attached["abstract"] == "Abstract text"
    assert attached["external_id"] == "2401.12345"
    assert os.path.exists(attached["file_path"])
    assert "Abstract text" in attached["extracted_text"]


def test_refresh_pdf_metadata_repairs_a_legacy_blank_record(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "legacy.pdf")
    create_test_pdf(source_path)
    library = Library(root)
    project = library.create_project("Legacy imports")
    paper = library.import_pdf(project["id"], source_path)

    connection = library.connect()
    connection.execute(
        """
        UPDATE papers
        SET title = 'Test', authors = '', abstract = '', external_id = ''
        WHERE id = ?
        """,
        (paper["id"],),
    )
    connection.commit()
    connection.close()

    repaired = library.refresh_pdf_metadata(paper["id"])

    assert repaired["title"] == "Test Paper Title"
    assert repaired["authors"] == "Jane Doe"
    assert repaired["abstract"] == "Abstract text"
    assert repaired["external_id"] == "2401.12345"


def test_copy_paper_creates_independent_record_and_pdf(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "source.pdf")
    create_test_pdf(source_path)

    library = Library(root)
    source_project = library.create_project("Source")
    first_target = library.create_project("First target")
    second_target = library.create_project("Second target")
    paper = library.import_pdf(source_project["id"], source_path)
    paper = library.update_paper_metadata(
        paper["id"],
        {
            "title": "Copied Research Paper",
            "abstract": "Shared metadata with independent records.",
            "citation_count": 12,
        },
    )
    paper = library.update_paper_notes(
        paper["id"],
        "Compare this method with the baseline.",
    )
    library.create_paper_comment(
        paper["id"],
        1,
        "Revisit the assumptions on this page.",
    )

    copies = library.copy_paper_to_projects(
        paper["id"],
        [first_target["id"], second_target["id"]],
    )

    assert len(copies) == 2
    assert copies[0]["title"] == paper["title"]
    assert copies[0]["abstract"] == paper["abstract"]
    assert copies[0]["citation_count"] == 12
    assert copies[0]["notes"] == paper["notes"]
    copied_comments = library.list_paper_comments(copies[0]["id"])
    assert len(copied_comments) == 1
    assert copied_comments[0]["page_number"] == 1
    assert copied_comments[0]["body"] == (
        "Revisit the assumptions on this page."
    )
    assert copies[0]["file_path"] != paper["file_path"]
    assert copies[0]["file_path"] != copies[1]["file_path"]
    assert os.path.exists(copies[0]["file_path"])
    assert os.path.exists(copies[1]["file_path"])


def test_pdf_comments_are_page_specific_and_follow_paper_lifecycle(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "source.pdf")
    create_test_pdf(source_path)
    library = Library(root)
    project = library.create_project("Reading")
    paper = library.import_pdf(project["id"], source_path)

    first = library.create_paper_comment(paper["id"], 2, "Check figure 2.")
    library.create_paper_comment(paper["id"], 1, "Strong introduction.")

    comments = library.list_paper_comments(paper["id"])
    assert [comment["page_number"] for comment in comments] == [1, 2]
    library.delete_paper_comment(first["id"])
    assert [comment["body"] for comment in library.list_paper_comments(
        paper["id"]
    )] == ["Strong introduction."]

    library.delete_paper(paper["id"])
    assert library.list_paper_comments(paper["id"]) == []


def test_duplicate_detection_is_scoped_to_destination_projects(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    first_project = library.create_project("First")
    second_project = library.create_project("Second")
    paper = library.create_paper_from_search(
        first_project["id"],
        {
            "title": "A Study of Generalist Robot Policies",
            "doi": "https://doi.org/10.1000/ROBOT.1",
            "external_id": "2401.12345",
        },
    )

    doi_matches = library.find_duplicate_papers(
        [first_project["id"]],
        {"doi": "10.1000/robot.1", "title": "Different title"},
    )
    assert doi_matches[0]["id"] == paper["id"]
    assert doi_matches[0]["match_reason"] == "same DOI"

    title_matches = library.find_duplicate_papers(
        [first_project["id"]],
        {"title": "A study of generalist robot-policies!"},
    )
    assert title_matches[0]["match_reason"] == "same title"
    assert library.find_duplicate_papers(
        [second_project["id"]],
        {"doi": "10.1000/robot.1"},
    ) == []


def test_reading_history_status_and_project_notes_persist(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "source.pdf")
    create_test_pdf(source_path)
    library = Library(root)
    first_project = library.create_project("First")
    second_project = library.create_project("Second")
    paper = library.import_pdf(first_project["id"], source_path)
    library.save_reader_state(paper["id"], 2, "1.25")
    library.update_reading_status(paper["id"], "reading")
    library.update_paper_favorite(paper["id"], True)
    library.update_reader_source(paper["id"], "pdf_preview")
    library.update_project_notes(first_project["id"], "Compare baseline assumptions.")

    reopened = Library(root)
    saved = reopened.get_paper(paper["id"])
    assert saved["last_page"] == 2
    assert saved["reader_zoom"] == "1.25"
    assert saved["reading_status"] == "reading"
    assert saved["favorite"] == 1
    assert saved["reader_source"] == "pdf_preview"
    assert reopened.recent_reading_papers()[0]["id"] == paper["id"]
    assert reopened.get_project(first_project["id"])["notes"] == (
        "Compare baseline assumptions."
    )
    assert reopened.get_project(second_project["id"])["notes"] == ""
    copied = reopened.copy_paper(paper["id"], second_project["id"])
    assert copied["reading_status"] == "reading"
    assert copied["favorite"] == 1
    assert copied["reader_source"] == "pdf_preview"
    assert copied["last_page"] == 0
    assert copied["last_opened"] == ""
    with pytest.raises(ValueError, match="Choose"):
        reopened.update_reading_status(paper["id"], "finished")
    with pytest.raises(ValueError, match="Reader source"):
        reopened.update_reader_source(paper["id"], "unknown")
    with pytest.raises(ValueError, match="zoom"):
        reopened.save_reader_state(paper["id"], 0, "invalid")
    for zoom in ("nan", "inf", "0.1", "4.1"):
        with pytest.raises(ValueError, match="zoom"):
            reopened.save_reader_state(paper["id"], 0, zoom)
    for zoom in ("0.25", "2.16", "4.0", "fit_width", "fit_page"):
        reopened.save_reader_state(paper["id"], 0, zoom)
        assert reopened.get_paper(paper["id"])["reader_zoom"] == zoom


def test_library_search_covers_saved_text_notes_and_pdf_comments(tmp_path):
    library = Library(str(tmp_path / "library"))
    first_project = library.create_project("Robotics")
    second_project = library.create_project("Vision")
    first = library.create_paper_from_search(
        first_project["id"],
        {"title": "Robot Policies", "authors": "Jane Doe", "abstract": "Tactile feedback improves control."},
    )
    second = library.create_paper_from_search(
        second_project["id"],
        {"title": "Visual Learning", "authors": "John Roe"},
    )
    library.update_paper_notes(first["id"], "Check the matched budget baseline: 50%.")
    library.create_paper_comment(second["id"], 3, "Investigate the calibration failure.")
    connection = library.connect()
    connection.execute(
        "UPDATE papers SET extracted_text = ? WHERE id = ?",
        ("The model uses latent actions for demonstration transfer.", second["id"]),
    )
    connection.commit()
    connection.close()

    assert library.search_library("Jane")[0]["match_field"] == "authors"
    assert library.search_library("tactile")[0]["match_field"] == "abstract"
    assert library.search_library("50%")[0]["match_field"] == "notes"
    text_match = library.search_library("latent actions")[0]
    assert text_match["id"] == second["id"]
    assert text_match["project_name"] == "Vision"
    assert text_match["match_field"] == "extracted_text"
    comment_match = library.search_library("calibration")[0]
    assert comment_match["match_field"] == "pdf_comment"
    assert comment_match["match_page_number"] == 3
    assert "calibration" in comment_match["match_excerpt"]
    assert library.search_library("Robot tactile")[0]["id"] == first["id"]
    assert library.search_library("latent nonexistent") == []
    assert library.search_library("_") == []
    assert library.search_library("") == []


def test_project_favorites_are_grouped_with_independent_persistent_orders(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    first = library.create_project("First")
    second = library.create_project("Second")
    third = library.create_project("Third")
    scrapbook = library.get_scrapbook()
    paper = library.create_paper_from_search(first["id"], {"title": "One Paper"})
    assert first["favorite"] == 0
    library.update_project_favorite(second["id"], True)
    library.update_project_favorite(third["id"], True)
    assert [project["id"] for project in library.list_projects()] == [
        scrapbook["id"], second["id"], third["id"], first["id"],
    ]
    library.reorder_projects([third["id"], second["id"]], True)
    assert [project["id"] for project in library.list_projects()] == [
        scrapbook["id"], third["id"], second["id"], first["id"],
    ]
    library.update_project_favorite(third["id"], False)
    assert [project["id"] for project in library.list_projects()] == [
        scrapbook["id"], second["id"], first["id"], third["id"],
    ]
    library.reorder_projects([third["id"], first["id"]], False)
    library.update_project_favorite(second["id"], False)
    assert [project["id"] for project in library.list_projects()] == [
        scrapbook["id"], third["id"], second["id"], first["id"],
    ]
    library.update_project_favorite(second["id"], True)
    with pytest.raises(ValueError, match="cannot be favorited"):
        library.update_project_favorite(scrapbook["id"], True)
    reopened = Library(root)
    assert reopened.get_project(second["id"])["favorite"] == 1
    assert reopened.get_scrapbook()["favorite"] == 0
    assert reopened.get_scrapbook()["color"] == "#4F7FD8"
    assert reopened.get_project(first["id"])["favorite"] == 0
    assert reopened.get_paper(paper["id"])["favorite"] == 0
    assert [project["id"] for project in reopened.list_projects()] == [
        scrapbook["id"], second["id"], third["id"], first["id"],
    ]
    reopened.update_project_favorite(second["id"], False)
    assert reopened.get_project(second["id"])["favorite"] == 0
    with pytest.raises(ValueError, match="does not match"):
        reopened.reorder_projects([first["id"]], False)
    with pytest.raises(ValueError, match="Project not found"):
        reopened.update_project_favorite(-1, True)


def test_projects_archive_without_losing_contents_and_restore(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    active = library.create_project("Active")
    archived = library.create_project("Archive me")
    paper = library.create_paper_from_search(
        archived["id"],
        {"title": "Preserved research source"},
    )
    library.update_project_notes(archived["id"], "Keep this synthesis.")
    library.update_project_favorite(archived["id"], True)

    library.archive_project(archived["id"])

    assert [project["id"] for project in library.list_projects()] == [
        library.get_scrapbook()["id"], active["id"],
    ]
    archived_projects = library.list_archived_projects()
    assert [project["id"] for project in archived_projects] == [archived["id"]]
    assert archived_projects[0]["paper_count"] == 1
    assert library.get_project(archived["id"])["notes"] == "Keep this synthesis."
    assert library.get_project(archived["id"])["favorite"] == 1
    assert library.get_paper(paper["id"])["title"] == "Preserved research source"
    assert library.list_sources(archived["id"]) == []
    assert library.search_library("Preserved research source") == []
    with pytest.raises(ValueError, match="Restore the archived project"):
        library.validate_destination_projects([archived["id"]])
    with pytest.raises(ValueError, match="cannot be archived"):
        library.archive_project(library.get_scrapbook()["id"])

    library.restore_project(archived["id"])

    assert library.list_archived_projects() == []
    assert library.get_project(archived["id"])["archived"] == 0
    assert library.get_project(archived["id"])["favorite"] == 1
    assert library.list_sources(archived["id"])[0]["id"] == paper["id"]
    assert library.search_library("Preserved research source")[0]["id"] == paper["id"]


def test_tags_are_normalized_searchable_copied_and_independent(tmp_path):
    root = str(tmp_path / "library")
    library = Library(root)
    first = library.create_project("Robotics")
    second = library.create_project("Vision")
    paper = library.create_paper_from_search(first["id"], {"title": "Human's Policy Learning"})
    other = library.create_paper_from_search(second["id"], {"title": "Robot Learning"})
    assert library.get_paper(paper["id"])["tags"] == []
    tags = library.update_paper_tags(paper["id"], " Robotics, LATENT   Actions, robotics, , 50%_data")
    assert tags == ["50%_data", "latent actions", "robotics"]
    reopened = Library(root)
    assert reopened.get_paper(paper["id"])["tags"] == tags
    assert reopened.search_library("robotics")[0]["match_field"] == "tags"
    assert reopened.search_library("tag:ROBOTICS")[0]["id"] == paper["id"]
    assert reopened.search_library('tag:"latent actions" Human')[0]["id"] == paper["id"]
    assert reopened.search_library('tag:robotics tag:"latent actions"')[0]["id"] == paper["id"]
    assert reopened.search_library("tag:robot") == []
    assert reopened.search_library("tag:50%_data")[0]["id"] == paper["id"]
    assert reopened.search_library("tag:") == []
    assert reopened.search_library('tag:"latent') == []
    assert reopened.search_library("Human's")[0]["id"] == paper["id"]
    copied = reopened.copy_paper(paper["id"], second["id"])
    assert copied["tags"] == tags
    assert len(reopened.search_library("tag:robotics")) == 2
    reopened.update_paper_tags(paper["id"], "")
    assert reopened.get_paper(paper["id"])["tags"] == []
    assert reopened.get_paper(copied["id"])["tags"] == tags
    assert reopened.search_library("tag:robotics")[0]["id"] == copied["id"]
    assert reopened.get_paper(other["id"])["tags"] == []
    reopened.update_paper_tags(other["id"], ['human\'s "demos"', "동작"])
    assert reopened.search_library('tag:"human\'s \\"demos\\""')[0]["id"] == other["id"]
    assert reopened.search_library("tag:동작")[0]["id"] == other["id"]
    with pytest.raises(ValueError, match="Paper not found"):
        reopened.update_paper_tags(-1, "missing")


def test_scrapbook_is_exclusive_and_moves_papers_out(tmp_path):
    root = str(tmp_path / "library")
    source_path = str(tmp_path / "source.pdf")
    create_test_pdf(source_path)

    library = Library(root)
    scrapbook = library.get_scrapbook()
    project = library.create_project("Permanent project")
    paper = library.import_pdf(scrapbook["id"], source_path)
    paper = library.update_paper_notes(
        paper["id"],
        "Potentially useful for a future project.",
    )
    original_path = paper["file_path"]

    assert scrapbook["name"] == "ScrapBook"
    assert scrapbook["kind"] == "scrapbook"
    with pytest.raises(ValueError, match="only destination"):
        library.validate_destination_projects(
            [scrapbook["id"], project["id"]]
        )
    moved = library.move_scrapbook_paper(paper["id"], project["id"])

    assert moved["id"] == paper["id"]
    assert moved["project_id"] == project["id"]
    assert moved["notes"] == "Potentially useful for a future project."
    assert moved["file_path"] != original_path
    assert os.path.exists(moved["file_path"])
    assert not os.path.exists(original_path)
    assert library.list_papers(scrapbook["id"]) == []
    with pytest.raises(ValueError, match="cannot be copied"):
        library.copy_paper(moved["id"], scrapbook["id"])


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
