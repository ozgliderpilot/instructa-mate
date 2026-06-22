"""Stage-1 ingestion parser: one (Source, Unit) of a GFA guide PDF -> verified Markdown.

The single public seam is :func:`render_unit_markdown`; :func:`write_unit_markdown`
is a thin batch wrapper that persists the result to the stable path convention
``corpus/md/<source>/unit-NN.md``. See ADR 0002 for the output contract.

PyMuPDF (``fitz``) is the only text layer (settled by the extractor bake-off — no
``pdftotext`` engine, no LLM fallback). We read ``get_text("dict")`` rather than
``get_text("text")``: it is the *same* PyMuPDF extraction enriched with the per-span
font/size/weight that lets section structure be detected deterministically instead of
by brittle "ALL-CAPS at col 0" text heuristics.
"""
from __future__ import annotations

import re
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


@dataclass(frozen=True)
class Line:
    """One visual line of a page, with the font signal used for classification."""

    text: str
    size: float
    bold: bool
    font: str


BBox = tuple[float, float, float, float]


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


def _scan_footers(doc) -> dict[int, tuple[int, str, int]]:
    """Every page with a readable footer, as ``page_index -> (unit, variant, page)``.

    ``variant`` is ``""`` for a plain numeric unit and the letter for a variant sub-unit
    (``"A"`` for the 13A footer ``Page 13A - 1``), so variant pages are recognised here
    rather than looking footer-less. One scan serves every structure decision below.
    """
    footers: dict[int, tuple[int, str, int]] = {}
    for i in range(doc.page_count):
        m = FOOTER_RE.search(doc[i].get_text("text"))
        if m:
            footers[i] = (int(m.group(1)), m.group(2) or "", int(m.group(3)))
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
    footers = _scan_footers(doc)
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


