"""Stage-1 ingestion parser: one (Source, Unit) of a GFA guide PDF -> verified Markdown.

The single public seam is :func:`render_unit_markdown`; :func:`write_unit_markdown`
is a thin batch wrapper that persists the result to the stable path convention
``corpus/md/<source>/unit-NN.md``. See ADR 0002 for the output contract.

PyMuPDF (``fitz``) is the only text layer (settled by the extractor bake-off, ADR 0003 —
no ``pdftotext`` engine, no LLM fallback). We read ``get_text("dict")`` rather than
``get_text("text")``: it is the *same* PyMuPDF extraction enriched with the per-span
font/size/weight that lets section structure be detected deterministically instead of
by brittle "ALL-CAPS at col 0" text heuristics.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import fitz
import pdfplumber

class UnitStructureError(ValueError):
    """A ``(source, unit)`` whose page/footer structure can't be faithfully rendered.

    Raised instead of emitting silently-wrong Markdown when a unit's pages don't form a
    clean consecutive ``U-1..U-n`` run — it is variant-split (e.g. 13A/13S/13W), absent
    from the Source, or non-consecutive. The batch wrapper catches it and reports the
    skip so Citation errors surface at parse time, not at answer time.
    """


# Footer Citation: "Page 5 - 5" (Unit 5, page 5). Units 13/14/20 are variant-split and
# print a letter token — "Page 13A - 1" — captured in group 2 so those pages are
# recognised (not mistaken for footer-less) and routed to a clear variant error.
FOOTER_RE = re.compile(r"Page\s*(\d+)\s*([A-Z])?\s*-\s*(\d+)")
REVISION_RE = re.compile(r"Revision:?\s+([0-9.]+)")  # Trainer prints "Revision: 1.0", Pilot "Revision 1.0"
# A unit identity: a plain number ("5") or a variant sub-unit token ("13A"). The letter
# is the variant (A/S/W = aerotow / self-launch / winch); units 13/14/20 are split this
# way in both Sources (see :func:`_resolve_unit_pages`).
UNIT_ID_RE = re.compile(r"(\d+)([A-Z]*)")


def _parse_unit_id(unit: int | str) -> tuple[int, str]:
    """Split a unit identity into ``(number, variant)`` — ``(13, "A")`` for ``"13A"``,
    ``(5, "")`` for a plain ``5``."""
    m = UNIT_ID_RE.fullmatch(str(unit).strip())
    if not m:
        raise ValueError(f"not a unit identity: {unit!r} (expected e.g. 5 or '13A')")
    return int(m.group(1)), m.group(2)

_BOLD_FLAG = 1 << 4  # PyMuPDF span flag bit for bold
# The running banner. Non-bold and banner-sized on content pages (stripped by size),
# but on the unit's title page (the inferred U-1) it prints large and bold, so it is
# also stripped by its constant text.
_BANNER_TEXTS = {"GLIDING AUSTRALIA TRAINING MANUAL", "TRAINER GUIDE", "PILOT GUIDE"}
_SECTION_MIN_SIZE = 13.0  # section headings are the large bold banner-sized font (~14.3)
_BANNER_MIN_SIZE = 10.5  # non-bold lines this big are the running header banner (11.2 / 14.3)
# Top-of-page band where running headers print. Across all four PDFs the header lines
# (bare-name banners, wrapped second-line fragments like "Computers"/"selection"/
# "Certificate", dash-less "Unit 1 Lookout Awareness") sit at y0 <= ~116pt; the first
# banner-sized *body* line anywhere in the corpus (Pilot 25's closing callout) starts at
# y0 ~379. 140 splits the two with margin on both sides.
_HEADER_BAND_MAX_Y0 = 140.0
_FOOTER_MAX_SIZE = 8.5  # the Revision / date / Page footer block prints at ~8.2

# Per-Source header dictionary -> content_type role (the taxonomy in CONTEXT.md).
# Structured to take both Sources; only the Pilot vocabulary needs to be correct in
# this slice. Keys are normalized (uppercase, whitespace-collapsed) for tolerant
# matching against case / indentation / typo variants.
HEADER_DICTIONARY: dict[str, dict[str, str]] = {
    "pilot": {
        "WHAT THIS UNIT IS ABOUT": "aim",
        "WHAT ARE THE PRE-REQUISITES FOR THIS UNIT?": "admin",
        "KEY MESSAGES": "key_messages",
        "PILOT GUIDE FOR THIS UNIT": "theory",
        "FLIGHT EXERCISES FOR THIS UNIT": "exercise",
        "THINGS YOU MIGHT HAVE DIFFICULTY WITH": "common_problems",
        "HOW DO YOU DEMONSTRATE COMPETENCE?": "competency",
        "RESOURCES & REFERENCES": "admin",
        "COMPLEMENTARY UNITS": "admin",
        "SELF-CHECK QUESTIONS": "self_check",
        # GPC (Units 27-44) and a few Solo units borrow Trainer-side vocabulary or
        # introduce ground-operations sections (issue #8).
        "THREAT AND ERROR MANAGEMENT": "airmanship",  # rare in Pilot (Solo 13A, GPC 42)
        "COMMON PROBLEMS": "common_problems",  # Solo 14S uses the Trainer label
        "FLIGHT EXERCISES": "exercise",  # Solo 17 drops "FOR THIS UNIT"
        "EXERCISES FOR THIS UNIT": "exercise",  # GPC 35
        "PERSONAL PREPARATION": "briefing",  # GPC 35 ground-ops prep
        "GLIDER PREPARATION": "briefing",
        "TRAILER AND RETRIEVE PREPARATION": "briefing",
        "TASK PLANNER": "exercise",  # GPC 38 flight-planning worksheet
    },
    "trainer": {
        "AIM": "aim",
        "PREREQUISITE UNITS": "admin",
        "PRE-REQUISITE UNITS": "admin",  # GPC + Solo 11/13W/20S/25/26 hyphenate it
        "WHAT ARE THE PRE-REQUISITES FOR THIS UNIT?": "admin",  # Solo 24 uses Pilot-side label
        "RECOGNITION OF PRIOR LEARNING": "admin",  # Solo 21 (radio operator)
        "RADIOTELEPHONE OPERATOR AUTHORISATION": "admin",  # Solo 21
        "COMPLEMENTARY UNITS": "admin",
        "KEY MESSAGES": "key_messages",
        "LESSON PLANNING AND CONDUCT": "briefing",
        "PRE-FLIGHT BRIEFING": "briefing",
        "BRIEFING": "briefing",  # Solo 24, GPC 33
        "INSTRUCTOR NOTES": "briefing",  # GPC 42 (powered)
        "TRAINING NOTES AND LESSON PLANNING FOR POWERED SAILPLANE PILOTS": "briefing",  # GPC 42
        "FLIGHT EXERCISES": "exercise",
        "STUDENT EXERCISES": "exercise",  # GPC 31
        "EXERCISES": "exercise",  # GPC 35
        "PERSONAL PREPARATION": "briefing",  # GPC 35 ground-ops prep
        "GLIDER PREPARATION": "briefing",
        "TRAILER AND RETRIEVE PREPARATION": "briefing",
        "CHECKLIST": "briefing",  # GPC 35
        "SEARCH AND RESCUE": "theory",  # GPC 36 (navigation)
        "BASIC NAVIGATION PRINCIPLES": "theory",  # GPC 36
        "THREAT AND ERROR MANAGEMENT": "airmanship",
        "COMMON PROBLEMS": "common_problems",
        "COMPETENCY ELEMENTS AND PERFORMANCE STANDARDS": "competency",
        "TRAINING MATERIALS AND REFERENCES": "admin",
        # Per-control theory headers (``Use of Elevator`` …) are intentionally absent:
        # they render as ``###`` sub-exercise headings, not sections.
    },
}


def _normalize_header(text: str) -> str:
    # Strip a leading non-alphanumeric glyph: Pilot Unit 9 prints ".RESOURCES & REFERENCES"
    # (a stray bullet mis-extracted as a period) which must still match the dictionary key.
    norm = re.sub(r"\s+", " ", text).strip().upper()
    return re.sub(r"^[^0-9A-Z]+", "", norm)


def _content_type(source: str, text: str) -> str | None:
    return HEADER_DICTIONARY.get(source, {}).get(_normalize_header(text))


BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Line:
    """One visual line of a page, with the font signal used for classification.

    ``bbox`` is the line's ``(x0, y0, x1, y1)`` on the page. It is carried so a table
    block whose left and right cells PyMuPDF grouped together can be re-segmented by the
    column boundary (see :func:`_column_runs`); the prose/heading path ignores it.
    """

    text: str
    size: float
    bold: bool
    font: str
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)


def _page_blocks_with_bbox(page) -> list[tuple[BBox, list[Line]]]:
    """Group a page into ``(bbox, lines)`` PyMuPDF blocks of reading-order lines.

    A block is PyMuPDF's paragraph-ish grouping: a bullet (marker line + wrapped text
    lines), a wrapped paragraph, or a single heading line. The bbox locates the block
    on the page so it can be matched against pdfplumber table regions. Blank lines are
    dropped; empty blocks are omitted.
    """
    blocks: list[tuple[BBox, list[Line]]] = []
    for block in page.get_text("dict")["blocks"]:
        lines: list[Line] = []
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            lines.append(
                Line(
                    text=text,
                    size=round(max(s["size"] for s in spans), 1),
                    bold=all(s["flags"] & _BOLD_FLAG for s in spans),
                    font=spans[0]["font"],
                    bbox=tuple(line["bbox"]),
                )
            )
        if lines:
            blocks.append((tuple(block["bbox"]), lines))
    return blocks


def _page_blocks(page) -> list[list[Line]]:
    """Reading-order blocks of a page, without their bbox."""
    return [lines for _bbox, lines in _page_blocks_with_bbox(page)]


def _page_lines(page) -> list[Line]:
    """Flatten a page into reading-order :class:`Line` records."""
    return [line for block in _page_blocks(page) for line in block]


# Known source-document footer errors, per Source: {printed unit -> actual unit}.
# The Pilot GPC guide prints Unit 38's footer Citations as ``Page 37-1``..``37-6``
# (physical pages 75-80) while every running header on those pages reads ``Unit 38 -
# Meteorology and Flight Planning`` — the source is authoritatively wrong (issues
# #12/#13). The correction is deliberately narrow: a footer is reattributed ONLY when
# its printed unit is listed here AND the same page's running header names the actual
# unit. A blanket header-trumps-footer rule would silently mask future defects; an
# unlisted conflict still fails loud through the non-consecutive-run guard.
_FOOTER_CORRECTIONS: dict[str, dict[int, int]] = {"pilot": {37: 38}}
# The running-header title line — ``Unit 38 – Meteorology and Flight Planning`` — used
# only to corroborate a listed footer correction. Anchored to the line start and
# requiring the dash so body prose that merely mentions a unit number doesn't match.
_HEADER_UNIT_RE = re.compile(r"^Unit\s+(\d{1,2})\s*[-–]", re.MULTILINE)


def _scan_footers(doc, source: str = "") -> dict[int, tuple[int, str, int]]:
    """Every page with a readable footer, as ``page_index -> (unit, variant, page)``.

    ``variant`` is ``""`` for a plain numeric unit and the letter for a variant sub-unit
    (``"A"`` for the 13A footer ``Page 13A - 1``), so variant pages are recognised here
    rather than looking footer-less. One scan serves every structure decision below.

    A footer listed in :data:`_FOOTER_CORRECTIONS` for *source* is reattributed to its
    actual unit when the page's running header corroborates it (see the table's comment);
    pages whose header agrees with the printed footer are untouched.
    """
    corrections = _FOOTER_CORRECTIONS.get(source, {})
    footers: dict[int, tuple[int, str, int]] = {}
    for i in range(doc.page_count):
        text = doc[i].get_text("text")
        m = FOOTER_RE.search(text)
        if not m:
            continue
        unit, variant, page = int(m.group(1)), m.group(2) or "", int(m.group(3))
        actual = corrections.get(unit)
        if actual is not None and not variant:
            h = _HEADER_UNIT_RE.search(text)
            if h and int(h.group(1)) == actual:
                unit = actual
        footers[i] = (unit, variant, page)
    return footers


def _variant_letters(footers: Mapping[int, tuple[int, str, int]], number: int) -> list[str]:
    """The variant letters footers carry for *number* — ``["A", "S", "W"]`` for a
    variant-split unit (13/14/20), ``[]`` for a plain one. Lets the resolver and the batch
    wrapper agree on what a unit's sub-units are from the single footer scan."""
    return sorted({v for (u, v, _p) in footers.values() if u == number and v})


