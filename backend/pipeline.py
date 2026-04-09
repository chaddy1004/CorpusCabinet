import os
import re
import json
import fitz  # PyMuPDF
import anthropic
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from backend.models import Paper, Project
from backend.serpapi import get_paper_metadata

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SUMMARY_PROMPT = """You are a research assistant. Given the text of an academic paper, extract:
1. Task: what problem/task does this paper address? (1-2 sentences)
2. Methodology: what is the core technical approach? (2-3 sentences)
3. Datasets: list the dataset names used for evaluation
4. Metrics: list the evaluation metrics used

Respond ONLY with a valid JSON object, no markdown, no preamble:
{
  "task": "...",
  "methodology": "...",
  "datasets": ["..."],
  "metrics": ["..."]
}"""

METADATA_PROMPT = """Extract the title and authors from this academic paper's first page.

Rules:
- Title: the full paper title only (not the venue, journal, or arXiv ID)
- Authors: in BibTeX format — "Last, First and Last, First" (e.g. "Wang, Ziyang and Yu, Shoubin and Stengel-Eskin, Elias")
- If you cannot find the authors, return an empty string for authors

Respond ONLY with a valid JSON object, no markdown, no preamble:
{
  "title": "...",
  "authors": "..."
}"""


def extract_first_page_text(pdf_path: str) -> str:
    """Extract plain text from the first page of a PDF."""
    doc = fitz.open(pdf_path)
    text = doc[0].get_text("text").strip()
    doc.close()
    return text


def _extract_title_fallback(pdf_path: str) -> str:
    """Fallback title extraction: largest font span on page 0."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    blocks = page.get_text("dict")["blocks"]
    best_text, best_size = "", 0
    for block in blocks:
        if block.get("type") != 0:
            continue
        
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size", 0)
                text = span.get("text", "").strip()
                if text and size > best_size:
                    best_size = size
                    best_text = text
    if not best_text:
        plain = page.get_text("text").strip()
        best_text = plain.split("\n")[0].strip() if plain else "Unknown Title"
    doc.close()
    return best_text


def extract_metadata_with_haiku(pdf_path: str) -> dict:
    """
    Use Claude Haiku to extract title and full author names from the PDF first page.
    Returns dict with keys: title, authors.
    """
    first_page = extract_first_page_text(pdf_path)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"{METADATA_PROMPT}\n\n--- FIRST PAGE ---\n{first_page[:3000]}"
            }]
        )
        raw = message.content[0].text.strip()
        print(f"[pipeline] Haiku raw response: {raw[:200]}")

        # Strip markdown code fences if Haiku wrapped the JSON
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()

        data = json.loads(raw)
        print(f"[pipeline] Haiku extracted title: {data.get('title', '')}")
        print(f"[pipeline] Haiku extracted authors: {data.get('authors', '')}")
        return data
    except Exception as e:
        print(f"[pipeline] extract_metadata_with_haiku failed: {e}")
        # Fallback: extract title from largest font span on first page
        return {"title": _extract_title_fallback(pdf_path), "authors": ""}


def extract_text(pdf_path: str, max_chars: int = 8000) -> str:
    """
    Extract plain text from a PDF, truncated to max_chars for the AI prompt.
    Reads first N pages to get abstract + intro (most informative for summarization).
    """
    doc = fitz.open(pdf_path)
    text = ""
    for i, page in enumerate(doc):
        if len(text) >= max_chars or i >= 6:  # first 6 pages max
            break
        text += page.get_text("text")
    doc.close()
    return text[:max_chars]


def summarize_paper(text: str) -> dict:
    """
    Call Claude API to extract structured summary from paper text.
    Returns dict with keys: task, methodology, datasets, metrics.
    """
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": f"{SUMMARY_PROMPT}\n\n--- PAPER TEXT ---\n{text}"
            }]
        )
        raw = message.content[0].text.strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[pipeline] summarize_paper failed: {e}")
        return {"task": "", "methodology": "", "datasets": [], "metrics": []}


def process_pdf(
    pdf_path: str,
    project: Project,
    db: Session,
    original_filename: str,
) -> Paper:
    """
    Full pipeline: PDF file → Paper row in DB.

    1. Extract title from PDF
    2. Search SerpAPI for metadata + BibTeX
    3. Extract text + call Claude for summary
    4. Save Paper to DB
    """
    # 1. Use Haiku to extract title + full authors from PDF
    print(f"[pipeline] Extracting metadata with Haiku...")
    pdf_meta = extract_metadata_with_haiku(pdf_path)
    pdf_title   = pdf_meta.get("title", "").strip()
    pdf_authors = pdf_meta.get("authors", "").strip()

    # If Haiku returned no title, fall back to font-size heuristic
    if not pdf_title:
        pdf_title = _extract_title_fallback(pdf_path)
        print(f"[pipeline] Using fallback title: {pdf_title}")

    # 2. Scholar metadata + BibTeX (pass Haiku authors for BibTeX construction)
    print(f"[pipeline] Searching Scholar for: {pdf_title}")
    meta = get_paper_metadata(pdf_title, pdf_authors=pdf_authors)

    # Prefer Scholar's title (canonical), fall back to Haiku's
    final_title   = meta.get("title") or pdf_title
    final_authors = pdf_authors or meta.get("authors", "")

    print(f"[pipeline] Title: {final_title}")
    print(f"[pipeline] Authors: {final_authors}")
    if meta['bibtex']:
        print(f"[pipeline] BibTeX ✓  conference={meta['conference']}, year={meta['year']}")
    else:
        print(f"[pipeline] BibTeX ✗  conference={meta['conference']}, year={meta['year']}")

    # 3. AI summary
    print(f"[pipeline] Summarizing paper...")
    text = extract_text(pdf_path)
    summary_data = summarize_paper(text)

    datasets = ", ".join(summary_data.get("datasets", []))
    metrics  = ", ".join(summary_data.get("metrics", []))

    # 4. Save to DB
    paper = Paper(
        project_id  = project.id,
        title       = final_title,
        authors     = final_authors,
        conference  = meta["conference"],
        year        = meta["year"],
        bibtex      = meta["bibtex"],
        task        = summary_data.get("task", ""),
        methodology = summary_data.get("methodology", ""),
        datasets    = datasets,
        metrics     = metrics,
        file_path   = pdf_path,
        scholar_id  = meta["scholar_id"],
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    print(f"[pipeline] Saved paper id={paper.id}")
    return paper


def save_pdf_to_project(upload_file_content: bytes, filename: str, project: Project) -> str:
    """
    Save uploaded PDF bytes to the project's folder.
    Returns the absolute file path.
    """
    os.makedirs(project.folder_path, exist_ok=True)
    dest = os.path.join(project.folder_path, filename)

    # Avoid overwriting — append _1, _2, etc.
    base, ext = os.path.splitext(dest)
    counter = 1
    while os.path.exists(dest):
        dest = f"{base}_{counter}{ext}"
        counter += 1

    with open(dest, "wb") as f:
        f.write(upload_file_content)
    return dest
