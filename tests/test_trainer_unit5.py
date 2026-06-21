"""Golden tests for the stage-1 parser on Trainer Unit 5 (Primary Effects of Controls).

This is the *hard* fixture: Reference Patter (present for Elevator, Aileron AND
Rudder — contrary to issue #3's text), the two-column ruled tables (competency
ELEMENT/PERFORMANCE STANDARDS and Problem/Probable Cause) rendered in reading order
as left-column sub-headings + right-column bullets, the Trainer header vocabulary,
multi-page sections, and the inferred ``5-1`` opening-page marker.

As in ``test_pilot_unit1``, every assertion is on the emitted Markdown *string*
returned by the public seam ``render_unit_markdown`` — never on internals.
"""
from __future__ import annotations

from instructamate.stage1_parser import render_unit_markdown, write_unit_markdown


def test_frontmatter_carries_chunk_invariant_fields(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)

    assert md.startswith("---\n")
    fm = md.split("---\n", 2)[1]
    assert "source: trainer\n" in fm
    assert "unit: 5\n" in fm
    assert "unit_name: Primary Effects of Controls\n" in fm
    assert 'revision: "1.0"\n' in fm
    # The two-line title page (``Unit 5`` / ``Primary Effects of Controls``) is
    # reassembled into a single H1.
    assert "# Unit 5 — Primary Effects of Controls\n" in md


def test_page_markers_present_in_order_incl_inferred_first_page(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)

    # 5-1's footer is unreadable on the Trainer title page; it is inferred as the
    # page immediately before 5-2. 5-2..5-11 are read from the footers directly.
    markers = [f"<!-- page: 5-{p} -->" for p in range(1, 12)]
    positions = [md.find(m) for m in markers]
    assert all(pos != -1 for pos in positions), dict(zip(markers, positions))
    assert positions == sorted(positions)


def test_trainer_section_vocabulary_maps_to_content_type(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)

    assert "## AIM\n<!-- content_type: aim -->" in md
    assert "## KEY MESSAGES\n<!-- content_type: key_messages -->" in md
    assert "## COMPETENCY ELEMENTS AND PERFORMANCE STANDARDS\n<!-- content_type: competency -->" in md
    assert "## COMMON PROBLEMS\n<!-- content_type: common_problems -->" in md
    assert "## THREAT AND ERROR MANAGEMENT\n<!-- content_type: airmanship -->" in md
    # The Trainer prints these two section banners at 12pt — the same size as the
    # "Classroom Briefing" *sub*heading — so they are recognised by the header
    # dictionary, not by a font-size threshold.
    assert "## FLIGHT EXERCISES\n<!-- content_type: exercise -->" in md
    assert "## PRE-FLIGHT BRIEFING\n<!-- content_type: briefing -->" in md
    assert "## LESSON PLANNING AND CONDUCT\n<!-- content_type: briefing -->" in md


def test_trainer_bullets_and_subheadings_render(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)
    lines = md.splitlines()

    # The Trainer's top-level bullet glyph is "●" (Calibri), not the Pilot's "•".
    assert "- develop effective reference to the horizon for controlling aircraft attitude;" in lines
    assert "- demonstrate use of controls to vary pitch, bank angle and yaw." in lines
    # The intro line above the bullets is a paragraph, not a bullet.
    assert "The aim of this unit is for the student to:" in lines
    # Per-control theory headers and exercise sub-blocks are ### subheadings.
    assert "### Use of Elevator" in lines
    assert "### Hand-over/take-over Procedure" in lines


def test_bullet_with_oversized_marker_is_not_dropped_as_banner(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)
    lines = md.splitlines()

    # KEY MESSAGES has five bullets; the last one's "●" glyph happens to print at 11pt
    # — above banner size — and must not be mistaken for the running banner and culled.
    assert (
        "- We isolate the effect of each control so the student understands the "
        "relationship between the control input and aircraft response."
    ) in lines
    key_messages = md.split("## KEY MESSAGES", 1)[1].split("## LESSON PLANNING", 1)[0]
    assert key_messages.count("\n- ") == 5


