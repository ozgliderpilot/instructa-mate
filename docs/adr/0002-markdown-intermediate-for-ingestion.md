# Markdown intermediate as the ingestion source of truth

Ingestion is two stages, not one. Stage 1 parses each PDF into a **human-verified Markdown
rendering**, one file per `(source, unit)` (e.g. `corpus/md/trainer/unit-05.md`). Stage 2 derives
chunk records from the Markdown — never directly from the PDF. The Markdown is the source of truth
that everything downstream (chunks, embeddings, citations) is built from and audited against.

Carriage of metadata in the Markdown:
- **YAML frontmatter** for chunk-invariant fields: `source`, `unit`, `unit_name`, `revision`.
- **Heading levels** for section structure (`#` Unit, `##` Section, `###` Sub-exercise, `####`
  blocks like Suggested Patter).
- **Inline HTML-comment markers** for page boundaries (`<!-- page: 5-5 -->`) and content-type hints
  (`<!-- reference-patter -->`). Page numbers are the citation unit and must survive into the MD.

The verbatim rule holds: Markdown adds structure markup and normalization (ligature repair,
header/footer stripping, bullet/dash fixes), but never changes the source words.

## Why

- Inspectable, diffable, hand-correctable intermediate — for ~190 pages the MD can be hand-polished
  to 100% correct once, making every downstream citation auditable against a diffable artifact.
- Separates messy extraction (stage 1) from chunking (stage 2); chunking iterates without
  re-parsing PDFs.
- Normalization and the LLM table-fallback land in the MD where a `git diff` reveals them.
- Powers the "update corpus from my machine" workflow: re-parse → diff the MD → re-embed only
  changed sections.

## Trade-off

A two-stage pipeline with an editable intermediate, vs. direct PDF→chunks. Chosen because the
corpus is small and high-stakes (citation-safety-critical), where human-verifiability beats
pipeline simplicity.