def _resolve_unit_pages(doc, source: str, number: int, variant: str) -> list[tuple[int, str]]:
    """Physical pages of a ``(number, variant)`` unit as ``(page_index, "U-P")``, or raise.

    The same resolver serves a plain unit (``variant=""``) and a variant sub-unit
    (``variant="A"`` for 13A): it keeps the footer pages whose letter matches and labels
    them with the variant token (``"13A-1"``). The footer-less first page (``Page U-1``,
    whose glyphs don't map in any extractor) is *inferred* as the page immediately before
    ``Page U-2`` — true for variant title pages too. Rather than return a silently-
    incomplete run, this raises :class:`UnitStructureError` when a plain unit is in fact
    variant-split (13/14/20 → A/S/W), the unit is absent from the Source, or the run is
    non-consecutive — so a missing Citation is caught at parse time, not at answer time.
    """
    token = f"{number}{variant}"
    footers = _scan_footers(doc, source)
    matched = sorted(i for i, (u, v, _p) in footers.items() if u == number and v == variant)
    if not matched:
        if variant:
            raise UnitStructureError(
                f"no pages found for variant sub-unit {token} in the {source} guide "
                f"(absent from this Source, or its footers don't map)"
            )
        variants = [f"{number}{v}" for v in _variant_letters(footers, number)]
        if variants:
            raise UnitStructureError(
                f"unit {number} of the {source} guide is variant-split into "
                f"{', '.join(variants)} (footer e.g. 'Page {variants[0]} - 1'); "
                f"render each variant sub-unit by its token (e.g. render_unit_markdown(..., '{variants[0]}'))"
            )
        raise UnitStructureError(
            f"no pages found for unit {number} in the {source} guide "
            f"(absent from this Source, or its footers don't map)"
        )

    pages: list[tuple[int, str]] = []
    if footers[matched[0]][2] == 2:  # Page U-1 footer unreadable -> infer it
        pages.append((matched[0] - 1, f"{token}-1"))
    pages.extend((i, f"{token}-{footers[i][2]}") for i in matched)

    pnums = [int(label.rsplit("-", 1)[1]) for _idx, label in pages]
    if pnums != list(range(1, len(pnums) + 1)):
        raise UnitStructureError(
            f"unit {token} of the {source} guide has a non-consecutive page run "
            f"{pnums}; expected 1..{len(pnums)} — a page's footer Citation is missing"
        )
    return pages


