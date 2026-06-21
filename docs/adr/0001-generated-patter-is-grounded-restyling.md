# Generated Patter is grounded restyling, not free generation

InstructaMate generates suggested patter for exercises the GFA manual leaves without patter — a
core feature that appears to contradict the project's "never invent a procedure or patter" safety
rule. We resolve this by splitting patter into two provenance classes: **Reference Patter**
(verbatim, cited from the Corpus, authoritative) and **Generated Patter** (AI-drafted). Generated
Patter operates under a strict contract: it may draw substance only from grounded Corpus content
for the exercise **and related Units** (option B), introduces **no new procedural or factual
claims** (only phrasing and sequencing are new), is always labelled as an AI suggestion and
instructor-reviewed, and is visually/structurally distinct from Reference Patter.

## Considered Options

- **(A)** Restyle from the exercise's own grounded content only.
- **(B)** Restyle from the exercise + related Units. **Chosen** — enables cross-unit consistency
  (lookout, hand-over/take-over conventions) while staying fully traceable to the Corpus.
- **(C)** Open generation from the model's own gliding knowledge. **Rejected** — uncitable,
  unverifiable, defeats the "grounded but generative" differentiator and breaks the held-out-patter
  eval.

## Consequences

- Requires a claim-grounding / faithfulness check over Generated Patter: no claim without a
  supporting Chunk (the citation-verification machinery applied to generated text).
- Requires a provenance-aware presentation (Reference vs Generated never confusable).
- Enables a semi-objective eval: hold out real Reference Patter for an exercise, generate patter
  from the rest of its grounded content, and score generated-vs-real.
