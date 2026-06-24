"""Variant sub-unit rendering for the stage-1 parser (issue #7).

Units 13/14/20 are variant-split into A/S/W sub-units (aerotow / self-launch / winch)
in both Sources; their footers carry a letter token (``Page 13A - 1``). Slice #4 made
the parser *recognise* these and fail loud; this slice *renders* them to their own
``(Source, Unit)`` files through the same ``render_unit_markdown`` seam — no second code
path — so the A/S/W content enters the Corpus with a faithful ``13A-n`` Citation.

As elsewhere, assertions are on observable behaviour (the emitted Markdown string, the
``CorpusReport``), never on parser internals.
"""
from __future__ import annotations

import pytest

from instructamate.stage1_parser import render_unit_markdown


def test_variant_unit_renders_through_the_single_seam(trainer_pdf):
    # Tracer: a variant sub-unit goes through the same render_unit_markdown seam, keyed
    # by its token. Its identity (frontmatter unit:, H1) and every page Citation carry
    # the variant letter — "13A", not "13".
    md = render_unit_markdown(trainer_pdf, "trainer", "13A")

    fm = md.split("---\n", 2)[1]
    assert "source: trainer\n" in fm
    assert "unit: 13A\n" in fm

    assert "# Unit 13A — " in md  # H1 reassembled, variant name not "13"

    # Trainer 13A is footers 13A-1..13A-9, a validated consecutive run.
    markers = [f"<!-- page: 13A-{p} -->" for p in range(1, 10)]
    positions = [md.find(m) for m in markers]
    assert all(pos != -1 for pos in positions), dict(zip(markers, positions))
    assert positions == sorted(positions)


def test_variant_first_page_is_inferred_when_footer_less(trainer_pdf):
    # Trainer 13W's footers start at 13W-2 (its title page glyphs don't map, like a plain
    # unit's). The same page-before-13W-2 inference must fire for a variant, so the run is
    # the validated consecutive 13W-1..13W-10 — no Citation lost at the variant's seam.
    md = render_unit_markdown(trainer_pdf, "trainer", "13W")

    assert "unit: 13W\n" in md.split("---\n", 2)[1]
    markers = [f"<!-- page: 13W-{p} -->" for p in range(1, 11)]
    positions = [md.find(m) for m in markers]
    assert all(pos != -1 for pos in positions), dict(zip(markers, positions))
    assert positions == sorted(positions)


def test_pilot_20S_spacing_footer_resolves_to_clean_token(pilot_pdf):
    # The Pilot 20S running header reads "Unit 20 S - …" and its footer "Page 20 S-1",
    # both with a stray space. The parser must normalise that to the same 20S token —
    # clean Citations (20S-1) and a name extracted past the "20 S -" header, not a name
    # that begins with the stray "S".
    md = render_unit_markdown(pilot_pdf, "pilot", "20S")

    assert "unit: 20S\n" in md.split("---\n", 2)[1]
    assert "<!-- page: 20S-1 -->" in md
    assert "<!-- page: 20S-4 -->" in md

    h1 = next(line for line in md.splitlines() if line.startswith("# Unit "))
    name = h1.split(" — ", 1)[1]
    assert name.startswith("Launch Emergencies")  # real name, not "" or a stray "S -"


def test_trainer_13A_matches_golden(trainer_pdf, trainer_unit13A_golden):
    # The hand-verified Trainer 13A is the committed source of truth (ADR 0002) and the
    # golden the variant pipeline must reproduce byte-for-byte. It locks the variant
    # identity (unit: 13A, 13A-n Citations) together with the full structure path the
    # plain goldens already cover (sections, the two-column competency table, the nested
    # standards bullets, and Reference Patter).
    assert render_unit_markdown(trainer_pdf, "trainer", "13A") == trainer_unit13A_golden
