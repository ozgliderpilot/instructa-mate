"""Stage-4 refuse-or-cite Q&A (#38).

Unit tests use in-memory fakes only — no network.
"""
from __future__ import annotations

import json
from typing import Sequence

from instructamate.stage3_retrieve import ParentHit
from instructamate.stage4_qa import (
    CANONICAL_REFUSAL,
    Citation,
    answer_from_parents,
    answer_question,
)


def test_empty_parents_yield_structured_refusal():
    result = answer_from_parents(
        "What is the MacCready ring setting on final glide?",
        parents=[],
        completer=_UnusedCompleter(),
    )

    assert result.grounded is False
    assert result.answer == CANONICAL_REFUSAL
    assert result.citations == ()


def test_grounded_answer_carries_checkable_citations():
    parents = [
        ParentHit(
            id="pilot:5:key-messages",
            source="pilot",
            unit="5",
            pages=("5-2",),
            heading_path=("KEY MESSAGES",),
            text="Fly the aircraft by attitude; primary attitude reference is the horizon.",
            content_type="key_messages",
        )
    ]
    completer = _FixedCompleter(
        {
            "grounded": True,
            "answer": "The natural horizon.",
            "citations": [{"source": "pilot", "unit": "5", "page": 2}],
        }
    )

    result = answer_from_parents(
        "What is your primary attitude reference for controlling pitch?",
        parents=parents,
        completer=completer,
    )

    assert result.grounded is True
    assert result.answer == "The natural horizon."
    assert result.citations == (Citation(source="pilot", unit="5", page=2),)
    assert "horizon" in completer.last_user.lower()
    assert "KEY MESSAGES" in completer.last_user


def test_model_refusal_yields_structured_refusal():
    parents = [
        ParentHit(
            id="pilot:5:key-messages",
            source="pilot",
            unit="5",
            pages=("5-2",),
            heading_path=("KEY MESSAGES",),
            text="Fly by attitude.",
            content_type="key_messages",
        )
    ]

    result = answer_from_parents(
        "What is ASK-21 VNE?",
        parents=parents,
        completer=_FixedCompleter({"grounded": False}),
    )

    assert result.grounded is False
    assert result.answer == CANONICAL_REFUSAL
    assert result.citations == ()


def test_unsupported_citation_is_refused():
    parents = [
        ParentHit(
            id="pilot:5:key-messages",
            source="pilot",
            unit="5",
            pages=("5-2",),
            heading_path=("KEY MESSAGES",),
            text="Fly by attitude.",
            content_type="key_messages",
        )
    ]

    result = answer_from_parents(
        "What is the primary attitude reference?",
        parents=parents,
        completer=_FixedCompleter(
            {
                "grounded": True,
                "answer": "The natural horizon.",
                # page 99 was never retrieved
                "citations": [{"source": "pilot", "unit": "5", "page": 99}],
            }
        ),
    )

    assert result.grounded is False
    assert result.answer == CANONICAL_REFUSAL
    assert result.citations == ()


def test_answer_question_retrieves_then_grounds_or_refuses():
    parents = {
        "pilot:5:key-messages": {
            "_id": "pilot:5:key-messages",
            "kind": "parent",
            "source": "pilot",
            "unit": "5",
            "content_type": "key_messages",
            "heading_path": ["KEY MESSAGES"],
            "pages": ["5-2"],
            "text": "Primary attitude reference is the horizon.",
        }
    }
    child_hits = [
        {"_id": "pilot:5:key-messages:c1", "parent_id": "pilot:5:key-messages"},
    ]
    collection = _RetrievingFakeCollection(child_hits=child_hits, parents=parents)

    result = answer_question(
        "What is your primary attitude reference for controlling pitch?",
        collection,
        embedder=_FakeQueryEmbedder(),
        completer=_FixedCompleter(
            {
                "grounded": True,
                "answer": "The natural horizon.",
                "citations": [{"source": "pilot", "unit": "5", "page": 2}],
            }
        ),
    )

    assert result.grounded is True
    assert result.citations == (Citation(source="pilot", unit="5", page=2),)


def test_malformed_completion_is_refused():
    parents = [
        ParentHit(
            id="pilot:5:key-messages",
            source="pilot",
            unit="5",
            pages=("5-2",),
            heading_path=("KEY MESSAGES",),
            text="Fly by attitude.",
            content_type="key_messages",
        )
    ]

    class _Broken:
        def complete(self, system: str, user: str) -> str:
            del system, user
            return "not json at all"

    result = answer_from_parents("Anything?", parents=parents, completer=_Broken())

    assert result.grounded is False
    assert result.answer == CANONICAL_REFUSAL
    assert result.citations == ()


def test_anthropic_completer_returns_text_blocks():
    from instructamate.stage4_qa import AnthropicCompleter, DEFAULT_COMPLETION_MODEL

    class _Block:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Message:
        content = [_Block('{"grounded": false}')]

    class _FakeClient:
        def __init__(self) -> None:
            self.last_kwargs: dict | None = None
            self.messages = self

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return _Message()

    client = _FakeClient()
    completer = AnthropicCompleter(client=client)

    assert completer.complete("sys", "usr") == '{"grounded": false}'
    assert client.last_kwargs is not None
    assert client.last_kwargs["model"] == DEFAULT_COMPLETION_MODEL
    assert client.last_kwargs["system"] == "sys"
    assert client.last_kwargs["messages"] == [{"role": "user", "content": "usr"}]


class _FakeQueryEmbedder:
    def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1, 0.2, 0.3]


class _RetrievingFakeCollection:
    def __init__(
        self,
        *,
        child_hits: list[dict],
        parents: dict[str, dict],
    ) -> None:
        self.child_hits = child_hits
        self.parents = parents

    def aggregate(self, pipeline: Sequence):
        del pipeline
        return list(self.child_hits)

    def find(self, filter: dict, projection: dict | None = None):
        del projection
        for parent_id in filter.get("_id", {}).get("$in", []):
            doc = self.parents.get(parent_id)
            if doc is not None:
                yield dict(doc)


class _UnusedCompleter:
    def complete(self, system: str, user: str) -> str:
        raise AssertionError("completer must not be called when parents are empty")


class _FixedCompleter:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_system = ""
        self.last_user = ""

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return json.dumps(self.payload)
