"""Stage-5 eval harness + ablation scoring (#40).

Public seams:

- :func:`recall_at_k` — retrieval hit against golden citations
- :func:`score_refusal` — automated structured-refusal metric
- :func:`load_golden_set` — load/normalize ``evals/golden_set.json``
- :func:`retrieve_for_step` — Stage 3 ablation step → parents
- :func:`judge_citation_faithfulness` / :func:`judge_groundedness` — LLM-as-judge
- :func:`judge_answer_correctness` — LLM-as-judge vs golden ``expected_answer``
- :func:`run_ablation_curve` — vector → hybrid → hybrid+rerank metrics
- :class:`ItemEvalResult` / ``on_item`` — per-item progress callback
- :class:`EvalTracer` / :class:`NullTracer` / :class:`LangfuseTracer` — curve traces
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from instructamate.stage3_retrieve import (
    DEFAULT_N,
    DEFAULT_P,
    ParentHit,
    ParentReranker,
    QueryEmbedder,
    retrieve_parents,
)
from instructamate.stage4_qa import (
    CANONICAL_REFUSAL,
    Citation,
    Completer,
    QaResult,
    answer_from_parents,
)

__all__ = [
    "ABLATION_STEPS",
    "AblationPoint",
    "AblationStep",
    "CompleterJudge",
    "CorrectnessVerdict",
    "DEFAULT_GOLDEN_SET",
    "EvalItem",
    "EvalTracer",
    "ItemEvalResult",
    "Judge",
    "JudgeVerdict",
    "LangfuseTracer",
    "NullTracer",
    "judge_answer_correctness",
    "judge_citation_faithfulness",
    "judge_groundedness",
    "load_golden_set",
    "recall_at_k",
    "retrieve_for_step",
    "run_ablation_curve",
    "score_refusal",
]

_PAGE_TOKEN = re.compile(r"^(?P<unit>.+)-(?P<page>\d+)$")

DEFAULT_GOLDEN_SET = (
    Path(__file__).resolve().parents[2] / "evals" / "golden_set.json"
)

AblationStep = Literal["vector", "hybrid", "hybrid_rerank"]
ABLATION_STEPS: tuple[AblationStep, ...] = ("vector", "hybrid", "hybrid_rerank")

_FAITHFULNESS_SYSTEM = """\
You are judging citation faithfulness for InstructaMate answers.

citation faithfulness: every cited (source, unit, page) must support the answer;
the answer must not rely on uncited claims from the evidence.

Respond with a single JSON object and nothing else:
{"faithful": <bool>, "grounded": <bool>, "rationale": "<short reason>"}
Set grounded to true when the answer is supportable by the evidence overall.
"""

_GROUNDEDNESS_SYSTEM = """\
You are judging groundedness for InstructaMate answers.

groundedness: every factual claim in the answer must be supported by the
provided parent chunks. Flag invented numbers, type-specific facts, or
claims absent from the evidence.

Respond with a single JSON object and nothing else:
{"faithful": <bool>, "grounded": <bool>, "rationale": "<short reason>"}
Set faithful to true when cited locations (if any) match the evidence pages.
"""

_CORRECTNESS_SYSTEM = """\
You are judging answer correctness for InstructaMate against a gold answer.

correctness: the candidate answer must convey the same essential facts as the
expected answer for this question. Paraphrase is fine; missing a required fact,
contradicting the gold, or refusing when gold answers is incorrect.

