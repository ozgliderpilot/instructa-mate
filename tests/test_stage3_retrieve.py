"""Stage-3 retrieval: vector / hybrid / parent rerank (#35–#37).

Unit tests use in-memory fakes only — no network. Live smoke lives in
``test_stage3_retrieve_live.py`` and skips without credentials.
"""
from __future__ import annotations

from typing import Any, Sequence

from instructamate.stage3_ingest import SEARCH_INDEX_NAME, VECTOR_INDEX_NAME
from instructamate.stage3_retrieve import (
    DEFAULT_N,
    DEFAULT_P,
    PRIMARY_CONTENT_TYPES,
    RERANK_MODEL,
    ParentHit,
    VoyageReranker,
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


class RecordingReranker:
    """Fake ParentReranker: records parent texts; returns a fixed index order."""

    def __init__(self, order: list[int]) -> None:
        self.order = order
        self.calls: list[dict[str, Any]] = []

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_k: int | None = None,
    ) -> list[int]:
        self.calls.append(
            {"query": query, "documents": list(documents), "top_k": top_k}
        )
        ranked = [i for i in self.order if i < len(documents)]
        if top_k is not None:
            ranked = ranked[:top_k]
        return ranked


def test_retrieve_parents_reranks_unique_parents_not_children():
    """Expand then rerank: multi-child same parent is one rerank slot (#37)."""
    parents = {
        "trainer:5:key-messages": _parent_doc(
            "trainer:5:key-messages",
            text="stable platform key messages",
            pages=["5-3"],
        ),
        "trainer:5:briefing": _parent_doc(
            "trainer:5:briefing",
            content_type="briefing",
            heading_path=["PRE-FLIGHT BRIEFING"],
            text="briefing body",
            pages=["5-5"],
        ),
        "trainer:16:briefing": _parent_doc(
            "trainer:16:briefing",
            unit="16",
            content_type="briefing",
            heading_path=["PRE-FLIGHT BRIEFING"],
            text="Perform pre landing check (FUST).",
            pages=["16-3"],
        ),
        "trainer:4:theory": _parent_doc(
            "trainer:4:theory",
            unit="4",
            content_type="theory",
            heading_path=["THEORY"],
            text="theory body",
            pages=["4-2"],
        ),
    }
    # Three children from key-messages would waste top slots if we reranked
    # children then expanded; expand-first collapses them to one parent.
    child_hits = [
        {"_id": "trainer:5:key-messages:c1", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:5:key-messages:c2", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:5:key-messages:c3", "parent_id": "trainer:5:key-messages"},
        {"_id": "trainer:5:briefing:c1", "parent_id": "trainer:5:briefing"},
        {"_id": "trainer:16:briefing:c1", "parent_id": "trainer:16:briefing"},
        {"_id": "trainer:4:theory:c1", "parent_id": "trainer:4:theory"},
    ]
    collection = RetrievingFakeCollection(child_hits=child_hits, parents=parents)
    # Prefer FUST briefing over the expand-order leader (key-messages).
    reranker = RecordingReranker(order=[2, 0, 1, 3])

    hits = retrieve_parents(
        "FUST pre-landing check",
        collection,
        FakeQueryEmbedder(),
        reranker=reranker,
        p=3,
    )

    assert len(reranker.calls) == 1
    call = reranker.calls[0]
    assert call["query"] == "FUST pre-landing check"
    assert call["top_k"] == 3
    assert call["documents"] == [
        "stable platform key messages",
        "briefing body",
        "Perform pre landing check (FUST).",
        "theory body",
    ]
    assert [hit.id for hit in hits] == [
        "trainer:16:briefing",
        "trainer:5:key-messages",
        "trainer:5:briefing",
    ]
    assert hits[0].pages == ("16-3",)


def test_voyage_reranker_uses_rerank_2_5_and_returns_index_order():
    class FakeVoyageClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def rerank(self, query, documents, *, model, top_k=None):
            self.calls.append(
                {
                    "query": query,
                    "documents": list(documents),
                    "model": model,
                    "top_k": top_k,
                }
            )

            class Result:
                def __init__(self, index: int, score: float) -> None:
                    self.index = index
                    self.relevance_score = score
                    self.document = documents[index]

            ranked = [Result(2, 0.9), Result(0, 0.5), Result(1, 0.1)]
            if top_k is not None:
                ranked = ranked[:top_k]

            class Reranking:
                results = ranked

            return Reranking()

    client = FakeVoyageClient()
    reranker = VoyageReranker(client=client)
    docs = ["alpha", "beta", "gamma"]

    assert reranker.rerank("q", docs, top_k=2) == [2, 0]
    assert client.calls == [
        {
            "query": "q",
            "documents": docs,
            "model": RERANK_MODEL,
            "top_k": 2,
        }
    ]
    assert RERANK_MODEL == "rerank-2.5"
    assert DEFAULT_P == 10
