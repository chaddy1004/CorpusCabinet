"""Verify bounded article and GitHub capture without contacting the internet.

The tests use in-memory HTTP responses. They do not write outside pytest's
temporary directory and never call a live website or GitHub API.
"""

import base64

import pytest

from corpus_cabinet.web_sources import (
    SourceCaptureError,
    canonicalize_source_url,
    capture_web_source,
    github_repository_identity,
)


class FakeResponse:
    def __init__(self, url, content=b"", content_type="text/html", json_data=None):
        self.url = url
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.json_data = json_data

    def raise_for_status(self):
        return None

    def json(self):
        if self.json_data is None:
            raise ValueError("No JSON fixture")
        return self.json_data


def web_config():
    return {
        "timeout_seconds": 2,
        "max_html_bytes": 100000,
        "max_image_bytes": 10000,
        "max_images": 3,
        "minimum_text_characters": 40,
        "max_index_characters": 10000,
        "cache_version": 1,
    }


def test_article_capture_preserves_readable_blocks_and_canonical_url():
    html = b"""
    <html><head>
      <title>Ignored title</title>
      <meta property="og:title" content="Practical Robot Control">
      <meta name="author" content="Ada Engineer">
      <meta property="og:site_name" content="Engineering Notes">
      <meta property="article:published_time" content="2025-03-02">
      <meta name="description" content="A practical controller walkthrough.">
      <link rel="canonical" href="https://example.com/control?utm_source=newsletter">
    </head><body><nav>Unrelated navigation</nav><article>
      <h1>Practical Robot Control</h1>
      <p>This implementation note explains a robust controller from first principles.</p>
      <h2>Implementation</h2>
      <p>The final system uses bounded feedback and explicit failure handling.</p>
    </article></body></html>
    """

    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(url, html)

    capture = capture_web_source(
        "https://example.com/control?utm_source=test#section",
        web_config(),
        fake_get,
    )

    assert capture["source_type"] == "article"
    assert capture["canonical_url"] == "https://example.com/control"
    assert capture["title"] == "Practical Robot Control"
    assert capture["authors"] == "Ada Engineer"
    assert capture["year"] == 2025
    assert "bounded feedback" in capture["extracted_text"]
    assert "Unrelated navigation" not in capture["extracted_text"]
    assert capture["document"]["engine"] == "Web article"


def test_github_capture_preserves_readme_and_repository_identity():
    readme = "# Robot Stack\n\nA reusable manipulation stack.\n\n## Install\n\n```bash\nuv sync\n```"
    responses = {
        "https://api.github.com/repos/acme/robot-stack/readme": {
            "content": base64.b64encode(readme.encode()).decode(),
        },
    }

    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(
            url,
            b"{}",
            "application/json",
            responses[url],
        )

    capture = capture_web_source(
        "https://github.com/acme/robot-stack/tree/main?utm_source=test",
        web_config(),
        fake_get,
    )

    assert github_repository_identity(capture["canonical_url"]) == "acme/robot-stack"
    assert capture["source_type"] == "github_repository"
    assert capture["canonical_url"] == "https://github.com/acme/robot-stack"
    assert capture["title"] == "acme/robot-stack"
    assert capture["source_metadata"]["owner"] == "acme"
    assert capture["source_metadata"]["repository"] == "robot-stack"
    assert "commit_sha" not in capture["source_metadata"]
    assert "license" not in capture["source_metadata"]
    assert "reusable manipulation stack" in capture["extracted_text"].casefold()
    assert any(block["kind"] == "code" for block in capture["document"]["blocks"])


def test_source_urls_reject_credentials_and_private_networks():
    with pytest.raises(SourceCaptureError):
        canonicalize_source_url("file:///tmp/private.html")
    with pytest.raises(SourceCaptureError):
        canonicalize_source_url("http://user:secret@example.com/article")
    with pytest.raises(SourceCaptureError):
        canonicalize_source_url("http://127.0.0.1/private")
