# InstructaMate

A companion app for gliding instructors (Geelong Gliding Club) built on retrieval over the
Gliding Federation of Australia (GFA) student/trainee guides. This glossary fixes the vocabulary
so design discussions stay precise. It is a glossary only — not a spec.

## Source material & retrieval

**Corpus**:
The set of GFA source documents the system is allowed to ground answers in. For the PoC this is two
documents: the **Trainer Guides** and the **Pilot Guides**, both covering Units 1–26 (Solo).
Supplementary docs (MOSP 2, Ref Cards, theory slides, logbook, syllabus chart) exist in the
`corpus/` folder but are out of PoC scope. The boundary of "what the system is allowed to know."
_Avoid_: "the RAG", "the data", "knowledge base"

**Source**:
The document a Chunk or Citation comes from — Trainer Guide or Pilot Guide. Mandatory on every
Chunk and Citation: the same (Unit, page) exists in both documents with different content, so a
citation without Source is ambiguous. Reference Patter has Source = Trainer Guide only.

**Chunk**:
A retrievable unit of the Corpus — a passage of source text plus its metadata. The thing that
gets embedded and indexed.

**Index**:
The Atlas-side structures that make Chunks searchable — the vector index over embeddings and the
full-text (Atlas Search) index. Built during Ingestion.

**Ingestion**:
The batch process that turns source documents into indexed Chunks: parse → chunk → attach
metadata → embed → write to Atlas. This is what "updating the corpus from my machine" actually does.
_Avoid_: "updating the RAG"

**Retrieval**:
Finding the Chunks most relevant to a query (hybrid vector + full-text, fused, then reranked).
Lives in the serving backend, not in Atlas.

**RAG**:
The *technique* of grounding LLM generation in retrieved Chunks. Not a stored artifact — nothing
called "the RAG" is stored in Atlas or updated; the Corpus and Index are.
_Avoid_: "the RAG" as a noun for the corpus or index

**Grounding**:
The hard constraint that every claim in a generated answer must trace to a retrieved Chunk. When
it cannot, the system refuses ("not covered in the guides I have") rather than answer.

## Domain content

**Unit**:
A GFA syllabus module. The PoC Corpus is the Trainer Guides, Units 1–26 (Solo). Each Unit has a
regular section structure (Aim, Key Messages, Pre-Flight Briefing, Flight Exercises, …).

**Exercise (Sub-exercise)**:
A discrete manoeuvre/skill taught within a Unit's Flight Exercises (e.g. Elevator, Aileron, Rudder
within Unit 5). The granularity at which Suggested Patter, when present, attaches.

**Patter**:
The standardized spoken instructional commentary an instructor delivers for an exercise. Two
provenance classes that must NEVER be confused: Reference Patter and Generated Patter.

**Reference Patter**:
Verbatim patter taken from the Corpus (the manual labels it "Suggested Patter"). Authoritative,
quoted exactly, cited (Unit, page). Present for only some sub-exercises.
_Collision warning_: the manual's own heading "Suggested Patter" is Reference Patter — NOT the
app's Generated Patter feature. Do not name the app feature "suggested patter".

**Generated Patter**:
App-drafted patter for an exercise that has no Reference Patter — a core product feature. Styled on
GFA patter conventions. Grounding contract: may draw substance from the exercise's grounded content
and related Units (option B), but introduces **no new procedural or factual claims** — only phrasing
and sequencing are new. Always labelled as an AI suggestion, instructor-reviewed, and visually
distinct from Reference Patter; never presented as authoritative GFA patter. See ADR 0001.

**Citation**:
A reference attached to an answer pointing to its source location in the Corpus, expressed as
(Unit, page) — e.g. "Unit 5, p.5". Page numbers in the Corpus are scoped per Unit ("Page 5 - 5"),
not global to the PDF.

**Unit Guide**:
The generated, multi-screen teaching artifact for a single Unit, produced for the PoC demo
(validation aspect #2): assembled briefing + flight exercises, with Reference Patter quoted (and
cited) where the Corpus provides it and Generated Patter drafted where it does not. Distinct from
the runtime Flight Pack.
_Avoid_: "flight plan guide" (ambiguous with Flight Pack)

**Flight Pack**:
The pre-compiled, offline JSON artifact consumed by the in-flight card stepper at runtime.
Distinct from a Unit Guide (which is a pre-flight teaching artifact, not a runtime one).

## Content roles (content_type)

A role tag on every Chunk, derived deterministically from its section heading via a per-Source
header dictionary.

- **Primary** (retrievable; drive Q&A and Generated-Patter grounding): `key_messages`, `theory`,
  `briefing`, `exercise`, `reference_patter`, `common_problems`, `airmanship` (from the
  "Threat and Error Management" sections).
- **Secondary** (stored, excluded from default retrieval): `aim`, `competency`, `self_check`
  (the Pilot Guide "Self-Check Questions" — used to seed evals for the PoC; may later surface in
  brief/debrief), `admin` (prerequisites, complementary units, resources & references).
