"""Sync Plan reconciliation (ADR 0004): pure dict-diff, no network, no git.

Synthetic record sets and ``{id: hash}`` maps through the public seam
``plan_sync`` only — insert / update / delete classification.
"""
from __future__ import annotations

from instructamate.stage2_chunker import ChunkRecord, plan_sync


def _rec(chunk_id: str, content_hash: str) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        kind="parent",
        source="trainer",
        unit="5",
        unit_name="Primary Effects of Controls",
        revision="1.0",
        content_type="key_messages",
        heading_path=["KEY MESSAGES"],
        pages=["5-2"],
        text="body",
        content_hash=content_hash,
    )


def test_fresh_index_classifies_everything_as_insert():
    records = [_rec("trainer:5:aim", "h1"), _rec("trainer:5:key-messages", "h2")]

    plan = plan_sync(records, {})

    assert plan.insert == ["trainer:5:aim", "trainer:5:key-messages"]
    assert plan.update == []
    assert plan.delete == []


def test_unchanged_corpus_yields_empty_plan():
    records = [_rec("trainer:5:aim", "h1"), _rec("trainer:5:key-messages", "h2")]

    plan = plan_sync(records, {"trainer:5:aim": "h1", "trainer:5:key-messages": "h2"})

    assert plan.insert == [] and plan.update == [] and plan.delete == []


def test_wording_edit_updates_exactly_that_chunk():
    records = [_rec("trainer:5:aim", "h1-edited"), _rec("trainer:5:key-messages", "h2")]

    plan = plan_sync(records, {"trainer:5:aim": "h1", "trainer:5:key-messages": "h2"})

    assert plan.insert == []
    assert plan.update == ["trainer:5:aim"]  # untouched IDs absent from the plan
    assert plan.delete == []


def test_removed_section_deletes_its_indexed_ids():
    records = [_rec("trainer:5:aim", "h1")]
    indexed = {
        "trainer:5:aim": "h1",
        "trainer:5:old-section": "h9",
        "trainer:5:old-section:c1": "h10",
    }

    plan = plan_sync(records, indexed)

    assert plan.insert == [] and plan.update == []
    assert plan.delete == ["trainer:5:old-section", "trainer:5:old-section:c1"]


def test_plan_sync_is_idempotent():
    records = [_rec("trainer:5:aim", "h1-edited"), _rec("trainer:5:new", "h3")]
    indexed = {"trainer:5:aim": "h1", "trainer:5:gone": "h2"}

    first = plan_sync(records, indexed)
    second = plan_sync(records, indexed)

    assert first == second
    assert (first.insert, first.update, first.delete) == (
        ["trainer:5:new"],
        ["trainer:5:aim"],
        ["trainer:5:gone"],
    )
