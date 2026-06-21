# Handoff — InstructaMate: Deterministic Parser Build (Stage 1: PDF → verified Markdown)

> For a fresh agent picking up the **stage-1 ingestion parser** for the InstructaMate RAG PoC.
> Design is settled; this is a build task. Read the referenced artifacts first, then build.

## What InstructaMate is (one paragraph)
A companion app for gliding instructors (Geelong Gliding Club), RAG over the GFA Trainer/Pilot
guides. This phase builds **only the RAG layer, locally in Python against MongoDB Atlas**, to
validate a PoC. No Cloudflare Workers / ASR / in-flight UI in this phase.

## Read these first (do not duplicate — they are the source of truth)
- `C:\pet\instructa-mate\CONTEXT.md` — domain glossary (Corpus, Chunk, Source, Patter/Reference/
  Generated, Citation, Unit Guide, content_type roles).
- `C:\pet\instructa-mate\docs\adr\0001-generated-patter-is-grounded-restyling.md` — patter contract.
- `C:\pet\instructa-mate\docs\adr\0002-markdown-intermediate-for-ingestion.md` — **the MD output
  contract the parser must satisfy.** This is the parser's spec for its output.
- `C:\pet\instructa-mate\gliding-instructor-companion-handover.md` — original project handover.

## Parser scope
PDF → **one verified Markdown file per `(source, unit)`** (e.g. `corpus/md/trainer/unit-05.md`).
The parser owns *only* this arrow. Chunking is stage 2 (deferred). The MD is hand-correctable and
becomes the source of truth everything downstream is built/audited against.

Decision already made: **deterministic + verbatim-preserving** parser (no LLM rewriting of source
prose). LLM allowed *only* as a narrow fallback for the scrambled competency table. Output format,
markers, and frontmatter are specified in ADR 0002 — follow it exactly.

## Corpus facts the parser MUST handle (empirical — found by inspecting the real PDFs; not in ADRs)

**Two in-scope docs**, both "Gliding Australia Training Manual … Units 1–26 (Solo)":
- `corpus/00 Combined Trainer Guides units 1-26 Solo  BBB.pdf` (86 pp) — instructor-facing, **has
  patter**.
- `corpus/00-Combined Pilot Guides 1-26 Solo.pdf` (104 pp) — student-facing, **no patter**.

**Per-source section vocabularies differ → need a per-source header dictionary, NOT one regex:**

| Trainer headers | Pilot headers | content_type |
|---|---|---|
| `AIM` | `WHAT THIS UNIT IS ABOUT` | aim |
| `KEY MESSAGES` | `KEY MESSAGES` | key_messages |
| *Use of Elevator/…* (Title-case) | `PILOT GUIDE FOR THIS UNIT` | theory |
| `LESSON PLANNING AND CONDUCT`, `PRE-FLIGHT BRIEFING` | — | briefing |
| `FLIGHT EXERCISES` (+ `Suggested Patter`) | `FLIGHT EXERCISES FOR THIS UNIT` | exercise / reference_patter |
| `THREAT AND ERROR MANAGEMENT` | (rare) | airmanship |
| `COMMON PROBLEMS` | `THINGS YOU MIGHT HAVE DIFFICULTY WITH` | common_problems |
| `COMPETENCY ELEMENTS AND PERFORMANCE STANDARDS` | — | competency |
| — | `SELF-CHECK QUESTIONS` | self_check |
| `PREREQUISITE`/`COMPLEMENTARY UNITS`, `TRAINING MATERIALS AND REFERENCES` | `COMPLEMENTARY UNITS`, `RESOURCES & REFERENCES` | admin |

(Full content_type list + primary/secondary split is in `CONTEXT.md` → "Content roles".)

**Hard-won gotchas the parser must survive:**
- **Header detection cannot be "ALL-CAPS at col 0".** `AIM` is too short; `KEY MESSAGES` is
  sometimes indented; theory headers (`Use of Elevator`) are Title-case; spelling variants/typos
  exist (`PRE-REQUISITE` vs `PREREQUISITE`, `TOW RELEAE`). → match a curated header dictionary.
