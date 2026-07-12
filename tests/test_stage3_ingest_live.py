"""Live Atlas + Voyage smoke for issue #34.

Skipped unless both ``MONGODB_URI`` and ``VOYAGE_API_KEY`` are set. When
credentials are present this ingests the full committed ``corpus/md`` tree and
checks ``trainer:5:key-messages:c1`` by id and near-phrase vector search.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Pull local ``.env`` into ``os.environ`` without printing values."""
    path = _ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


_load_dotenv()

pytestmark = pytest.mark.skipif(
    not (os.environ.get("MONGODB_URI") and os.environ.get("VOYAGE_API_KEY")),
    reason="requires MONGODB_URI and VOYAGE_API_KEY",
)

SMOKE_CHUNK_ID = "trainer:5:key-messages:c1"
SMOKE_QUERY = (
    "aircraft as a stable platform with three axes around the centre of gravity"
)
MD_ROOT = _ROOT / "corpus" / "md"


def test_full_corpus_ingest_and_vector_smoke():
    from instructamate.stage3_ingest import (
        VECTOR_INDEX_NAME,
        VoyageEmbedder,
        chunks_collection,
        ingest_corpus,
    )

    collection = chunks_collection(os.environ["MONGODB_URI"])
    embedder = VoyageEmbedder(api_key=os.environ["VOYAGE_API_KEY"])

    report = ingest_corpus(MD_ROOT, collection=collection, embedder=embedder)
    # Fresh cluster: large insert; re-run: mostly no-ops. Either way the smoke
    # chunk must exist afterwards (asserted below).
    assert isinstance(report.inserted, int) and report.inserted >= 0
    assert isinstance(report.updated, int) and report.updated >= 0
    assert isinstance(report.deleted, int) and report.deleted >= 0

    doc = collection.find_one({"_id": SMOKE_CHUNK_ID})
    assert doc is not None
    assert doc["kind"] == "child"
    assert doc["parent_id"] == "trainer:5:key-messages"
    assert len(doc["embedding"]) == 1024
    assert "stable platform" in doc["text"]

    query_vector = embedder.embed_query(SMOKE_QUERY)

    # Vector index build can lag briefly after a large upsert.
    hits: list[dict] = []
    for _ in range(30):
        hits = list(
            collection.aggregate(
                [
                    {
                        "$vectorSearch": {
                            "index": VECTOR_INDEX_NAME,
                            "path": "embedding",
                            "queryVector": query_vector,
                            "numCandidates": 50,
                            "limit": 10,
                            "filter": {"kind": {"$eq": "child"}},
                        }
                    },
                    {"$project": {"_id": 1, "text": 1, "score": {"$meta": "vectorSearchScore"}}},
                ]
            )
        )
        if any(hit["_id"] == SMOKE_CHUNK_ID for hit in hits):
            break
        time.sleep(2)

    assert any(hit["_id"] == SMOKE_CHUNK_ID for hit in hits), (
        f"{SMOKE_CHUNK_ID} not in vector hits: {[h['_id'] for h in hits]}"
    )
