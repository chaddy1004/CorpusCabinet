"""Convert local PDFs into an experimental, source-linked reading document.

Reads a PDF and prefetched Docling models. Writes document.json and PNG visual
crops under <library>/reader_cache/<PDF SHA-256>/<settings digest>/. Conversion
runs offline; original PDFs are never edited. The JSON stores ordered blocks,
source page/bounds, embedded internal-link destinations, and conversion warnings.
"""

import hashlib
import importlib.metadata
import json
import logging
import os
import tempfile

import pymupdf

from corpus_cabinet.storage import calculate_file_sha256


logger = logging.getLogger(__name__)
VISUAL_LABELS = {"picture", "table", "formula"}


def load_reader_config():
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("Install the optional Reader dependencies with uv sync --extra reader.") from error
    path = os.path.join(os.path.dirname(__file__), "configs", "config.yaml")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["reader"]


def reader_models_directory(config):
    override = os.environ.get("CORPUS_READER_MODELS")
    if override:
        return os.path.abspath(override)
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(root, config["models_directory"])


def reader_cache_path(library_path, pdf_path, config, full=False):
    settings = {
        "cache_version": config["cache_version"],
        "preview_pages": config["preview_pages"],
        "max_pages": config["max_pages"],
        "crop_scale": config["crop_scale"],
    }
    settings["full"] = bool(full)
    try:
        settings["docling_version"] = importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        settings["docling_version"] = "unavailable"
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    return os.path.join(library_path, "reader_cache", calculate_file_sha256(pdf_path), digest)


def read_cached_document(cache_path):
    path = os.path.join(cache_path, "document.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        for block in document["blocks"]:
            asset = block.get("asset")
            if asset and not os.path.isfile(os.path.join(cache_path, asset)):
                return None
        document["cache_path"] = cache_path
        return document
    except (OSError, ValueError, KeyError, TypeError):
        return None


def find_cached_pdf_document(library_path, pdf_path, config, full=False):
    """Load the canonical PDF cache or a compatible legacy cache for this file."""
    canonical_path = reader_cache_path(library_path, pdf_path, config, full)
    document = read_cached_document(canonical_path)
    if document:
        return document
    cache_root = os.path.dirname(canonical_path)
    if not os.path.isdir(cache_root):
        return None
    for name in sorted(os.listdir(cache_root), reverse=True):
        candidate_path = os.path.join(cache_root, name)
        if candidate_path == canonical_path or not os.path.isdir(candidate_path):
            continue
        document = read_cached_document(candidate_path)
        if not document:
            continue
        if document.get("engine") != "Docling":
            continue
        if document.get("version") != config["cache_version"]:
            continue
        if full and document.get("partial"):
            continue
        return document
    return None


def top_left_bounds(provenance, page_height):
    bounds = provenance.bbox.to_top_left_origin(page_height)
    return [bounds.l, bounds.t, bounds.r, bounds.b]


def source_links(page, bounds):
    """Retain internal links only; previews must not launch files or URLs."""
    links = []
    region = pymupdf.Rect(bounds)
    for link in page.get_links():
        # arXiv PDFs often encode local destinations as LINK_NAMED even though
        # PyMuPDF has resolved a valid page. Reject external targets explicitly.
        if link.get("page", -1) < 0 or link.get("uri") or link.get("file"):
            continue
        if not region.intersects(link["from"]):
            continue
        destination = link.get("to", pymupdf.Point())
        label = " ".join(page.get_textbox(link["from"]).split())
        if label:
            links.append({"label": label, "page": link["page"] + 1,
                          "point": [destination.x, destination.y]})
    return links


def normalize_document(converted, pdf, output_path, config, pages_converted):
    """Keep Docling's reading order and original PDF provenance, not paraphrases."""
    blocks = []
    warnings = ["Experimental conversion: reading order and completeness have not been verified."]
    os.makedirs(output_path, exist_ok=True)
    converted_items = list(converted.iterate_items())
    first_body_top = None
    for item, level in converted_items:
        if not item.prov or item.prov[0].page_no != 1:
            continue
        label = item.label.value
        text = str(getattr(item, "text", "") or "")
        bounds = top_left_bounds(item.prov[0], pdf[0].rect.height)
        if label == "text" and text.casefold().lstrip().startswith("abstract"):
            first_body_top = bounds[1]
            break
        if label == "section_header" and bounds[1] > pdf[0].rect.height * 0.15:
            first_body_top = bounds[1]
            break
    for item, level in converted_items:
        label = item.label.value
        provenance = item.prov
        if not provenance:
            warnings.append("A " + label + " block has no source location.")
            continue
        location = provenance[0]
        if len(provenance) > 1:
            warnings.append("A block spans multiple source regions; its source link shows the first region.")
        page_number = location.page_no
        if page_number < 1 or page_number > len(pdf):
            warnings.append("A block has an invalid source page.")
            continue
        page = pdf[page_number - 1]
        bounds = top_left_bounds(location, page.rect.height)
        region = pymupdf.Rect(bounds) & page.rect
        text = str(getattr(item, "text", "") or "")
        block = {"id": len(blocks), "kind": label, "text": text,
                 "page": page_number, "bounds": bounds, "links": source_links(page, bounds)}
        if page_number == 1 and first_body_top is not None and bounds[1] < first_body_top:
            if label not in VISUAL_LABELS:
                block["role"] = "front_matter"
        if label in VISUAL_LABELS:
            if region.is_empty:
                warnings.append("A " + label + " crop is unavailable; consult its original page.")
            else:
                asset = "visual-" + str(len(blocks)) + ".png"
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(config["crop_scale"], config["crop_scale"]),
                                        clip=region, alpha=False)
                pixmap.save(os.path.join(output_path, asset))
                block["asset"] = asset
                block["image_width"] = pixmap.width
                block["image_height"] = pixmap.height
        if text or label in VISUAL_LABELS:
            blocks.append(block)
    if not blocks:
        raise RuntimeError("No readable blocks were extracted. Use the original PDF; this may need OCR.")
    found_pages = {block["page"] for block in blocks}
    for index in range(pages_converted):
        page = pdf[index]
        if index + 1 not in found_pages and page.get_text().strip():
            warnings.append("No blocks recovered from nonempty page " + str(index + 1) + ".")
    return {"version": config["cache_version"], "engine": "Docling",
            "page_count": len(pdf), "pages_converted": pages_converted,
            "partial": pages_converted < len(pdf), "warnings": sorted(set(warnings)),
            "blocks": blocks}


