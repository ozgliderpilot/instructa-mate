"""Bulk text-retention guard: no committed ``unit-NN.md`` may silently drop PDF prose.

Spot-checking a couple of units by hand can't prove the parser keeps *every* unit's text
(it missed Unit 18's lost title and Pilot 25's dropped callout for months). This test
reconciles every committed Markdown unit against its source PDF as a token multiset: any
PDF word whose Markdown count is lower is a *lost* token. Page chrome is removed first —
the manual banner / running header (auto-detected as any line repeating on ≥60% of a
unit's pages) and the footer date band — and a small stoplist covers the tokens the parser
drops *by design* (two-column table column headers).

Whatever survives is a real omission. The handful of units that legitimately drop text
embedded in **figures/diagrams** (stage-1 has no figure handling) are pinned in
``KNOWN_LOSSES`` with their reason. The test
fails if any *other* unit loses a token, or if a pinned unit loses a *new* one: that is the
signal that a parser change started shedding prose.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import fitz
import pytest

from instructamate.stage1_parser import _parse_unit_id, _resolve_unit_pages

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
MD_ROOT = CORPUS / "md"

# (source, unit-number predicate) -> PDF. Unit numbers don't overlap across the two ranges.
SOLO = {"pilot": CORPUS / "00-Combined Pilot Guides 1-26 Solo.pdf",
        "trainer": CORPUS / "00 Combined Trainer Guides units 1-26 Solo  BBB.pdf"}
GPC = {"pilot": CORPUS / "Combined Pilot Guides 27 - 44 GPC.pdf",
       "trainer": CORPUS / "00 Combined Trainer Guides units 27-44 GPC.pdf"}

# Page chrome stripped before tokenizing (the auto-detected running banner catches the
# rest). The footer prints the date, which lives nowhere in the body.
_CHROME_LINE = re.compile(
    r"^\s*("
    r"Gliding Australia Training Manual"
    r"|(Trainer|Pilot) Guide"
    r"|Unit \d+[A-Z]? ?[-–].*"
    r"|Revision[: ].*"
    r"|(January|February|March|April|May|June|July|August|September|October|November|December) \d{4}"
    r"|Page \d+ ?[A-Z]? ?[-–] ?\d+"
    r")\s*$"
)
_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"}
_YEARS = {str(y) for y in range(2018, 2031)}
# Tokens the parser drops by design: two-column table column headers (the competency
# ELEMENT|PERFORMANCE STANDARD header row, and the COMMON PROBLEMS Problem|Probable Cause /
# Problem|Solution header row), plus bare date chrome that escaped a footer line.
_EXPECTED_DROPPED = {
    "element", "performance", "standard", "standards",
    "problem", "probable", "cause", "solution", "solutions",
} | _MONTHS | _YEARS

# Units that legitimately shed text the stage-1 parser can't render. Pinned with their exact
# token set so a *new* loss in the same unit still trips the test. Grouped by cause:
#   • figure/diagram/legend labels embedded in raster or vector art (no figure handling yet)
#   • title-page banner residue on a 2-page unit (below the auto-detect's ≥3-page threshold)
KNOWN_LOSSES: dict[str, frozenset[str]] = {
    # Primary-effects-of-controls diagram labels on 5-3 (Control/Movement/axis of rotation …).
    "pilot/unit-05": frozenset({"aircraft", "around", "axis", "changes", "control", "creates",
                                "force", "movement", "position", "resulting", "rotation",
                                "surface", "that"}),
    # Airspace-classification map legend on 36-4 (CTAF/Class E/Danger Area/frequency …).
    "pilot/unit-36": frozenset({"area", "class", "ctaf", "danger", "details", "for",
                                "frequency", "green", "heights", "including", "indicates",
                                "shown"}),
    # Winch-launch profile diagram labels (ground run/separation/initial climb/full climb …).
    "pilot/unit-14W": frozenset({"area", "climb", "full", "ground", "initial", "manoeuvring",
                                 "non", "release", "run", "separation"}),
    "pilot/unit-13W": frozenset({"area", "climb", "full", "ground", "initial", "manoeuvring",
                                 "non", "run", "separation"}),
    # Field-selection "WSSSSSS / Field Selection Check list" figure heading on 34-2.
    "pilot/unit-34": frozenset({"check", "field", "list", "selection", "wssssss"}),
    # "Computers" — second line of the wrapped running banner on a short unit.
    "pilot/unit-39": frozenset({"computers"}),
    # "1.5 Km / 700 m" closing-speed diagram label.
    "pilot/unit-09": frozenset({"700"}),
    # "Required actions" figure label.
    "pilot/unit-06": frozenset({"actions", "required"}),
    # Title-page "Trainer Guide" banner residue (2-page units, under the ≥3-page auto-detect).
    "trainer/unit-01": frozenset({"guide", "trainer"}),
    "trainer/unit-02": frozenset({"guide", "trainer"}),
}


@lru_cache(maxsize=None)
def _doc(path: str) -> fitz.Document:
    return fitz.open(path)


def _tokenize(text: str) -> Counter:
    text = text.replace("’", "'").replace("‘", "'")
    return Counter(w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2)


def _pdf_tokens(doc, pages) -> Counter:
    per_page = [[ln.strip() for ln in doc[idx].get_text("text").splitlines() if ln.strip()]
                for idx, _ in pages]
    seen = Counter(ln for lines in per_page for ln in set(lines))
    n = len(pages)
    running = {ln for ln, c in seen.items() if n >= 3 and c >= max(2, round(n * 0.6))}
    counter: Counter = Counter()
    for lines in per_page:
        kept = [ln for ln in lines if ln not in running and not _CHROME_LINE.match(ln)]
        counter += _tokenize("\n".join(kept))
    return counter


def _md_tokens(text: str) -> Counter:
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return _tokenize(text)


def _md_files() -> list[Path]:
    if not MD_ROOT.exists():
        return []
    return sorted(MD_ROOT.glob("*/unit-*.md"))


def _key(md_path: Path) -> str:
    return f"{md_path.parent.name}/{md_path.stem}"


@pytest.mark.parametrize("md_path", _md_files(), ids=_key)
def test_unit_retains_all_meaningful_prose(md_path: Path) -> None:
    source = md_path.parent.name
    number, variant = _parse_unit_id(md_path.stem.removeprefix("unit-"))
    pdf = (SOLO if number <= 26 else GPC)[source]
    if not pdf.exists():
        pytest.skip(f"corpus PDF not present: {pdf} (gitignored)")

    pages = _resolve_unit_pages(_doc(str(pdf)), source, number, variant)
    pdf_c = _pdf_tokens(_doc(str(pdf)), pages)
    md_c = _md_tokens(md_path.read_text(encoding="utf-8"))

    lost = {w for w, n in pdf_c.items() if n > md_c[w] and w not in _EXPECTED_DROPPED}
    unexpected = lost - KNOWN_LOSSES.get(_key(md_path), frozenset())
    assert not unexpected, (
        f"{_key(md_path)} dropped PDF tokens not seen anywhere in the Markdown: "
        f"{sorted(unexpected)}. If this is genuinely figure-embedded text, pin it in "
        f"KNOWN_LOSSES with its cause; otherwise the parser is shedding prose."
    )