def _unit_metadata(
    doc, source: str, number: int, variant: str, pages: list[tuple[int, str]]
) -> dict[str, str]:
    """Pull ``unit_name`` and ``revision`` from the unit's running banner/footer.

    The name regex is anchored to this unit's *known* number and variant rather than a
    bare ``Unit \\d+`` pattern, because the variant running header reads ``Unit 13S -
    Launch …`` (or, for Pilot 20S, ``Unit 20 S - …`` with a space). A generic optional
    ``[A-Z]?`` would swallow the first letter of an un-dashed plain name (``Unit 1
    Lookout Awareness``); anchoring to the resolved variant avoids that ambiguity.

    A few units (both Sources' Unit 18) print the running banner as the *bare* name —
    "Spin / Spiral Dive Avoidance and Recovery", with no ``Unit 18 -`` prefix — so the
    anchored regex never matches. Rather than emit a blank title, fall back to the
    repeating banner line (see :func:`_running_banner_name`).
    """
    title_re = re.compile(rf"Unit\s+{number}(?!\d)\s*{variant}\s*[-–]?\s*(.+)")
    name, revision = "", ""
    for idx, _label in pages:
        for line in _page_lines(doc[idx]):
            if not name and not line.bold:
                m = title_re.fullmatch(line.text)
                if m:
                    name = m.group(1).strip()
            if not revision:
                m = REVISION_RE.search(line.text)
                if m:
                    revision = m.group(1)
        if name and revision:
            break
    if not name:
        name = _running_banner_name(doc, source, pages)
    return {"unit_name": name, "revision": revision}


def _running_banner_name(doc, source: str, pages: list[tuple[int, str]]) -> str:
    """The bare-name running banner for units that omit the ``Unit NN -`` title prefix.

    The unit-name banner is the one non-bold, section-sized line that repeats on every
    page; a genuine section heading is the same size but appears once and maps to a
    ``content_type``. So the banner is the most-repeated section-sized non-bold line that
    is neither manual-banner / ``Unit NN`` chrome nor a known section heading. Requiring it
    on >= 2 pages keeps a one-off large line from being mistaken for a title; if nothing
    repeats, the name stays empty rather than guessing wrong.
    """
    counts: Counter[str] = Counter()
    for idx, _label in pages:
        seen: set[str] = set()
        for line in _page_lines(doc[idx]):
            text = line.text.strip()
            if line.bold or line.size < _SECTION_MIN_SIZE or text in seen:
                continue
            if _normalize_header(text) in _BANNER_TEXTS or _UNIT_CHROME_RE.fullmatch(text):
                continue
            if _content_type(source, text) is not None:
                continue
            seen.add(text)
            counts[text] += 1
    if counts:
        text, pages_seen = counts.most_common(1)[0]
        if pages_seen >= 2:
            return text
    return ""


def _frontmatter(source: str, token: str, meta: dict[str, str]) -> str:
    return (
        "---\n"
        f"source: {source}\n"
        f"unit: {token}\n"
        f"unit_name: {meta['unit_name']}\n"
        f'revision: "{meta["revision"]}"\n'
        "---\n"
    )


# Sections that print at sub-section (~12pt) size, so font size alone can't tell them
# from an inline bold sub-heading — recognised by this curated per-Source vocabulary
# instead. It is deliberately *only* the original Solo section vocabulary, NOT the whole
# (expanded) header dictionary: the GPC headers added later are mapped for their
# content_type but all print at >= 13pt, so they are detected by size and must not promote
# a same-named sub-section-size sub-heading — e.g. the Trainer's 12pt "Briefing" in Unit
# 13A, vs the 14pt "BRIEFING" section in Unit 24 / GPC Unit 33 — into a section.
_SUBSECTION_SECTIONS: dict[str, frozenset[str]] = {
    "trainer": frozenset({
        "AIM", "PREREQUISITE UNITS", "COMPLEMENTARY UNITS", "KEY MESSAGES",
        "LESSON PLANNING AND CONDUCT", "PRE-FLIGHT BRIEFING", "FLIGHT EXERCISES",
        "THREAT AND ERROR MANAGEMENT", "COMMON PROBLEMS",
        "COMPETENCY ELEMENTS AND PERFORMANCE STANDARDS",
        "TRAINING MATERIALS AND REFERENCES",
    }),
    "pilot": frozenset({
        "WHAT THIS UNIT IS ABOUT", "WHAT ARE THE PRE-REQUISITES FOR THIS UNIT?",
        "KEY MESSAGES", "PILOT GUIDE FOR THIS UNIT", "FLIGHT EXERCISES FOR THIS UNIT",
        "THINGS YOU MIGHT HAVE DIFFICULTY WITH", "HOW DO YOU DEMONSTRATE COMPETENCE?",
        "RESOURCES & REFERENCES", "COMPLEMENTARY UNITS", "SELF-CHECK QUESTIONS",
    }),
}