Respond with a single JSON object and nothing else:
{"correct": <bool>, "rationale": "<short reason>"}
"""


@dataclass(frozen=True)
class EvalItem:
    """One golden-set item with normalized citation units."""

    id: str
    question: str
    expected_behavior: str
    expected_answer: str
    citations: tuple[Citation, ...]
    category: str
    content_type: str
    difficulty: str
    verified_absent_terms: tuple[str, ...]


@dataclass(frozen=True)
class JudgeVerdict:
    """Structured LLM-as-judge outcome for faithfulness / groundedness."""

    faithful: bool
    grounded: bool
    rationale: str


@dataclass(frozen=True)
class CorrectnessVerdict:
    """Structured LLM-as-judge outcome vs golden ``expected_answer``."""

    correct: bool
    rationale: str


@dataclass(frozen=True)
class AblationPoint:
    """Aggregated metrics for one Stage 3 ablation step."""

    step: AblationStep
    recall_at_k: float
    refusal_accuracy: float | None
    faithfulness: float | None
    groundedness: float | None
    correctness: float | None


@dataclass(frozen=True)
class ItemEvalResult:
    """One item's scores under one ablation step (for progress callbacks)."""

    step: AblationStep
    index: int
    total: int
    item: EvalItem
    result: QaResult | None
    recall: float | None
    refusal_ok: bool | None
    faithful: bool | None
    grounded: bool | None
    correct: bool | None


class Judge(Protocol):
    """Thin port over an LLM (or a fake) for eval judging."""

    def judge(self, system: str, user: str) -> str:
        """Return judge completion text."""


class EvalTracer(Protocol):
    """Port for ablation-curve traces (Langfuse or a no-op)."""

    def start_run(self, name: str, metadata: dict[str, Any]) -> Any:
        """Begin a curve run; return an opaque run handle if useful."""

    def trace_item(self, *, step: AblationStep, item_id: str, **payload: Any) -> None:
        """Record one item evaluation under the current run."""

    def end_run(self) -> None:
        """Flush / close the current run."""


class NullTracer:
    """No-op tracer for offline unit tests."""

    def start_run(self, name: str, metadata: dict[str, Any]) -> None:
        del name, metadata
        return None

    def trace_item(self, *, step: AblationStep, item_id: str, **payload: Any) -> None:
        del step, item_id, payload

    def end_run(self) -> None:
        return None


class LangfuseTracer:
    """Thin Langfuse adapter for ablation-curve traces.

    Reads ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` (and optional
    ``LANGFUSE_BASE_URL``) via the Langfuse SDK defaults when ``client`` is
    omitted. Requires the optional ``langfuse`` extra.
    """

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            from langfuse import Langfuse

            client = Langfuse()
        self._client = client
        self._root: Any | None = None
        self._root_cm: Any | None = None

    def start_run(self, name: str, metadata: dict[str, Any]) -> Any:
        self._root_cm = self._client.start_as_current_observation(
            name=name,
            as_type="span",
            metadata=metadata,
        )
        self._root = self._root_cm.__enter__()
        return self._root

    def trace_item(self, *, step: AblationStep, item_id: str, **payload: Any) -> None:
        if self._root is None:
            return
        with self._root.start_as_current_observation(
            name=f"{step}:{item_id}",
            as_type="evaluator",
            input={"step": step, "item_id": item_id},
            output=payload,
            metadata={"step": step, "item_id": item_id},
        ) as obs:
            for key in ("recall", "refusal_ok", "faithful", "grounded", "correct"):
                if key in payload and payload[key] is not None:
                    value = payload[key]
                    if isinstance(value, bool):
                        value = 1.0 if value else 0.0
                    obs.score(name=key, value=float(value), data_type="NUMERIC")

    def end_run(self) -> None:
        if self._root_cm is not None:
            self._root_cm.__exit__(None, None, None)
            self._root_cm = None
            self._root = None
        flush = getattr(self._client, "flush", None)
        if callable(flush):
            flush()


class CompleterJudge:
    """Adapt a :class:`~instructamate.stage4_qa.Completer` to :class:`Judge`."""

    def __init__(self, completer: Completer) -> None:
        self._completer = completer

    def judge(self, system: str, user: str) -> str:
        return self._completer.complete(system, user)


def recall_at_k(
    retrieved: Sequence[ParentHit],
    gold_citations: Sequence[Citation],
    *,
    k: int | None = None,
) -> float:
    """Return 1.0 if any gold citation key appears in the top-``k`` parents.

    Citation identity is ``(source, unit, page)``. Empty gold yields 0.0.
    """
    if not gold_citations:
        return 0.0
    top = list(retrieved) if k is None else list(retrieved)[:k]
    allowed = _citation_keys_from_parents(top)
    gold_keys = {(c.source, c.unit, c.page) for c in gold_citations}
    return 1.0 if gold_keys & allowed else 0.0


