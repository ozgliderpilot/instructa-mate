"""Shared test fixtures for the stage-1 parser.

The real GFA PDFs live in the gitignored ``corpus/`` (copyright). Tests that need
them auto-skip when they are absent, so a fresh checkout without the corpus still
runs green.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"

PILOT_PDF = CORPUS / "00-Combined Pilot Guides 1-26 Solo.pdf"
TRAINER_PDF = CORPUS / "00 Combined Trainer Guides units 1-26 Solo  BBB.pdf"

# The hand-verified Markdown is the committed source of truth (ADR 0002); it doubles
# as the golden the parser must reproduce.
PILOT_UNIT1_GOLDEN = CORPUS / "md" / "pilot" / "unit-01.md"
TRAINER_UNIT5_GOLDEN = CORPUS / "md" / "trainer" / "unit-05.md"
TRAINER_UNIT13A_GOLDEN = CORPUS / "md" / "trainer" / "unit-13A.md"


@pytest.fixture
def pilot_pdf() -> Path:
    if not PILOT_PDF.exists():
        pytest.skip(f"Pilot guide PDF not present at {PILOT_PDF} (gitignored corpus)")
    return PILOT_PDF


@pytest.fixture
def trainer_pdf() -> Path:
    if not TRAINER_PDF.exists():
        pytest.skip(f"Trainer guide PDF not present at {TRAINER_PDF} (gitignored corpus)")
    return TRAINER_PDF


@pytest.fixture
def corpus_sources() -> dict[str, Path]:
    """The ``{source: pdf_path}`` mapping the batch wrapper ingests. Skips unless both
    copyright PDFs are present locally."""
    missing = [p for p in (PILOT_PDF, TRAINER_PDF) if not p.exists()]
    if missing:
        pytest.skip(f"corpus PDFs not present: {missing} (gitignored)")
    return {"trainer": TRAINER_PDF, "pilot": PILOT_PDF}


@pytest.fixture
def pilot_unit1_golden() -> str:
    if not PILOT_UNIT1_GOLDEN.exists():
        pytest.skip(f"Golden not present at {PILOT_UNIT1_GOLDEN}")
    return PILOT_UNIT1_GOLDEN.read_text(encoding="utf-8")


@pytest.fixture
def trainer_unit5_golden() -> str:
    if not TRAINER_UNIT5_GOLDEN.exists():
        pytest.skip(f"Golden not present at {TRAINER_UNIT5_GOLDEN}")
    return TRAINER_UNIT5_GOLDEN.read_text(encoding="utf-8")


@pytest.fixture
def trainer_unit13A_golden() -> str:
    if not TRAINER_UNIT13A_GOLDEN.exists():
        pytest.skip(f"Golden not present at {TRAINER_UNIT13A_GOLDEN}")
    return TRAINER_UNIT13A_GOLDEN.read_text(encoding="utf-8")
