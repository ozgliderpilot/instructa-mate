# PyMuPDF as the text layer, pdfplumber for tables, no LLM table-fallback

The stage-1 parser reads each GFA PDF with **PyMuPDF (`fitz`) `get_text("text")`** as the
primary text and geometry layer, and uses **pdfplumber `extract_tables()`** only for the
bordered two-column tables. **`pdftotext -layout` is not used** as an engine — it served as
a diff oracle during evaluation and is otherwise dropped.

Settled by a head-to-head bake-off (PyMuPDF vs pdfplumber vs pdftotext) over the corpus's
worst real pages — footers, the competency table, the Problem/Probable-Cause table, the
colour-control tables, and dense prose. The harness and per-page dumps that produced this
are not kept in-tree (they live in git history; the original was `bakeoff/`); this ADR is
the durable record.

## Why

- **Text fidelity.** On the worst pages PyMuPDF yields **0 `U+FFFD`** and **0 split-words**;
  pdftotext mangled the same prose (ligatures `ef f ective` / `f light`, replacement glyphs,
  ~30 split-words/page). The planned "de-ligature normalization pass" is therefore
  unnecessary with PyMuPDF — it reads the source verbatim-clean.
- **Reading-order tables, so no LLM fallback.** The "scrambled tables" that earlier scoping
  feared were a *pdftotext* artifact (it space-aligns columns visually but interleaves them
  in the text stream). PyMuPDF's reading order keeps each cell's text coherent
  (problem→cause pairs stay paired), so the table strategy is: try pdfplumber
  `extract_tables()` and emit GFM when it returns **one clean table**; otherwise emit
  PyMuPDF reading-order text verbatim. Never trust pdfplumber's *messy* grids (e.g. the
  fragmented 44×6 Problem/Cause) — reading-order text is strictly better there. **The LLM
  table-fallback is dropped from scope.**
- **Page objects are reliable delimiters.** The PDFs have one page object per logical page,
  consecutive within a unit (Trainer 266 pp / Pilot 236 pp), so iterating `doc` pages
  delimits units — no form-feed parsing.

## Trade-off / open risk

- **PyMuPDF is AGPL-3.0.** Fine for the local PoC. If InstructaMate ships as a closed
  product this must be resolved: either buy the Artifex commercial license, or fall back to
  pdfplumber as the single extractor (MIT, ~10× slower but here equally clean on text).
  pdfplumber-only is a viable escape hatch if the AGPL constraint bites — re-run the
  comparison (recoverable from git history) before switching.
- Two extraction libraries instead of one (PyMuPDF for text/geometry, pdfplumber for table
  ruling). Accepted: each is used where it is strictly better, and pdfplumber is the ready
  fallback should the licensing trade-off change.
