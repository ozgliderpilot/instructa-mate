"""Live Stage-5 ablation smoke (#40).

Skips without Atlas/Voyage credentials. Uses a tiny golden subset and
recall-only scoring so Anthropic/Langfuse are optional.
"""
from __future__ import annotations

import os

import pytest

from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection
from instructamate.stage3_retrieve import VoyageReranker
from instructamate.stage5_eval import load_golden_set, run_ablation_curve

pytestmark = pytest.mark.skipif(
    not (os.environ.get("MONGODB_URI") and os.environ.get("VOYAGE_API_KEY")),
    reason="requires MONGODB_URI and VOYAGE_API_KEY",
)


def test_ablation_curve_recall_on_tiny_golden_subset() -> None:
    items = [item for item in load_golden_set() if item.citations][:3]
    assert items, "golden set must include answer items with citations"

    points = run_ablation_curve(
        items,
        chunks_collection(os.environ["MONGODB_URI"]),
        embedder=VoyageEmbedder(),
        reranker=VoyageReranker(),
        k=10,
    )

    assert [p.step for p in points] == ["vector", "hybrid", "hybrid_rerank"]
    assert all(0.0 <= p.recall_at_k <= 1.0 for p in points)
    assert all(p.refusal_accuracy is None for p in points)
    assert all(p.faithfulness is None for p in points)