def score_refusal(result: QaResult, expected_behavior: str) -> bool:
    """Return whether ``result`` matches the expected refuse-or-cite behaviour.

    ``refuse`` requires the structured refusal signal (``grounded`` false +
    canonical string). ``answer`` requires a grounded result.
    """
    is_refusal = (
        result.grounded is False
        and result.answer == CANONICAL_REFUSAL
        and result.citations == ()
    )
    if expected_behavior == "refuse":
        return is_refusal
    if expected_behavior == "answer":
        return result.grounded is True and not is_refusal
    return False


def load_golden_set(path: Path | None = None) -> list[EvalItem]:
    """Load golden-set items, normalizing citation ``unit`` to ``str``."""
    target = DEFAULT_GOLDEN_SET if path is None else Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError(f"golden set missing items list: {target}")
    return [_parse_eval_item(item) for item in raw_items]


def retrieve_for_step(
    step: AblationStep,
    query: str,
    collection: Any,
    embedder: QueryEmbedder,
    *,
    reranker: ParentReranker | None = None,
    n: int = DEFAULT_N,
    p: int = DEFAULT_P,
) -> list[ParentHit]:
    """Retrieve parents for one Stage 3 ablation step.

    ``vector`` → fusion vector, no rerank.
    ``hybrid`` → ``$rankFusion``, no rerank.
    ``hybrid_rerank`` → hybrid + required ``reranker``.
    """
    if step == "vector":
        return retrieve_parents(
            query, collection, embedder, n=n, p=p, fusion="vector", reranker=None
        )
    if step == "hybrid":
        return retrieve_parents(
            query, collection, embedder, n=n, p=p, fusion="hybrid", reranker=None
        )
    if step == "hybrid_rerank":
        if reranker is None:
            raise ValueError("hybrid_rerank step requires a reranker")
        return retrieve_parents(
            query,
            collection,
            embedder,
            n=n,
            p=p,
            fusion="hybrid",
            reranker=reranker,
        )
    raise ValueError(f"unknown ablation step: {step!r}")


def judge_citation_faithfulness(
    question: str,
    answer: str,
    citations: Sequence[Citation],
    evidence: Sequence[ParentHit],
    judge: Judge,
) -> JudgeVerdict:
    """LLM-as-judge: do citations faithfully support the answer?"""
    user = _judge_user_prompt(question, answer, citations, evidence)
    return _parse_verdict(judge.judge(_FAITHFULNESS_SYSTEM, user))


def judge_groundedness(
    question: str,
    answer: str,
    parents: Sequence[ParentHit],
    judge: Judge,
) -> JudgeVerdict:
    """LLM-as-judge: is every claim in the answer grounded in parents?"""
    user = _judge_user_prompt(question, answer, (), parents)
    return _parse_verdict(judge.judge(_GROUNDEDNESS_SYSTEM, user))


def judge_answer_correctness(
    question: str,
    answer: str,
    expected_answer: str,
    judge: Judge,
) -> CorrectnessVerdict:
    """LLM-as-judge: does ``answer`` match gold ``expected_answer`` in substance?"""
    user = (
        f"Question:\n{question}\n\n"
        f"Expected answer:\n{expected_answer}\n\n"
        f"Candidate answer:\n{answer}"
    )
    return _parse_correctness_verdict(judge.judge(_CORRECTNESS_SYSTEM, user))


