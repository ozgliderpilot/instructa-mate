"""Child Chunks: the search units emitted under each Parent (issue #30).

Golden assertions on the committed Trainer Unit 5 Markdown plus synthetic MD
for the size-split and hash-sensitivity rules — all through the public seam.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from instructamate.stage2_chunker import chunk_unit_markdown

CORPUS_MD = Path(__file__).resolve().parent.parent / "corpus" / "md"


@pytest.fixture(scope="module")
def unit5_records():
    md = (CORPUS_MD / "trainer" / "unit-05.md").read_text(encoding="utf-8")
    return chunk_unit_markdown(md)


def _children_of(records, parent_id):
    return [r for r in records if r.kind == "child" and r.parent_id == parent_id]


def test_intro_line_and_its_bullet_list_are_one_child(unit5_records):
    # AIM is one intro line ending in ":" plus its bullet list — a single
    # coherent Child whose text equals the (tiny) Parent's.
    [child] = _children_of(unit5_records, "trainer:5:aim")
    parent = next(r for r in unit5_records if r.id == "trainer:5:aim")

    assert child.id == "trainer:5:aim:c1"
    assert child.kind == "child"
    assert child.text == parent.text
    assert child.content_type == "aim"
    assert child.embedding_text == (
        "Trainer Guide, Unit 5 — Primary Effects of Controls > AIM [aim]\n\n" + child.text
    )
    assert child.content_hash == hashlib.sha256(
        child.embedding_text.encode("utf-8")
    ).hexdigest()


def test_paragraphs_and_bullet_list_split_into_separate_children(unit5_records):
    # FLIGHT EXERCISES > Rudder: six prose paragraphs then one bullet list
    # (no intro line ending in ":") — seven Children, in document order.
    children = _children_of(unit5_records, "trainer:5:flight-exercises:rudder")

    assert [c.id for c in children] == [
        f"trainer:5:flight-exercises:rudder:c{n}" for n in range(1, 8)
    ]
    assert children[0].text == (
        "A demonstration is given which shows that the glider is yawed (not turned) "
        "by use of rudder."
    )
    assert children[3].text.startswith("The purpose of the exercise")
    assert children[6].text.startswith("- Invite the student")
    assert children[6].text.rstrip().endswith(
        "This demonstrates the positive stability of the aircraft in yaw."
    )


def test_children_carry_the_pages_their_own_text_spans(unit5_records):
    rudder = _children_of(unit5_records, "trainer:5:flight-exercises:rudder")
    assert rudder[0].pages == ["5-7"]  # before the 5-8 marker
    assert rudder[3].pages == ["5-8"]  # "The purpose..." sits after it

    # PRE-FLIGHT BRIEFING is one bullet list crossing the 5-4/5-5 page break.
    [briefing] = _children_of(unit5_records, "trainer:5:pre-flight-briefing")
    assert briefing.pages == ["5-4", "5-5"]


def test_nested_sub_bullets_stay_with_their_list(unit5_records):
    [briefing] = _children_of(unit5_records, "trainer:5:pre-flight-briefing")
    # The indented sub-bullet stays attached inside the single list Child.
    assert "  - Elevator - forward pressure, nose pitch down" in briefing.text


def test_parents_are_never_embed_eligible_and_all_children_are(unit5_records):
    for record in unit5_records:
        if record.kind == "parent":
            assert record.embedding_text is None
            assert record.parent_id is None
        else:
            assert record.embedding_text is not None  # secondaries included
            assert record.parent_id is not None


def _unit(body: str) -> str:
    return (
        "---\nsource: trainer\nunit: 5\nunit_name: Primary Effects of Controls\n"
        'revision: "1.0"\n---\n\n# Unit 5 — Primary Effects of Controls\n\n' + body
    )


def test_wording_edit_keeps_id_and_changes_only_the_hash():
    a = chunk_unit_markdown(_unit("## AIM\n<!-- content_type: aim -->\n\nOld wording.\n"))
    b = chunk_unit_markdown(_unit("## AIM\n<!-- content_type: aim -->\n\nNew wording.\n"))

    child_a = next(r for r in a if r.kind == "child")
    child_b = next(r for r in b if r.kind == "child")
    assert child_a.id == child_b.id == "trainer:5:aim:c1"
    assert child_a.content_hash != child_b.content_hash


def test_content_type_remap_flips_the_hash_even_with_unchanged_text():
    a = chunk_unit_markdown(_unit("## AIM\n<!-- content_type: aim -->\n\nSame text.\n"))
    b = chunk_unit_markdown(_unit("## AIM\n<!-- content_type: admin -->\n\nSame text.\n"))

    child_a = next(r for r in a if r.kind == "child")
    child_b = next(r for r in b if r.kind == "child")
    assert child_a.id == child_b.id
    assert child_a.text == child_b.text  # stored text is verbatim either way
    assert child_a.content_hash != child_b.content_hash  # prefix is in the hash


def test_oversized_list_splits_only_at_top_level_bullet_boundaries():
    # 60 top-level bullets of ~14 tokens each (~840 tokens), a nested
    # sub-bullet under each odd one — must split into >1 Child, each within
    # budget, with sub-bullets still attached to their parent bullet.
    lines = []
    for n in range(60):
        lines.append(f"- Top level bullet number {n} with enough words to add up the token count.")
        if n % 2 == 1:
            lines.append(f"  - Nested detail for bullet {n} that must stay with its parent.")
    md = _unit("## AIM\n<!-- content_type: aim -->\n\n" + "\n".join(lines) + "\n")

    children = [r for r in chunk_unit_markdown(md) if r.kind == "child"]

    assert len(children) > 1
    for child in children:
        assert len(child.text.split()) <= 500
        for line in child.text.splitlines():
            if line.startswith("  - Nested detail for bullet"):
                n = int(line.split("bullet ")[1].split()[0])
                assert f"- Top level bullet number {n} " in child.text
    # Nothing lost, order kept.
    rejoined = "\n".join(c.text for c in children)
    assert rejoined == "\n".join(lines)
