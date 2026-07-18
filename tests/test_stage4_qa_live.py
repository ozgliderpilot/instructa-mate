"""Live Atlas + Voyage + Anthropic smoke for refuse-or-cite Q&A (#38).

Skipped unless ``MONGODB_URI``, ``VOYAGE_API_KEY``, and ``ANTHROPIC_API_KEY``
are set. Reuses the Unit 5 ingest path from stage-3 live smoke.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection
from instructamate.stage3_retrieve import VoyageReranker
from instructamate.stage4_qa import AnthropicCompleter, CANONICAL_REFUSAL, answer_question

# conftest.load_dotenv() runs at import; credentials may come from .env.
pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("MONGODB_URI")
        and os.environ.get("VOYAGE_API_KEY")
        and os.environ.get("ANTHROPIC_API_KEY")
    ),
    reason="requires MONGODB_URI, VOYAGE_API_KEY, and ANTHROPIC_API_KEY",
)

MD_ROOT = Path(__file__).resolve().parents[1] / "corpus" / "md"
SMOKE_CHILD_ID = "trainer:5:key-messages:c1"


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


def test_answer_question_grounds_in_corpus_topic():
    collection = chunks_collection(os.environ["MONGODB_URI"])
    voyage = VoyageEmbedder()
    _ensure_corpus(collection, voyage)

    result = answer_question(
        "What is your primary attitude reference for controlling pitch?",
        collection,
        embedder=voyage,
        completer=AnthropicCompleter(),
        reranker=VoyageReranker(),
    )

    assert result.grounded is True
    assert result.answer.strip()
    assert result.citations
    for cite in result.citations:
        assert cite.source in {"pilot", "trainer"}
        assert cite.page >= 1


def test_answer_question_refuses_absent_topic():
    collection = chunks_collection(os.environ["MONGODB_URI"])
    voyage = VoyageEmbedder()
    _ensure_corpus(collection, voyage)

    result = answer_question(
        "What is the MacCready ring setting for final glide in an ASK-21?",
        collection,
        embedder=voyage,
        completer=AnthropicCompleter(),
        reranker=VoyageReranker(),
    )

    assert result.grounded is False
    assert result.answer == CANONICAL_REFUSAL
    assert result.citations == ()