- **Page numbers are per-unit, in the footer:** `Revision: 1.0  October 2021  Page 5 - 5` ⇒ Unit 5,
  page 5. Trainer uses `Page 5 - 5`; Pilot uses `Page 5-x`. This is the citation unit `(source,
  unit, page)` and MUST be emitted as `<!-- page: U-P -->` markers in the MD.
- **Running headers/footers pollute mid-content** — the repeating "Gliding Australia Training
  Manual / Trainer Guide / Unit N - Name" lines interleave *inside* sentences and split content
  across page breaks (see Trainer extract lines ~1216–1220). Must be stripped.
- **`Suggested Patter:` blocks** appear inside Flight-Exercise sub-exercises, are **sparse**
  (Unit 5 has them for Elevator + Aileron, deliberately none for Rudder), contain placeholders like
  `[reference point]`, and must be fenced + tagged `<!-- reference-patter -->` so they're isolatable.
- **Tables come out scrambled** by `pdftotext -layout`: the `COMPETENCY ELEMENTS … PERFORMANCE
  STANDARDS` two-column table interleaves; also colour-coded control tables
  (`UNDERCARRIAGE … BLACK`, `AIRBRAKES … BLUE`) and `PROBLEM / PROBABLE CAUSE` tables. → GFM table,
  LLM-fallback only for the genuinely-scrambled ones.
- **Ligature mangling, worse in the Pilot guide:** `ef f ective`, `f light`, `aircraf t`, `saf ety`;
  bullets/dashes come through as `�`. Breaks BM25 + verbatim quotes. → deterministic normalization
  pass (de-ligature, normalize bullets/dashes/replacement chars). "Verbatim" = faithful to what the
  page says, not byte-identical to a broken extraction.
- **Form-feed count anomaly:** `pdftotext` emitted 266 form-feeds for an 86-page PDF — investigate
  page-break handling before trusting `\f` as the page delimiter.

## Working artifacts already on disk (from `pdftotext -layout`, for inspection only)
`corpus/_extracted.txt` (Trainer), `corpus/_extracted_pilot.txt` (Pilot),
`corpus/_extracted_refcards.txt`, `corpus/_extracted_mosp.txt`. These were throwaway probes — the
build should decide the extractor properly (see below) and can delete these.

## Recommended build approach
1. **Extractor bake-off first.** Compare `pdftotext -layout` vs **PyMuPDF (`fitz`)** on the *worst*
   inputs — a ligature-heavy Pilot page and the scrambled competency table — and pick the better
   text layer before committing. (PyMuPDF often handles ligatures better.)
2. **TDD against hand-verified fixtures.** The parser is deterministic ⇒ ideal test-first target.
   Golden fixtures: **Trainer Unit 5** (patter + competency table + multi-page sections) and **Pilot
   Unit 1** (ligature mangling). Hand-write the expected MD, build until reproduced. Start Trainer
   (harder), then generalize across all units, then Pilot.
3. **Human-verification gate** (ADR 0002): parser emits MD → human reviews/corrects → that is the
   source of truth.
4. **First deliverable:** a short parser/ingestion *design* (extractor choice from the bake-off, the
   per-source header dictionary, normalization rules, MD-emission spec), then the TDD implementation.

## Environment notes (Windows)
- Shell: PowerShell primary; Git Bash available. Working dir: `C:\pet\instructa-mate` (NOT a git
  repo yet — consider `git init` before building).
- `pdftotext` present at `/mingw64/bin/pdftotext`. Python 3.12 with `pypdf` installed. **PyMuPDF
  (`fitz`) not confirmed — check/install.** `poppler pdftoppm` is NOT available, so the Read tool
  cannot render PDF pages to images — use text extraction (`pdftotext`/PyMuPDF) to read PDFs.

## Suggested skills for the next agent
- **`superpowers:test-driven-development`** (or `tdd`) — build the parser test-first against the two
  fixtures. Primary skill for this task.
- **`superpowers:writing-plans`** — write the parser/ingestion design as a short plan before coding.
- **`domain-modeling`** — keep `CONTEXT.md` updated if new terms surface during the build; the
  parser's header dictionary effectively encodes the section ontology.
- **`superpowers:verification-before-completion`** — before claiming the parser works, diff its MD
  output against the hand-verified fixtures and show the result.
