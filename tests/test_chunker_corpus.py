"""chunk_corpus: the tree walk over the committed corpus/md Markdown (issue #31).

Both Sources, variant sub-units (13A/13S/13W...), and the JSONL debug dump —
all on a fresh clone: no PDFs, no network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from instructamate.stage2_chunker import chunk_corpus, dump_records_jsonl

CORPUS_MD = Path(__file__).resolve().parent.parent / "corpus" / "md"


@pytest.fixture(scope="module")
def corpus_records():
    return chunk_corpus(CORPUS_MD)


def test_every_unit_in_both_sources_chunks_without_raising(corpus_records):
    expected_units = {
        (path.parent.name, path.stem.removeprefix("unit-").lstrip("0"))
        for path in CORPUS_MD.rglob("unit-*.md")
    }
    chunked_units = {(r.source, r.unit) for r in corpus_records}
    assert chunked_units == expected_units
    assert len({r.id for r in corpus_records}) == len(corpus_records)  # globally unique


def test_variant_unit_token_is_preserved_in_ids_and_pages(corpus_records):
    aim_13a = next(
        r for r in corpus_records if r.id == "trainer:13A:aim" and r.kind == "parent"
    )
    assert aim_13a.unit == "13A"
    assert aim_13a.pages == ["13A-1"]
    assert all(p.startswith("13A-") for r in corpus_records if r.unit == "13A" for p in r.pages)


def test_jsonl_dump_round_trips_every_record(tmp_path, corpus_records):
    import json

    dump = tmp_path / "chunks.jsonl"
    dump_records_jsonl(corpus_records, dump)

    lines = dump.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(corpus_records)
    first = json.loads(lines[0])
    assert first["id"] == corpus_records[0].id
    assert first["kind"] == corpus_records[0].kind
    assert first["text"] == corpus_records[0].text


def test_default_dump_name_is_gitignored():
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "chunks.jsonl" in gitignore


def test_pilot_unit_27_chunks_cross_source(corpus_records):
    pilot27 = [r for r in corpus_records if r.source == "pilot" and r.unit == "27"]
    assert pilot27, "Pilot 27 missing from the corpus walk"
    assert all(r.id.startswith("pilot:27:") for r in pilot27)
    assert any(r.kind == "child" and r.embedding_text.startswith("Pilot Guide, Unit 27 — ") for r in pilot27)