def _is_section_header(line: Line, source: str) -> bool:
    # Two signals, either sufficient: the large banner font (Pilot, and the 14pt Trainer
    # sections) OR membership in the curated sub-section-size vocabulary (the Trainer
    # prints FLIGHT EXERCISES / PRE-FLIGHT BRIEFING at 12pt — sub-heading size — so font
    # alone can't tell them from "Classroom Briefing"; only the vocabulary can).
    if line.size >= _SECTION_MIN_SIZE:
        if line.bold:
            return True
        # A non-bold section-sized line is normally the title-page unit name (chrome), but
        # Trainer Units 8 & 11 print some/all section headings in non-bold 14pt Sylfaen
        # rather than bold Helvetica. The header dictionary disambiguates: the unit name is
        # never in it, a real section heading always is.
        return _content_type(source, line.text) is not None
    return line.bold and _normalize_header(line.text) in _SUBSECTION_SECTIONS.get(source, frozenset())


# Sub-section labels a Source prints WITHOUT bold (the Pilot "COMMON PROBLEMS" inside
# "THINGS YOU MIGHT HAVE DIFFICULTY WITH" is a non-bold ~12pt line — body-plus-two, but
# the author left it unbolded). Font alone reads them as prose, so they are promoted to
# ### sub-headings by this curated per-Source vocabulary. Kept deliberately minimal: only
# labels seen unbolded that must still render as headings.
_NONBOLD_SUBHEADINGS: dict[str, frozenset[str]] = {
    "pilot": frozenset({"COMMON PROBLEMS"}),
}


def _is_subheading(line: Line, source: str = "") -> bool:
    # A whole-line-bold label below section size. An inline bold fragment inside a
    # sentence does not qualify: its line also carries non-bold spans, so Line.bold
    # (all spans bold) is False. A curated non-bold label (Pilot "COMMON PROBLEMS") is
    # promoted too, when below section size.
    if line.size >= _SECTION_MIN_SIZE:
        return False
    if line.bold:
        return True
    return _normalize_header(line.text) in _NONBOLD_SUBHEADINGS.get(source, frozenset())


def _is_reference_patter(line: Line) -> bool:
    """Whether a block's leading line is a ``Suggested Patter:`` heading."""
    return line.bold and _normalize_header(line.text).rstrip(":") == "SUGGESTED PATTER"


_BULLET_GLYPHS = ("•", "●")  # Pilot prints "•" (SymbolMT); Trainer prints "●" (Calibri)
_SUBBULLET_GLYPH = "o"
# A numbered-list marker printed as its own line — a bare "1." / "2." token (Pilot Unit 11
# SELF-CHECK and the centring-technique list). Only a standalone token qualifies: an inline
# "1. What is …" line keeps its number as prose (Unit 6 self-check), so it is left untouched.
# Capped at two digits so a paragraph that wraps a year or phone fragment onto its own line
# ("2010.", "306630.") is not mistaken for a list marker — real list ordinals are 1..99.
_ORDERED_MARKER_RE = re.compile(r"\d{1,2}\.")


def _marker_kind(line: Line) -> str | None:
    """Classify a block's leading line as a list marker, by glyph font or lead glyph.

    The glyph is usually its own line (marker line + wrapped text), but a short item
    can print the glyph inline (``● Theory Lesson 2``); both forms are recognised. A bare
    numeric token (``1.``) is an ordered-list marker whose number is kept as the rendered
    prefix.
    """
    text = line.text
    if line.font == "SymbolMT" or any(text == g or text.startswith(g + " ") for g in _BULLET_GLYPHS):
        return "bullet"
    if line.font == "CourierNewPSMT" or text == _SUBBULLET_GLYPH:
        return "subbullet"
    if _ORDERED_MARKER_RE.fullmatch(text):
        return "ordered"
    return None


def _marker_body(block: list[Line]) -> str:
    """The text of a list item, with any leading inline glyph stripped."""
    head = block[0].text
    for glyph in (*_BULLET_GLYPHS, _SUBBULLET_GLYPH):
        if head == glyph:
            head = ""
        elif head.startswith(glyph + " "):
            head = head[len(glyph) + 1:]
    return " ".join(part for part in (head, *(line.text for line in block[1:])) if part)


# One line of the footer band: "Revision 1.0" (Trainer: "Revision: 1.0"), the bare
# month-year date, or the "Page 37-1" Citation.
_FOOTER_LINE_RE = re.compile(
    r"Revision:?\s+[0-9.]+"
    r"|(January|February|March|April|May|June|July|August|September|October|November"
    r"|December)\s+\d{4}"
    r"|Page\s*\d+\s*[A-Z]?\s*-\s*\d+"
)


def _is_footer_block(block: list[Line]) -> bool:
    """Whether a block is the page-footer band (Revision / date / Page Citation).

    Normally recognised by its ~8pt size, but the Pilot GPC Unit 38 pages — the same
    pasted-in pages whose printed footers mislabel the unit (see
    :data:`_FOOTER_CORRECTIONS`) — print the footer at 9pt, inside the body-size range,
    so the band is also recognised by its text: a block whose *every* line is footer
    text. Body prose never forms such a block, so real content is untouched.
    """
    return (all(line.size <= _FOOTER_MAX_SIZE for line in block)
            or all(_FOOTER_LINE_RE.fullmatch(line.text) for line in block))


# Element = (kind, text). Kinds: h1, marker, section (text="## H\n<!-- ... -->"),
# subheading, paragraph, bullet, subbullet.
Element = tuple[str, str]


# A running-title line: "Unit 30", "Unit 13A - Launch …", "Unit 20 S - …". Stripped
# wherever it appears — large + bold on a title page, banner-sized + non-bold mid-content.
_UNIT_CHROME_RE = re.compile(r"Unit\s+\d+\s*[A-Z]?\s*(?:[-–].*)?")

