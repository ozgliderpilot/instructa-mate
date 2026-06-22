"""Corpus-scale generalization tests for the stage-1 parser (issue #4).

The two golden Units (Pilot 1, Trainer 5) prove the happy path; these tests prove the
parser is *trustworthy at corpus scale*: it runs every ``(Source, Unit)`` through the
single ``render_unit_markdown`` seam, and on structure it cannot faithfully render
(variant-split units like 13A/S/W, absent units, non-consecutive page runs) it **raises
loudly** instead of emitting silently-wrong Markdown. The batch wrapper collects those
raises and still writes the clean tree for the ADR-0002 human-verification gate.

As elsewhere, assertions are on observable behaviour — the emitted Markdown string, the
raised ``UnitStructureError``, and the ``CorpusReport`` — never on parser internals.
"""
from __future__ import annotations

import fitz
import pytest

from instructamate.stage1_parser import (
    UnitStructureError,
    render_unit_markdown,
    write_corpus,
)


def _pdf_with_footers(path, footers: list[str]) -> str:
    """A throwaway PDF, one page per footer line — to exercise structure validation
    without the gitignored copyright corpus."""
    doc = fitz.open()
    for footer in footers:
        page = doc.new_page()
        page.insert_text((72, 72), "Body text.")
        page.insert_text((72, 760), footer)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_absent_unit_raises_instead_of_emitting_empty(trainer_pdf):
    # The Trainer guide has no body for Unit 23 (just a divider page), so there is no
    # (23, page) citation to emit. The old behaviour returned a ~72-char file with an
    # empty H1 and zero page markers; the parser must now refuse loudly.
    with pytest.raises(UnitStructureError) as excinfo:
        render_unit_markdown(trainer_pdf, "trainer", 23)
    assert "23" in str(excinfo.value)


def test_variant_split_unit_raises_naming_its_variants(trainer_pdf):
    # Units 13/14/20 are not plain units: each is split into A/S/W sub-units whose
    # footers read "Page 13A - 1". Those pages are real content, not footer-less — so
    # the parser must RECOGNISE the variant footers and refuse with a reason that names
    # the variants (proving the page's Citation wasn't silently lost), distinct from the
    # generic "absent" message.
    with pytest.raises(UnitStructureError) as excinfo:
        render_unit_markdown(trainer_pdf, "trainer", 13)
    message = str(excinfo.value)
    assert "13A" in message and "13S" in message and "13W" in message


def test_non_consecutive_page_run_raises(tmp_path):
    # A unit whose footers skip a page (1, then 3) can't be a faithful U-1..U-n run: a
    # page's Citation is missing. Rather than emit Markdown with a hole, refuse loudly.
    pdf = _pdf_with_footers(tmp_path / "gap.pdf", ["Page 7 - 1", "Page 7 - 3"])
    with pytest.raises(UnitStructureError) as excinfo:
        render_unit_markdown(pdf, "trainer", 7)
    assert "7" in str(excinfo.value)


def test_consecutive_synthetic_run_does_not_raise(tmp_path):
    # The guard must not fire on a clean run: 1, 2, 3 renders without complaint.
    pdf = _pdf_with_footers(tmp_path / "ok.pdf", ["Page 7 - 1", "Page 7 - 2", "Page 7 - 3"])
    md = render_unit_markdown(pdf, "trainer", 7)
    assert "<!-- page: 7-1 -->" in md and "<!-- page: 7-3 -->" in md


def test_clean_uncovered_unit_renders_with_structural_invariants(trainer_pdf):
    # Trainer Unit 1 has no hand-verified golden, but the generalized parser must still
    # produce a structurally sound rendering: the fail-loud guards must not fire on a
    # clean unit, and the inferred 1-1 page (Trainer's footer-less title page, before
    # 1-2) must still resolve so the run is consecutive.
    md = render_unit_markdown(trainer_pdf, "trainer", 1)

    fm = md.split("---\n", 2)[1]
    assert "source: trainer\n" in fm
    assert "unit: 1\n" in fm

    markers = [f"<!-- page: 1-{p} -->" for p in range(1, 7)]
    positions = [md.find(m) for m in markers]
    assert all(pos != -1 for pos in positions), dict(zip(markers, positions))
    assert positions == sorted(positions)

    assert "# Unit 1 — " in md  # H1 reassembled from the title page
    # At least one dictionary-tagged section was detected (structure, not raw prose).
    assert "<!-- content_type:" in md


def test_write_corpus_emits_clean_tree_and_reports_skips(corpus_sources, tmp_path):
    report = write_corpus(corpus_sources, out_root=tmp_path)

    written = {(o.source, o.unit) for o in report.written}
    skipped = {(o.source, o.unit) for o in report.skipped}

    # Both Sources go through the one pipeline; the two goldens are among the written.
    assert ("trainer", 5) in written
    assert ("pilot", 1) in written

    # The variant-split (13/14/20) and absent units (Trainer 23, Pilot 26) are skipped —
    # loudly, with a reason — not silently emitted.
    assert {u for s, u in skipped if s == "trainer"} == {13, 14, 20, 23}
    assert {u for s, u in skipped if s == "pilot"} == {13, 14, 20, 26}
    assert all(o.error for o in report.skipped)
    assert all(o.path is None for o in report.skipped)

    # Files land at the stable path only for written units.
    assert (tmp_path / "trainer" / "unit-05.md").exists()
    assert (tmp_path / "pilot" / "unit-01.md").exists()
    assert not (tmp_path / "trainer" / "unit-13.md").exists()
    assert not (tmp_path / "pilot" / "unit-26.md").exists()
    assert all(o.path is not None and o.path.exists() for o in report.written)


def test_write_corpus_reproduces_committed_goldens(corpus_sources, tmp_path, trainer_unit5_golden, pilot_unit1_golden):
    # The batch must reproduce the hand-verified source of truth byte-for-byte: going
    # through write_corpus is the same as the per-unit seam (subset kept small for speed).
    write_corpus(corpus_sources, out_root=tmp_path, units=[1, 5])

    assert (tmp_path / "trainer" / "unit-05.md").read_text(encoding="utf-8") == trainer_unit5_golden
    assert (tmp_path / "pilot" / "unit-01.md").read_text(encoding="utf-8") == pilot_unit1_golden


def test_write_corpus_rerun_is_byte_identical(corpus_sources, tmp_path):
    # Re-parsing an unchanged Corpus is byte-stable, so a git diff of corpus/md/ shows
    # only real changes and re-embedding can be scoped to what actually moved.
    first = write_corpus(corpus_sources, out_root=tmp_path / "a", units=[1, 5])
    second = write_corpus(corpus_sources, out_root=tmp_path / "b", units=[1, 5])

    assert {(o.source, o.unit) for o in first.written} == {(o.source, o.unit) for o in second.written}
    for outcome in first.written:
        twin = tmp_path / "b" / outcome.source / outcome.path.name
        assert outcome.path.read_bytes() == twin.read_bytes()
