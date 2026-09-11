"""Verify direct PDF downloads stream to disk and reject non-PDF responses."""

import os

import pytest

from corpus_cabinet.downloads import PdfDownloadError, download_pdf


class FakeResponse:
    """Provide the small response surface used by download_pdf."""

    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for position in range(0, len(self.payload), 3):
            yield self.payload[position:position + 3]

    def close(self):
        self.closed = True


class FakeSession:
    """Capture a streaming request without using the network."""

    def __init__(self, payload):
        self.response = FakeResponse(payload)
        self.kwargs = None

    def get(self, url, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_download_pdf_streams_a_pdf_to_disk(tmp_path):
    destination = os.path.join(str(tmp_path), "paper.pdf")
    session = FakeSession(b"%PDF-1.7\nsmall test pdf")

    size = download_pdf("https://example.org/paper.pdf", destination, session=session)

    assert size == os.path.getsize(destination)
    with open(destination, "rb") as handle:
        assert handle.read(5) == b"%PDF-"
    assert session.kwargs["stream"] is True
    assert session.response.closed is True


def test_download_pdf_rejects_html_response(tmp_path):
    destination = os.path.join(str(tmp_path), "paper.pdf")
    session = FakeSession(b"<html>login page</html>")

    with pytest.raises(PdfDownloadError, match="did not return a PDF"):
        download_pdf("https://example.org/paper.pdf", destination, session=session)