def test_reference_patter_fenced_and_tagged_where_present(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)

    # Each "Suggested Patter:" block becomes a #### sub-block tagged with the
    # content_type marker. NOTE: contrary to issue #3's text, Unit 5 carries patter
    # for ALL THREE sub-exercises (Elevator, Aileron, Rudder) — confirmed against the
    # PDF and the bakeoff page map (p34/36/38). The parser renders the source as-is.
    tag = "#### Suggested Patter\n<!-- content_type: reference_patter -->"
    assert md.count(tag) == 3

    # Patter text is preserved verbatim (curly quotes/apostrophes intact).
    assert (
        '- “Look ahead at the horizon. This is the correct attitude for normal flight '
        "in this glider. See the position of the nose in relation to the horizon and "
        'hear the air sound.”'
    ) in md.splitlines()
    assert (
        "- We’re going to have a look at the Primary Effect of the Rudder. Come lightly "
        "on the rudder pedals and feel what I am doing."
    ) in md.splitlines()


def test_reference_patter_never_relabelled_as_generated(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)

    # ADR 0001 / CONTEXT.md collision: this is Reference Patter, never the app's
    # Generated Patter feature.
    assert "Generated Patter" not in md
    assert "generated-patter" not in md
    # The literal source heading "Suggested Patter" survives as the block title.
    assert "Suggested Patter" in md


def test_competency_table_renders_as_element_subheadings_with_nested_standards(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)
    lines = md.splitlines()

    # No GFM grid: each ELEMENT is a sub-heading, its PERFORMANCE STANDARDS its bullets.
    assert "| ELEMENT" not in md and "| --- |" not in md
    assert "### 1. Effects of controls – general" in lines
    assert "### 4. Effect of controls – rudder." in lines
    # The right column keeps its Describe/Demonstrate structure, and the first cell's
    # six "o" sub-bullets (one PyMuPDF block) are split, not merged into one bullet.
    assert "- Describe:" in lines
    assert "  - The need for aircraft controls." in lines
    assert "  - How to use aircraft attitude as a reference." in lines
    assert "- Demonstrate:" in lines
    # The ELEMENT / PERFORMANCE STANDARDS column-header row is dropped, not rendered.
    assert "ELEMENT PERFORMANCE STANDARDS" not in md
    assert "### ELEMENT" not in md
    # The table spans 5-2 → 5-3; later elements sit after the page marker.
    assert md.index("### 2. Effect of controls – elevator") < md.index("<!-- page: 5-3 -->")
    assert md.index("<!-- page: 5-3 -->") < md.index("### 3. Effect of controls – aileron.")


def test_problem_cause_table_renders_as_problem_subheadings(trainer_pdf):
    md = render_unit_markdown(trainer_pdf, "trainer", 5)
    lines = md.splitlines()

    # pdfplumber's grid for this table is 80% empty, so it is rebuilt from block
    # positions: each Problem (left column) is a sub-heading, each Probable Cause
    # (right column) a bullet beneath it. No GFM grid, and the column-header dropped.
    common = md.split("## COMMON PROBLEMS", 1)[1].split("## THREAT AND ERROR MANAGEMENT", 1)[0]
    assert "| --- |" not in common
    assert "Problem Probable Cause" not in md and "### Problem" not in md
    assert "### Student fixation on cockpit instruments:" in lines
    # The two probable causes of the first problem both attach to it as bullets.
    assert (
        "- Student is nervous and wants to maintain focus inside cockpit. Give student "
        "additional time with Orientation & Stability to assist with familiarisation of flight."
    ) in lines
    assert any(ln.startswith("- Cockpit instruments present a distraction.") for ln in lines)


def test_reproduces_committed_golden(trainer_pdf, trainer_unit5_golden):
    assert render_unit_markdown(trainer_pdf, "trainer", 5) == trainer_unit5_golden


def test_render_is_deterministic(trainer_pdf):
    assert render_unit_markdown(trainer_pdf, "trainer", 5) == render_unit_markdown(
        trainer_pdf, "trainer", 5
    )


def test_batch_wrapper_writes_byte_identical_file(trainer_pdf, tmp_path):
    out = write_unit_markdown(trainer_pdf, "trainer", 5, out_root=tmp_path)
    assert out == tmp_path / "trainer" / "unit-05.md"

    first = out.read_bytes()
    write_unit_markdown(trainer_pdf, "trainer", 5, out_root=tmp_path)  # re-run, unchanged PDF
    assert out.read_bytes() == first
    assert out.read_text(encoding="utf-8") == render_unit_markdown(trainer_pdf, "trainer", 5)
