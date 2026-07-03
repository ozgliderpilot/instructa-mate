"""Fail-loud contract of the stage-2 chunker (peer of stage 1's UnitStructureError).

Small synthetic Markdown strings, mirroring stage 1's synthetic-PDF prior art:
structural ambiguity must raise, never emit silently-wrong records.
"""
from __future__ import annotations

import pytest

from instructamate.stage2_chunker import ChunkStructureError, chunk_unit_markdown


def _unit(body: str) -> str:
    return (
        "---\n"
        "source: trainer\n"
        "unit: 5\n"
        "unit_name: Primary Effects of Controls\n"
        'revision: "1.0"\n'
        "---\n"
        "\n"
        "# Unit 5 — Primary Effects of Controls\n"
        "\n" + body
    )


def test_body_text_before_first_section_raises():
    md = _unit("Orphan prose with no section.\n\n## AIM\n<!-- content_type: aim -->\n\nBody.\n")
    with pytest.raises(ChunkStructureError, match="before the first"):
        chunk_unit_markdown(md)


def test_section_without_content_type_marker_raises():
    md = _unit("## AIM\n\nBody text.\n")
    with pytest.raises(ChunkStructureError, match="content_type"):
        chunk_unit_markdown(md)


def test_unknown_content_type_raises():
    md = _unit("## AIM\n<!-- content_type: vibes -->\n\nBody text.\n")
    with pytest.raises(ChunkStructureError, match="vibes"):
        chunk_unit_markdown(md)