def convert_pdf(library_path, pdf_path, config, full=False):
    """Called in an isolated CPU-only process; publish a cache only after success."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(config["cpu_threads"])
    cache_path = reader_cache_path(library_path, pdf_path, config, full)
    cached = find_cached_pdf_document(
        library_path, pdf_path, config, full
    )
    if cached:
        return cached
    if os.path.getsize(pdf_path) > config["max_file_bytes"]:
        raise ValueError("This PDF exceeds the experimental Reader's file-size limit.")
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import ConversionStatus, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as error:
        raise RuntimeError("Install the optional Reader dependencies with uv sync --extra reader.") from error
    models = reader_models_directory(config)
    if not os.path.isdir(models):
        raise RuntimeError("Reader models are not installed. See the README's Reader setup instructions.")
    options = PdfPipelineOptions(artifacts_path=models)
    options.do_ocr = False
    options.do_table_structure = False
    options.enable_remote_services = False
    options.accelerator_options = AcceleratorOptions(num_threads=config["cpu_threads"],
                                                     device=AcceleratorDevice.CPU)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    with pymupdf.open(pdf_path) as pdf:
        if full:
            pages_converted = len(pdf)
        else:
            pages_converted = min(len(pdf), config["preview_pages"])
        if pages_converted > config["max_pages"]:
            raise ValueError("This PDF exceeds the experimental Reader's full-conversion page limit.")
        result = converter.convert(pdf_path, page_range=(1, pages_converted),
                                   max_file_size=config["max_file_bytes"])
        if result.status != ConversionStatus.SUCCESS:
            raise RuntimeError("Conversion did not finish successfully. The original PDF is still available.")
        parent = os.path.dirname(cache_path)
        os.makedirs(parent, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="reader-build-", dir=parent) as temporary:
            document = normalize_document(result.document, pdf, temporary, config, pages_converted)
            document["source_sha256"] = calculate_file_sha256(pdf_path)
            if document["source_sha256"] != os.path.basename(parent):
                raise RuntimeError("The PDF changed during conversion. Please generate the preview again.")
            with open(os.path.join(temporary, "document.json"), "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False)
            # Publish the manifest last; incomplete caches can be repaired safely.
            if not os.path.exists(cache_path):
                os.rename(temporary, cache_path)
            else:
                cached = read_cached_document(cache_path)
                if cached:
                    return cached
                for asset in os.listdir(temporary):
                    if asset != "document.json":
                        os.replace(os.path.join(temporary, asset), os.path.join(cache_path, asset))
                os.replace(os.path.join(temporary, "document.json"), os.path.join(cache_path, "document.json"))
    return read_cached_document(cache_path)


def destination_preview(pdf_path, page_number, point):
    """Return readable text and a focused source crop around an internal link."""
    with pymupdf.open(pdf_path) as pdf:
        if page_number < 1 or page_number > len(pdf):
            raise ValueError("This link has no valid destination in the PDF.")
        page = pdf[page_number - 1]
        x = max(min(point[0], page.rect.width), 0)
        y = max(min(point[1], page.rect.height), 0)
        candidates = []
        for block in page.get_text("blocks"):
            if block[6] != 0 or not str(block[4]).strip():
                continue
            rectangle = pymupdf.Rect(block[:4])
            vertical_distance = min(abs(rectangle.y0 - y), abs(rectangle.y1 - y))
            if rectangle.y0 - 8 <= y <= rectangle.y1 + 8:
                vertical_distance = 0
            horizontal_distance = min(abs(rectangle.x0 - x), abs(rectangle.x1 - x))
            if rectangle.x0 - 8 <= x <= rectangle.x1 + 8:
                horizontal_distance = 0
            distance = vertical_distance * 3 + horizontal_distance
            candidates.append((distance, rectangle, str(block[4]).strip()))
        candidates.sort(key=lambda item: (item[0], item[1].y0, item[1].x0))
        text = ""
        if candidates:
            rectangle = candidates[0][1]
            region = pymupdf.Rect(rectangle.x0 - 10, rectangle.y0 - 10,
                                  rectangle.x1 + 10, rectangle.y1 + 10) & page.rect
            text = " ".join(candidates[0][2].split())
        else:
            region = pymupdf.Rect(0, max(y - 12, 0), page.rect.width,
                                  min(y + 180, page.rect.height))
        if region.is_empty:
            region = page.rect
        image = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), clip=region,
                                alpha=False).tobytes("png")
        return {"image": image, "text": text}
