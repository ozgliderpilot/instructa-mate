"""Stage-5 eval harness + ablation scoring (#40).

Unit tests use in-memory fakes only — no network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from instructamate.stage3_retrieve import ParentHit
from instructamate.stage4_qa import CANONICAL_REFUSAL, Citation, QaResult
from instructamate.stage5_eval import (
    AblationPoint,
    CompleterJudge,
    CorrectnessVerdict,
    EvalItem,
    ItemEvalResult,
    JudgeVerdict,
    LangfuseTracer,
    NullTracer,
    judge_answer_correctness,
    judge_citation_faithfulness,
    judge_groundedness,
    load_golden_set,
    recall_at_k,
    retrieve_for_step,
    run_ablation_curve,
    score_refusal,
)


def _parent(
    *,
    id: str = "pilot:1:key-messages",
    source: str = "pilot",
    unit: str = "1",
    pages: tuple[str, ...] = ("1-2",),
    text: str = "body",
) -> ParentHit:
    return ParentHit(
        id=id,
        source=source,
        unit=unit,
        pages=pages,
        heading_path=("KEY MESSAGES",),
        text=text,
        content_type="key_messages",
    )


def test_recall_at_k_hits_when_gold_page_in_top_k_parents() -> None:
    retrieved = [
        _parent(id="pilot:1:a", pages=("1-2",)),
        _parent(id="pilot:9:b", source="pilot", unit="9", pages=("9-6",)),
    ]
    gold = [Citation(source="pilot", unit="1", page=2)]

    assert recall_at_k(retrieved, gold, k=1) == 1.0


def test_recall_at_k_misses_when_gold_only_beyond_k() -> None:
    retrieved = [
        _parent(id="trainer:5:a", source="trainer", unit="5", pages=("5-1",)),
        _parent(id="pilot:1:a", pages=("1-2",)),
    ]
    gold = [Citation(source="pilot", unit="1", page=2)]

    assert recall_at_k(retrieved, gold, k=1) == 0.0
    assert recall_at_k(retrieved, gold, k=2) == 1.0


def test_recall_at_k_misses_wrong_source_same_page() -> None:
    retrieved = [_parent(source="trainer", unit="1", pages=("1-2",))]
    gold = [Citation(source="pilot", unit="1", page=2)]

    assert recall_at_k(retrieved, gold, k=1) == 0.0


def test_recall_at_k_empty_gold_is_zero() -> None:
    assert recall_at_k([_parent()], [], k=1) == 0.0


def test_score_refusal_detects_structured_refusal() -> None:
    refused = QaResult(grounded=False, answer=CANONICAL_REFUSAL, citations=())
    answered = QaResult(
        grounded=True,
        answer="The natural horizon.",
        citations=(Citation(source="pilot", unit="5", page=2),),
    )

    assert score_refusal(refused, "refuse") is True
    assert score_refusal(answered, "refuse") is False
    assert score_refusal(answered, "answer") is True
    assert score_refusal(refused, "answer") is False


def test_load_golden_set_normalizes_units_and_citations(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "items": [
            {
                "id": "g-1",
                "question": "Where is 9 o'clock?",
                "expected_behavior": "answer",
                "expected_answer": "Left.",
                "citations": [{"source": "pilot", "unit": 1, "page": 2}],
                "category": "self_check",
                "content_type": "self_check",
                "difficulty": "easy",
                "verified_absent_terms": [],
            },
            {
                "id": "g-2",
                "question": "What is ASK-21 VNE?",
                "expected_behavior": "refuse",
                "expected_answer": CANONICAL_REFUSAL,
                "citations": [],
                "category": "type_specific",
                "content_type": "self_check",
                "difficulty": "easy",
                "verified_absent_terms": ["VNE"],
            },
        ],
    }
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    items = load_golden_set(path)

    assert len(items) == 2
    assert items[0].id == "g-1"
    assert items[0].citations == (Citation(source="pilot", unit="1", page=2),)
    assert items[1].expected_behavior == "refuse"
    assert items[1].citations == ()


def test_load_golden_set_default_path_has_answer_and_refuse() -> None:
    items = load_golden_set()
    behaviors = {item.expected_behavior for item in items}
    assert "answer" in behaviors
    assert "refuse" in behaviors
    assert all(isinstance(c.unit, str) for item in items for c in item.citations)


def test_retrieve_for_step_maps_fusion_and_reranker() -> None:
    parents = {
        "pilot:1:key-messages": {
            "_id": "pilot:1:key-messages",
            "kind": "parent",
            "source": "pilot",
            "unit": "1",
            "content_type": "key_messages",
            "heading_path": ["KEY MESSAGES"],
            "pages": ["1-2"],
            "text": "clock code",
        }
    }
    child_hits = [
        {"_id": "pilot:1:key-messages:c1", "parent_id": "pilot:1:key-messages"},
    ]
    collection = _CapturingCollection(child_hits=child_hits, parents=parents)
    embedder = _FakeQueryEmbedder()
    reranker = _IdentityReranker()

    vector_hits = retrieve_for_step(
        "vector", "q", collection, embedder, reranker=reranker
    )
    assert len(vector_hits) == 1
    assert collection.last_pipeline is not None
    assert "$vectorSearch" in collection.last_pipeline[0]
    assert "$rankFusion" not in collection.last_pipeline[0]
    assert reranker.calls == 0

    hybrid_hits = retrieve_for_step(
        "hybrid", "q", collection, embedder, reranker=reranker
    )
    assert len(hybrid_hits) == 1
    assert "$rankFusion" in collection.last_pipeline[0]
    assert reranker.calls == 0

    rerank_hits = retrieve_for_step(
        "hybrid_rerank", "q", collection, embedder, reranker=reranker
    )
    assert len(rerank_hits) == 1
    assert "$rankFusion" in collection.last_pipeline[0]
    assert reranker.calls == 1


def test_retrieve_for_step_hybrid_rerank_requires_reranker() -> None:
    with pytest.raises(ValueError, match="reranker"):
        retrieve_for_step(
            "hybrid_rerank",
            "q",
            _CapturingCollection(child_hits=[], parents={}),
            _FakeQueryEmbedder(),
            reranker=None,
        )


def test_judge_citation_faithfulness_parses_structured_verdict() -> None:
    parents = [_parent(text="9 o'clock is left.")]
    citations = (Citation(source="pilot", unit="1", page=2),)
    judge = _FixedJudge(
        {"faithful": True, "grounded": True, "rationale": "Cited page supports answer."}
    )

    verdict = judge_citation_faithfulness(
        question="Where is 9 o'clock?",
        answer="Left.",
        citations=citations,
        evidence=parents,
        judge=judge,
    )

    assert verdict == JudgeVerdict(
        faithful=True, grounded=True, rationale="Cited page supports answer."
    )
    assert "citation faithfulness" in judge.last_system.lower()


def test_judge_groundedness_parses_structured_verdict() -> None:
    parents = [_parent(text="Fly by attitude.")]
    judge = _FixedJudge(
        {
            "faithful": True,
            "grounded": False,
            "rationale": "Answer invents a speed limit.",
        }
    )

    verdict = judge_groundedness(
        question="What is VNE?",
        answer="VNE is 280 km/h.",
        parents=parents,
        judge=judge,
    )

    assert verdict.faithful is True
    assert verdict.grounded is False
    assert "groundedness" in judge.last_system.lower()


def test_judge_answer_correctness_compares_to_expected_answer() -> None:
    judge = _FixedJudge(
        {"correct": True, "rationale": "Same meaning as gold."}
    )

    verdict = judge_answer_correctness(
        question="Where is 9 o'clock?",
        answer="To the left.",
        expected_answer="Left.",
        judge=judge,
    )

    assert verdict == CorrectnessVerdict(correct=True, rationale="Same meaning as gold.")
    assert "correctness" in judge.last_system.lower()
    assert "Left." in judge.last_user
    assert "To the left." in judge.last_user


def test_run_ablation_curve_emits_three_points() -> None:
    items = [
        EvalItem(
            id="a-1",
            question="Where is 9 o'clock?",
            expected_behavior="answer",
            expected_answer="Left.",
            citations=(Citation(source="pilot", unit="1", page=2),),
            category="self_check",
            content_type="self_check",
            difficulty="easy",
            verified_absent_terms=(),
        ),
        EvalItem(
            id="r-1",
            question="What is ASK-21 VNE?",
            expected_behavior="refuse",
            expected_answer=CANONICAL_REFUSAL,
            citations=(),
            category="type_specific",
            content_type="self_check",
            difficulty="easy",
            verified_absent_terms=("VNE",),
        ),
    ]
    parents = {
        "pilot:1:key-messages": {
            "_id": "pilot:1:key-messages",
            "kind": "parent",
            "source": "pilot",
            "unit": "1",
            "content_type": "key_messages",
            "heading_path": ["KEY MESSAGES"],
            "pages": ["1-2"],
            "text": "9 o'clock is left.",
        }
    }
    child_hits = [
        {"_id": "pilot:1:key-messages:c1", "parent_id": "pilot:1:key-messages"},
    ]
    collection = _CapturingCollection(child_hits=child_hits, parents=parents)
    completer = _BehaviorCompleter()
    judge = _PromptAwareJudge()
    tracer = _RecordingTracer()
    progress: list[ItemEvalResult] = []

    points = run_ablation_curve(
        items,
        collection,
        embedder=_FakeQueryEmbedder(),
        completer=completer,
        judge=judge,
        reranker=_IdentityReranker(),
        k=10,
        tracer=tracer,
        on_item=progress.append,
    )

    assert [p.step for p in points] == ["vector", "hybrid", "hybrid_rerank"]
    assert all(isinstance(p, AblationPoint) for p in points)
    assert all(p.recall_at_k == 1.0 for p in points)
    assert all(p.refusal_accuracy == 1.0 for p in points)
    assert all(p.faithfulness == 1.0 for p in points)
    assert all(p.groundedness == 1.0 for p in points)
    assert all(p.correctness == 1.0 for p in points)
    assert tracer.started is True
    assert tracer.ended is True
    assert len(tracer.item_traces) == 6  # 2 items × 3 steps
    assert len(progress) == 6
    answered = [r for r in progress if r.item.id == "a-1"]
    assert answered[0].result is not None
    assert answered[0].result.answer == "Left."
    assert answered[0].correct is True
    assert answered[0].index == 1
    assert answered[0].total == 2


def test_null_tracer_is_safe_noop() -> None:
    tracer = NullTracer()
    run = tracer.start_run("curve", {"k": 10})
    tracer.trace_item(step="vector", item_id="x", recall=1.0)
    tracer.end_run()
    assert run is None


def test_completer_judge_delegates_to_completer() -> None:
    completer = _FixedCompleterForJudge('{"faithful": false, "grounded": true, "rationale": "x"}')
    judge = CompleterJudge(completer)
    assert judge.judge("sys", "usr") == completer.payload
    assert completer.last_system == "sys"


def test_langfuse_tracer_records_item_scores() -> None:
    client = _FakeLangfuseClient()
    tracer = LangfuseTracer(client=client)

    tracer.start_run("ablation_curve", {"k": 10})
    tracer.trace_item(
        step="hybrid",
        item_id="a-1",
        recall=1.0,
        refusal_ok=True,
        faithful=True,
        grounded=False,
    )
    tracer.end_run()

    assert client.flushed is True
    assert client.root.entered is True
    assert client.root.exited is True
    assert len(client.root.child_scores) == 4
    names = {s["name"] for s in client.root.child_scores}
    assert names == {"recall", "refusal_ok", "faithful", "grounded"}


def test_langfuse_tracer_records_correct_score() -> None:
    client = _FakeLangfuseClient()
    tracer = LangfuseTracer(client=client)
    tracer.start_run("ablation_curve", {"k": 10})
    tracer.trace_item(step="vector", item_id="a-1", correct=True)
    tracer.end_run()
    assert client.root.child_scores == [
        {"name": "correct", "value": 1.0, "data_type": "NUMERIC"}
    ]


class _FakeQueryEmbedder:
    def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1, 0.2, 0.3]


class _IdentityReranker:
    def __init__(self) -> None:
        self.calls = 0

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_k: int | None = None,
    ) -> list[int]:
        del query
        self.calls += 1
        limit = len(documents) if top_k is None else min(top_k, len(documents))
        return list(range(limit))


class _CapturingCollection:
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
        del projection
        for parent_id in filter.get("_id", {}).get("$in", []):
            doc = self.parents.get(parent_id)
            if doc is not None:
                yield dict(doc)


class _FixedJudge:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.last_system = ""
        self.last_user = ""

    def judge(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return json.dumps(self.payload)


class _PromptAwareJudge:
    """Returns verdict JSON shaped for the active judge prompt."""

    def __init__(self) -> None:
        self.last_system = ""
        self.last_user = ""

    def judge(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        if "correctness" in system.lower():
            return json.dumps({"correct": True, "rationale": "matches gold"})
        return json.dumps(
            {"faithful": True, "grounded": True, "rationale": "ok"}
        )


class _BehaviorCompleter:
    """Grounds clock-code questions; refuses everything else."""

    def complete(self, system: str, user: str) -> str:
        del system
        question = user
        if "Question:\n" in user:
            question = user.split("Question:\n", 1)[1].split("\n\n", 1)[0]
        q = question.lower()
        if "9 o'clock" in q or "9 o’clock" in q:
            return json.dumps(
                {
                    "grounded": True,
                    "answer": "Left.",
                    "citations": [{"source": "pilot", "unit": "1", "page": 2}],
                }
            )
        return json.dumps({"grounded": False})


class _RecordingTracer:
    def __init__(self) -> None:
        self.started = False
        self.ended = False
        self.item_traces: list[dict[str, Any]] = []

    def start_run(self, name: str, metadata: dict[str, Any]) -> Any:
        del name, metadata
        self.started = True
        return "run"

    def trace_item(self, *, step: str, item_id: str, **payload: Any) -> None:
        self.item_traces.append({"step": step, "item_id": item_id, **payload})

    def end_run(self) -> None:
        self.ended = True


class _FixedCompleterForJudge:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_system = ""
        self.last_user = ""

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self.payload


class _FakeObservation:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.child_scores: list[dict[str, Any]] = []
        self._child: _FakeObservation | None = None

    def __enter__(self) -> _FakeObservation:
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.exited = True

    def start_as_current_observation(self, **kwargs: Any) -> _FakeObservation:
        del kwargs
        child = _FakeObservation()
        self._child = child
        original_score = child.score

        def _score(**score_kwargs: Any) -> None:
            self.child_scores.append(score_kwargs)
            original_score(**score_kwargs)

        child.score = _score  # type: ignore[method-assign]
        return child

    def score(self, **kwargs: Any) -> None:
        del kwargs


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.root = _FakeObservation()
        self.flushed = False

    def start_as_current_observation(self, **kwargs: Any) -> _FakeObservation:
        del kwargs
        return self.root

    def flush(self) -> None:
        self.flushed = True

