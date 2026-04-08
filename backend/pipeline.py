import os
import shutil
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


def extract_title(pdf_path: str) -> str:
    """
    Extract the paper title from a PDF.
    Strategy: find the largest font text on the first page.
    Falls back to the first non-empty line of text.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]

    # Get all text blocks with font size info
    blocks = page.get_text("dict")["blocks"]
    best_text = ""
    best_size = 0

    for block in blocks:
        if block.get("type") != 0:  # 0 = text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size", 0)
                text = span.get("text", "").strip()
                if text and size > best_size:
                    best_size = size
                    best_text = text

    # Fallback: first line of plain text
    if not best_text:
        plain = page.get_text("text").strip()
        best_text = plain.split("\n")[0].strip() if plain else "Unknown Title"

    doc.close()
    return best_text


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
    # 1. Extract title
    title = extract_title(pdf_path)
    print(f"[pipeline] Extracted title: {title}")

    # 2. Scholar metadata + BibTeX
    print(f"[pipeline] Searching Scholar for: {title}")
    meta = get_paper_metadata(title)
    print(f"[pipeline] Got metadata: conference={meta['conference']}, year={meta['year']}")

    # 3. AI summary
    print(f"[pipeline] Summarizing paper...")
    text = extract_text(pdf_path)
    summary_data = summarize_paper(text)

    datasets = ", ".join(summary_data.get("datasets", []))
    metrics  = ", ".join(summary_data.get("metrics", []))

    # 4. Save to DB
    paper = Paper(
        project_id  = project.id,
        title       = title,
        authors     = meta["authors"],
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
