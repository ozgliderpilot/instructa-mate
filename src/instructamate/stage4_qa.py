"""Stage-4 refuse-or-cite Q&A (#38).

Public seams:

- :func:`answer_from_parents` — generate from retrieved parents, or refuse
- :func:`answer_question` — retrieve parents then answer (paste-through path)
- :class:`QaResult` / :class:`Citation` — grounded answer or structured refusal
- :class:`Completer` / :class:`AnthropicCompleter` — LLM port
- :data:`CANONICAL_REFUSAL` — machine-detectable refusal string
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from instructamate.stage3_retrieve import (
    DEFAULT_N,
    DEFAULT_P,
    ParentHit,
    ParentReranker,
    QueryEmbedder,
    retrieve_parents,
)

__all__ = [
    "CANONICAL_REFUSAL",
    "Citation",
    "Completer",
    "AnthropicCompleter",
    "DEFAULT_COMPLETION_MODEL",
    "QaResult",
    "answer_from_parents",
    "answer_question",
]

CANONICAL_REFUSAL = "not covered in the guides I have"
DEFAULT_COMPLETION_MODEL = "claude-sonnet-4-5"

_SYSTEM_PROMPT = """\
You are InstructaMate, a citation-safe co-pilot for gliding instructors.

Answer ONLY using the Parent Chunks provided in the user message. Do not use
outside knowledge. Every factual claim must be supportable by those chunks.

If the chunks do not cover the question, refuse. Do not guess.

Respond with a single JSON object and nothing else:
- Covered: {"grounded": true, "answer": "<concise answer>", \
"citations": [{"source": "<pilot|trainer>", "unit": "<unit id>", "page": <int>}]}
- Not covered: {"grounded": false}

Citations must use source/unit/page values that appear on the provided chunks.
Page is the integer page within the unit (e.g. token "5-2" → page 2).
"""

_PAGE_TOKEN = re.compile(r"^(?P<unit>.+)-(?P<page>\d+)$")


@dataclass(frozen=True)
class Citation:
    """Corpus location a claim cites — checkable by an instructor."""

    source: str
    unit: str
    page: int


@dataclass(frozen=True)
class QaResult:
    """Grounded answer with citations, or a structured refusal.

    ``grounded`` is the machine-detectable refuse-or-cite signal: ``False`` means
    refusal (``answer`` is :data:`CANONICAL_REFUSAL`, ``citations`` empty).
    """

    grounded: bool
    answer: str
    citations: tuple[Citation, ...]


class Completer(Protocol):
    """Thin port over an LLM (or a fake) — return model text for a prompt pair."""

    def complete(self, system: str, user: str) -> str:
        """Return the model completion text."""


class AnthropicCompleter:
    """Explicit Anthropic Messages completion for refuse-or-cite Q&A."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_COMPLETION_MODEL,
        max_tokens: int = 1024,
        client: Any | None = None,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self._client = client
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts: list[str] = []
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts)


def answer_from_parents(
    question: str,
    parents: Sequence[ParentHit],
    completer: Completer,
) -> QaResult:
    """Answer from retrieved parents only, or emit a structured refusal.

    Empty ``parents`` refuses without calling the completer (nothing to ground in).
    Post-generation, every citation must map to a retrieved parent (source, unit,
    and page token); otherwise the result is refused.
    """
    if not parents:
        return _refusal()

    raw = completer.complete(_SYSTEM_PROMPT, _user_prompt(question, parents))
    parsed = _parse_completion(raw)
    if parsed is None or not parsed.get("grounded"):
        return _refusal()

    answer = parsed.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return _refusal()

    citations = _parse_citations(parsed.get("citations"))
    if citations is None or not citations:
        return _refusal()

    if not _citations_supported(citations, parents):
        return _refusal()

    return QaResult(grounded=True, answer=answer.strip(), citations=citations)


def answer_question(
    question: str,
    collection: Any,
    embedder: QueryEmbedder,
    completer: Completer,
    *,
    n: int = DEFAULT_N,
    p: int = DEFAULT_P,
    fusion: Literal["vector", "hybrid"] = "hybrid",
    reranker: ParentReranker | None = None,
) -> QaResult:
    """Retrieve parents for ``question``, then refuse-or-cite.

    Paste-through path for club GPC questions: hybrid retrieval by default
    (ADR 0005), then :func:`answer_from_parents`.
    """
    parents = retrieve_parents(
        question,
        collection,
        embedder,
        n=n,
        p=p,
        fusion=fusion,
        reranker=reranker,
    )
    return answer_from_parents(question, parents, completer)


def _refusal() -> QaResult:
    return QaResult(grounded=False, answer=CANONICAL_REFUSAL, citations=())


def _user_prompt(question: str, parents: Sequence[ParentHit]) -> str:
    blocks = [_format_parent(i, parent) for i, parent in enumerate(parents, start=1)]
    joined = "\n\n".join(blocks)
    return f"Question:\n{question}\n\nParent Chunks:\n{joined}"


def _format_parent(index: int, parent: ParentHit) -> str:
    pages = ", ".join(parent.pages) if parent.pages else "(none)"
    heading = " > ".join(parent.heading_path) if parent.heading_path else "(none)"
    return (
        f"[{index}] id={parent.id}\n"
        f"source={parent.source} unit={parent.unit} pages=[{pages}]\n"
        f"heading={heading} content_type={parent.content_type}\n"
        f"{parent.text}"
    )


def _parse_completion(raw: str) -> dict[str, Any] | None:
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


def _parse_citations(raw: Any) -> tuple[Citation, ...] | None:
    if not isinstance(raw, list) or not raw:
        return None
    citations: list[Citation] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        source = item.get("source")
        unit = item.get("unit")
        page = item.get("page")
        if not isinstance(source, str) or not source.strip():
            return None
        if unit is None:
            return None
        unit_str = str(unit).strip()
        if not unit_str:
            return None
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return None
        citations.append(Citation(source=source.strip(), unit=unit_str, page=page))
    return tuple(citations)


def _citations_supported(
    citations: Sequence[Citation],
    parents: Sequence[ParentHit],
) -> bool:
    allowed = _allowed_citation_keys(parents)
    return all((c.source, c.unit, c.page) in allowed for c in citations)


def _allowed_citation_keys(
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
