# Handoff — InstructaMate: Deferred Grilling Items

> Design decisions that were **consciously deferred** during the RAG-design grilling session, so a
> future session can resume the interview at the right place. Ordered roughly by when they become
> relevant. Each has the open question(s) + any leaning already surfaced.
>
> Context + locked decisions live in: `C:\pet\instructa-mate\CONTEXT.md`,
> `docs\adr\0001-*.md`, `docs\adr\0002-*.md`, and the parser handoff (`parser-build.md`).
> Re-grill with the **`grilling`** + **`domain-modeling`** skills (this session used both).

## Already locked (do NOT re-litigate)
PoC = local Python vs Atlas; corpus = Trainer+Pilot guides (supplementary deferred); validation =
(#1) citation accuracy ≥~90% via two-tier eval, (#2) instructor-approved static Unit Guide (PDF/HTML)
for Unit 5; Generated Patter = grounded restyling option B, no new facts (ADR 0001); ingestion =
deterministic verbatim parser → Markdown intermediate (ADR 0002) → paragraph-child / leaf-section-
parent chunks, reference_patter isolated; content_type = 11-value taxonomy; embedding text = (b)
deterministic context-prefix, with (c) LLM contextual-retrieval as an eval-gated upgrade.

## Deferred items

### 1. Chunk identity & change-detection — *settle FIRST, before the chunker*
Stable chunk IDs + content hashes so "re-ingest from my machine" re-embeds only what changed. Has a
chunk-**schema** implication (id + hash fields). Does not touch the parser. **Open:** ID scheme
(stable across re-parses?), hashing granularity (child vs parent), how MD-diff drives re-embedding.

### 2. Retrieval pipeline (stage 3)
**Leaning:** build **incrementally and measure the ablation curve** (vector-only → +full-text RRF →
+rerank → +contextual-retrieval), because aspect #1's value is seeing each component earn its keep.
**Open:** `$rankFusion` server-side vs app-side RRF (**verify Atlas/MongoDB 8.1 `$rankFusion`
availability**); top-k at each stage (retrieve N, fuse, rerank to M, expand to parents, pass P to
LLM); reranker = Voyage rerank (**verify model id** — handover says "Voyage reranker"; known lineup
`rerank-2.5`). Depends on eval harness (#5) existing.

### 3. Grounded generation + citation verification (Q&A path, stage 3)
Refuse-or-cite contract. **Must emit a structured refusal signal** (e.g. `grounded:false` / canonical
refusal string) so the automated refusal metric can detect it. Post-generation step verifies every
citation maps to a retrieved chunk before display. **Open:** prompt/grounding strategy, refusal
threshold, citation-verification mechanics.

### 4. Generated Patter + claim-grounding check (stage 4) — see ADR 0001
**Open:** retrieval scope for grounding (exercise + related units, filtered by content_type
`{exercise,briefing,theory,key_messages,common_problems,airmanship}`); using `reference_patter` as
*style* exemplars; the claim-grounding/faithfulness check (no claim without a supporting chunk);
provenance-aware rendering (Reference vs Generated never confusable). Feeds the aspect-#2 Unit Guide.

### 5. Eval harness internals + golden-set finalization
**Locked:** two-tier eval — fast loop (`recall@k`, `refusal` automated; `citation faithfulness`,
`groundedness` via LLM-as-judge) + SME milestone (rate `answer correctness`, spot-check ~15–20% of
judge verdicts to calibrate). Langfuse for tracing the curve.
**Golden set:** user (SME) provides **~30–40 in-corpus** instructor questions; agent provides the
**out-of-corpus refusal set**. `self_check` (Pilot "Self-Check Questions") kept in reserve for thin
content_type coverage. Persist to `evals/golden_set.json`, schema:
`{id, question, expected_behavior: refuse|correct|decline, expected_answer, citations:[{source,unit,page}], category, content_type, difficulty, verified_absent_terms:[]}`.
**Open / to persist:** the ~12 drafted refusal probes below (NOT yet saved anywhere); whether to
include the false-premise/**Correction** probes in the PoC; final per-category counts.

**Three "must-not-fabricate" behaviours (don't lump into one metric):** *Refuse* (topic absent →
"not covered in the guides I have" — the headline metric), *Correct* (false premise about in-corpus
content → correct it WITH citation), *Decline* (out-of-domain/real-time/action).

**Drafted out-of-corpus probes (every topic verified absent via grep against both guides):**
- A · absent jargon: SWAFTS check items; HASELL pre-aerobatic check.
- B · beyond syllabus: MacCready ring on final glide; ridge-soaring lift technique; Silver C
  distance + outlanding; rigging/de-rigging + trailer loading.
- C · type-specific/numeric: ASK-21 VNE + max load factor; flutter onset airspeed; Form 2 annual
  inspection due/contents.
- D · meteorology depth: sea-breeze front formation + soaring use.
- E · false premise (Correction, not refusal): "since the rudder turns the glider, how much rudder
  to start a turn?" (Unit 5 teaches rudder yaws ≠ turns → correct w/ citation).
- F · out-of-domain: this weekend's weather at the club.
(Verified PRESENT, so rejected as probes: crosswind, winch, cable break, spin recovery, thermal,
airspace, aerobatics, parachute, ballast, stall speed, VHF, GPS.)

### 6. Aspect-#2 eval
**Held-out-patter eval:** hide real `reference_patter` for an exercise, generate from the rest of its
grounded content, score generated-vs-real (semantic similarity + SME). **SME rubric:** make "not bad"
concrete (e.g. "would brief a student with minor edits" vs "wrong/dangerous"). **Open:** rubric
dimensions (coverage, ordering/pedagogy, style match, factual grounding), scoring scale.

### 7. Atlas setup specifics
Cluster tier in **ap-southeast-2 (Sydney)**; Vector Search index (dims/similarity; filter fields
`source`,`unit`,`content_type`); Atlas Search (full-text) analyzer tuned to keep jargon tokens
(`FUST`, `CHAOTIC`, exercise names/numbers) intact. **Verify Voyage embedding model id** — handover
says `voyage-4-lite`/`voyage-4-large`; known lineup is `voyage-3.5`/`voyage-3-large`. Doesn't change
design, only config.

### 8. Update-from-machine workflow
Re-parse → `git diff` the MD → re-embed only changed chunks. Depends on #1 (chunk identity).
**Open:** orchestration, idempotency, index/version tagging.

## Suggested skills for the next agent
- **`grilling`** — resume the relentless one-question-at-a-time interview on the above.
- **`domain-modeling`** — keep `CONTEXT.md`/ADRs current as decisions crystallize.
- (When a node is settled and ready to build) **`superpowers:writing-plans`**, then
  **`superpowers:test-driven-development`**.
