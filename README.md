# InstructaMate

> A grounded, citation-safe AI co-pilot for gliding instructors.

![Python](https://img.shields.io/badge/python-3.12+-blue)
![Status](https://img.shields.io/badge/status-stage%202%20of%204%20built-orange)
![Scope](https://img.shields.io/badge/scope-proof%20of%20concept-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

InstructaMate answers a gliding instructor's questions **strictly from the official
Gliding Federation of Australia (GFA) training manuals** — and refuses when the answer
isn't in them. Every claim it makes traces back to a specific Unit and page; nothing is
invented. It's built for the instructors of the Geelong Gliding Club, over the GFA
Trainer and Pilot guides.

The interesting problem isn't "wire up a RAG pipeline." It's making one that is
**safe to trust in an aircraft**: where a fabricated procedure is a safety failure, every
sentence must be attributable to the manual, and the system must say *"not covered in the
guides I have"* rather than guess.

---

## Domain in 60 seconds

If you know RAG but not gliding instruction, these five terms carry the design:

- **Unit** — a GFA syllabus module (1–26 for the *Solo* phase). Each has a regular
  section structure (Aim, Key Messages, Pre-Flight Briefing, Flight Exercises, …).
- **Source** — *which* guide a fact comes from: the **Trainer Guide** (instructor-facing,
  has patter) or the **Pilot Guide** (student-facing, no patter). The same `(Unit, page)`
  exists in both with different content, so every citation must name its Source.
- **Patter** — the standardised spoken commentary an instructor delivers during a
  manoeuvre ("looking left… stick central… ease back…").
- **Reference Patter vs Generated Patter** — patter quoted **verbatim from the manual**
  (authoritative, cited) versus patter **AI-drafted** for exercises the manual leaves
  blank. These must *never* be confused; the app keeps them provenance-distinct.
- **Grounding** — the hard rule that every generated claim cites a retrieved source, or
  the system refuses.

Full glossary → [`CONTEXT.md`](CONTEXT.md).

---

## Architecture

Ingestion is deliberately split so the messy, high-stakes part (extraction) is frozen and
human-verified before anything is embedded. The Markdown intermediate is the **source of
truth** that every downstream citation is audited against.

```
   GFA PDFs                                                         ✅ = built
  (Trainer,                                                         ◻ = designed
   Pilot)
      │
      ▼
┌─────────────────────────────┐
│ Stage 1 · Ingestion      ✅ │  PyMuPDF (text+geometry) + pdfplumber (tables)
│   PDF → verified Markdown   │  → one .md per (Source, Unit), human-verified
└──────────────┬──────────────┘
               │  corpus/md/<source>/unit-NN.md   ← the source of truth
               ▼
┌─────────────────────────────┐
│ Stage 2 · Chunking       ✅ │  Markdown → chunk records
│   parent/child + metadata   │  (content_type, page citation, stable IDs + hashes)
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Stage 3 · Retrieval      ◐  │  Ingest+hybrid ✅ · parent rerank still ◻
│   over MongoDB Atlas        │  (ADR 0005)
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Stage 4 · Generation     ◻  │  grounded answer + citations, OR refuse;
│   refuse-or-cite contract   │  Generated Patter as grounded restyling
└─────────────────────────────┘
```

### Stage 1 · Ingestion — **built**

A deterministic, **verbatim-preserving** parser that turns each PDF into one verified
Markdown file per `(Source, Unit)`. No LLM rewrites the source prose. This is the only
stage that runs today, and it's where most of the engineering went, because the PDFs fight
back. What it does:

- **Detects structure from font geometry, not text heuristics.** It reads PyMuPDF's
  `get_text("dict")` and classifies headings by size/weight/font rather than the brittle
  "ALL-CAPS at column 0" rule (which fails on `AIM`, indented `KEY MESSAGES`, Title-case
  theory headers, and typos like `TOW RELEAE`).
- **Maps every section heading to a `content_type` role** via a per-Source header
  dictionary — an 11-value taxonomy split into *primary* (retrievable: `key_messages`,
  `theory`, `briefing`, `exercise`, `reference_patter`, `common_problems`, `airmanship`)
  and *secondary* (stored, excluded from default retrieval: `aim`, `competency`,
  `self_check`, `admin`).
- **Preserves the citation unit.** GFA page numbers are *per-Unit* and live in the footer
  (`Page 5 - 5` ⇒ Unit 5, page 5). These survive into the Markdown as inline
  `<!-- page: U-P -->` markers, so a chunk always knows its `(source, unit, page)`.
- **Fails loud instead of emitting silently-wrong citations.** When a Unit's pages aren't
  a clean consecutive run — it's variant-split (Units 13/14/20 each fork into A/S/W for
  aerotow / self-launch / winch), absent from a Source, or has a missing footer — the
  parser raises `UnitStructureError`. A citation error surfaces at *parse* time, not at
  *answer* time.
- **Renders the ruled two-column tables** (competency standards, Problem/Probable-Cause)
  in reading order — left column as a `###` sub-heading, right column as its bullets —
  rather than as a grid that buries which side is which.
- **Generalises across the corpus.** The same single seam handles both Sources, the
  variant-split Units, and the GPC guides (Units 27–44), driven by data (the header
  dictionary + footer scan), not per-unit special-casing.

The emitted Markdown carries metadata in three ways: **YAML frontmatter** for
chunk-invariant fields (`source`, `unit`, `unit_name`, `revision`); **heading levels** for
structure (`#` Unit, `##` Section, `###` Sub-exercise, `####` blocks like Suggested
Patter); and **HTML-comment markers** for page boundaries and `content_type`. The verbatim
rule holds throughout: the parser adds structure and repairs extraction artefacts
(ligatures, stray bullets), but never changes the source words. *"Verbatim" means faithful
to what the page says — not byte-identical to a broken extraction.*

### Stage 2 · Chunking — **built**

Derives chunk records from the verified Markdown (never from the PDF directly):
leaf-section *parent* chunks with paragraph *child* chunks, `reference_patter` isolated as
its own chunk. Structural Chunk IDs + content hashes drive a Sync Plan so re-ingest
re-embeds only what changed (ADR 0004).

### Stage 3 · Retrieval — **ingest + hybrid children built; parent rerank open**

**Ingest (#34):** Terraform provisions Atlas Flex (`AP_SOUTHEAST_2`); runtime Sync Plan
embeds children with explicit `voyage-4-large` (`input_type=document`) into
`instructamate.chunks` and code-ensures Vector Search index `chunks_vector` plus
Atlas Search index `chunks_search` (jargon-preserving `jargon_text` analyzer). See
[`terraform/README.md`](terraform/README.md).

**Query path (#35–#36):** embed query → vector-only or server-side `$rankFusion`
(vector + full-text on children, keep 70) → expand to unique **parents** → top
**P=10**. Parent `rerank-2.5` remains open (ADR 0005).

### Stage 4 · Generation — **designed**

Two grounded outputs, both under a strict **refuse-or-cite** contract:

- **Q&A** — answer from retrieved chunks with citations, or emit a structured refusal.
  Three behaviours kept distinct: *refuse* (topic absent), *correct* (false premise about
  in-corpus content → correct it *with* a citation), *decline* (out-of-domain / real-time).
- **Generated Patter** — drafts patter for exercises the manual leaves blank, as
  **grounded restyling**: it may re-phrase and re-sequence existing grounded content but
  introduces *no new procedural or factual claims*, is always labelled as an AI suggestion,
  and is rendered visually distinct from Reference Patter (see ADR 0001).

---

## Key design decisions

The reasoning behind the load-bearing choices lives in short ADRs:

- **[ADR 0001](docs/adr/0001-generated-patter-is-grounded-restyling.md)** — Generated
  Patter is *grounded restyling, not free generation*. Resolves the apparent contradiction
  between "draft new patter" and "never invent a procedure."
- **[ADR 0002](docs/adr/0002-markdown-intermediate-for-ingestion.md)** — A
  human-verified **Markdown intermediate** is the ingestion source of truth. Trades
  pipeline simplicity for an inspectable, diffable, hand-correctable artefact — worth it
  for a small, citation-safety-critical corpus.
- **[ADR 0003](docs/adr/0003-pymupdf-extractor.md)** — **PyMuPDF** for text+geometry,
  **pdfplumber** for table ruling, **no LLM table-fallback**. Settled by a head-to-head
  bake-off on the corpus's worst pages (PyMuPDF read them with zero replacement glyphs and
  zero split words, so the planned de-ligature pass became unnecessary).
- **[ADR 0004](docs/adr/0004-structural-chunk-identity.md)** — Structural **Chunk IDs** +
  content-hash change detection; Sync Plan reconciles against the index (no git-diff parsing).
- **[ADR 0005](docs/adr/0005-hybrid-retrieval-fuse-children-rerank-parents.md)** — Stage 3
  hybrid retrieval: server-side `$rankFusion` on children, expand to parents, then rerank
  parents (`rerank-2.5`).

---

## Running stage 1–3 (library)

Requires Python 3.12+.

```bash
pip install -e ".[dev]"
pytest
```

The real GFA PDFs are **not committed** (third-party copyright — see below), so they live
in a gitignored `corpus/`. Tests that need them auto-skip, so a fresh checkout runs green
without them. Atlas/Voyage live smoke also skips unless `MONGODB_URI` and
`VOYAGE_API_KEY` are set.

With the PDFs present, the parser's public API is three functions in
`instructamate.stage1_parser`:

```python
from instructamate.stage1_parser import render_unit_markdown, write_corpus

# one (source, unit) → Markdown string
md = render_unit_markdown("corpus/…Trainer….pdf", "trainer", 5)
md = render_unit_markdown("corpus/…Trainer….pdf", "trainer", "13A")  # a variant sub-unit

# the whole corpus → corpus/md/<source>/unit-NN.md, with a report of skips + reasons
report = write_corpus({
    "trainer": "corpus/00 Combined Trainer Guides units 1-26 Solo  BBB.pdf",
    "pilot":   "corpus/00-Combined Pilot Guides 1-26 Solo.pdf",
})
print(len(report.written), "written;", len(report.skipped), "skipped")
```

```python
import os
from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection, ingest_corpus

report = ingest_corpus(
    "corpus/md",
    collection=chunks_collection(os.environ["MONGODB_URI"]),
    embedder=VoyageEmbedder(),  # uses VOYAGE_API_KEY
)
print(report)
```

`render_unit_markdown` is the single seam; `write_unit_markdown` and `write_corpus` are
thin batch wrappers. There is no CLI yet.

---

## Repository layout

```
src/instructamate/stage1_parser.py   PDF → verified Markdown
src/instructamate/stage2_chunker.py  Markdown → ChunkRecords + Sync Plan
src/instructamate/stage3_ingest.py   Sync Plan → Voyage embed → Atlas chunks
src/instructamate/data/chunks_vector.json  Vector Search index definition
terraform/                           Atlas Flex cluster (existing project)
corpus/                              GFA PDFs — gitignored (copyright)
corpus/md/<source>/unit-NN.md        verified Markdown — the source of truth
docs/adr/                            architecture decision records
tests/                               TDD suite against hand-verified goldens
CONTEXT.md                           domain glossary (ubiquitous language)
parser-build.md                      stage-1 build handover
defered-grill.md                     designed-but-unbuilt decisions (stages 2–4)
```

---

## Status & roadmap

- [x] **Stage 1 — Ingestion**: deterministic verbatim PDF → Markdown parser; fail-loud on
      structural ambiguity; generalises across Trainer/Pilot, variant-split Units, and GPC.
- [x] **Stage 2 — Chunking**: chunk schema, stable IDs + content hashes, Sync Plan.
- [ ] **Stage 3 — Retrieval**: Atlas ingest ✅; vector→expand ✅; `$rankFusion` hybrid ✅;
      parent `rerank-2.5` still open (ADR 0005).
- [ ] **Stage 4 — Generation**: refuse-or-cite Q&A and Generated Patter, with a
      claim-grounding check.
- [ ] **Eval harness**: two-tier (automated `recall@k`/refusal + LLM-as-judge faithfulness,
      then an SME milestone) validating citation accuracy and an instructor-approved
      Unit Guide.

The PoC's two success criteria: (1) citation accuracy at roughly ≥90% on an instructor
question set, and (2) an instructor-approved, fully-cited static Unit Guide for Unit 5.

---

## Corpus & licensing

The InstructaMate source code is released under the **MIT License** — see
[`LICENSE`](LICENSE). The corpus is a separate matter:

The GFA Trainer and Pilot guides are **third-party copyright** and are not redistributed in
this repository; the parser is designed to run against locally-supplied copies. The
hand-verified Markdown under `corpus/md/` is a derived rendering of that copyrighted
material and is treated accordingly.

Note that **PyMuPDF is AGPL-3.0** — fine for this local proof of concept, but a constraint
to resolve (commercial licence, or fall back to pdfplumber as a single extractor) before
any closed-source distribution. See [ADR 0003](docs/adr/0003-pymupdf-extractor.md).
