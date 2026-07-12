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

_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    path = _ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


_load_dotenv()

pytestmark = pytest.mark.skipif(
    not (os.environ.get("MONGODB_URI") and os.environ.get("VOYAGE_API_KEY")),
    reason="requires MONGODB_URI and VOYAGE_API_KEY",
)

SMOKE_CHILD_ID = "trainer:5:key-messages:c1"
SMOKE_PARENT_ID = "trainer:5:key-messages"
SMOKE_QUERY = (
    "aircraft as a stable platform with three axes around the centre of gravity"
)
MD_ROOT = _ROOT / "corpus" / "md"


def test_vector_retrieve_expands_to_sensible_unit5_parent():
    from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection, ingest_corpus
    from instructamate.stage3_retrieve import retrieve_parents

    collection = chunks_collection(os.environ["MONGODB_URI"])
    embedder = VoyageEmbedder()

    if collection.find_one({"_id": SMOKE_CHILD_ID}) is None:
        ingest_corpus(MD_ROOT, collection=collection, embedder=embedder)

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
