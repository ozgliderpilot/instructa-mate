"""GPC logbook training card: page markers must be whole lines the chunker records."""

from __future__ import annotations

from pathlib import Path

from instructamate.stage2_chunker import chunk_unit_markdown
from instructamate.stage4_qa import page_from_token

CARD = (
    Path(__file__).resolve().parent.parent
    / "corpus"
    / "md"
    / "pilot"
    / "gpc-training-card.md"
)


def test_gpc_training_card_page_markers_are_citation_keys():
    md = CARD.read_text(encoding="utf-8")
    assert not any(line.startswith("| <!-- page:") for line in md.splitlines())

    [parent] = [record for record in chunk_unit_markdown(md) if record.kind == "parent"]
    assert parent.pages == [str(number) for number in range(12, 30)]
    assert [page_from_token(token, parent.unit) for token in parent.pages] == list(
        range(12, 30)
    )