# Labels scattered around an in-diagram image cluster, each its own body-sized block, so
# they pass every font filter and read out as garbled prose (Pilot Unit 5 page 5-3's
# "control → surface → force → rotation" figure). There is no safe corpus-wide geometric
# signal — real prose and captions also overlay images elsewhere — so these are suppressed
# by a curated set scoped per ``(source, unit token)``. The scope is essential: generic
# fragments like "in"/"that" must not suppress real text in other units. The diagram
# graphic itself is not captured, so dropping its labels loses no rendered content.
_FIGURE_LABELS: dict[tuple[str, str], frozenset[str]] = {
    ("pilot", "5"): frozenset({
        "Rotation of", "Control Movement", "Control Surface Position",
        "Force on an aircraft axis of", "aircraft around an", "that",
        "resulting", "changes", "rotation", "in", "creates", "axis.",
    }),
    # Solo 9 page 9-3: distance labels on the circuit diagram, printed at 15.8pt.
    ("pilot", "9"): frozenset({"1.5 Km", "700", "m"}),
    # GPC 34 page 34-3: the outlanding mnemonic and check-list title inside the figure.
    ("pilot", "34"): frozenset({"WSSSSSS", "Field Selection Check list"}),
}


def _is_chrome_line(line: Line, source: str = "", first_page: bool = False) -> bool:
    """Whether a single line is running header/title chrome to strip wherever it sits.

    The Solo guides print the manual banner / unit title as their own block, but the GPC
    guides interleave them inside a content block (a running ``Unit NN`` title glued to the
    end of a paragraph). So chrome is judged per line, not per block: the manual banner,
    a ``Unit NN`` running title, or a banner-sized non-bold line *where chrome prints* —
    the top header band on any page (bare-name banners and their wrapped second-line
    fragments) or anywhere on the unit's first page (the large unit name, which sits
    mid-page). A banner-sized non-bold line in the *body* of a later page is genuine
    content (Pilot 25's closing callout) and survives; in-figure labels that also print
    that big are suppressed by :data:`_FIGURE_LABELS`, not by font size. List markers are
    never chrome — a bullet glyph occasionally prints at banner size and must survive.
    """
    if _marker_kind(line) is not None:
        return False
    if _normalize_header(line.text) in _BANNER_TEXTS:
        return True
    if _UNIT_CHROME_RE.fullmatch(line.text):
        return True
    # Body prose runs ~10-11pt, so the strip is keyed to section size — never the 11pt
    # running banner (caught by its exact text above) and never an 11pt body line glued
    # mid-block. A non-bold section-sized line that maps to a known section heading is
    # real content, not chrome (Trainer Units 8/11 print sections in non-bold 14pt
    # Sylfaen).
    return (not line.bold and line.size >= _SECTION_MIN_SIZE
            and _content_type(source, line.text) is None
            and (first_page or line.bbox[1] < _HEADER_BAND_MAX_Y0))


def _classify_block(
    block: list[Line], source: str, token: str, first_page: bool = False
) -> list[Element]:
    """Segment a page block into its rendered elements (chrome dropped).

    The Solo guides give one element per block (one bullet, one heading, one paragraph);
    only table cells pack several items. The GPC guides group far more loosely — multiple
    bullets per block, and a section heading glued onto the tail of the preceding paragraph
    or bullet — so a block is segmented line-by-line rather than classified by its head:

    * a list marker opens an item; the wrapped lines after it are its body, until the next
      marker or heading;
    * consecutive section-heading lines join into one ``##`` (a heading that wraps across
      two lines, e.g. "TRAINING NOTES … FOR POWERED / SAILPLANE PILOTS"); a section heading
      absent from the dictionary raises rather than emit ``content_type: None``;
    * consecutive bold sub-heading lines join into one ``###`` (the wrapped Problem /
      Probable Cause label);
    * Reference Patter (``Suggested Patter:``) fences a ``####`` block (ADR 0001);
    * everything else accumulates into a paragraph.

    On a Solo block (a single item/heading/paragraph) this yields exactly the one element
    the old head-based classifier did, so the committed Solo Markdown is unchanged.
    """
    if _is_footer_block(block):
        return []  # footer chrome (Revision / date / Page) — already parsed
    # A scattered in-diagram label is its own block whose lines join to a curated, unit-
    # scoped phrase ("Control" + "Movement" -> "Control Movement"); drop the whole block.
    if " ".join(line.text for line in block) in _FIGURE_LABELS.get((source, token), frozenset()):
        return []

    elements: list[Element] = []
    para: list[str] = []
    item: list[str] | None = None
    item_kind: str | None = None
    heading: list[str] | None = None
    heading_kind: str | None = None  # "section" | "subheading"

    def flush_para() -> None:
        nonlocal para
        if para:
            elements.append(("paragraph", " ".join(para)))
            para = []

    def flush_item() -> None:
        nonlocal item, item_kind
        if item is not None:
            elements.append((item_kind, " ".join(item)))
            item, item_kind = None, None

    def flush_heading() -> None:
        nonlocal heading, heading_kind
        if heading is None:
            return
        text = " ".join(heading)
        if heading_kind == "section":
            role = _content_type(source, text)
            if role is None:
                raise UnitStructureError(
                    f"unmapped section heading {text!r} in the {source} guide: it is "
                    f"section-sized but absent from the header dictionary, so it would emit "
                    f"'content_type: None' — add it to HEADER_DICTIONARY['{source}'] with its "
                    f"content_type role (or it is chrome that must be stripped)"
                )
            elements.append(("section", f"## {text}\n<!-- content_type: {role} -->"))
        else:
            elements.append(("subheading", "### " + text))
        heading, heading_kind = None, None

    for line in block:
        if _is_chrome_line(line, source, first_page):
            continue
        kind = _marker_kind(line)
        if kind:  # a list item opens here; later wrapped lines are its body
            flush_heading()
            flush_para()
            flush_item()
            body = _marker_body([line])
            item, item_kind = ([body] if body else []), kind
            continue
        if _is_reference_patter(line):
            flush_heading()
            flush_para()
            flush_item()
            elements.append(("patter", "#### Suggested Patter\n<!-- content_type: reference_patter -->"))
            continue
        if _is_section_header(line, source):
            flush_para()
            flush_item()
            # Join only a genuine wrap: when the open heading is not yet a complete (mapped)
            # section, this line continues it ("TRAINING NOTES … FOR POWERED" + "SAILPLANE
            # PILOTS"). When the open heading already maps, this line is the next section
            # ("LESSON PLANNING AND CONDUCT" then "Briefing", glued in one block) — flush.
            if heading_kind == "section" and _content_type(source, " ".join(heading)) is None:
                heading.append(line.text)
            else:
                flush_heading()
                heading, heading_kind = [line.text], "section"
            continue
        if _is_subheading(line, source):
            flush_para()
            # A bare ordered marker ("1.") immediately followed by a bold sub-heading is a
            # *numbered heading* ("1." + "Awareness" → "### 1. Awareness"), not a list whose
            # first item is a heading — the number belongs to the heading. (A number followed
            # by non-bold prose, as in a SELF-CHECK list, stays an ordered item.) Carry the
            # pending number into the heading instead of flushing it as its own item.
            number = item[0] if item_kind == "ordered" and len(item) == 1 else None
            if number is not None:
                item, item_kind = None, None
            else:
                flush_item()
            if heading_kind == "subheading":
                if number is not None:
                    heading.append(number)
                heading.append(line.text)
            else:
                flush_heading()
                heading = [number, line.text] if number is not None else [line.text]
                heading_kind = "subheading"
            continue
        # plain text: the body of an open list item, else paragraph prose
        if item is not None:
            item.append(line.text)
        else:
            flush_heading()
            para.append(line.text)

    flush_item()
    flush_para()
    flush_heading()
    return elements


