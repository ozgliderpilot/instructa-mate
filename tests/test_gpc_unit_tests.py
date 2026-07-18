"""Schema + content checks for evals/gpc_unit_tests_unit*.json (issue #39 seed).

Locks the refuse-or-cite eval fixture shape. The GPC unit-test JSON is the source
of truth — we do not lock choices against club form HTML.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
GPC_UNIT_TEST_PATHS = sorted(EVALS.glob("gpc_unit_tests_unit*.json"))

BEHAVIORS = frozenset({"answer", "refuse", "correct", "decline"})
REQUIRED_ITEM_KEYS = frozenset(
    {
        "id",
        "question",
        "expected_behavior",
        "expected_answer",
        "citations",
        "category",
        "content_type",
        "difficulty",
        "verified_absent_terms",
    }
)

UNIT_SEEDS = {
    "gpc_unit_tests_unit1.json": {
        "source_form": "gpc-unit1-lookout-awareness",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit2.json": {
        "source_form": "gpc-unit2-ground-handling-and-signals",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit3.json": {
        "source_form": "gpc-unit3-pre-flight-preparation",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit4.json": {
        "source_form": "gpc-unit4-orientation-and-sailplane-stability",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit5.json": {
        "source_form": "gpc-unit5-primary-effects-of-controls",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit6.json": {
        "source_form": "gpc-unit6-aileron-drag-rudder-coordination",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit7.json": {
        "source_form": "gpc-unit7-straight-flight-various-speeds-trim",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit8.json": {
        "source_form": "gpc-unit8-sustained-turns-all-controls",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit9.json": {
        "source_form": "gpc-unit9-lookout-scan-procedures",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit10.json": {
        "source_form": "gpc-unit10-use-of-ancillary-controls",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit11.json": {
        "source_form": "gpc-unit11-introduction-to-soaring",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit12.json": {
        "source_form": "gpc-unit12-slow-flying-and-stalling",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit13a.json": {
        "source_form": "gpc-unit13a-launch-release-aerotow",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit14a.json": {
        "source_form": "gpc-unit14a-takeoff-aerotow",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit15.json": {
        "source_form": "gpc-unit15-break-off-circuit-planning",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit16.json": {
        "source_form": "gpc-unit16-circuit-joining-execution",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit17.json": {
        "source_form": "gpc-unit17-stabilised-approach-landing",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit23.json": {
        "source_form": "gpc-unit23-rules-of-the-air-ybss",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit24.json": {
        "source_form": "gpc-unit24-human-factors-pilot-limitations",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit25.json": {
        "source_form": "gpc-unit25-threat-and-error-management",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit27.json": {
        "source_form": "gpc-unit27-advanced-aerotowing",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit28.json": {
        "source_form": "gpc-unit28-sideslipping",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
    "gpc_unit_tests_unit29.json": {
        "source_form": "gpc-unit29-steep-turns",
        "n_items": 20,
        "source_items": set(range(1, 21)),
    },
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=GPC_UNIT_TEST_PATHS, ids=lambda p: p.name)
def fixture_path(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    assert path.is_file(), f"missing {path}"
    return path


@pytest.fixture(scope="module")
def fixture(fixture_path: Path) -> dict:
    return _load(fixture_path)


def test_at_least_one_gpc_unit_test_exists() -> None:
    assert GPC_UNIT_TEST_PATHS, "no evals/gpc_unit_tests_unit*.json files found"


def test_gpc_unit_tests_top_level_shape(fixture: dict) -> None:
    assert fixture["version"] == 1
    assert isinstance(fixture["items"], list)
    assert len(fixture["items"]) >= 1


def test_each_item_has_required_fields_and_valid_behavior(fixture: dict) -> None:
    ids: set[str] = set()
    for item in fixture["items"]:
        missing = REQUIRED_ITEM_KEYS - item.keys()
        assert not missing, f"{item.get('id')}: missing {sorted(missing)}"
        assert item["id"] not in ids, f"duplicate id {item['id']}"
        ids.add(item["id"])
        assert item["expected_behavior"] in BEHAVIORS
        assert isinstance(item["question"], str) and item["question"].strip()
        assert isinstance(item["expected_answer"], str)
        assert isinstance(item["verified_absent_terms"], list)
        assert isinstance(item["citations"], list)


def test_answer_items_carry_checkable_citations(fixture: dict) -> None:
    for item in fixture["items"]:
        if item["expected_behavior"] != "answer":
            continue
        assert item["expected_answer"].strip(), f"{item['id']}: empty expected_answer"
        assert item["citations"], f"{item['id']}: answer items need citations"
        for cite in item["citations"]:
            assert cite["source"] in {"pilot", "trainer"}
            unit = cite["unit"]
            if isinstance(unit, int):
                assert unit >= 1
            else:
                assert isinstance(unit, str) and re.fullmatch(r"\d+[A-Za-z]?", unit), (
                    f"{item['id']}: unit must be int or lettered unit id, got {unit!r}"
                )
            assert isinstance(cite["page"], int) and cite["page"] >= 1


def test_refuse_items_list_verified_absent_terms(fixture: dict) -> None:
    for item in fixture["items"]:
        if item["expected_behavior"] != "refuse":
            continue
        assert item["verified_absent_terms"], f"{item['id']}: refuse needs absent terms"
        assert item["citations"] == []


def test_unit_seed_item_count_and_ids(fixture_path: Path, fixture: dict) -> None:
    meta = UNIT_SEEDS.get(fixture_path.name)
    if meta is None:
        pytest.skip(f"no seed metadata registered for {fixture_path.name}")
    seeded = [
        item
        for item in fixture["items"]
        if item.get("source_form") == meta["source_form"]
    ]
    assert len(seeded) == meta["n_items"]
    assert all(item["expected_behavior"] == "answer" for item in seeded)
    assert {item["source_item"] for item in seeded} == meta["source_items"]


def test_choices_match_expected_answer(fixture: dict) -> None:
    for item in fixture["items"]:
        if "choices" not in item:
            continue
        choices = item["choices"]
        assert choices, f"{item['id']}: empty choices"
        qtype = item.get("question_type", "multiple_choice")
        if qtype == "checkboxes":
            correct = item.get("correct_choices")
            assert correct, f"{item['id']}: checkboxes need correct_choices"
            assert set(correct) <= set(choices)
            assert item["expected_answer"] == "; ".join(correct)
        else:
            assert item["expected_answer"] in choices, (
                f"{item['id']}: expected_answer not one of the choices"
            )
