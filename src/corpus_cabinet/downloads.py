"""Download direct open-access PDFs to a caller-provided temporary path.

The module reads a PDF URL and HTTP settings, streams the response with a
bounded buffer, and writes one validated PDF file at the exact destination
provided by the caller. It does not add files to a library by itself.
"""

import os

import requests


class PdfDownloadError(RuntimeError):
    """Raised when a direct PDF download is unavailable or invalid."""


def download_pdf(url, destination, config=None, session=None):
    """Stream one direct PDF URL to destination and return its byte count."""
    if not url:
        raise PdfDownloadError("No direct PDF URL is available")

    if config is None:
        config = {}
    if session is None:
        session = requests.Session()

    timeout = config.get("download_timeout_seconds", 60)
    maximum_bytes = config.get(
        "download_max_bytes",
        100 * 1024 * 1024,
    )
    chunk_size = 64 * 1024
    response = None
    first_bytes = b""
    total_bytes = 0

    try:
        response = session.get(
            url,
            headers={"User-Agent": "CorpusCabinet/0.1"},
            timeout=timeout,
            stream=True,
        )
        response.raise_for_status()
        with open(destination, "wb") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                if len(first_bytes) < 5:
                    first_bytes += chunk
                    first_bytes = first_bytes[:5]
                total_bytes += len(chunk)
                if total_bytes > maximum_bytes:
                    raise PdfDownloadError(
                        "The PDF exceeds the configured download limit"
                    )
                handle.write(chunk)
    except requests.RequestException as error:
        raise PdfDownloadError(str(error)) from error
    except OSError as error:
        raise PdfDownloadError(str(error)) from error
    finally:
        if response is not None:
            response.close()

    if total_bytes == 0:
        raise PdfDownloadError("The source returned an empty file")
    if first_bytes != b"%PDF-":
        raise PdfDownloadError(
            "The source did not return a PDF; open the source page instead"
        )
    if not os.path.exists(destination):
        raise PdfDownloadError("The downloaded PDF was not written")

    return total_bytes
