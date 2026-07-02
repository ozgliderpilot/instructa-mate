# AGENTS.md

## Cursor Cloud specific instructions

InstructaMate is a **single Python 3.12+ library** — a deterministic stage-1 parser that
turns GFA guide PDFs into verified Markdown. There is **no server, daemon, CLI, or
database**; the "application" is invoked as a Python function and validated with `pytest`.
Standard build/test commands live in `README.md` (section "Running stage 1").

### Environment
- Dependencies are installed into a virtualenv at `.venv/` by the startup update script
  (`pip install -e ".[dev]"`). It is gitignored. Run tools via `.venv/bin/<tool>` or
  `source .venv/bin/activate` first.
- `python3 -m venv` needs the `python3.12-venv` system package; it is preinstalled in the
  VM snapshot, so the update script does not reinstall it.

### Test / run
- Test: `.venv/bin/pytest` (config is in `pyproject.toml`; `pythonpath=["src"]`,
  `testpaths=["tests"]`).
- Expect **most tests to SKIP**: the real GFA Trainer/Pilot PDFs are third-party copyright
  and gitignored under `corpus/` (see `tests/conftest.py`). A fresh checkout runs green
  with only a handful of corpus-free tests passing — this is correct, not a failure. To run
  the skipped tests you must locally supply the PDFs named in `tests/conftest.py`.
- No linter/formatter is configured (no ruff/black/flake8 in `pyproject.toml`).
- To exercise the parser without the corpus, build a synthetic PDF with `fitz`: section
  headings at font size >= 13 whose text is a key in `HEADER_DICTIONARY[source]`, body at
  ~10pt, and per-page footers `Page <unit> - <n>` at <= 8.5pt, then call
  `instructamate.stage1_parser.render_unit_markdown(pdf, source, unit)`.
