# Structural chunk IDs with content-hash change detection

Every chunk gets two identity fields, with distinct jobs:

- **Chunk ID** — the stable structural path `source:unit:section-slug[:sub-slug][:cN]`
  (e.g. `trainer:5:lesson-planning-and-conduct:use-of-elevator:c1`), derived
  deterministically from the Markdown's heading tree and child ordinals. Slugs come from the
  verbatim headings (lowercased, non-alphanumeric runs → hyphen, leading ordinals kept).
- **Content Hash** — `sha256` of the chunk's full embedding input (deterministic context
  prefix + verbatim text), so a heading-path or content_type change re-embeds even when the
  body text didn't change.

Sync never parses a git diff. Each run regenerates all chunk records from the MD tree (cheap,
deterministic), then reconciles against the index by ID: new ID → embed+insert; same ID with a
changed hash → re-embed+replace; ID gone → delete. The git diff of the Markdown remains the
human audit view only.

## Why

- Wording edits (the common case — hand-corrections to the MD) keep their ID and change only
  the hash, so exactly the touched chunks re-embed, and external references to IDs
  (golden-set citations in `evals/golden_set.json`, saved answers) survive.
- Reconciling against the index's actual state is idempotent and self-healing: a half-failed
  sync converges on the next run, a fresh clone or dirty working tree needs no git history,
  and there is no second source of truth to drift (as a committed lock file would).
- Structural paths are readable in logs and evals — an instructor-facing citation dispute can
  be traced to `trainer:5:key-messages:c1` by eye.

## Trade-off

Alternatives: content-hash-as-ID (any edit destroys identity — no "same chunk, updated", every
touch-up breaks eval references), positional index IDs (one inserted paragraph shifts every
subsequent ID in the unit), and parsing the git diff for change detection (requires clean git
state, breaks on first sync and on partial-failure recovery). Structural paths accept one known
weakness: renaming a heading or reordering children rewrites the affected IDs, forcing those
chunks to re-embed and orphaning references to them. Accepted because headings are verbatim
source text and effectively frozen — the corpus changes by hand-correction, not restructuring.
