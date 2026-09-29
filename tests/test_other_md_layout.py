"""Layout fixes for the other-corpus Markdown (PR #49).

The committed files are the source of truth. These checks lock the grid,
figure notes, and form repairs without needing the copyright PDFs.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "corpus" / "md" / "other"
FIGURE = "*[Diagram / figure — see source PDF.]*"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _page(md: str, number: int) -> str:
    marker = f"<!-- page: {number} -->"
    start = md.index(marker) + len(marker)
    rest = md[start:]
    nxt = rest.find("<!-- page:")
    return rest if nxt < 0 else rest[:nxt]


def test_instructor_matrix_keeps_an_empty_aei_cell():
    manual = _read("unit-training-manual.md")
    assert "| 9. Lookout scan procedures |  | ✔ |" in manual
    assert "| 26. Assessment of competence for First Solo |  | L2 or L3 |" in manual


def test_gpc_syllabus_keeps_unit_element_and_nested_bullets():
    manual = _read("unit-training-manual.md")
    page = _page(manual, 77)
    assert "| GPC Unit | Element | Performance standards |" in page
    assert "| 6 | 1. Knowledge of Aerodynamics of Control Surfaces |" in page
    carried = _page(manual, 81)
    assert "| 20A | 3. Initial climb emergencies |" in carried
    assert "  - A wingtip touches the ground." in carried
    assert "| 15 | 2. Determine appropriate landing area, circuit pattern and associated circuit joining area. |" in _page(
        manual, 97
    )


def test_narrow_form_headers_stay_on_one_baseline():
    manual = _read("unit-training-manual.md")
    page = _page(manual, 68)
    assert "Briefed by" in page
    assert "Competent" in page
    assert "\nCompeten\n" not in page
    assert page.index("Following satisfactory") < page.index("Pilot:")
    assert "Once the trainee" in _page(manual, 65)
    assert all("<br>" not in line for line in manual.splitlines() if line.startswith("#"))
    assert "Name    Date of birth" in _page(manual, 55)


def test_diagrams_on_prose_pages_leave_a_pointer():
    principles = _read("unit-training-principles.md")
    page8 = _page(principles, 8)
    assert FIGURE in page8
    assert page8.index(FIGURE) < page8.index("## The Role of the Trainer")
    assert FIGURE in _page(principles, 22)


def test_hyphens_superscripts_and_paired_fields():
    mosp = _read("unit-mosp2.md")
    assert "non-compliance" in mosp
    assert "non- compliance" not in mosp
    assert "Sailplanes¹³" in mosp
    assert "| Email Address: | Phone: | Membership Number: |" in mosp


def test_aei_syllabus_is_two_grids_without_invented_prose():
    page = _page(_read("unit-aei.md"), 12)
    assert "Fields recorded" not in page
    assert page.count("| Exercise | Brief | Comp | Date |") == 2
    assert "| Name | Date of birth |" in page
    assert "| Phone (home and work) | Email: |" in page
    assert "AIRBORNE TRAINING" in page
    assert "Signature / Date" not in page
    assert "Once the trainee" in page


def test_no_orphan_bullet_or_split_header_lines():
    for name in (
        "unit-training-manual.md",
        "unit-mosp2.md",
        "unit-training-principles.md",
        "unit-aei.md",
    ):
        lines = _read(name).splitlines()
        assert "o" not in {line.strip() for line in lines}
        assert "Competen" not in {line.strip() for line in lines}
