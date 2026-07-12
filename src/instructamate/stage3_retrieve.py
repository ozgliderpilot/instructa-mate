"""Stage-3 retrieval: vector-only children → expand parents (issue #35).

Public seams (ADR 0005 ablation step 1):

- :func:`expand_to_unique_parents` — child hits → parent ids (best-child order)
- :func:`retrieve_parents` — embed query → ``$vectorSearch`` → expand → top P
- :class:`ParentHit` — parent chunk with citation metadata
- :data:`PRIMARY_CONTENT_TYPES` — CONTEXT.md primary roles (query-time filter)
- :data:`DEFAULT_N` / :data:`DEFAULT_P` — ADR 0005 starting widths (N=20, P=5)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from instructamate.stage3_ingest import VECTOR_INDEX_NAME

__all__ = [
    "DEFAULT_N",
    "DEFAULT_P",
    "PRIMARY_CONTENT_TYPES",
    "ParentHit",
    "QueryEmbedder",
    "expand_to_unique_parents",
    "retrieve_parents",
]

DEFAULT_N = 20
DEFAULT_P = 5

PRIMARY_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "key_messages",
        "theory",
        "briefing",
        "exercise",
        "reference_patter",
        "common_problems",
        "airmanship",
    }
)

#: Citation fields loaded when expanding child hits to parents.
_PARENT_PROJECTION = {
    "_id": 1,
    "source": 1,
    "unit": 1,
    "pages": 1,
    "heading_path": 1,
    "text": 1,
    "content_type": 1,
}


class QueryEmbedder(Protocol):
    """Thin port over Voyage (or a fake) — embed a retrieval query."""

    def embed_query(self, text: str) -> list[float]:
        """Return one embedding vector (``input_type=query``)."""


@dataclass(frozen=True)
class ParentHit:
    """A Parent Chunk returned by retrieval, with citation metadata intact."""

    id: str
    source: str
    unit: str
    pages: tuple[str, ...]
    heading_path: tuple[str, ...]
    text: str
    content_type: str


def expand_to_unique_parents(child_hits: Sequence[dict[str, Any]]) -> list[str]:
    """Return unique ``parent_id`` values in best-child-hit order."""
    seen: set[str] = set()
    parents: list[str] = []
    for hit in child_hits:
        parent_id = hit["parent_id"]
        if parent_id in seen:
            continue
        seen.add(parent_id)
        parents.append(parent_id)
    return parents


def retrieve_parents(
    query: str,
    collection: Any,
    embedder: QueryEmbedder,
    *,
    n: int = DEFAULT_N,
    p: int = DEFAULT_P,
) -> list[ParentHit]:
    """Vector-search primary children, expand/dedupe parents, return top ``p``.

    Flow (ADR 0005 ablation step 1): embed query → ``$vectorSearch`` children
    (limit ``n``, primary ``content_type`` filter) → unique parents in best-child
    order → load citation metadata → keep first ``p``.
    """
    query_vector = embedder.embed_query(query)
    child_hits = list(
        collection.aggregate(
            [
                {
                    "$vectorSearch": {
                        "index": VECTOR_INDEX_NAME,
                        "path": "embedding",
                        "queryVector": query_vector,
                        "numCandidates": max(n * 10, 50),
                        "limit": n,
                        "filter": {
                            "kind": {"$eq": "child"},
                            "content_type": {"$in": sorted(PRIMARY_CONTENT_TYPES)},
                        },
                    }
                },
                {"$project": {"_id": 1, "parent_id": 1}},
            ]
        )
    )
    parent_ids = expand_to_unique_parents(child_hits)
    if not parent_ids:
        return []

    by_id: dict[str, dict[str, Any]] = {}
    for doc in collection.find({"_id": {"$in": parent_ids}}, _PARENT_PROJECTION):
        by_id[doc["_id"]] = doc

    hits: list[ParentHit] = []
    for parent_id in parent_ids:
        if len(hits) >= p:
            break
        doc = by_id.get(parent_id)
        if doc is None:
            continue
        hits.append(
            ParentHit(
                id=doc["_id"],
                source=doc["source"],
                unit=doc["unit"],
                pages=tuple(doc["pages"]),
                heading_path=tuple(doc["heading_path"]),
                text=doc["text"],
                content_type=doc["content_type"],
            )
        )
    return hits
