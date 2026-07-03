"""Golden tests for the stage-2 chunker on the committed Trainer Unit 5 Markdown.

Unlike the stage-1 suite these need no PDFs: the fixture is the verified
``corpus/md/trainer/unit-05.md`` tree committed to the repo (ADR 0002 — the
Markdown intermediate is the only chunker input).

Every assertion is on the emitted ``ChunkRecord`` values returned by the public
seam ``chunk_unit_markdown`` — never on internals.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from instructamate.stage2_chunker import chunk_unit_markdown

CORPUS_MD = Path(__file__).resolve().parent.parent / "corpus" / "md"


@pytest.fixture(scope="module")
def unit5_records():
    md = (CORPUS_MD / "trainer" / "unit-05.md").read_text(encoding="utf-8")
    return chunk_unit_markdown(md)


def _by_id(records, chunk_id):
    matches = [r for r in records if r.id == chunk_id]
    assert len(matches) == 1, f"expected exactly one record {chunk_id!r}, got {len(matches)}"
    return matches[0]


def test_leaf_section_becomes_one_parent_with_structural_id(unit5_records):
    parent = _by_id(unit5_records, "trainer:5:key-messages")

    assert parent.kind == "parent"
    assert parent.source == "trainer"
    assert parent.unit == "5"
    assert parent.unit_name == "Primary Effects of Controls"
    assert parent.revision == "1.0"
    assert parent.content_type == "key_messages"
    assert parent.heading_path == ["KEY MESSAGES"]
    # Verbatim body: first and last bullet of the section, no markers, no heading.
    assert parent.text.startswith(
        "- The aircraft is a stable platform with three axes around the C of G"
    )
    assert parent.text.rstrip().endswith(
        "relationship between the control input and aircraft response."
    )
    assert "<!--" not in parent.text
    assert "##" not in parent.text


def test_section_preamble_becomes_its_own_parent(unit5_records):
    # FLIGHT EXERCISES opens with two intro paragraphs/bullets before its first
    # ### — that preamble is a Parent under the section's own ID, so it can't be
    # mis-attributed to a sibling sub-section.
    preamble = _by_id(unit5_records, "trainer:5:flight-exercises")

    assert preamble.kind == "parent"
    assert preamble.content_type == "exercise"
    assert preamble.heading_path == ["FLIGHT EXERCISES"]
    assert preamble.text.startswith("Specific training advice for this unit is:")
    assert "Hand-over/take-over" not in preamble.text  # first ### is NOT part of it


def test_leaf_subheading_inherits_content_type_from_enclosing_section(unit5_records):
    leaf = _by_id(unit5_records, "trainer:5:lesson-planning-and-conduct:use-of-elevator")

    assert leaf.kind == "parent"
    assert leaf.content_type == "briefing"  # inherited from LESSON PLANNING AND CONDUCT
    assert leaf.heading_path == ["LESSON PLANNING AND CONDUCT", "Use of Elevator"]
    assert leaf.text.startswith("- Elevator controls rotation around the LATERAL axis")


def test_consecutive_page_markers_resolve_to_later_page(unit5_records):
    # The unit opens ``<!-- page: 5-1 --> <!-- page: 5-2 -->`` with no content
    # between (title page) — AIM starts on the page it actually sits on.
    aim = _by_id(unit5_records, "trainer:5:aim")
    assert aim.pages == ["5-2"]


def test_section_spanning_a_page_break_lists_all_its_pages(unit5_records):
    rudder = _by_id(unit5_records, "trainer:5:flight-exercises:rudder")
    assert rudder.pages == ["5-7", "5-8"]
    # The 5-9 marker right before the next heading belongs to the *next* chunk.
    patter = _by_id(unit5_records, "trainer:5:flight-exercises:rudder:suggested-patter")
    assert patter.pages == ["5-9"]


def test_container_heading_without_direct_body_emits_no_parent(unit5_records):
    # LESSON PLANNING AND CONDUCT has no prose of its own (### Classroom Briefing
    # follows immediately) — no preamble Parent for it.
    assert not any(r.id == "trainer:5:lesson-planning-and-conduct" for r in unit5_records)


def test_each_suggested_patter_block_is_its_own_reference_patter_parent(unit5_records):
    # ADR 0001: Reference Patter is structurally isolated — its own Parent with
    # content_type reference_patter, one per control exercise in Unit 5.
    patter_parents = [r for r in unit5_records if r.content_type == "reference_patter"]

    assert [r.id for r in patter_parents] == [
        "trainer:5:flight-exercises:elevator:suggested-patter",
        "trainer:5:flight-exercises:aileron:suggested-patter",
        "trainer:5:flight-exercises:rudder:suggested-patter",
    ]
    assert all(r.kind == "parent" for r in patter_parents)
    assert all(r.heading_path[-1] == "Suggested Patter" for r in patter_parents)


def test_exercise_parents_contain_no_patter_wording(unit5_records):
    # Parent expansion must never hand Generated-Patter prompts authoritative
    # patter text: no non-patter record contains the patter's wording.
    patter_lines = [
        "SEE the position of the nose below the horizon",  # elevator patter
        "SEE the wing go down as I move the stick to the right.",  # aileron patter
        "SEE the nose go to the right as I push the right pedal forward.",  # rudder patter
    ]
    for record in unit5_records:
        if record.content_type == "reference_patter":
            continue
        for line in patter_lines:
            assert line not in record.text, f"patter wording leaked into {record.id}"


def test_patter_isolation_loses_no_text(unit5_records):
    # Exercise Parent + patter Parent together retain the section's content.
    elevator = _by_id(unit5_records, "trainer:5:flight-exercises:elevator")
    patter = _by_id(unit5_records, "trainer:5:flight-exercises:elevator:suggested-patter")

    assert elevator.content_type == "exercise"
    assert elevator.text.startswith("- During the teaching of elevator")
    assert elevator.text.rstrip().endswith(
        "this demonstrates the positive stability of the aircraft in pitch."
    )
    assert patter.text.startswith(
        "- “Look ahead at the horizon. This is the correct attitude for normal flight"
    )
    assert patter.text.rstrip().endswith("- Now it’s your turn... (repeat)")


def test_parent_content_hash_is_sha256_of_its_verbatim_text(unit5_records):
    import hashlib

    parent = _by_id(unit5_records, "trainer:5:key-messages")
    assert parent.content_hash == hashlib.sha256(parent.text.encode("utf-8")).hexdigest()


def test_slugs_keep_leading_ordinals(unit5_records):
    leaf = _by_id(
        unit5_records,
        "trainer:5:competency-elements-and-performance-standards:1-effects-of-controls-general",
    )
    assert leaf.content_type == "competency"


def test_repeated_sibling_headings_get_unique_ids(unit5_records):
    # FLIGHT EXERCISES has three "Student practice and feedback" siblings
    # (one per control; the third capitalises "Feedback" but slugs identically).
    base = "trainer:5:flight-exercises:student-practice-and-feedback"
    first = _by_id(unit5_records, base)
    second = _by_id(unit5_records, f"{base}-2")
    third = _by_id(unit5_records, f"{base}-3")

    assert first.text.startswith("- The elevator control is handed to the student")
    assert second.text.startswith("- Exactly the same as for the elevator.")
    assert third.heading_path[-1] == "Student practice and Feedback"
    # And every ID in the unit is unique.
    ids = [r.id for r in unit5_records]
    assert len(ids) == len(set(ids))
