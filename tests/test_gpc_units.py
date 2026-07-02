"""GPC half of the Corpus — Units 27-44, both Sources (issue #8).

The GPC guides reuse the Solo machinery (same footer Citation, same per-Source page
model, two-column tables, Reference Patter) but introduce **new section vocabulary** in
both Sources. Slice #4 made the parser fail loud on page/footer structure it can't
render; this slice extends that contract to *section* structure: a section-sized heading
the per-Source dictionary doesn't know is unhandled structure and must raise
``UnitStructureError`` rather than emit a silently-wrong ``content_type: None``. The
known GPC (and a handful of previously-silent Solo) headers are then mapped so their
units render through the same single seam.

As elsewhere, assertions are on observable behaviour — the emitted Markdown string, the
raised ``UnitStructureError``, the ``CorpusReport`` — never on parser internals.
"""
from __future__ import annotations

import fitz
import pytest

from instructamate.stage1_parser import (
    UnitStructureError,
    render_unit_markdown,
    write_corpus,
)

GPC_UNITS = range(27, 45)


def _pdf_with_section(path, heading: str, footer: str) -> str:
    """A one-page throwaway PDF carrying a section-sized bold *heading* and a *footer*
    Citation — to exercise section-structure validation without the gitignored corpus."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), heading, fontname="hebo", fontsize=14)
    page.insert_text((72, 140), "Body text.", fontname="helv", fontsize=11)
    page.insert_text((72, 760), footer, fontname="helv", fontsize=8)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_unmapped_section_header_fails_loud(tmp_path):
    # A section-sized bold heading absent from the per-Source dictionary is unhandled
    # structure: emitting it as `## H` + `content_type: None` is silently wrong (a Chunk
    # with no role). The parser must instead raise UnitStructureError naming the heading,
    # so the gap is reported at parse time and fixed by mapping it — not shipped silent.
    pdf = _pdf_with_section(tmp_path / "x.pdf", "ZZZ NONSENSE HEADER", "Page 9 - 1")
    with pytest.raises(UnitStructureError) as excinfo:
        render_unit_markdown(pdf, "trainer", 9)
    assert "ZZZ NONSENSE HEADER" in str(excinfo.value)


def test_trainer_gpc_unit_renders_with_mapped_vocabulary(trainer_gpc_pdf):
    # A GPC Trainer unit goes through the one render_unit_markdown seam (Units 27-44 live
    # in a different PDF but the same Source). Its GPC-only headers — e.g. the hyphenated
    # `PRE-REQUISITE UNITS` — must be mapped to a content_type, so the unit renders fully
    # with no `content_type: None` rather than fail loud on unhandled vocabulary.
    md = render_unit_markdown(trainer_gpc_pdf, "trainer", 30)

    fm = md.split("---\n", 2)[1]
    assert "source: trainer\n" in fm
    assert "unit: 30\n" in fm

    assert "content_type: None" not in md
    assert "## PRE-REQUISITE UNITS\n<!-- content_type: admin -->" in md
    # Unit 30 carries Suggested Patter — the Reference-Patter path still fires in GPC.
    assert "#### Suggested Patter\n<!-- content_type: reference_patter -->" in md


def test_wrapped_section_heading_joins_into_one_section(trainer_gpc_pdf):
    # GPC Unit 42's "TRAINING NOTES AND LESSON PLANNING FOR POWERED SAILPLANE PILOTS"
    # prints as one heading wrapped across two lines. The two lines must join into a single
    # mapped section, not split into a bogus second "## SAILPLANE PILOTS" with no role.
    md = render_unit_markdown(trainer_gpc_pdf, "trainer", 42)
    assert (
        "## TRAINING NOTES AND LESSON PLANNING FOR POWERED SAILPLANE PILOTS\n"
        "<!-- content_type: briefing -->"
    ) in md
    assert "## SAILPLANE PILOTS" not in md
    assert "content_type: None" not in md


def test_pilot_title_page_unit_banner_is_stripped(pilot_gpc_pdf):
    # Pilot Unit 33's title page prints "Unit 33" (bold, section-sized) on its own line
    # with the unit name on a separate line. The bold "Unit 33" is running-title chrome —
    # it must be stripped, not emitted as a "## Unit 33" section — while the name is still
    # recovered into the H1 from the content page's running header.
    md = render_unit_markdown(pilot_gpc_pdf, "pilot", 33)
    assert "unit: 33\n" in md.split("---\n", 2)[1]
    assert "## Unit 33" not in md
    assert "content_type: None" not in md
    h1 = next(line for line in md.splitlines() if line.startswith("# Unit "))
    assert h1 == "# Unit 33 — Thermal Source and Structure"


def test_trainer_gpc_30_matches_golden(trainer_gpc_pdf, trainer_unit30_golden):
    # The hand-verified Trainer GPC Unit 30 is the committed source of truth (ADR 0002)
    # and the golden the GPC pipeline must reproduce byte-for-byte — it locks the GPC
    # vocabulary, Suggested Patter, and the competency table on the Trainer side.
    assert render_unit_markdown(trainer_gpc_pdf, "trainer", 30) == trainer_unit30_golden


def test_pilot_gpc_27_matches_golden(pilot_gpc_pdf, pilot_unit27_golden):
    # The hand-verified Pilot GPC Unit 27 locks the Pilot-side GPC output (the full
    # per-Source section vocabulary, self-check questions) and is a clean cross-Source
    # check that the one pipeline ingests both GPC guides.
    assert render_unit_markdown(pilot_gpc_pdf, "pilot", 27) == pilot_unit27_golden


def test_write_corpus_ingests_gpc_and_reports_structural_skips(gpc_sources, tmp_path):
    # Both GPC PDFs run through the one write_corpus pipeline (same Source names, the
    # 27-44 range). Every Trainer unit emits; the Pilot guide omits Units 43/44, so those
    # two fail loud and are *reported* as skips with a reason — not emitted silently
    # wrong. Units 37/38 emit despite the source's mislabeled Unit 38 footers (below).
    report = write_corpus(gpc_sources, out_root=tmp_path, units=GPC_UNITS)

    written = {(o.source, str(o.unit)) for o in report.written}
    skipped = {(o.source, o.unit) for o in report.skipped}

    assert {f"{u}" for s, u in written if s == "trainer"} == {str(u) for u in GPC_UNITS}
    assert ("trainer", "30") in written and ("pilot", "27") in written

    assert {u for s, u in skipped if s == "pilot"} == {43, 44}
    assert {u for s, u in skipped if s == "trainer"} == set()
    assert all(o.error for o in report.skipped)

    assert (tmp_path / "trainer" / "unit-30.md").exists()
    assert (tmp_path / "pilot" / "unit-27.md").exists()
    assert (tmp_path / "pilot" / "unit-37.md").exists()
    assert (tmp_path / "pilot" / "unit-38.md").exists()


# --- Pilot 37/38: the source prints Unit 38's footers as "Page 37-x" (issues #12/#13) --


def test_pilot_gpc_37_and_38_emit_despite_mislabeled_footers(pilot_gpc_pdf):
    # The Pilot GPC guide prints Unit 38's six footer Citations as `Page 37-1`..`37-6`
    # while the running header on those pages reads "Unit 38 – Meteorology and Flight
    # Planning" — a source-document error. The header-corroborated footer correction
    # reattributes those pages, so Unit 37 renders only Passenger Carrying (its true
    # 3-page run) and Unit 38 renders with corrected 38-x Citations, not the printed 37-x.
    md37 = render_unit_markdown(pilot_gpc_pdf, "pilot", 37)
    assert "unit_name: Passenger Carrying" in md37
    assert "<!-- page: 37-3 -->" in md37 and "<!-- page: 37-4 -->" not in md37

    md38 = render_unit_markdown(pilot_gpc_pdf, "pilot", 38)
    assert "unit_name: Meteorology and Flight Planning" in md38
    for p in range(1, 7):
        assert f"<!-- page: 38-{p} -->" in md38
    assert "content_type: None" not in md38


def _pdf_with_footer_conflict(path, header: str, footer: str) -> str:
    """A one-page throwaway PDF whose running header and footer Citation disagree."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), header, fontname="helv", fontsize=14)
    page.insert_text((72, 140), "Body text.", fontname="helv", fontsize=11)
    page.insert_text((72, 760), footer, fontname="helv", fontsize=8)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_footer_correction_requires_header_corroboration(tmp_path):
    # The correction is narrow: a "Page 37-x" footer moves to Unit 38 ONLY when the same
    # page's running header names Unit 38. A page footered 37 whose header also says 37
    # must stay Unit 37 — a blanket header-trumps-footer rule could silently mask future
    # source defects, so an uncorroborated page is left exactly as printed.
    pdf = _pdf_with_footer_conflict(
        tmp_path / "agree.pdf", "Unit 37 - Passenger Carrying", "Page 37 - 1"
    )
    md = render_unit_markdown(pdf, "pilot", 37)
    assert "<!-- page: 37-1 -->" in md
    with pytest.raises(UnitStructureError):
        render_unit_markdown(pdf, "pilot", 38)  # nothing reattributed -> 38 absent


def test_write_corpus_gpc_rerun_is_byte_identical(gpc_sources, tmp_path):
    # Re-parsing the unchanged GPC corpus is byte-stable, so a git diff of corpus/md/
    # shows only real changes (subset kept small for speed).
    first = write_corpus(gpc_sources, out_root=tmp_path / "a", units=[27, 30])
    second = write_corpus(gpc_sources, out_root=tmp_path / "b", units=[27, 30])
    assert {(o.source, o.unit) for o in first.written} == {(o.source, o.unit) for o in second.written}
    for outcome in first.written:
        twin = tmp_path / "b" / outcome.source / outcome.path.name
        assert outcome.path.read_bytes() == twin.read_bytes()
