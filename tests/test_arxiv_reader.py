"""Verify official arXiv HTML becomes safe shared Reader blocks."""

import os
from types import SimpleNamespace

from corpus_cabinet.arxiv_reader import (
    allowed_arxiv_url, html_cache_path, parse_arxiv_html, resolve_arxiv_id,
)


HTML = b"""
<html><body><article class="ltx_document">
<h1 class="ltx_title_document">A Paper &lt;Title&gt;</h1>
<h2>Introduction</h2>
<p class="ltx_p">Safe &lt;text&gt; <script>not markup</script>
and <math alttext="x^2"><semantics>x</semantics></math>
cites <a class="ltx_ref" href="#bib.bib1">[1]</a>.</p>
<table class="ltx_equation ltx_eqn_table"><tr><td>
<math alttext="\\frac{x_1}{2}"><semantics><mfrac><msub><mi>x</mi><mn>1</mn></msub>
<mn>2</mn></mfrac><annotation encoding="application/x-tex">\\frac{x_1}{2}</annotation>
</semantics></math></td></tr></table>
<figure class="ltx_figure"><img src="images/figure.png">
<figcaption>Figure 1: Original pixels.</figcaption></figure>
<h2>References</h2><ul><li class="ltx_bibitem" id="bib.bib1">A. Author. Reference.</li></ul>
</article></body></html>
"""


class Response:
    def __init__(self, data, url="https://arxiv.org/html/1234.56789"):
        self.data = data
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "image/png", "content-length": str(len(data))}

    def iter_content(self, size):
        yield self.data

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, image):
        self.image = image
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return Response(self.image, url)


def config_fixture():
    return {"html_timeout_seconds": 20, "max_html_bytes": 8388608,
            "max_html_image_bytes": 8388608, "max_html_images": 200,
            "html_cache_version": 2}


def test_html_parser_preserves_structure_assets_math_and_reference_targets(tmp_path):
    # One valid one-pixel PNG; Qt validates display separately in Reader UI tests.
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")
    session = Session(png)
    document = parse_arxiv_html(HTML, "https://arxiv.org/html/1234.56789", str(tmp_path),
                                 config_fixture(), session)
    assert document["engine"] == "arXiv HTML"
    assert not document["partial"]
    assert [block["kind"] for block in document["blocks"]] == [
        "section_header", "section_header", "text", "formula", "picture", "caption",
        "section_header", "reference",
    ]
    paragraph = document["blocks"][2]
    assert "&lt;text&gt;" in paragraph["html"]
    assert "<script>" not in paragraph["html"]
    assert '<span class="math"' in paragraph["html"]
    assert "x^2" not in paragraph["html"]
    assert 'href="preview:2:0"' in paragraph["html"]
    assert paragraph["links"][0]["reference_id"] == "bib.bib1"
    assert document["references"]["bib.bib1"] == "A. Author. Reference."
    formula = document["blocks"][3]
    assert "x<sub>1</sub>" in formula["html"]
    assert "\\frac" not in formula["html"]
    asset = document["blocks"][4]["asset"]
    assert os.path.isfile(tmp_path / asset)
    assert session.urls == ["https://arxiv.org/html/images/figure.png"]


def test_arxiv_urls_cache_and_exact_title_resolution(tmp_path, monkeypatch):
    assert allowed_arxiv_url("https://arxiv.org/html/1234.56789")
    assert allowed_arxiv_url("https://export.arxiv.org/api/query")
    assert not allowed_arxiv_url("http://arxiv.org/html/1234.56789")
    assert not allowed_arxiv_url("https://arxiv.org.example.com/html/1234.56789")
    config = config_fixture()
    assert html_cache_path(str(tmp_path), "1234.56789", config) == html_cache_path(
        str(tmp_path), "1234.56789", config
    )
    assert resolve_arxiv_id({"external_url": "https://arxiv.org/abs/1801.02854v3"},
                            config, object()) == "1801.02854"
    search = {
        "title": "Exact Paper",
        "external_id": "2601.01234",
        "external_url": "",
    }
    provider = SimpleNamespace(search=lambda title: [search])
    monkeypatch.setattr("corpus_cabinet.arxiv_reader.ArxivProvider",
                        lambda config, session: provider)
    assert resolve_arxiv_id({"title": "Exact Paper"}, config, object()) == "2601.01234"
    assert resolve_arxiv_id({"title": "Different Paper", "external_id": "arXiv:2602.12345"},
                            config, object()) == "2602.12345"
