"""Check source-linked Reader blocks, previews, cache identity, and Qt state.

Tests use temporary PDF fixtures and fake conversion output. They never download
models or contact network services; real Docling quality is a separate trial.
"""

import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from corpus_cabinet.reader import (
    destination_preview, find_cached_pdf_document, normalize_document,
    read_cached_document, reader_cache_path, source_links,
)
from corpus_cabinet.reader_ui import ReaderPanel, ReaderTask, linked_text, reader_html
from corpus_cabinet.storage import calculate_file_sha256


class Bounds:
    """Represent controlled top-left provenance without importing Docling."""

    def __init__(self, values):
        self.l, self.t, self.r, self.b = values

    def to_top_left_origin(self, height):
        return self


class ConvertedDocument:
    def __init__(self, items):
        self.items = items

    def iterate_items(self):
        for item in self.items:
            yield item, 0


class FakeProcess:
    def __init__(self, output="", cancelled=None):
        self.output = output
        self.cancelled = cancelled
        self.returncode = 0
        self.args = ["isolated-reader"]
        self.terminated = False

    def communicate(self, timeout=None):
        if self.cancelled is not None and not self.terminated:
            self.cancelled.set()
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.output, ""

    def terminate(self):
        self.terminated = True


def config_fixture():
    return {"preview_pages": 3, "max_pages": 80, "max_file_bytes": 104857600,
            "timeout_seconds": 180, "cpu_threads": 2, "crop_scale": 2,
            "cache_version": 3, "models_directory": ".reader_models",
            "html_timeout_seconds": 20, "max_html_bytes": 8388608,
            "max_html_image_bytes": 8388608, "max_html_images": 200,
            "html_cache_version": 2}


def application_fixture():
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    return application


def create_pdf(path):
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        first.insert_text((72, 100), "First paragraph cites [12].")
        first.draw_rect(pymupdf.Rect(72, 160, 260, 240), color=(0, 0, 1))
        first.insert_text((80, 200), "An original vector figure")
        second = pdf.new_page()
        second.insert_text((72, 100), "[12] A reference on the second page.")
        first = pdf[0]
        first.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(173, 87, 201, 103),
                           "page": 1, "to": pymupdf.Point(72, 90)})
        pdf.save(path)


def item_fixture(kind, text, bounds, page=1):
    return SimpleNamespace(label=SimpleNamespace(value=kind), text=text,
                           prov=[SimpleNamespace(page_no=page, bbox=Bounds(bounds))])


def document_fixture(cache_path):
    return {"cache_path": cache_path, "engine": "Docling", "pages_converted": 1, "page_count": 2,
            "partial": True, "warnings": ["Experimental conversion"],
            "blocks": [{"id": 0, "kind": "text", "text": "We cite [12] but not the value 12.",
                        "page": 1, "bounds": [72, 85, 300, 110],
                        "links": [{"label": "12", "page": 2, "point": [72, 90]}]}]}


def test_normalization_keeps_order_visual_pixels_and_original_pdf(tmp_path):
    path = str(tmp_path / "paper.pdf")
    create_pdf(path)
    before = calculate_file_sha256(path)
    items = [item_fixture("text", "First paragraph cites [12].", [72, 85, 300, 110]),
             item_fixture("picture", "", [72, 160, 260, 240]),
             item_fixture("caption", "Figure 1: A vector figure.", [72, 245, 300, 260]),
             item_fixture("formula", "x + y", [72, 160, 260, 240]),
             item_fixture("table", "", [72, 160, 260, 240])]
    output = str(tmp_path / "cache")
    with pymupdf.open(path) as pdf:
        result = normalize_document(ConvertedDocument(items), pdf, output, config_fixture(), 1)
    assert [block["kind"] for block in result["blocks"]] == ["text", "picture", "caption", "formula", "table"]
    assert result["partial"]
    assert result["pages_converted"] == 1
    assert result["blocks"][0]["links"][0]["page"] == 2
    for block in result["blocks"]:
        if block.get("asset"):
            pixmap = pymupdf.Pixmap(os.path.join(output, block["asset"]))
            assert block["image_width"] == 376
            assert block["image_height"] == 160
    assert calculate_file_sha256(path) == before


def test_cache_identity_tracks_pdf_settings_and_full_mode(tmp_path):
    path = str(tmp_path / "paper.pdf")
    create_pdf(path)
    config = config_fixture()
    preview = reader_cache_path(str(tmp_path), path, config)
    assert preview == reader_cache_path(str(tmp_path), path, config)
    assert preview != reader_cache_path(str(tmp_path), path, config, True)
    config["crop_scale"] = 3
    assert preview != reader_cache_path(str(tmp_path), path, config)
    config = config_fixture()
    config["html_cache_version"] = 99
    assert preview == reader_cache_path(str(tmp_path), path, config)
    with pymupdf.open(path) as pdf:
        pdf.set_metadata({"title": "Changed source"})
        pdf.save(str(tmp_path / "changed.pdf"))
    assert preview != reader_cache_path(str(tmp_path), str(tmp_path / "changed.pdf"), config_fixture())


def test_pdf_cache_recovers_compatible_legacy_config_digest(tmp_path):
    path = str(tmp_path / "paper.pdf")
    create_pdf(path)
    config = config_fixture()
    canonical = reader_cache_path(str(tmp_path), path, config)
    legacy = os.path.join(os.path.dirname(canonical), "legacy-settings-digest")
    os.makedirs(legacy)
    document = document_fixture(legacy)
    document["version"] = config["cache_version"]
    with open(os.path.join(legacy, "document.json"), "w") as handle:
        json.dump(document, handle)

    recovered = find_cached_pdf_document(str(tmp_path), path, config)

    assert recovered["cache_path"] == legacy
    assert find_cached_pdf_document(str(tmp_path), path, config, True) is None