# Kinds that stay visually tight against a preceding list/marker. A page marker is
# here so a page break falling mid-list doesn't split the list with blank lines.
_TIGHT_KINDS = {"bullet", "subbullet", "ordered", "marker"}


def _blank_between(prev: str, cur: str) -> bool:
    """Whether a blank line separates a *prev*-kind element from a *cur*-kind one."""
    if cur in ("section", "subheading", "paragraph", "h1", "patter"):
        return True
    if cur in _TIGHT_KINDS:  # tight when continuing a list; blank when one starts fresh
        return prev not in _TIGHT_KINDS
    return False


def _render_element(kind: str, text: str) -> str:
    if kind == "marker":
        return f"<!-- page: {text} -->"
    if kind == "bullet":
        return f"- {text}"
    if kind == "subbullet":
        return f"  - {text}"
    return text  # h1, section, subheading, paragraph carry their own markup


def _assemble(elements: list[Element]) -> str:
    out = ""
    prev: str | None = None
    for kind, text in elements:
        if prev is not None:
            out += "\n\n" if _blank_between(prev, kind) else "\n"
        out += _render_element(kind, text)
        prev = kind
    return out + "\n"


# --- Table strategy (settled by the extractor bake-off + golden review) ------------
# Both ruled tables in Unit 5 are two-column reference tables (ELEMENT / PERFORMANCE
# STANDARDS and Problem / Probable Cause). A GFM grid buries which side is which and
# (for the competency table) crushes the nested ●/o standards into one cell, so both
# are rendered in reading order with the left column as a ### sub-heading and the right
# column as its bullets. pdfplumber.find_tables() only locates the ruled regions; the
# left/right split, header drop, and bullet structure come from PyMuPDF block geometry.
# A single-column list mis-detected as a grid (KEY MESSAGES) resolves to one column and
# falls through to the normal prose/bullet path.

_COLUMN_GAP_MIN = 50.0  # min x-gap (pt) between two real table columns
# Max y-gap (pt) from one left-column block to the next for them to be one wrapped label
# rather than two table rows. A wrap's continuation abuts the line above (gap ~0-1pt);
# distinct rows/labels are separated by the inter-row spacing (>=8pt observed).
_WRAP_GAP_MAX = 5.0
# Column-header rows to drop (their labels become the structure, not content). Matched
# on the whole header block, whose two labels PyMuPDF joins onto one line. The Pilot
# COMMON PROBLEMS tables head the right column variously (Solution / Solutions / Actions
# required) where the Trainer uses Probable Cause.
_TABLE_HEADER_TEXTS = {
    "ELEMENT PERFORMANCE STANDARDS",
    "ELEMENT PERFORMANCE STANDARD",  # GPC Trainer 34/36 print the singular form
    "PROBLEM PROBABLE CAUSE",
    "PROBLEM SOLUTION",
    "PROBLEM SOLUTIONS",
    "PROBLEM ACTIONS REQUIRED",
}


def _page_blocks_in(blocks: list[tuple[BBox, list[Line]]], bbox: BBox) -> list[tuple[BBox, list[Line]]]:
    """Those *blocks* whose vertical centre lies within *bbox*, in reading order."""
    top, bottom = bbox[1], bbox[3]
    return [(bb, ls) for bb, ls in blocks if top - 1 <= (bb[1] + bb[3]) / 2 <= bottom + 1]


def _block_text(lines: list[Line]) -> str:
    """A block's text: a list item's body (glyph stripped) or its lines joined."""
    return _marker_body(lines) if _marker_kind(lines[0]) else " ".join(l.text for l in lines)


def _split_list_items(block: list[Line]) -> list[Element]:
    """Split a block into one element per list marker.

    Most list blocks hold a single item, but a table cell can pack several (the first
    competency standards cell is one block of six ``o`` sub-bullets); each marker line
    opens a new item and the lines after it are its wrapped text.
    """
    items: list[tuple[str, list[str]]] = []
    for line in block:
        kind = _marker_kind(line)
        if kind:
            head = line.text
            for glyph in (*_BULLET_GLYPHS, _SUBBULLET_GLYPH):
                if head == glyph:
                    head = ""
                elif head.startswith(glyph + " "):
                    head = head[len(glyph) + 1:]
            items.append((kind, [head] if head else []))
        elif items:
            items[-1][1].append(line.text)
        else:  # text before any marker — keep it rather than drop
            items.append(("paragraph", [line.text]))
    return [(kind, " ".join(parts)) for kind, parts in items]


def _split_glued_bullets(lines: list[Line]) -> tuple[list[Line], list[Line]]:
    """Split a left-column block that swallowed the start of its right-column bullets.

    When an ELEMENT label is short enough to share a visual row with the first ``●``
    bullet of its PERFORMANCE STANDARDS (Trainer Unit 1 "Lookout Priority", Unit 3
    "Inspect the aircraft"), PyMuPDF groups the label and that bullet into one left-column
    block — ``1. Lookout Priority ● Describe:``. Split at the first ●/o glyph so the leading
    lines stay the row label and the glyph-onward lines render as its bullets. A normal
    label (no embedded glyph, because its bullets wrap to their own block) is returned
    unchanged. The ``i and`` guard keeps the label line itself (a numbered title never opens
    with a glyph) from being mistaken for a bullet.
    """
    for i, line in enumerate(lines):
        if i and _marker_kind(line) in ("bullet", "subbullet"):
            return lines[:i], lines[i:]
    return lines, []


