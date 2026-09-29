"""Verify local HTML/Markdown archival and immutable revision history.

The tests create self-contained fixtures below pytest's temporary directory.
They do not execute JavaScript or access the network.
"""

import os

import pytest

from corpus_cabinet.document_ui import version_diff_html
from corpus_cabinet.local_documents import capture_local_document
from corpus_cabinet.storage import Library


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def long_paragraph(label):
    return (
        label
        + " documents a repeatable engineering procedure with enough detailed "
        "context to remain useful during offline implementation and review. "
        "The archived copy should preserve its exact source and searchable text."
    )


def test_html_capture_keeps_exact_source_widgets_and_relative_assets(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"small-image-fixture")
    script_path = tmp_path / "widget.js"
    script_path.write_text(
        "document.getElementById('value').textContent = 'ready';",
        encoding="utf-8",
    )
    html_path = tmp_path / "interactive.html"
    html_text = """<!doctype html><html><head><title>Interactive Guide</title></head>
    <body><main><h1>Interactive Guide</h1><p>""" + long_paragraph("This guide") + """</p>
    <input id="control" type="range"><span id="value">waiting</span>
    <img src="diagram.png" alt="Control diagram"><script src="widget.js"></script>
    </main></body></html>"""
    write_text(str(html_path), html_text)

    capture = capture_local_document(str(html_path))

    assert capture["source_type"] == "document"
    assert capture["title"] == "Interactive Guide"
    assert capture["source_metadata"]["interactive"] is True
    assert capture["source_html"] == html_text.encode()
    assert b"asset-1.png" in capture["interactive_html"]
    assert b"asset-2.js" in capture["interactive_html"]
    assert {asset["filename"] for asset in capture["assets"]} == {
        "asset-1.png", "asset-2.js",
    }
    assert any(block["kind"] == "picture" for block in capture["document"]["blocks"])
    assert "repeatable engineering procedure" in capture["extracted_text"]


def test_markdown_capture_preserves_code_and_local_image(tmp_path):
    image_path = tmp_path / "recording.png"
    image_path.write_bytes(b"small-image-fixture")
    markdown_path = tmp_path / "README_RECORDING.md"
    markdown = (
        "# Raw CAN recording\n\n"
        + long_paragraph("The recording guide")
        + "\n\n```bash\nuv run python run_record.py --side both\n```\n\n"
        + "![Recording flow](recording.png)\n"
    )
    write_text(str(markdown_path), markdown)

    capture = capture_local_document(str(markdown_path))

    assert capture["title"] == "Raw CAN recording"
    assert capture["source_metadata"]["source_format"] == "markdown"
    assert capture["source_html"] == markdown.encode()
    assert any(block["kind"] == "code" for block in capture["document"]["blocks"])
    assert any(block["kind"] == "picture" for block in capture["document"]["blocks"])
    assert capture["assets"][0]["filename"] == "asset-1.png"


def test_document_revisions_persist_activate_compare_and_copy(tmp_path):
    root = str(tmp_path / "library")
    document_path = tmp_path / "guide.md"
    write_text(
        str(document_path),
        "# First title\n\n" + long_paragraph("Version one"),
    )
    library = Library(root)
    first_project = library.create_project("First")
    second_project = library.create_project("Second")
    capture_one = capture_local_document(str(document_path))
    source = library.create_source_from_capture(first_project["id"], capture_one)
    library.update_paper_notes(source["id"], "Keep this document-level note.")
    library.update_paper_tags(source["id"], "documentation, controls")

    version_one = library.get_document_version(source["id"])
    assert version_one["version_number"] == 1
    assert os.path.isfile(version_one["original_path"])
    with open(version_one["original_path"], "rb") as handle:
        assert handle.read() == capture_one["source_html"]

    write_text(
        str(document_path),
        "# Second title\n\n" + long_paragraph("Version two adds calibration"),
    )
    capture_two = capture_local_document(str(document_path))
    version_two = library.add_document_revision(source["id"], capture_two)
    saved = library.get_paper(source["id"])
    assert version_two["version_number"] == 2
    assert saved["title"] == "Second title"
    assert saved["notes"] == "Keep this document-level note."
    assert saved["tags"] == ["controls", "documentation"]
    assert len(library.list_document_versions(source["id"])) == 2
    assert "Version one" in version_diff_html(version_one, version_two)
    assert "Version two adds calibration" in version_diff_html(version_one, version_two)

    with pytest.raises(ValueError, match="already saved"):
        library.add_document_revision(source["id"], capture_two)

    library.activate_document_version(source["id"], version_one["id"])
    assert library.get_paper(source["id"])["title"] == "First title"
    reopened = Library(root)
    assert reopened.get_document_version(source["id"])["id"] == version_one["id"]
    copied = reopened.copy_paper(source["id"], second_project["id"])
    assert copied["source_type"] == "document"
    assert len(reopened.list_document_versions(copied["id"])) == 2
    assert reopened.get_document_version(copied["id"])["version_number"] == 1