def run_ablation_curve(
    items: Sequence[EvalItem],
    collection: Any,
    embedder: QueryEmbedder,
    *,
    completer: Completer | None = None,
    judge: Judge | None = None,
    reranker: ParentReranker | None = None,
    k: int = DEFAULT_P,
    n: int = DEFAULT_N,
    p: int = DEFAULT_P,
    tracer: EvalTracer | None = None,
    on_item: Callable[[ItemEvalResult], None] | None = None,
) -> list[AblationPoint]:
    """Score the three Stage 3 ablation steps on ``items``.

    ``recall_at_k`` averages over items with non-empty gold citations.
    ``refusal_accuracy`` averages over all items when ``completer`` is set.
    Faithfulness / groundedness / correctness average over grounded answers
    when ``judge`` is set. ``on_item`` receives each item as it finishes.
    Traces land on ``tracer`` when provided.
    """
    active = tracer if tracer is not None else NullTracer()
    active.start_run(
        "ablation_curve",
        {"k": k, "n": n, "p": p, "items": len(items)},
    )
    try:
        return [
            _score_step(
                step,
                items,
                collection,
                embedder,
                completer=completer,
                judge=judge,
                reranker=reranker,
                k=k,
                n=n,
                p=p,
                tracer=active,
                on_item=on_item,
            )
            for step in ABLATION_STEPS
        ]
    finally:
        active.end_run()


def _score_step(
    step: AblationStep,
    items: Sequence[EvalItem],
    collection: Any,
    embedder: QueryEmbedder,
    *,
    completer: Completer | None,
    judge: Judge | None,
    reranker: ParentReranker | None,
    k: int,
    n: int,
    p: int,
    tracer: EvalTracer,
    on_item: Callable[[ItemEvalResult], None] | None,
) -> AblationPoint:
    recalls: list[float] = []
    refusal_hits: list[float] = []
    faithful_hits: list[float] = []
    grounded_hits: list[float] = []
    correct_hits: list[float] = []
    total = len(items)

    for index, item in enumerate(items, start=1):
        parents = retrieve_for_step(
            step,
            item.question,
            collection,
            embedder,
            reranker=reranker,
            n=n,
            p=p,
        )
        recall: float | None = None
        if item.citations:
            recall = recall_at_k(parents, item.citations, k=k)
            recalls.append(recall)

        refusal_ok: bool | None = None
        result: QaResult | None = None
        if completer is not None:
            result = answer_from_parents(item.question, parents, completer)
            refusal_ok = score_refusal(result, item.expected_behavior)
            refusal_hits.append(1.0 if refusal_ok else 0.0)

        faithful: bool | None = None
        grounded: bool | None = None
        correct: bool | None = None
        if (
            judge is not None
            and result is not None
            and result.grounded
            and item.expected_behavior == "answer"
        ):
            faith_verdict = judge_citation_faithfulness(
                item.question,
                result.answer,
                result.citations,
                parents,
                judge,
            )
            ground_verdict = judge_groundedness(
                item.question,
                result.answer,
                parents,
                judge,
            )
            correct_verdict = judge_answer_correctness(
                item.question,
                result.answer,
                item.expected_answer,
                judge,
            )
            faithful = faith_verdict.faithful
            grounded = ground_verdict.grounded
            correct = correct_verdict.correct
            faithful_hits.append(1.0 if faithful else 0.0)
            grounded_hits.append(1.0 if grounded else 0.0)
            correct_hits.append(1.0 if correct else 0.0)

        tracer.trace_item(
            step=step,
            item_id=item.id,
            recall=recall,
            refusal_ok=refusal_ok,
            faithful=faithful,
            grounded=grounded,
            correct=correct,
        )
        if on_item is not None:
            on_item(
                ItemEvalResult(
                    step=step,
                    index=index,
                    total=total,
                    item=item,
                    result=result,
                    recall=recall,
                    refusal_ok=refusal_ok,
                    faithful=faithful,
                    grounded=grounded,
                    correct=correct,
                )
            )

    return AblationPoint(
        step=step,
        recall_at_k=_mean(recalls),
        refusal_accuracy=_mean_or_none(refusal_hits, enabled=completer is not None),
        faithfulness=_mean_or_none(faithful_hits, enabled=judge is not None),
        groundedness=_mean_or_none(grounded_hits, enabled=judge is not None),
        correctness=_mean_or_none(correct_hits, enabled=judge is not None),
    )


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _mean_or_none(values: Sequence[float], *, enabled: bool) -> float | None:
    if not enabled or not values:
        return None
    return _mean(values)


