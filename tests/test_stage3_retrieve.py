"""Stage-3 retrieval: vector-only (#35) and hybrid $rankFusion (#36).

Unit tests use in-memory fakes only — no network. Live smoke lives in
``test_stage3_retrieve_live.py`` and skips without credentials.
"""
from __future__ import annotations

from typing import Any, Sequence

from instructamate.stage3_ingest import SEARCH_INDEX_NAME, VECTOR_INDEX_NAME
from instructamate.stage3_retrieve import (
    DEFAULT_N,
    PRIMARY_CONTENT_TYPES,
    ParentHit,
    expand_to_unique_parents,
    retrieve_parents,
)


def test_expand_to_unique_parents_preserves_best_child_order_and_dedupes():
    child_hits = [
        {"_id": "trainer:5:key-messages:c1", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:5:briefing:c2", "parent_id": "trainer:5:briefing"},
        {"_id": "trainer:5:key-messages:c2", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:4:theory:c1", "parent_id": "trainer:4:theory"},
    ]

    assert expand_to_unique_parents(child_hits) == [
        "trainer:5:key-messages",
        "trainer:5:briefing",
        "trainer:4:theory",
    ]


class FakeQueryEmbedder:
    def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1, 0.2, 0.3]


class RetrievingFakeCollection:
    """Captures ``$vectorSearch`` and returns seeded child/parent docs."""

    def __init__(
        self,
        *,
        child_hits: list[dict[str, Any]],
        parents: dict[str, dict[str, Any]],
    ) -> None:
        self.child_hits = child_hits
        self.parents = parents
        self.last_pipeline: list[dict[str, Any]] | None = None

    def aggregate(self, pipeline: Sequence[dict[str, Any]]):
        self.last_pipeline = list(pipeline)
        return list(self.child_hits)

    def find(self, filter: dict[str, Any], projection: dict | None = None):
        # Seeded parents are already slim; projection is accepted for pymongo shape.
        _ = projection
        for parent_id in filter.get("_id", {}).get("$in", []):
            doc = self.parents.get(parent_id)
            if doc is not None:
                yield dict(doc)


def _parent_doc(
    parent_id: str,
    *,
    source: str = "trainer",
    unit: str = "5",
    content_type: str = "key_messages",
    pages: list[str] | None = None,
    heading_path: list[str] | None = None,
    text: str = "parent body",
) -> dict[str, Any]:
    return {
        "_id": parent_id,
        "kind": "parent",
        "source": source,
        "unit": unit,
        "content_type": content_type,
        "heading_path": heading_path or ["KEY MESSAGES"],
        "pages": pages or ["5-3"],
        "text": text,
    }


def test_retrieve_parents_expands_dedupes_and_returns_citation_metadata():
    parents = {
        "trainer:5:key-messages": _parent_doc(
            "trainer:5:key-messages",
            text="stable platform key messages",
            pages=["5-3", "5-4"],
        ),
        "trainer:5:briefing": _parent_doc(
            "trainer:5:briefing",
            content_type="briefing",
            heading_path=["PRE-FLIGHT BRIEFING"],
            text="briefing body",
            pages=["5-5"],
        ),
        "trainer:5:exercise": _parent_doc(
            "trainer:5:exercise",
            content_type="exercise",
            heading_path=["FLIGHT EXERCISES"],
            text="exercise body",
            pages=["5-8"],
        ),
    }
    child_hits = [
        {"_id": "trainer:5:key-messages:c1", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:5:briefing:c1", "parent_id": "trainer:5:briefing"},
        {"_id": "trainer:5:key-messages:c2", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:5:exercise:c1", "parent_id": "trainer:5:exercise"},
    ]
    collection = RetrievingFakeCollection(child_hits=child_hits, parents=parents)

    hits = retrieve_parents(
        "stable platform three axes",
        collection,
        FakeQueryEmbedder(),
        p=2,
    )

    assert hits == [
        ParentHit(
            id="trainer:5:key-messages",
            source="trainer",
            unit="5",
            pages=("5-3", "5-4"),
            heading_path=("KEY MESSAGES",),
            text="stable platform key messages",
            content_type="key_messages",
        ),
        ParentHit(
            id="trainer:5:briefing",
            source="trainer",
            unit="5",
            pages=("5-5",),
            heading_path=("PRE-FLIGHT BRIEFING",),
            text="briefing body",
            content_type="briefing",
        ),
    ]

    stage = collection.last_pipeline[0]["$vectorSearch"]
    assert stage["limit"] == DEFAULT_N
    assert stage["filter"]["kind"] == {"$eq": "child"}
    assert set(stage["filter"]["content_type"]["$in"]) == set(PRIMARY_CONTENT_TYPES)
    assert stage["queryVector"] == [0.1, 0.2, 0.3]


def test_retrieve_parents_hybrid_uses_rank_fusion_then_expand():
    parents = {
        "trainer:16:briefing": _parent_doc(
            "trainer:16:briefing",
            unit="16",
            content_type="briefing",
            heading_path=["PRE-FLIGHT BRIEFING"],
            text="Perform pre landing check (FUST).",
            pages=["16-3"],
        ),
        "trainer:5:key-messages": _parent_doc(
            "trainer:5:key-messages",
            text="stable platform key messages",
            pages=["5-3"],
        ),
    }
    child_hits = [
        {"_id": "trainer:16:briefing:c1", "parent_id": "trainer:16:briefing"},
        {"_id": "trainer:5:key-messages:c1", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:16:briefing:c2", "parent_id": "trainer:16:briefing"},
    ]
    collection = RetrievingFakeCollection(child_hits=child_hits, parents=parents)

    hits = retrieve_parents(
        "FUST pre-landing check",
        collection,
        FakeQueryEmbedder(),
        fusion="hybrid",
        p=2,
    )

    assert [hit.id for hit in hits] == [
        "trainer:16:briefing",
        "trainer:5:key-messages",
    ]
    assert hits[0].pages == ("16-3",)
    assert "FUST" in hits[0].text

    fusion = collection.last_pipeline[0]["$rankFusion"]
    pipelines = fusion["input"]["pipelines"]
    assert set(pipelines) == {"vector", "fullText"}

    vector_stage = pipelines["vector"][0]["$vectorSearch"]
    assert vector_stage["index"] == VECTOR_INDEX_NAME
    assert vector_stage["limit"] == DEFAULT_N
    assert vector_stage["filter"]["kind"] == {"$eq": "child"}
    assert set(vector_stage["filter"]["content_type"]["$in"]) == set(PRIMARY_CONTENT_TYPES)
    assert vector_stage["queryVector"] == [0.1, 0.2, 0.3]

    search_stage = pipelines["fullText"][0]["$search"]
    assert search_stage["index"] == SEARCH_INDEX_NAME
    assert search_stage["compound"]["must"][0]["text"]["query"] == "FUST pre-landing check"
    assert search_stage["compound"]["must"][0]["text"]["path"] == "text"
    filters = search_stage["compound"]["filter"]
    assert {"equals": {"path": "kind", "value": "child"}} in filters
    content_type_filter = next(f for f in filters if "in" in f)
    assert set(content_type_filter["in"]["value"]) == set(PRIMARY_CONTENT_TYPES)
    assert pipelines["fullText"][1] == {"$limit": DEFAULT_N}

    assert collection.last_pipeline[1] == {"$limit": DEFAULT_N}
    assert collection.last_pipeline[2] == {"$project": {"_id": 1, "parent_id": 1}}
