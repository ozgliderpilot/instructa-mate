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
Chunk identity = structural Chunk IDs + content-hash change detection via Sync Plan (ADR 0004;
stage 2 built). Stage 3 hybrid retrieval (ADR 0005): server-side `$rankFusion` on Atlas MongoDB
8.0+; fuse **children** then expand/dedupe **parents** then rerank parents (`rerank-2.5`);
starting top-k **N=70 / keep 70 / P=10** (eval-tunable); embed with `voyage-4-large`.
Ablation curve still measured
(vector-only → +full-text `$rankFusion` → +parent rerank → +contextual-retrieval).

## Deferred items

### 1. Chunk identity & change-detection — ✅ settled (ADR 0004)
Stable chunk IDs + content hashes; Sync Plan reconciles against the index. Built in stage 2.

### 2. Retrieval pipeline (stage 3) — ✅ settled (ADR 0005)
Build incrementally and measure the ablation curve. Open work is **implementation + eval**, not
design. Depends on eval harness (#5) existing to score each ablation step.

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

### 5. Eval harness internals + GPC unit-tests finalization
**Locked:** two-tier eval — fast loop (`recall@k`, `refusal` automated; `citation faithfulness`,
`groundedness` via LLM-as-judge) + SME milestone (rate `answer correctness`, spot-check ~15–20% of
judge verdicts to calibrate). Langfuse for tracing the curve.
**GPC unit tests:** user (SME) provides **~30–40 in-corpus** instructor questions; agent provides the
**out-of-corpus refusal set**. `self_check` (Pilot "Self-Check Questions") kept in reserve for thin
content_type coverage. Persist to `evals/gpc_unit_tests_unit*.json`, schema:
`{id, question, expected_behavior: refuse|correct|decline, expected_answer, citations:[{source,unit,page}], category, content_type, difficulty, verified_absent_terms:[]}`.
**Persisted:** refusal set in `evals/refusal_probes.json` (self_check type/site + re-validated
deferred list). **PoC lock (#39 grill):** in-corpus gold = Pilot self_check Q&A runs →
`evals/golden_set.json` (217 answer + 17 refuse after dropping rudder stem that also lives in
GPC fixtures). **SME-confirmed** (human-checked). **Both** `golden_set.json` and
`evals/gpc_unit_tests_unit*.json` are eval surfaces. **Guidance (no test):** do not put the same
question stem in both sets; on the rudder clash, kept GPC and deleted `self_check-pilot-8-08`.

**Three "must-not-fabricate" behaviours (don't lump into one metric):** *Refuse* (topic absent →
"not covered in the guides I have" — the headline metric), *Correct* (false premise about in-corpus
content → correct it WITH citation), *Decline* (out-of-domain/real-time/action).
**PoC lock (#39 grill):** Collapse Refuse+Decline into one eval behaviour `refuse` (same runtime
signal: `grounded:false` / canonical string). Slice by `category` metadata (`type_specific`,
`site_local`, `absent_topic`, …) when diagnosing. Reintroduce distinct `decline` only if/when the
product emits a separate signal or message. **Correction dropped** for PoC (no good probe source).

**Drafted out-of-corpus probes (re-validated 2026-07-18 against both guides):**
- A · absent jargon: SWAFTS ✅ kept; ~~HASELL~~ ❌ DROP (Pilot U18 spells out full checklist).
- B · beyond syllabus: MacCready *ring* on final glide ✅ (theory present; ring/setting absent);
  ridge-soaring technique ✅; Silver C distance + outlanding ✅; derig/trailer-load procedure ✅
  (U35 prep checklist ≠ how-to).
- C · type-specific/numeric: ASK-21 VNE ✅; flutter onset ✅; Form 2 due/contents ✅ (named, not detailed).
- D · meteorology: sea-breeze front ✅.
- E · Correction: ~~dropped for PoC~~.
- F · out-of-domain: weekend weather ✅ (`category: out_of_domain`).
(Verified PRESENT, so rejected as probes: crosswind, winch, cable break, spin recovery, thermal,
airspace, aerobatics, parachute, ballast, stall speed, VHF, GPS.)

### 6. Aspect-#2 eval
**Held-out-patter eval:** hide real `reference_patter` for an exercise, generate from the rest of its
grounded content, score generated-vs-real (semantic similarity + SME). **SME rubric:** make "not bad"
concrete (e.g. "would brief a student with minor edits" vs "wrong/dangerous"). **Open:** rubric
dimensions (coverage, ordering/pedagogy, style match, factual grounding), scoring scale.

### 7. Atlas setup specifics — ✅ settled for ingest (#34)
Cluster: **Atlas Flex** in **ap-southeast-2 (Sydney)** (MongoDB ≥8.0; required for `$rankFusion`).
Terraform provisions Flex cluster + DB user against an **existing** Atlas project; PoC IP access
`0.0.0.0/0`; connection string via output → `MONGODB_URI`. Single DB/collection
`instructamate.chunks` (`_id` = Chunk ID, `kind` discriminator). Explicit `voyage-4-large` embeds
(`input_type=document`, `VOYAGE_API_KEY`) — not Atlas Automated Embedding. Vector Search index
`chunks_vector`: path `embedding`, 1024-d cosine, filter fields `source`,`unit`,`content_type`,`kind`;
**code-ensure** from committed index JSON (not Terraform). Atlas Search index `chunks_search`:
child `text` via custom `jargon_text` analyzer (standard tokenizer + lowercase only — no stemming,
so jargon tokens like `FUST` / `CHAOTIC` stay intact); `kind` / `content_type` as `token` for
filters; **code-ensure** from committed JSON. Rerank remains `rerank-2.5`.

### 8. Update-from-machine workflow
Sync Plan (ADR 0004) is the reconciliation; git diff of Markdown is human audit only.
**#34 implements:** `fetch_indexed_hashes` → `plan_sync` → `apply_sync` over the full committed
`corpus/md/` tree (insert/update embed+upsert; delete by id). **Open beyond #34:** higher-level
orchestration wrappers, index/version tagging for eval ablations.

## Suggested skills for the next agent
- **`grilling`** — resume the relentless one-question-at-a-time interview on the above.
- **`domain-modeling`** — keep `CONTEXT.md`/ADRs current as decisions crystallize.
- (When a node is settled and ready to build) **`superpowers:writing-plans`**, then
  **`superpowers:test-driven-development`**.
