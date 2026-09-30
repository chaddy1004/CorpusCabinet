# Corpus Cabinet TODO

Living feature backlog. Add new ideas here as they arise; unchecked items are
planned, not promises. Keep project sorting instead of dedicated project filters.

## Current priorities

Implement in this order: **1 → 4 → 5 → 7**.

- [x] **1. Resume reading** — remember each paper's PDF page and zoom; show
  recently opened PDFs in a Home “Recently opened” section.
- [x] **4. Reading status** — Unread / Reading / Read, optional favorite, visible
  on paper cards, with reading-status sorting.
- [x] **5. Library-wide search** — separate from online search; search titles,
  authors, abstracts, extracted PDF text, personal notes, and PDF comments across
  projects, with excerpts and navigation back to the matching paper.
- [x] **7. Project research notes** — an autosaved place to synthesize multiple
  papers, record open questions, and plan baselines for each project.

Implemented September 18, 2026. Reading history is per independent paper copy;
status stays manual. Library search works offline and searches the bounded PDF
text extracted during import, not scanned images/OCR. Project notes are plain
text and remain separate from individual paper notes.

## Other proposed features

- [ ] **2. PDF reading toolkit** — find text, jump to page, document outline,
  highlights, and comments anchored to selected passages.
- [ ] **3. Editable metadata** — correct titles, authors, year, venue, and DOI
  manually when extraction or online metadata is wrong.
- [ ] **6. Backup, restore, and undo** — recover deleted papers; back up the
  database, PDFs, notes, and comments together. Citation export is not a backup.
- [ ] **8. Project export** — bibliography as `.bib`; notes and comments as
  Markdown with paper titles and page references.

## Future ideas

- [ ] Paper recommendations / genuine discovery based on research interests.
- [ ] Citation relationships and graphs.
- [ ] Source-grounded AI reading assistance and cross-paper synthesis.
- [ ] Multi-device synchronization, after backup and recovery are dependable.
- [ ] Dark theme, after the light-theme workflow is polished.

## New ideas

Add new proposals below, then promote them into priorities when agreed.

- [ ] First-run interactive onboarding: a skippable guided walkthrough that
  explains projects, adding or dropping sources, source organization, reading
  controls, notes, tags, and where library data is stored. Make it replayable
  later from the app so users are not forced to remember everything at launch.
- [x] Favorite projects grouped above regular projects, with independent,
  persistent drag ordering and restoration of regular position when unfavorited.
- [x] Keep the blue ScrapBook fixed at the top of the sidebar and hide it
  whenever its staging area is empty, without treating it as a favorite project.
- [x] Experimental comfortable Reader UI: adjustable single-column typography,
  source-page actions, expandable visual crops, cached structured documents,
  cancellable/offline conversion process, persistent generated sources, readable
  internal-reference previews, and a side-by-side PDF comparison mode.
- [x] Validate actual Docling conversion on a representative user PDF after
  installing the optional dependencies and prefetched layout model.
- [ ] Reader quality checks: omissions, column ordering, captions, inline math,
  and cases where individual original page regions are a safer fallback.
- [x] Prefer official arXiv HTML when an identifier or exact-title match exists;
  cache its source, structured blocks, figures, formulas, and reference targets.
- [ ] Publisher HTML sources, saved Reader scroll position, section navigation,
  and reference previews inline instead of in a separate dialog.
- [x] General research-source foundation: backwards-compatible source types,
  canonical URLs, cached-content paths, source indicators and project filtering.
- [x] Reviewed public article capture with readable offline Reader snapshots,
  original HTML, bounded images, search indexing, and duplicate warnings.
- [x] Reviewed GitHub repository capture with offline, searchable README Reader
  snapshots and a link back to the full repository.
- [x] Local HTML and Markdown import with exact archived originals, relative
  assets, isolated interactive widgets, searchable Reader copies, and immutable
  revision history with activation and text comparison.
- [ ] YouTube metadata, transcripts, timestamped notes, and transcript search.
- [ ] Full GitHub documentation/code indexing and explicit snapshot refresh.
