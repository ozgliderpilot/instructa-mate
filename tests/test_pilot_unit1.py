"""Golden tests for the stage-1 parser, proven on Pilot Unit 1 (Lookout Awareness).

Every assertion is on the emitted Markdown *string* returned by the public seam
``render_unit_markdown`` — never on internal page maps, the classifier, or the
normalizer.
"""
from __future__ import annotations

from instructamate.stage1_parser import render_unit_markdown, write_unit_markdown


def test_frontmatter_carries_chunk_invariant_fields(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)

    assert md.startswith("---\n")
    fm = md.split("---\n", 2)[1]
    assert "source: pilot\n" in fm
    assert "unit: 1\n" in fm
    assert "unit_name: Lookout Awareness\n" in fm
    assert 'revision: "1.0"\n' in fm


def test_page_boundary_markers_present_in_order(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)

    markers = [f"<!-- page: 1-{p} -->" for p in (1, 2, 3, 4)]
    positions = [md.find(m) for m in markers]
    assert all(pos != -1 for pos in positions), positions
    assert positions == sorted(positions)


def test_section_headings_tagged_with_content_type(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)

    assert "## WHAT THIS UNIT IS ABOUT\n<!-- content_type: aim -->" in md
    assert "## KEY MESSAGES\n<!-- content_type: key_messages -->" in md
    assert "## PILOT GUIDE FOR THIS UNIT\n<!-- content_type: theory -->" in md
    assert "## FLIGHT EXERCISES FOR THIS UNIT\n<!-- content_type: exercise -->" in md
    assert "## THINGS YOU MIGHT HAVE DIFFICULTY WITH\n<!-- content_type: common_problems -->" in md
    assert "## SELF-CHECK QUESTIONS\n<!-- content_type: self_check -->" in md


def test_subheadings_emitted_for_bold_group_labels(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)

    assert "### Lookout\n" in md
    assert "### Collision Avoidance\n" in md
    assert "### Rules of the Air.\n" in md
    assert "### Limitations of vision\n" in md
    # An inline bold fragment inside a sentence ("10 o'clock, high") is NOT a heading.
    assert "### 10 o'clock" not in md
    assert "10 o’clock, high" not in md.splitlines()


def test_bullets_subbullets_and_paragraphs(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)
    lines = md.splitlines()

    # Top-level bullet, "•" -> "- ".
    assert "- An effective lookout is the most important element of Airmanship and safety in the air." in lines
    # Nested sub-bullet, "o" -> indented "- ".
    assert "  - What has happened recently?" in lines
    # A wrapped bullet is joined into a single line (no mid-sentence break).
    assert (
        "- You must learn and apply the basic Rules of the Air (see more in GPC Unit 23 – "
        "this unit will be covered later but the basic rules of the air are listed in that unit)"
    ) in lines
    # Non-bullet body lines render as paragraphs.
    assert "To develop the primacy of effective lookout," in lines
    assert "Sitting in the front seat of a glider:" in lines


def test_running_header_and_footer_banner_stripped(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)
    lines = md.splitlines()

    assert "Gliding Australia Training Manual" not in md
    assert "1 March 2022" not in md
    assert "Revision 1.0" not in md  # the revision survives in frontmatter, not as a line
    assert "Pilot Guide" not in lines  # banner line; the "PILOT GUIDE..." heading differs
    assert "Unit 1 Lookout Awareness" not in lines  # running title; H1 differs


def test_verbatim_text_preserved_and_normalized(pilot_pdf):
    md = render_unit_markdown(pilot_pdf, "pilot", 1)

    assert "�" not in md  # no residual replacement chars
    # Curly quotes / apostrophes / en-dashes are part of the source — kept verbatim.
    assert "“Alerted See and Avoid”" in md
    assert "Unit 4 – Orientation and Stability" in md
    assert "9 O’clock Low?" in md
    # PyMuPDF reads the Pilot prose clean — no ligature/word-split damage.
    for word in ("effective", "flight", "safety", "aircraft"):
        assert word in md
    for damage in ("ef f ective", "f light", "saf ety", "aircraf t"):
        assert damage not in md


def test_reproduces_committed_golden(pilot_pdf, pilot_unit1_golden):
    assert render_unit_markdown(pilot_pdf, "pilot", 1) == pilot_unit1_golden


def test_render_is_deterministic(pilot_pdf):
    assert render_unit_markdown(pilot_pdf, "pilot", 1) == render_unit_markdown(pilot_pdf, "pilot", 1)


def test_batch_wrapper_writes_byte_identical_file(pilot_pdf, tmp_path):
    out = write_unit_markdown(pilot_pdf, "pilot", 1, out_root=tmp_path)
    assert out == tmp_path / "pilot" / "unit-01.md"

    first = out.read_bytes()
    write_unit_markdown(pilot_pdf, "pilot", 1, out_root=tmp_path)  # re-run on unchanged PDF
    assert out.read_bytes() == first
    assert out.read_text(encoding="utf-8") == render_unit_markdown(pilot_pdf, "pilot", 1)