def _lines_bbox(lines: list[Line]) -> BBox:
    """The bounding box enclosing *lines* — the union of their per-line bboxes."""
    return (
        min(l.bbox[0] for l in lines),
        min(l.bbox[1] for l in lines),
        max(l.bbox[2] for l in lines),
        max(l.bbox[3] for l in lines),
    )


def _column_runs(lines: list[Line], split: float) -> list[tuple[BBox, list[Line]]]:
    """Re-segment a block's lines into maximal runs that sit on one side of *split*.

    PyMuPDF sometimes groups a table row's left (label) cell and right (body) cell into a
    single block — ``● Speed change is very slow.`` glued onto its Probable Cause prose —
    even though each visual line keeps its own column's x. Splitting by line x recovers the
    two cells as separate ``(bbox, lines)`` sub-blocks, so the row renders as a heading plus
    its body rather than one glued heading. A block already wholly in one column yields a
    single run identical to the input, so a table PyMuPDF did *not* merge is untouched.
    """
    runs: list[list[Line]] = []
    side: bool | None = None
    for line in lines:
        left = line.bbox[0] < split
        if not runs or left != side:
            runs.append([])
            side = left
        runs[-1].append(line)
    return [(_lines_bbox(run), run) for run in runs]


@dataclass(frozen=True)
class TableRegion:
    """A ruled two-column table, pre-rendered to reading-order elements."""

    bbox: BBox
    elements: tuple[Element, ...]


def _two_column_region(
    page_blocks: list[tuple[BBox, list[Line]]], bbox: BBox, source: str
) -> TableRegion | None:
    """Render a ruled region as left-column sub-headings + right-column bullets.

    Returns ``None`` when the region is really one column (a mis-detected bullet list).
    A block that is entirely a *sub-section-sized* section heading (the curated 12pt
    vocabulary, not the 14pt banner font) is evicted first and the region top is trimmed
    below it: in Trainer 34 / 20S the ruled box extends up over the 12pt bold "COMMON
    PROBLEMS" heading, which would otherwise render as a left-column ``###`` row label
    with no ``content_type`` — trimmed out, the block falls outside the region and takes
    :func:`_classify_block`'s normal section path. A banner-sized heading inside the box
    is *not* evicted: that is the Pilot 14S box title (14pt bold "COMMON PROBLEMS"
    printed inside the ruled COMMON PROBLEMS table, under its real "THINGS YOU MIGHT
    HAVE DIFFICULTY WITH" section), which deliberately stays a ``###`` label.
    Consecutive left-column blocks merge into one heading only when the next one *abuts*
    the previous — a true wrap, where a label spills onto a second block on the very next
    text line ("1. Effects of controls –" + "general"; or the Trainer 13A label whose
    continuation even flips to bold, "1. Conduct an aerotow" + "glider launch above …").
    A left block separated by a row gap is a *distinct* label, not a continuation, so the
    open heading is flushed first: this keeps the Pilot COMMON PROBLEMS tables (a non-bold
    "COMMON PROBLEMS" title, then the "Problem | Actions required" header row, then the
    first problem label, all stacked above the first bullet) from collapsing into one run.
    Right-column blocks split into their bullets and attach to the open heading; the
    column-header row is dropped.

    The column split is taken from *line* x-positions, not block x0: PyMuPDF sometimes
    groups a row's left and right cells into one block (Trainer Unit 7's COMMON PROBLEMS —
    ``● Speed change is very slow.`` fused to its cause prose), and where *every* row is
    merged the block x0 collapses to one column and the table would be missed. Header rows
    are dropped whole — their two labels ("Problem"/"Probable Cause") may share a block —
    then each remaining block is re-segmented by column so a merged row splits back into its
    label and its body.
    """
    blocks = [
        (bb, lines) for bb, lines in _page_blocks_in(page_blocks, bbox)
        if not all(line.size < _SECTION_MIN_SIZE and _is_section_header(line, source)
                   for line in lines)
    ]
    if not blocks:
        return None
    bbox = (bbox[0], min(bb[1] for bb, _lines in blocks), bbox[2], bbox[3])
    xs = sorted({round(line.bbox[0]) for _bb, lines in blocks for line in lines})
    gap, at = max(((xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)), default=(0, 0))
    if gap < _COLUMN_GAP_MIN:
        return None  # one column (or too little content) — not a two-column table
    split = (xs[at] + xs[at + 1]) / 2

    subblocks: list[tuple[BBox, list[Line]]] = []
    for bb, lines in blocks:
        if _normalize_header(_block_text(lines)) in _TABLE_HEADER_TEXTS:
            continue  # column-header row (its two labels may share one block)
        subblocks.extend(_column_runs(lines, split))

    elements: list[Element] = []
    heading: list[str] = []
    last_bottom: float | None = None  # y-bottom of the last block merged into the heading
    flushed = False

    def flush() -> None:
        nonlocal flushed
        if heading and not flushed:
            elements.append(("subheading", "### " + " ".join(heading)))
            flushed = True

    for bb, lines in subblocks:
        if bb[0] < split:  # left column — (part of) a row label
            label_lines, bullet_lines = _split_glued_bullets(lines)
            if flushed:  # the previous row's bullets are done → start a new label
                heading, last_bottom, flushed = [], None, False
            elif heading and bb[1] - last_bottom > _WRAP_GAP_MAX:  # a row gap, not a wrap
                flush()
                heading, last_bottom, flushed = [], None, False
            heading.append(_block_text(label_lines))
            last_bottom = bb[3]
            if bullet_lines:  # a short label shared its row with the first ● bullet
                flush()
                elements.extend(_split_list_items(bullet_lines))
        else:  # right column — the label's body (● bullets, or a plain-prose cause)
            flush()
            elements.extend(_split_list_items(lines))
    flush()  # a trailing label with no right-column body
    return TableRegion(bbox, tuple(elements)) if elements else None


def _contains(outer: BBox, inner: BBox) -> bool:
    return (outer[0] <= inner[0] + 1 and outer[1] <= inner[1] + 1
            and outer[2] >= inner[2] - 1 and outer[3] >= inner[3] - 1)


def _table_regions(
    plumber_page, page_blocks: list[tuple[BBox, list[Line]]], source: str
) -> list[TableRegion]:
    """Two-column table regions on a page (overlapping ruled boxes deduped to the outer)."""
    boxes = [tuple(t.bbox) for t in plumber_page.find_tables()]
    outer = [b for b in boxes if not any(o != b and _contains(o, b) for o in boxes)]
    return [r for b in outer if (r := _two_column_region(page_blocks, b, source))]