def _unit_metadata(doc, number: int, variant: str, pages: list[tuple[int, str]]) -> dict[str, str]:
    """Pull ``unit_name`` and ``revision`` from the unit's running banner/footer.

    The name regex is anchored to this unit's *known* number and variant rather than a
    bare ``Unit \\d+`` pattern, because the variant running header reads ``Unit 13S -
    Launch …`` (or, for Pilot 20S, ``Unit 20 S - …`` with a space). A generic optional
    ``[A-Z]?`` would swallow the first letter of an un-dashed plain name (``Unit 1
    Lookout Awareness``); anchoring to the resolved variant avoids that ambiguity.
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
    return {"unit_name": name, "revision": revision}


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
    if not line.bold:
        return False
    if line.size >= _SECTION_MIN_SIZE:
        return True
    return _normalize_header(line.text) in _SUBSECTION_SECTIONS.get(source, frozenset())


def _is_subheading(line: Line) -> bool:
    # A whole-line-bold label below section size. An inline bold fragment inside a
    # sentence does not qualify: its line also carries non-bold spans, so Line.bold
    # (all spans bold) is False.
    return line.bold and line.size < _SECTION_MIN_SIZE


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


# Element = (kind, text). Kinds: h1, marker, section (text="## H\n<!-- ... -->"),
# subheading, paragraph, bullet, subbullet.
Element = tuple[str, str]


# A running-title line: "Unit 30", "Unit 13A - Launch …", "Unit 20 S - …". Stripped
# wherever it appears — large + bold on a title page, banner-sized + non-bold mid-content.
_UNIT_CHROME_RE = re.compile(r"Unit\s+\d+\s*[A-Z]?\s*(?:[-–].*)?")


def _is_chrome_line(line: Line) -> bool:
    """Whether a single line is running header/title chrome to strip wherever it sits.

    The Solo guides print the manual banner / unit title as their own block, but the GPC
    guides interleave them inside a content block (a running ``Unit NN`` title glued to the
    end of a paragraph). So chrome is judged per line, not per block: the manual banner,
    a ``Unit NN`` running title, or any other banner-sized non-bold line (the large unit
    name on a title page). List markers are never chrome — a bullet glyph occasionally
    prints at banner size and must survive.
    """
    if _marker_kind(line) is not None:
        return False
    if _normalize_header(line.text) in _BANNER_TEXTS:
        return True
    if _UNIT_CHROME_RE.fullmatch(line.text):
        return True
    # The big unit name on a title page is the only other non-bold chrome; body prose runs
    # ~10-11pt, so the strip is keyed to section size — never the 11pt running banner (that
    # is caught by its exact text above) and never an 11pt body line glued mid-block.
    return not line.bold and line.size >= _SECTION_MIN_SIZE


def _classify_block(block: list[Line], source: str) -> list[Element]:
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
    if all(line.size <= _FOOTER_MAX_SIZE for line in block):
        return []  # footer chrome (Revision / date / Page) — already parsed

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
        if _is_chrome_line(line):
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
        if _is_subheading(line):
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
# Column-header rows to drop (their labels become the structure, not content). Matched
# on the whole header block, whose two labels PyMuPDF joins onto one line.
_TABLE_HEADER_TEXTS = {"ELEMENT PERFORMANCE STANDARDS", "PROBLEM PROBABLE CAUSE"}


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


@dataclass(frozen=True)
class TableRegion:
    """A ruled two-column table, pre-rendered to reading-order elements."""

    bbox: BBox
    elements: tuple[Element, ...]


def _two_column_region(page_blocks: list[tuple[BBox, list[Line]]], bbox: BBox) -> TableRegion | None:
    """Render a ruled region as left-column sub-headings + right-column bullets.

    Returns ``None`` when the region is really one column (a mis-detected bullet list).
    Consecutive left-column blocks merge into one heading (an ELEMENT label wraps onto a
    second block); right-column blocks split into their bullets and attach to the open
    heading; the column-header row is dropped.
    """
    blocks = _page_blocks_in(page_blocks, bbox)
    if len(blocks) < 2:
        return None
    xs = sorted({round(bb[0]) for bb, _ in blocks})
    gap, at = max(((xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)), default=(0, 0))
    if gap < _COLUMN_GAP_MIN:
        return None  # one column — not a two-column table
    split = (xs[at] + xs[at + 1]) / 2

    elements: list[Element] = []
    heading: list[str] = []
    flushed = False

    def flush() -> None:
        nonlocal flushed
        if heading and not flushed:
            elements.append(("subheading", "### " + " ".join(heading)))
            flushed = True

    for bb, lines in blocks:
        if _normalize_header(_block_text(lines)) in _TABLE_HEADER_TEXTS:
            continue  # column-header row
        if bb[0] < split:  # left column — (part of) a row label
            if flushed:  # the previous row's bullets are done → start a new label
                heading, flushed = [], False
            heading.append(_block_text(lines))
        else:  # right column — the label's bullets
            flush()
            elements.extend(_split_list_items(lines))
    flush()  # a trailing label with no right-column bullets
    return TableRegion(bbox, tuple(elements)) if elements else None


def _contains(outer: BBox, inner: BBox) -> bool:
    return (outer[0] <= inner[0] + 1 and outer[1] <= inner[1] + 1
            and outer[2] >= inner[2] - 1 and outer[3] >= inner[3] - 1)


def _table_regions(plumber_page, page_blocks: list[tuple[BBox, list[Line]]]) -> list[TableRegion]:
    """Two-column table regions on a page (overlapping ruled boxes deduped to the outer)."""
    boxes = [tuple(t.bbox) for t in plumber_page.find_tables()]
    outer = [b for b in boxes if not any(o != b and _contains(o, b) for o in boxes)]
    return [r for b in outer if (r := _two_column_region(page_blocks, b))]


def _region_for(block_bbox: BBox, regions: list[TableRegion]) -> TableRegion | None:
    """The table region whose bbox vertically contains *block_bbox*'s centre."""
    centre_y = (block_bbox[1] + block_bbox[3]) / 2
    for region in regions:
        if region.bbox[1] - 1 <= centre_y <= region.bbox[3] + 1:
            return region
    return None


def _emit_page(
    page_blocks: list[tuple[BBox, list[Line]]], regions: list[TableRegion], source: str
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
            elements.extend(_classify_block(payload, source))
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
        meta = _unit_metadata(doc, number, variant, pages)

        elements: list[Element] = [("h1", f"# Unit {token} — {meta['unit_name']}")]
        for idx, label in pages:
            elements.append(("marker", label))
            page_blocks = _page_blocks_with_bbox(doc[idx])
            regions = _table_regions(plumber.pages[idx], page_blocks)
            elements.extend(_emit_page(page_blocks, regions, source))
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
            footers = _scan_footers(doc)
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
