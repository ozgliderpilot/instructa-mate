# Extractor bake-off — findings

Run: `python bakeoff/extract_bakeoff.py` (UTF-8: `PYTHONUTF8=1` on Windows).
Dumps for side-by-side diffing land in `bakeoff/out/`.

## Decision

**Primary extractor: PyMuPDF (`fitz`) `get_text("text")`.**
**Table sidekick: pdfplumber `extract_tables()` for the bordered tables.**
**pdftotext -layout: drop as the engine; keep only as an occasional diff oracle.**

## Metrics (worst real pages)

| sample | extractor | chars | U+FFFD | split-words* | footer |
|---|---|---:|---:|---:|---|
| pilot-u1-p1 | **pymupdf** | 2020 | **0** | **0** | Page 1-1 |
| pilot-u1-p1 | pdfplumber | 1954 | 0 | 0 | Page 1-1 |
| pilot-u1-p1 | pdftotext | 2261 | **15** | **32** | Page 1-1 |
| pilot-u1-p2 | **pymupdf** | 1466 | **0** | 0† | Page 1-2 |
| pilot-u1-p2 | pdftotext | 1826 | **16** | **26** | Page 1-2 |
| trainer-u5-competency | pymupdf | 1768 | 0 | 0† | Page 5 - 2 |
| trainer-u5-competency | pdftotext | 2685 | 2 | 0† | Page 5 - 2 |

\* heuristic for ligature/word-split damage (`ef f ective`, `f light`).
† PyMuPDF's nonzero residuals are **all false positives**: literal `o` sub-bullet
markers in the source and possessive splits around curly apostrophes
(`individual's`, `pilot's`). No actual damage.

`pdfplumber.extract_tables()` on the competency page: **1 clean table detected**
(`ELEMENT | PERFORMANCE STANDARDS`, bullets `●`/`o` preserved).

## What this overturns in `parser-build.md`

Three "hard-won gotchas" were artifacts of the throwaway `pdftotext -layout`
probes, **not** properties of the PDFs:

1. **Ligature mangling** (`ef f ective`, `f light`, `saf ety`, `�` bullets) — a
   pdftotext artifact. PyMuPDF reads the Pilot prose verbatim-clean. The planned
   "de-ligature normalization pass" is **largely unnecessary** with PyMuPDF.
2. **Scrambled tables → LLM fallback** — **confirmed not needed; drop from scope.**
   No table type in the corpus is scrambled in PyMuPDF reading order (see "Table
   bake-off" below).
3. **Form-feed / page-count anomaly** — not an anomaly: the Trainer PDF genuinely
   has **266 page objects** (Pilot **236**); the handoff's "86 pp / 104 pp" were
   wrong. Page objects are 1:1 with logical pages and **consecutive within a
   unit**, so iterating `doc` pages is a reliable delimiter — no `\f` parsing.

## Table bake-off (LLM-fallback decision)

Tested all three table types named in `parser-build.md` (`python ... ` → "TABLE
BAKE-OFF" section; dumps in `out/tbl-*`). The RAG question is verbatim coherence,
not a perfect grid: does the linear text keep each cell paired, or interleave?

| table | page | pdfplumber GFM | PyMuPDF reading order | verdict |
|---|---|---|---|---|
| competency `ELEMENT/STANDARDS` | TR p31 | **clean 3×2** | clean | GFM via pdfplumber |
| `Problem / Probable Cause` | TR p39 | messy 44×6 (fragmented) | **coherent problem→cause pairs** | emit reading-order text |
| colour control (CANOPY/TRIM/…) | TR p79 | clean 8×3 | clean (`AIRBRAKES BLUE`…) | either |
| colour control (Pilot) | PI p69 | **0 tables found** | **clean** (`UNDERCARRIAGE -BLACK`…) | emit reading-order text |

Contrast with `pdftotext -layout` on the same pages: it space-aligns columns
visually but **interleaves them in the text stream** (Problem/Cause rows mix
problems and causes line-by-line) — that interleave is the "scrambled tables"
the handoff saw. PyMuPDF does not produce it.

**Conclusion: drop the LLM table-fallback entirely.** Parser strategy for tables:
- try pdfplumber `extract_tables()`; if it returns **one clean table** (cols match,
  few empty cells), emit GFM;
- otherwise emit PyMuPDF reading-order text verbatim (already coherent).
- Never trust pdfplumber's *messy* grids (e.g. the 44×6 Problem/Cause) — they
  fragment cells; reading-order text is strictly better there.

## New finding the parser MUST handle

**The first page of each unit has no readable footer.** `Page U-1` is absent from
the text layer in *all three* extractors (the glyphs don't map). Footer-based
page numbering works from `Page U-2` onward; **page 1 of each unit must be
inferred** (it's the page immediately before `Page U-2`, carrying the unit title).
The `<!-- page: U-1 -->` marker is computed, not read.

## Page map used (0-indexed physical pages)

- Trainer Unit 5: p30 `5-1` (no footer), p31 competency table (`5-2`),
  p34/36/38 Suggested Patter (`5-5/5-7/5-9`).
- Pilot Unit 1: p2 `1-1`, p3 `1-2`.

## Caveat to settle later

PyMuPDF is **AGPL-3.0**. Fine for the local PoC; if InstructaMate ships as a
closed product, either buy the Artifex commercial license or fall back to
pdfplumber (MIT, ~10x slower but here equally clean on text). pdfplumber alone
could be the single extractor if the AGPL constraint bites.