def _region_for(block_bbox: BBox, regions: list[TableRegion]) -> TableRegion | None:
    """The table region whose bbox vertically contains *block_bbox*'s centre."""
    centre_y = (block_bbox[1] + block_bbox[3]) / 2
    for region in regions:
        if region.bbox[1] - 1 <= centre_y <= region.bbox[3] + 1:
            return region
    return None


def _emit_page(
    page_blocks: list[tuple[BBox, list[Line]]],
    regions: list[TableRegion],
    source: str,
    token: str,
    first_page: bool = False,
) -> list[Element]:
    """Classify a page's blocks into elements in true reading order.

    PyMuPDF's block order is not strictly geometric — a section box lower on the page can be
    emitted before the prose above it (Pilot Unit 11's KEY MESSAGES box, Unit 7's "Feel"
    sub-section), which scrambles section order. So the blocks are re-ordered top-to-bottom
    (then left-to-right) here. A two-column table is the one structure that must *not* be
    re-ordered — its left/right column blocks interleave by y, and sorting would split a
    wrapped row label or shuffle its rows — so each detected region is treated as a single
    indivisible unit anchored at its bbox top, keeping the natural intra-region order that
    :func:`_two_column_region` already laid out.
    """
    # (y, x, payload): payload is a region (emit its pre-rendered elements) or a block.
    units: list[tuple[float, float, TableRegion | list[Line]]] = []
    seen: set[int] = set()
    for bbox, lines in page_blocks:
        region = _region_for(bbox, regions)
        if region is not None:
            if id(region) not in seen:
                seen.add(id(region))
                units.append((region.bbox[1], region.bbox[0], region))
            continue
        units.append((bbox[1], bbox[0], lines))
    units.sort(key=lambda u: (round(u[0]), round(u[1])))

    elements: list[Element] = []
    for _y, _x, payload in units:
        if isinstance(payload, TableRegion):
            elements.extend(payload.elements)
        else:
            elements.extend(_classify_block(payload, source, token, first_page))
    return elements


def render_unit_markdown(pdf_path, source: str, unit: int | str) -> str:
    """Render the Markdown for a single ``(source, unit)`` of *pdf_path*.

    *unit* is a plain number (``5``) or a variant sub-unit token (``"13A"``); both run
    through this one seam. The variant letter flows into the page Citations (``13A-1``),
    the frontmatter ``unit:`` field, and the H1.
    """
    number, variant = _parse_unit_id(unit)
    token = f"{number}{variant}"
    doc = fitz.open(pdf_path)
    plumber = pdfplumber.open(pdf_path)
    try:
        pages = _resolve_unit_pages(doc, source, number, variant)
        meta = _unit_metadata(doc, source, number, variant, pages)

        elements: list[Element] = [("h1", f"# Unit {token} — {meta['unit_name']}")]
        for idx, label in pages:
            elements.append(("marker", label))
            page_blocks = _page_blocks_with_bbox(doc[idx])
            regions = _table_regions(plumber.pages[idx], page_blocks, source)
            elements.extend(
                _emit_page(page_blocks, regions, source, token, first_page=idx == pages[0][0])
            )
        return _frontmatter(source, token, meta) + "\n" + _assemble(elements)
    finally:
        plumber.close()
        doc.close()


def write_unit_markdown(pdf_path, source: str, unit: int | str, out_root="corpus/md") -> Path:
    """Render a unit and persist it to ``<out_root>/<source>/unit-NN.md``.

    Thin batch wrapper around :func:`render_unit_markdown`. A variant sub-unit lands at
    ``unit-13A.md`` (zero-padded number + variant letter). Writes LF line endings so
    re-running on an unchanged PDF yields a byte-identical file on every platform.
    """
    number, variant = _parse_unit_id(unit)
    out_path = Path(out_root) / source / f"unit-{number:02d}{variant}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_unit_markdown(pdf_path, source, unit), encoding="utf-8", newline="\n")
    return out_path


@dataclass(frozen=True)
class UnitOutcome:
    """The result of attempting one ``(source, unit)`` in a corpus batch.

    Exactly one of ``path`` / ``error`` is set: ``path`` for a written file, ``error``
    for a unit skipped because its structure couldn't be faithfully rendered.
    """

    source: str
    unit: int | str  # a plain number, or a variant token like "13A"
    path: Path | None
    error: str | None


@dataclass(frozen=True)
class CorpusReport:
    """Outcome of a corpus batch — what got written and what was skipped (with reasons)."""

    outcomes: tuple[UnitOutcome, ...]

    @property
    def written(self) -> tuple[UnitOutcome, ...]:
        return tuple(o for o in self.outcomes if o.path is not None)

    @property
    def skipped(self) -> tuple[UnitOutcome, ...]:
        return tuple(o for o in self.outcomes if o.path is None)


def write_corpus(
    sources: Mapping[str, str | Path],
    out_root: str | Path = "corpus/md",
    units: Iterable[int] = range(1, 27),
) -> CorpusReport:
    """Render every ``(source, unit)`` through the single seam and emit the clean tree.

    Both Source PDFs run through one pipeline. A variant-split unit (13/14/20) is expanded
    into its A/S/W sub-units — discovered from the footer scan, not hard-coded — and each
    variant is emitted to its own ``unit-13A.md`` through the same seam. Units whose
    structure can't be faithfully rendered (absent, non-consecutive) raise
    :class:`UnitStructureError`; those are *collected* as skips with their reason rather
    than aborting the batch, so a re-runnable, diffable ``corpus/md/<source>/unit-NN.md``
    tree is produced for the ADR-0002 human-verification gate while Citation errors stay
    visible.
    """
    outcomes: list[UnitOutcome] = []
    for source, pdf_path in sources.items():
        doc = fitz.open(pdf_path)
        try:
            footers = _scan_footers(doc, source)
        finally:
            doc.close()
        for unit in units:
            letters = _variant_letters(footers, unit)
            identities = [f"{unit}{v}" for v in letters] if letters else [unit]
            for identity in identities:
                try:
                    path = write_unit_markdown(pdf_path, source, identity, out_root=out_root)
                    outcomes.append(UnitOutcome(source, identity, path, None))
                except UnitStructureError as exc:
                    outcomes.append(UnitOutcome(source, identity, None, str(exc)))
    return CorpusReport(tuple(outcomes))