def test_cache_rejects_missing_assets(tmp_path):
    data = document_fixture(str(tmp_path))
    data["blocks"][0]["asset"] = "missing.png"
    with open(tmp_path / "document.json", "w") as handle:
        json.dump(data, handle)
    assert read_cached_document(str(tmp_path)) is None
    data["blocks"][0].pop("asset")
    with open(tmp_path / "document.json", "w") as handle:
        json.dump(data, handle)
    assert read_cached_document(str(tmp_path))["cache_path"] == str(tmp_path)


def test_reader_html_escapes_content_and_links_only_citation_numbers(tmp_path):
    application_fixture()
    data = document_fixture(str(tmp_path))
    block = data["blocks"][0]
    block["text"] += " <script>not markup</script>"
    text = linked_text(block)
    assert text.count('href="preview:0:0"') == 1
    assert "value 12" in text
    assert "&lt;script&gt;" in text
    output = reader_html(data, 680, 18, True)
    assert 'href="source:0"' in output
    assert "font-size:18px" in output
    assert "170%" in output
    dyslexic = reader_html(data, 680, 18, True, "OpenDyslexic")
    assert "font-family:'OpenDyslexic', sans-serif" in dyslexic


def test_preview_uses_original_destination_pixels_without_modifying_pdf(tmp_path):
    path = str(tmp_path / "paper.pdf")
    create_pdf(path)
    before = calculate_file_sha256(path)
    data = destination_preview(path, 2, [72, 90])
    assert data["image"].startswith(b"\x89PNG")
    assert "reference on the second page" in data["text"]
    assert calculate_file_sha256(path) == before
    with pymupdf.open(path) as pdf:
        links = source_links(pdf[0], [72, 85, 300, 110])
    assert links[0]["page"] == 2


def test_reader_panel_restores_cache_compares_pdf_and_previews_without_navigation(
        tmp_path, monkeypatch):
    application = application_fixture()
    path = str(tmp_path / "paper.pdf")
    create_pdf(path)
    panel = ReaderPanel("OpenDyslexic")
    assert not panel.convert_button.isEnabled()
    panel.set_context(str(tmp_path), {"id": 1, "file_path": path})
    revision = panel.revision
    panel.set_context(str(tmp_path), {"id": 2, "file_path": path})
    data = document_fixture(str(tmp_path))
    panel.conversion_finished({"revision": revision, "document": data, "full": False})
    assert panel.document_data is None
    saved_sources = []
    panel.readerSourceChanged.connect(
        lambda paper_id, source: saved_sources.append((paper_id, source))
    )
    panel.conversion_finished(
        {"revision": panel.revision, "document": data, "full": False}
    )
    assert "Preview only" in panel.status.text()
    assert saved_sources == [(2, "pdf_preview")]
    assert panel.full_button.isEnabled()
    panel.larger_button.click()
    assert panel.font_size == 17
    assert "We cite" in panel.browser.toPlainText()
    panel.dyslexic_toggle.click()
    assert panel.use_dyslexic
    assert "OpenDyslexic" in panel.browser.toHtml()
    navigation = Mock()
    panel.sourceRequested.connect(navigation)
    panel.activate_link(QUrl("preview:0:0"))
    application.processEvents()
    assert panel.preview_dialog.isVisible()
    navigation.assert_not_called()
    panel.preview_dialog.original_button.click()
    navigation.assert_called_once_with(2, None)

    cache_path = str(tmp_path / "saved-cache")
    os.makedirs(cache_path)
    cached = document_fixture(cache_path)
    with open(os.path.join(cache_path, "document.json"), "w") as handle:
        json.dump(cached, handle)
    monkeypatch.setattr(
        "corpus_cabinet.reader_ui.find_cached_pdf_document",
        lambda library, pdf, config, full=False: read_cached_document(cache_path),
    )
    panel.set_context(str(tmp_path), {"id": 3, "file_path": ""})
    assert panel.document_data is None
    panel.set_context(
        str(tmp_path),
        {"id": 2, "file_path": path, "reader_source": "pdf_preview"},
    )
    assert panel.document_data is not None
    assert "We cite" in panel.browser.toPlainText()
    panel.show()
    panel.compare_button.click()
    application.processEvents()
    assert not panel.compare_container.isHidden()
    assert panel.show_source_in_compare(2, None)
    panel.close()


def test_conversion_subprocess_is_offline_and_can_be_cancelled(monkeypatch):
    application_fixture()
    monkeypatch.setattr("corpus_cabinet.reader_ui.load_reader_config", config_fixture)
    task = ReaderTask("library", {"file_path": "paper.pdf"}, False, 1)
    data = document_fixture("cache")
    process = FakeProcess(json.dumps(data))
    launch = Mock(return_value=process)
    monkeypatch.setattr("corpus_cabinet.reader_ui.subprocess.Popen", launch)
    results = []
    task.signals.finished.connect(results.append)
    task.run()
    assert results[0]["document"] == data
    environment = launch.call_args.kwargs["env"]
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["OMP_NUM_THREADS"] == "2"
    cancelled = ReaderTask("library", {"file_path": "paper.pdf"}, False, 2)
    process = FakeProcess(cancelled=cancelled.cancelled)
    launch.return_value = process
    failures = []
    cancelled.signals.failed.connect(failures.append)
    cancelled.run()
    assert process.terminated
    assert "cancelled" in failures[0]["message"]
