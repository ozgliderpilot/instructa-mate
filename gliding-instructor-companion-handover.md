# Gliding Instructor Companion — Project Handover

> Context handover from a design discussion. Works as a `CLAUDE.md` seed.
> Status: architecture decided; nothing built yet. Decisions below include rationale so they don't get re-litigated.

## What we're building
A companion app for gliding instructors (Geelong Gliding Club) built on RAG over the **Gliding Federation of Australia (GFA) student/trainee guides** (MOSP Part 2 v9 and related). It helps an instructor brief, run, and debrief flight exercises.

Three features:
1. **Grounded Q&A** — instructor asks questions; answers carry exact citations (doc / unit / page). Must **never** invent a procedure or patter — this is aviation-safety critical.
2. **In-flight companion** — offline, glanceable card-stepper per exercise. Companion only; the instructor's own plan stays the source of truth (no safety dependence on the app).
3. **Patter feedback** — record the instructor's patter, transcribe, compare to the reference patter for that exercise, return improvement feedback.

## Constraints
- Phase 1: a couple of users.
- $1000 MongoDB Atlas credits — covers the Atlas cluster + Voyage usage; does **not** cover external hosting.
- Melbourne-based → Atlas in **ap-southeast-2 (Sydney)**; co-locate compute there.
- GFA guides are GFA copyright. Personal/club use likely fine; **get GFA sign-off before any public demo or open-source.**
- Doubles as a flagship production-RAG portfolio artifact (FDE interviews). The signal is evals + guardrails + observability + right-sized infra.

## Organizing principle — lifecycle split
Separate batch work from the serving backend from the offline in-flight layer.

- **Batch** (offline; GitHub Actions or local; Python): parse docs → chunk + metadata → embed → write to Atlas. Eval runs live here. Not a hosted service.
- **Serving backend** (Cloudflare Workers; TypeScript): briefing RAG, debrief processing, flight-pack delivery.
- **In-flight** (offline): a pre-compiled JSON flight pack + a state-machine card stepper. No network, no LLM calls in the air.

## RAG / data layer (Atlas)
- **Hybrid retrieval** via `$rankFusion` (RRF) combining vector + full-text. Full-text matters — the corpus is full of exact-match jargon (HASELL, FUST, SWAFTS, exercise numbers).
- **Voyage via Atlas API:** `voyage-4-lite` for embeddings; **Voyage reranker** after fusion. The reranker is the single highest-leverage component for citation accuracy (cited page actually contains the claim). Reserve `voyage-4-large` only if evals show recall gaps.
- Prefer **explicit embedding** over Atlas Automated Embedding — for chunking control and index versioning.
- **Chunking — preserve GFA structure.** Per-chunk metadata:
  `{ doc, version, unit, exercise_no, exercise_name, page, section, content_type }`
  where `content_type ∈ {briefing, airborne, patter, airmanship}`. The `content_type` tag lets retrieval filter to e.g. patter-only.
- **Small-to-big retrieval:** embed small chunks for precision, return the parent section to the LLM, cite the page.
- **Generation — hard grounding.** Refuse when out of corpus ("not covered in the guides I have") rather than hallucinate. Post-generation step verifies every citation maps to a retrieved chunk before it's shown.

## Backend hosting — DECIDED: all-Cloudflare Workers
- MongoDB driver now works on Workers via the `connect()` TCP socket API. Use a **Durable Object** to hold a warm Mongo connection and avoid per-invocation reconnect latency (for a couple of users, acceptable to skip and eat the reconnect).
- **Do not use the Atlas Data API — it's deprecated.**
- Workers fit RAG orchestration well: mostly `fetch()` to Atlas/Voyage/Anthropic; I/O wait doesn't count against CPU; streaming supported (snappy briefing chat).
- **R2** for audio storage; **Queues** for async debrief processing; **Cron Triggers** for scheduled eval runs.
- Alternative considered: **Vercel** (standard Node Mongo driver + module-scoped cached client, Sydney region) — only preferred if committing to Next.js.

## ASR (patter)
- Use an **API** (Groq Whisper or Deepgram), not a self-hosted model.
- Capture audio off the **intercom/headset** if possible — far better transcripts than an ambient cockpit mic.
- Record **per-exercise short clips**; upload directly to R2; process async via Queue. Never POST a 40-minute file through a function.

## Observability — DECIDED: Langfuse Cloud (Hobby / free)
- 50k observations/month, 2 seats, 30-day retention — ample for a couple of users.
- **Do not self-host** (v3 needs Postgres + ClickHouse + Redis + object storage — disproportionate). MIT license intact post-ClickHouse-acquisition if ever needed. Cloud is US/EU only; fine, since tracing is async.

## Evals — the differentiator (build early)
Make quality measurable from day one.
- **Golden set:** ~50–100 real instructor questions with known answers + source pages. SMEs: Vitaliy, Noel Vagg, Christopher Thorpe.
- **Metrics:** retrieval recall@k; **citation accuracy** (headline — cited page contains the answer); groundedness/faithfulness; answer correctness vs SME rating.
- **Tooling:** ragas or Arize Phoenix.
- Patter feedback is itself evaluable — GFA patter is standardized, so scoring can be semi-objective.

## In-flight UX — DECIDED
- Big glanceable buttons; companion only.
- **Card per exercise:** title + exercise number (large); patter as 4–6 scannable key points; "watch for" 2–3 bullets; citation small at the bottom; record toggle.
- **Controls:** Prev / Next / Skip / Jump-to-list / **Mark-for-debrief**. "Mark" flags items that auto-surface in the debrief — closes the loop between features.
- High-contrast (sunlight); oversized targets + generous spacing (turbulence); prefer undo over confirm dialogs; no typing.

## Stack summary
- **Serving + edge:** TypeScript on Cloudflare Workers (+ Durable Objects, R2, Queues, Cron).
- **Batch (ingest / embed / eval):** Python (LangChain or LlamaIndex, ragas) via GitHub Actions or local.
- **DB / vector:** MongoDB Atlas (Sydney) + Voyage embeddings & reranker via Atlas.
- **Obs:** Langfuse Cloud. **LLM:** Anthropic API. **ASR:** Groq / Deepgram.

## Open questions / not yet specced
- Flight-pack JSON schema.
- Chunking/metadata schema against the *actual* GFA docs — need to inspect a real PDF to see structure.
- All-Workers topology detail: route map vs Durable Object responsibilities vs R2/Queues/Cron wiring.
- ASR provider choice.
- Auth (simple, given a couple of users).

## Suggested build order
1. Ingestion + chunking/metadata schema → embeddings in Atlas; prove retrieval quality.
2. Eval harness + golden set (early — make quality measurable).
3. Briefing Q&A on Workers: hybrid + rerank + grounded generation + citation verification.
4. Flight-pack builder + offline in-flight card stepper.
5. Debrief: patter capture → ASR → feedback.
6. Langfuse tracing wired throughout.
