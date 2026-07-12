"""Live Atlas + Voyage smoke for issue #35 vector→expand retrieval.

Skipped unless both ``MONGODB_URI`` and ``VOYAGE_API_KEY`` are set. Expects
the Unit 5 key-messages chunk from issue #34 ingest (ingests ``corpus/md``
if that child is missing).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

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
MD_ROOT = Path(__file__).resolve().parents[1] / "corpus" / "md"


class _FixedQueryEmbedder:
    """Reuse one Voyage query vector across index-readiness polls."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_query(self, text: str) -> list[float]:
        _ = text
        return self._vector


def test_vector_retrieve_expands_to_sensible_unit5_parent():
    from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection, ingest_corpus
    from instructamate.stage3_retrieve import retrieve_parents

    collection = chunks_collection(os.environ["MONGODB_URI"])
    voyage = VoyageEmbedder()

    if collection.find_one({"_id": SMOKE_CHILD_ID}) is None:
        ingest_corpus(MD_ROOT, collection=collection, embedder=voyage)

    embedder = _FixedQueryEmbedder(voyage.embed_query(SMOKE_QUERY))
    hits: list = []
    for _ in range(30):
        hits = retrieve_parents(SMOKE_QUERY, collection, embedder)
        if any(hit.id == SMOKE_PARENT_ID for hit in hits):
            break
        time.sleep(2)

    assert hits, "retrieve_parents returned no parents"
    assert len(hits) <= 5
    assert any(hit.id == SMOKE_PARENT_ID for hit in hits), (
        f"{SMOKE_PARENT_ID} not in parent hits: {[h.id for h in hits]}"
    )
    key_messages = next(hit for hit in hits if hit.id == SMOKE_PARENT_ID)
    assert key_messages.source == "trainer"
    assert key_messages.unit == "5"
    assert key_messages.content_type == "key_messages"
    assert key_messages.pages
    assert "stable platform" in key_messages.text
