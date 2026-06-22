"""Reading-order and numbered-list fidelity (Pilot Solo + GPC).

Two structural defects this slice fixes, both proven on observable Markdown:

* **Reading order.** PyMuPDF's block order is not strictly geometric — a section box lower
  on the page can be emitted before the prose above it (Pilot Unit 11's KEY MESSAGES box,
  Unit 7's "Feel" sub-section). The parser now re-orders blocks top-to-bottom, so rendered
  section order matches the page. A two-column table is exempt (it would be split/shuffled),
  which the committed competency-table goldens already lock.

* **Numbered lists.** A bare "1." / "2." token printed as its own line is an ordered-list
  marker — its items must each land on their own line, not collapse onto one. But a bare
  number followed by a *bold* sub-heading is a numbered heading ("1." + "Awareness" →
  "### 1. Awareness"), which must be preserved, not split into a stray "1." + "### Awareness".

As elsewhere, assertions are on the emitted Markdown, never on parser internals.
"""
from __future__ import annotations

from instructamate.stage1_parser import render_unit_markdown


def _line_index(md: str, prefix: str) -> int:
    """Index of the first rendered line starting with *prefix* (-1 if absent)."""
    for i, line in enumerate(md.splitlines()):
        if line.startswith(prefix):
            return i
    return -1


def test_key_messages_section_lands_in_page_order(pilot_pdf):
    # Pilot Unit 11 prints KEY MESSAGES in a box at y≈320 — below WHAT THIS UNIT IS ABOUT
    # and the pre-requisites, above PILOT GUIDE — but PyMuPDF emits that box first. The
    # rendered section order must follow the page: pre-requisites, then KEY MESSAGES, then
    # the Pilot Guide.
    md = render_unit_markdown(pilot_pdf, "pilot", 11)

    about = _line_index(md, "## WHAT THIS UNIT IS ABOUT")
    prereq = _line_index(md, "## WHAT ARE THE PRE-REQUISITES FOR THIS UNIT?")
    key = _line_index(md, "## KEY MESSAGES")
    guide = _line_index(md, "## PILOT GUIDE FOR THIS UNIT")

    assert -1 not in (about, prereq, key, guide)
    assert about < prereq < key < guide


def test_subsection_lands_in_page_order(pilot_pdf):
    # Pilot Unit 7's "How do you achieve "Feel" when flying" sub-section sits above "Flying
    # Straight" on the page but PyMuPDF emits it after. Geometric ordering must restore it.
    md = render_unit_markdown(pilot_pdf, "pilot", 7)

    feel = _line_index(md, "### How do you achieve")
    straight = _line_index(md, "### Flying Straight")

    assert -1 not in (feel, straight)
    assert feel < straight


def test_self_check_numbers_each_on_their_own_line(pilot_pdf):
    # Pilot Unit 11's SELF-CHECK numbers print as separate "1." / "2." tokens that PyMuPDF
    # groups into one block; they must render as an ordered list, one item per line, not
    # collapsed onto a single paragraph line.
    md = render_unit_markdown(pilot_pdf, "pilot", 11)

    assert _line_index(md, "1. What are the pathways available") != -1
    assert _line_index(md, "2. What is the most common way") != -1
    assert _line_index(md, "3. What are two standard techniques") != -1


def test_numbered_list_with_prose_body_is_an_ordered_item(pilot_pdf):
    # Pilot Unit 9's scanning technique is a numbered list whose bodies are full prose
    # ("1. FULL SCAN: …"). The bare "1." is an ordered marker joined to its body, not a
    # bogus "### 1." heading with the text orphaned into a following paragraph.
    md = render_unit_markdown(pilot_pdf, "pilot", 9)

    assert _line_index(md, "1. FULL SCAN:") != -1
    assert _line_index(md, "2. CRUISING SCAN:") != -1
    assert "### 1." not in md


def test_numbered_bold_subheading_is_preserved_not_split(pilot_gpc_pdf):
    # Pilot Unit 32's principles print as bold numbered headings — "1." then a bold
    # "Awareness". The number belongs to the heading ("### 1. Awareness"); it must not be
    # mistaken for an ordered-list marker and split into a stray "1." + "### Awareness".
    md = render_unit_markdown(pilot_gpc_pdf, "pilot", 32)

    assert "### 1. Awareness" in md
    assert "### 2. Separation" in md
    assert "### 3. Predictability" in md
    assert "### Awareness" not in md.replace("### 1. Awareness", "")


def test_diagram_labels_on_page_5_3_are_suppressed(pilot_pdf):
    # Pilot Unit 5 page 5-3 has a "control movement → surface → force → rotation" figure
    # whose labels are scattered body-sized text blocks (each its own block, lines like
    # "Control" + "Movement"). They pass every font filter and read out as garbled prose,
    # so they are dropped by the curated _FIGURE_LABELS set. Each fragment must be gone as a
    # standalone block, while the real bullets framing the diagram survive.
    md = render_unit_markdown(pilot_pdf, "pilot", 5)
    lines = md.splitlines()

    for fragment in (
        "Control Movement", "Control Surface Position", "Force on an aircraft axis of",
        "aircraft around an", "Rotation of", "creates", "changes", "resulting",
    ):
        assert fragment not in lines, f"diagram label leaked as its own line: {fragment!r}"

    # The bullets on either side of the figure are real content and must remain.
    assert "  - The rudder is connected to the rudder pedals." in lines
    assert any(l.startswith("- The rotation of the aircraft in each axis") for l in lines)