def _judge_user_prompt(
    question: str,
    answer: str,
    citations: Sequence[Citation],
    evidence: Sequence[ParentHit],
) -> str:
    cite_lines = [
        f"- {c.source} unit={c.unit} page={c.page}" for c in citations
    ] or ["(none)"]
    evidence_blocks = [_format_parent(i, p) for i, p in enumerate(evidence, start=1)]
    evidence_text = "\n\n".join(evidence_blocks) if evidence_blocks else "(none)"
    return (
        f"Question:\n{question}\n\n"
        f"Answer:\n{answer}\n\n"
        f"Citations:\n" + "\n".join(cite_lines) + "\n\n"
        f"Evidence parents:\n{evidence_text}"
    )


def _format_parent(index: int, parent: ParentHit) -> str:
    pages = ", ".join(parent.pages) if parent.pages else "(none)"
    return (
        f"[{index}] source={parent.source} unit={parent.unit} pages=[{pages}]\n"
        f"{parent.text}"
    )


def _parse_verdict(raw: str) -> JudgeVerdict:
    data = _parse_json_object(raw)
    if data is None:
        return JudgeVerdict(faithful=False, grounded=False, rationale="unparseable judge output")
    faithful = data.get("faithful")
    grounded = data.get("grounded")
    rationale = data.get("rationale")
    if not isinstance(faithful, bool) or not isinstance(grounded, bool):
        return JudgeVerdict(faithful=False, grounded=False, rationale="invalid judge booleans")
    if not isinstance(rationale, str):
        rationale = ""
    return JudgeVerdict(faithful=faithful, grounded=grounded, rationale=rationale.strip())


def _parse_correctness_verdict(raw: str) -> CorrectnessVerdict:
    data = _parse_json_object(raw)
    if data is None:
        return CorrectnessVerdict(correct=False, rationale="unparseable judge output")
    correct = data.get("correct")
    rationale = data.get("rationale")
    if not isinstance(correct, bool):
        return CorrectnessVerdict(correct=False, rationale="invalid judge boolean")
    if not isinstance(rationale, str):
        rationale = ""
    return CorrectnessVerdict(correct=correct, rationale=rationale.strip())


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_eval_item(raw: Any) -> EvalItem:
    if not isinstance(raw, dict):
        raise ValueError("golden item must be an object")
    citations_raw = raw.get("citations")
    if not isinstance(citations_raw, list):
        raise ValueError(f"item {raw.get('id')!r} citations must be a list")
    citations = tuple(_parse_citation(c) for c in citations_raw)
    terms = raw.get("verified_absent_terms") or []
    if not isinstance(terms, list):
        raise ValueError(f"item {raw.get('id')!r} verified_absent_terms must be a list")
    return EvalItem(
        id=str(raw["id"]),
        question=str(raw["question"]),
        expected_behavior=str(raw["expected_behavior"]),
        expected_answer=str(raw["expected_answer"]),
        citations=citations,
        category=str(raw["category"]),
        content_type=str(raw["content_type"]),
        difficulty=str(raw["difficulty"]),
        verified_absent_terms=tuple(str(t) for t in terms),
    )


def _parse_citation(raw: Any) -> Citation:
    if not isinstance(raw, dict):
        raise ValueError("citation must be an object")
    source = raw.get("source")
    unit = raw.get("unit")
    page = raw.get("page")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("citation source must be a non-empty string")
    if unit is None:
        raise ValueError("citation unit is required")
    unit_str = str(unit).strip()
    if not unit_str:
        raise ValueError("citation unit must be non-empty")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("citation page must be a positive int")
    return Citation(source=source.strip(), unit=unit_str, page=page)


def _citation_keys_from_parents(
    parents: Sequence[ParentHit],
) -> set[tuple[str, str, int]]:
    keys: set[tuple[str, str, int]] = set()
    for parent in parents:
        for token in parent.pages:
            match = _PAGE_TOKEN.match(token)
            if match is None:
                continue
            if match.group("unit") != parent.unit:
                continue
            keys.add((parent.source, parent.unit, int(match.group("page"))))
    return keys
