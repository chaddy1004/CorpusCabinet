#!/usr/bin/env python3
"""
Reprocess existing papers after a code change.

Usage:
  python -m backend.reprocess --bibtex-only        # rebuild BibTeX from Scholar (1 SerpAPI call/paper)
  python -m backend.reprocess --summary-only        # re-run AI summary (1 Sonnet call/paper)
  python -m backend.reprocess --full                # full pipeline (Haiku + SerpAPI + Sonnet/paper)
  python -m backend.reprocess --bibtex-only --id 5  # single paper only
"""
import argparse
import sys

from backend.workspace_config import get_workspace_config
from backend.database import init_workspace, get_db_direct
from backend.models import Paper


def reprocess_bibtex(paper: Paper, db) -> bool:
    from backend.serpapi import fetch_citations, build_bibtex
    if not paper.scholar_id:
        print(f"  [skip] no scholar_id stored")
        return False
    citations = fetch_citations(paper.scholar_id)
    apa = next((c["snippet"] for c in citations if c["title"] == "APA"), "")
    if not apa:
        print(f"  [skip] no APA citation returned")
        return False
    paper.bibtex = build_bibtex(paper.title, paper.authors, apa)
    db.commit()
    print(f"  [ok] BibTeX updated")
    return True


def reprocess_summary(paper: Paper, db) -> bool:
    import os
    from backend.pipeline import extract_text, summarize_paper
    if not os.path.exists(paper.file_path):
        print(f"  [skip] PDF not found at {paper.file_path}")
        return False
    text = extract_text(paper.file_path)
    data = summarize_paper(text)
    paper.task        = data.get("task", "")
    paper.methodology = data.get("methodology", "")
    paper.datasets    = ", ".join(data.get("datasets", []))
    paper.metrics     = ", ".join(data.get("metrics", []))
    db.commit()
    print(f"  [ok] summary updated")
    return True


def reprocess_full(paper: Paper, db) -> bool:
    import os
    from backend.pipeline import (
        extract_metadata_with_haiku, _extract_title_fallback,
        extract_text, summarize_paper,
    )
    from backend.serpapi import get_paper_metadata

    if not os.path.exists(paper.file_path):
        print(f"  [skip] PDF not found at {paper.file_path}")
        return False

    pdf_meta    = extract_metadata_with_haiku(paper.file_path)
    pdf_title   = pdf_meta.get("title", "").strip() or _extract_title_fallback(paper.file_path)
    pdf_authors = pdf_meta.get("authors", "").strip()

    meta          = get_paper_metadata(pdf_title, pdf_authors=pdf_authors)
    final_title   = meta.get("title") or pdf_title
    final_authors = pdf_authors or meta.get("authors", "")

    text = extract_text(paper.file_path)
    data = summarize_paper(text)

    paper.title       = final_title
    paper.authors     = final_authors
    paper.conference  = meta["conference"]
    paper.year        = meta["year"]
    paper.bibtex      = meta["bibtex"]
    paper.scholar_id  = meta["scholar_id"]
    paper.task        = data.get("task", "")
    paper.methodology = data.get("methodology", "")
    paper.datasets    = ", ".join(data.get("datasets", []))
    paper.metrics     = ", ".join(data.get("metrics", []))
    db.commit()
    print(f"  [ok] fully reprocessed")
    return True


def main():
    parser = argparse.ArgumentParser(description="Reprocess papers after a code change")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bibtex-only",  action="store_true", help="Rebuild BibTeX only (1 SerpAPI call/paper)")
    group.add_argument("--summary-only", action="store_true", help="Re-run AI summary only (1 Sonnet call/paper)")
    group.add_argument("--full",         action="store_true", help="Full pipeline reprocess")
    parser.add_argument("--id", type=int, default=None, help="Process a single paper by ID")
    args = parser.parse_args()

    config = get_workspace_config()
    ws = config.get_active_workspace()
    if not ws:
        print("ERROR: No active workspace configured.")
        sys.exit(1)

    init_workspace(ws.path)
    db = get_db_direct()

    if args.id:
        papers = db.query(Paper).filter(Paper.id == args.id).all()
        if not papers:
            print(f"ERROR: No paper with id={args.id}")
            sys.exit(1)
    else:
        papers = db.query(Paper).order_by(Paper.id).all()

    print(f"Workspace : {ws.name} ({ws.path})")
    print(f"Papers    : {len(papers)}")
    print(f"Mode      : {'bibtex-only' if args.bibtex_only else 'summary-only' if args.summary_only else 'full'}")
    print()

    ok = 0
    for paper in papers:
        print(f"[{paper.id}] {paper.title[:70]}")
        if args.bibtex_only:
            ok += reprocess_bibtex(paper, db)
        elif args.summary_only:
            ok += reprocess_summary(paper, db)
        else:
            ok += reprocess_full(paper, db)

    print(f"\nDone: {ok}/{len(papers)} updated")
    db.close()


if __name__ == "__main__":
    main()
