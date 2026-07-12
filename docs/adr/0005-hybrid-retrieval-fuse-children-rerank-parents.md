# Hybrid retrieval: fuse children, expand, then rerank parents

Stage 3 retrieves Child Chunks (the embedded search unit), fuses the vector and
full-text rankings with Atlas **`$rankFusion` on MongoDB 8.0+**, expands the fused
children to unique Parent Chunks, then **reranks those parents** (`rerank-2.5`)
before passing the top parents to the LLM. Starting widths: **N=20** per channel,
keep **20** fused children, **P=5** parents after expand+rerank — eval-tunable.

## Considered Options

- **Fusion locus:** app-side RRF vs server-side `$rankFusion`. **Chosen: server-side** —
  GA on 8.0+, keeps hybrid next to the indexes, matches the Atlas PoC. App-side only
  if the cluster cannot be 8.0+.
- **Expand vs fuse order:** expand-to-parents before fusion vs fuse children first.
  **Chosen: fuse children first** — indexed/retrieved units are children; `$rankFusion`
  sub-pipelines cannot `$lookup` parents; collapsing before fusion throws away child-rank
  detail and forces app-side parent fusion.
- **Rerank target:** rerank children then expand vs expand then rerank parents.
  **Chosen: expand then rerank parents** — the delivery unit is the parent; child-then-
  expand wastes rerank slots when several top children share one parent.

## Consequences

- Atlas cluster must run MongoDB **8.0+** (Sydney / `ap-southeast-2`).
- Embeddings: **`voyage-4-lite`** by default; escalate to **`voyage-4-large`** only if
  evals show recall gaps (shared Voyage-4 embedding space).
- Ablation curve still starts vector-only, then adds `$search`+`$rankFusion`, then parent
  rerank — each step measured on the golden set.
