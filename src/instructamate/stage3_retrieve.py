"""Stage-3 retrieval: vector / hybrid children → expand parents (issues #35–#36).

Public seams (ADR 0005 ablation steps 1–2):

- :func:`expand_to_unique_parents` — child hits → parent ids (best-child order)
- :func:`retrieve_parents` — embed query → children (vector or ``$rankFusion``)
  → expand → top P
- :class:`ParentHit` — parent chunk with citation metadata
- :data:`PRIMARY_CONTENT_TYPES` — CONTEXT.md primary roles (query-time filter)
- :data:`DEFAULT_N` / :data:`DEFAULT_P` — ADR 0005 starting widths (N=20, P=5)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from instructamate.stage2_chunker import PRIMARY_CONTENT_TYPES
from instructamate.stage3_ingest import SEARCH_INDEX_NAME, VECTOR_INDEX_NAME

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

#: Stable ``$in`` list for the vector-search content_type filter.
_PRIMARY_CONTENT_TYPE_FILTER = sorted(PRIMARY_CONTENT_TYPES)

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
    return list(dict.fromkeys(hit["parent_id"] for hit in child_hits))


def retrieve_parents(
    query: str,
    collection: Any,
    embedder: QueryEmbedder,
    *,
    n: int = DEFAULT_N,
    p: int = DEFAULT_P,
    fusion: Literal["vector", "hybrid"] = "vector",
) -> list[ParentHit]:
    """Search primary children, expand/dedupe parents, return top ``p``.

    ``fusion="vector"`` (ablation step 1): embed → ``$vectorSearch`` (limit ``n``).

    ``fusion="hybrid"`` (ablation step 2): embed → server-side ``$rankFusion`` of
    vector + full-text child rankings (each channel ``n``, keep ``n`` fused) →
    same expand + P delivery.
    """
    query_vector = embedder.embed_query(query)
    if fusion == "hybrid":
        pipeline = _hybrid_child_pipeline(query, query_vector, n)
    else:
        pipeline = _vector_child_pipeline(query_vector, n)

    child_hits = list(collection.aggregate(pipeline))
    parent_ids = expand_to_unique_parents(child_hits)
    if not parent_ids:
        return []

    by_id = {
        doc["_id"]: doc
        for doc in collection.find({"_id": {"$in": parent_ids}}, _PARENT_PROJECTION)
    }

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


def _vector_child_pipeline(query_vector: list[float], n: int) -> list[dict[str, Any]]:
    return [
        _vector_search_stage(query_vector, n),
        {"$project": {"_id": 1, "parent_id": 1}},
    ]


def _hybrid_child_pipeline(
    query: str,
    query_vector: list[float],
    n: int,
) -> list[dict[str, Any]]:
    return [
        {
            "$rankFusion": {
                "input": {
                    "pipelines": {
                        "vector": [_vector_search_stage(query_vector, n)],
                        "fullText": [
                            {
                                "$search": {
                                    "index": SEARCH_INDEX_NAME,
                                    "compound": {
                                        "must": [
                                            {
                                                "text": {
                                                    "query": query,
                                                    "path": "text",
                                                }
                                            }
                                        ],
                                        "filter": [
                                            {
                                                "equals": {
                                                    "path": "kind",
                                                    "value": "child",
                                                }
                                            },
                                            {
                                                "in": {
                                                    "path": "content_type",
                                                    "value": _PRIMARY_CONTENT_TYPE_FILTER,
                                                }
                                            },
                                        ],
                                    },
                                }
                            },
                            {"$limit": n},
                        ],
                    }
                }
            }
        },
        {"$limit": n},
        {"$project": {"_id": 1, "parent_id": 1}},
    ]


def _vector_search_stage(query_vector: list[float], n: int) -> dict[str, Any]:
    return {
        "$vectorSearch": {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": query_vector,
            "numCandidates": max(n * 10, 50),
            "limit": n,
            "filter": {
                "kind": {"$eq": "child"},
                "content_type": {"$in": _PRIMARY_CONTENT_TYPE_FILTER},
            },
        }
    }
