"""Live Atlas + Voyage smoke for issues #35–#36 retrieval.

Skipped unless both ``MONGODB_URI`` and ``VOYAGE_API_KEY`` are set. Expects
the Unit 5 key-messages chunk from issue #34 ingest (ingests ``corpus/md``
if that child is missing).
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from pymongo.errors import OperationFailure

from instructamate.stage3_retrieve import ParentHit, retrieve_parents, DEFAULT_P

# conftest.load_dotenv() runs at import; credentials may come from .env.
pytestmark = pytest.mark.skipif(
    not (os.environ.get("MONGODB_URI") and os.environ.get("VOYAGE_API_KEY")),
    reason="requires MONGODB_URI and VOYAGE_API_KEY",
)

SMOKE_CHILD_ID = "trainer:5:key-messages:c1"
SMOKE_PARENT_ID = "trainer:5:key-messages"
SMOKE_QUERY = (
    "aircraft as a stable platform with three axes around the centre of gravity"
)
JARGON_QUERY = "FUST pre-landing check"
MD_ROOT = Path(__file__).resolve().parents[1] / "corpus" / "md"


class _FixedQueryEmbedder:
    """Reuse one Voyage query vector across index-readiness polls."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_query(self, text: str) -> list[float]:
        del text
        return self._vector


def _ensure_corpus(collection, voyage) -> None:
    from instructamate.stage3_ingest import (
        SEARCH_INDEX_NAME,
        VECTOR_INDEX_NAME,
        ensure_search_index,
        ensure_vector_index,
        ingest_corpus,
    )

    if collection.find_one({"_id": SMOKE_CHILD_ID}) is None:
        ingest_corpus(MD_ROOT, collection=collection, embedder=voyage)
    else:
        ensure_vector_index(collection)
        ensure_search_index(collection)

    _wait_until_search_indexes_ready(
        collection, (VECTOR_INDEX_NAME, SEARCH_INDEX_NAME)
    )


def _wait_until_search_indexes_ready(
    collection, names: tuple[str, ...], *, attempts: int = 60
) -> None:
    """Poll until named Atlas Search indexes report queryable/READY."""
    pending = set(names)
    for _ in range(attempts):
        for index in collection.list_search_indexes():
            name = index.get("name")
            if name not in pending:
                continue
            if index.get("queryable") is True or index.get("status") == "READY":
                pending.discard(name)
        if not pending:
            return
        time.sleep(2)
    raise AssertionError(f"search indexes not ready: {sorted(pending)}")


def _wait_until_hits(
    query: str,
    collection,
    embedder,
    *,
    fusion: Literal["vector", "hybrid"],
    predicate: Callable[[list[ParentHit]], bool],
    attempts: int = 30,
) -> list[ParentHit]:
    """Poll retrieve_parents until predicate matches (flat retry budget)."""
    hits: list[ParentHit] = []
    for _ in range(attempts):
        try:
            hits = retrieve_parents(query, collection, embedder, fusion=fusion)
        except OperationFailure as exc:
            message = str(exc)
            if "INITIAL_SYNC" not in message and "BUILDING" not in message:
                raise
            time.sleep(2)
            continue
        if predicate(hits):
            return hits
        time.sleep(2)
    raise AssertionError(
        f"retrieve_parents({fusion!r}) never satisfied predicate; last={[h.id for h in hits]}"
    )


def test_vector_retrieve_expands_to_sensible_unit5_parent():
    from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection

    collection = chunks_collection(os.environ["MONGODB_URI"])
    voyage = VoyageEmbedder()
    _ensure_corpus(collection, voyage)

    embedder = _FixedQueryEmbedder(voyage.embed_query(SMOKE_QUERY))
    hits = _wait_until_hits(
        SMOKE_QUERY,
        collection,
        embedder,
        fusion="vector",
        predicate=lambda hs: any(hit.id == SMOKE_PARENT_ID for hit in hs),
    )

    assert len(hits) <= DEFAULT_P
    key_messages = next(hit for hit in hits if hit.id == SMOKE_PARENT_ID)
    assert key_messages.source == "trainer"
    assert key_messages.unit == "5"
    assert key_messages.content_type == "key_messages"
    assert key_messages.pages
    assert "stable platform" in key_messages.text


def test_hybrid_jargon_query_surfaces_fust_parent():
    """Lexical jargon (FUST) should land via $rankFusion full-text channel."""
    from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection

    collection = chunks_collection(os.environ["MONGODB_URI"])
    voyage = VoyageEmbedder()
    _ensure_corpus(collection, voyage)

    embedder = _FixedQueryEmbedder(voyage.embed_query(JARGON_QUERY))
    hybrid_hits = _wait_until_hits(
        JARGON_QUERY,
        collection,
        embedder,
        fusion="hybrid",
        predicate=lambda hs: any("FUST" in hit.text for hit in hs),
    )
    vector_hits = retrieve_parents(
        JARGON_QUERY, collection, embedder, fusion="vector"
    )

    assert len(hybrid_hits) <= DEFAULT_P
    fust_hits = [hit for hit in hybrid_hits if "FUST" in hit.text]
    assert fust_hits, f"got {[h.id for h in hybrid_hits]}"

    vector_fust = [hit for hit in vector_hits if "FUST" in hit.text]
    if vector_fust:
        hybrid_rank = next(i for i, hit in enumerate(hybrid_hits) if "FUST" in hit.text)
        vector_rank = next(i for i, hit in enumerate(vector_hits) if "FUST" in hit.text)
        assert hybrid_rank <= vector_rank
