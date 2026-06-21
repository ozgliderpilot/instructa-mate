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
from dataclasses import dataclass
from pathlib import Path

import fitz
import pdfplumber

FOOTER_RE = re.compile(r"Page\s*(\d+)\s*-\s*(\d+)")
REVISION_RE = re.compile(r"Revision:?\s+([0-9.]+)")  # Trainer prints "Revision: 1.0", Pilot "Revision 1.0"
UNIT_TITLE_RE = re.compile(r"Unit\s+\d+\s*[-–]?\s*(.+)")

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
    },
    "trainer": {
        "AIM": "aim",
        "PREREQUISITE UNITS": "admin",
        "COMPLEMENTARY UNITS": "admin",
        "KEY MESSAGES": "key_messages",
        "LESSON PLANNING AND CONDUCT": "briefing",
        "PRE-FLIGHT BRIEFING": "briefing",
        "FLIGHT EXERCISES": "exercise",
        "THREAT AND ERROR MANAGEMENT": "airmanship",
        "COMMON PROBLEMS": "common_problems",
        "COMPETENCY ELEMENTS AND PERFORMANCE STANDARDS": "competency",
        "TRAINING MATERIALS AND REFERENCES": "admin",
        # Per-control theory headers (``Use of Elevator`` …) are intentionally absent:
        # they render as ``###`` sub-exercise headings, not sections.
    },
}


def _normalize_header(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


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


def _unit_pages(doc, unit: int) -> list[tuple[int, str]]:
    """Physical pages of *unit* as ``(page_index, "U-P")``, in order.

    Footer page numbers are read with a lenient regex. The footer-less first page of
    a unit (``Page U-1``, whose glyphs don't map in any extractor) is *inferred* as
    the page immediately before ``Page U-2``.
    """
    footers: dict[int, tuple[int, int]] = {}
    for i in range(doc.page_count):
        m = FOOTER_RE.search(doc[i].get_text("text"))
        if m:
            footers[i] = (int(m.group(1)), int(m.group(2)))

    unit_phys = sorted(i for i, (u, _p) in footers.items() if u == unit)
    if not unit_phys:
        return []

    pages: list[tuple[int, str]] = []
    if footers[unit_phys[0]][1] == 2:  # Page U-1 footer unreadable -> infer it
        pages.append((unit_phys[0] - 1, f"{unit}-1"))
    pages.extend((i, f"{u}-{p}") for i in unit_phys for (u, p) in [footers[i]])
    return pages


def _unit_metadata(doc, unit: int, pages: list[tuple[int, str]]) -> dict[str, str]:
    """Pull ``unit_name`` and ``revision`` from the unit's running banner/footer."""
    name, revision = "", ""
    for idx, _label in pages:
        for line in _page_lines(doc[idx]):
            if not name and not line.bold:
                m = UNIT_TITLE_RE.fullmatch(line.text)
                if m:
                    name = m.group(1).strip()
            if not revision:
                m = REVISION_RE.search(line.text)
                if m:
                    revision = m.group(1)
        if name and revision:
            break
    return {"unit_name": name, "revision": revision}


def _frontmatter(source: str, unit: int, meta: dict[str, str]) -> str:
    return (
        "---\n"
        f"source: {source}\n"
        f"unit: {unit}\n"
        f"unit_name: {meta['unit_name']}\n"
        f'revision: "{meta["revision"]}"\n'
        "---\n"
    )


def _is_section_header(line: Line, source: str) -> bool:
    # Two signals, either sufficient: the large banner font (Pilot, and the 14pt
    # Trainer sections) OR a dictionary match (the Trainer prints FLIGHT EXERCISES /
    # PRE-FLIGHT BRIEFING at 12pt — sub-heading size — so font alone can't tell them
    # from "Classroom Briefing"; only the curated vocabulary can).
    if not line.bold:
        return False
    return line.size >= _SECTION_MIN_SIZE or _content_type(source, line.text) is not None


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


def _marker_kind(line: Line) -> str | None:
    """Classify a block's leading line as a list marker, by glyph font or lead glyph.

    The glyph is usually its own line (marker line + wrapped text), but a short item
    can print the glyph inline (``● Theory Lesson 2``); both forms are recognised.
    """
    text = line.text
    if line.font == "SymbolMT" or any(text == g or text.startswith(g + " ") for g in _BULLET_GLYPHS):
        return "bullet"
    if line.font == "CourierNewPSMT" or text == _SUBBULLET_GLYPH:
        return "subbullet"
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


def _classify_block(block: list[Line], source: str) -> Element | None:
    """Map a page block to a rendered element, or ``None`` for stripped chrome."""
    head = block[0]
    if all(line.size <= _FOOTER_MAX_SIZE for line in block):
        return None  # footer chrome (Revision / date / Page) — already parsed
    if _normalize_header(head.text) in _BANNER_TEXTS:
        return None  # running banner, even where it prints large + bold (title page)

    kind = _marker_kind(head)
    if kind:  # list item: marker (own line or inline) + any wrapped continuation lines
        # Tested before the banner-size strip below: a stray bullet glyph occasionally
        # prints a touch larger (one KEY MESSAGES "●" is 11pt) and would otherwise be
        # mistaken for the non-bold running banner and the whole bullet dropped.
        return (kind, _marker_body(block))
    if not head.bold and head.size >= _BANNER_MIN_SIZE:
        return None  # running header banner (manual title / unit title)
    if _is_reference_patter(head):
        # The manual's "Suggested Patter:" heading = Reference Patter (verbatim,
        # authoritative), NOT the app's Generated Patter feature (ADR 0001). Fenced
        # as a #### sub-block and tagged; the bullets that follow are its content and
        # render on the normal list path.
        return ("patter", "#### Suggested Patter\n<!-- content_type: reference_patter -->")
    if _is_section_header(head, source):
        role = _content_type(source, head.text)
        return ("section", f"## {head.text}\n<!-- content_type: {role} -->")
    if _is_subheading(head):
        # Join all lines so a wrapped/two-column bold label (the Problem / Probable
        # Cause table header) keeps both parts instead of dropping the tail.
        return ("subheading", "### " + " ".join(line.text for line in block))
    return ("paragraph", " ".join(line.text for line in block))


# Kinds that stay visually tight against a preceding list/marker. A page marker is
# here so a page break falling mid-list doesn't split the list with blank lines.
_TIGHT_KINDS = {"bullet", "subbullet", "marker"}


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


def render_unit_markdown(pdf_path, source: str, unit: int) -> str:
    """Render the Markdown for a single ``(source, unit)`` of *pdf_path*."""
    doc = fitz.open(pdf_path)
    plumber = pdfplumber.open(pdf_path)
    try:
        pages = _unit_pages(doc, unit)
        meta = _unit_metadata(doc, unit, pages)

        elements: list[Element] = [("h1", f"# Unit {unit} — {meta['unit_name']}")]
        emitted: set[BBox] = set()
        for idx, label in pages:
            elements.append(("marker", label))
            page_blocks = _page_blocks_with_bbox(doc[idx])
            regions = _table_regions(plumber.pages[idx], page_blocks)
            for bbox, lines in page_blocks:
                region = _region_for(bbox, regions)
                if region is not None:  # block belongs to a two-column table
                    if region.bbox not in emitted:  # emit the whole table at its first block
                        elements.extend(region.elements)
                        emitted.add(region.bbox)
                    continue
                element = _classify_block(lines, source)
                if element is not None:
                    elements.append(element)
        return _frontmatter(source, unit, meta) + "\n" + _assemble(elements)
    finally:
        plumber.close()
        doc.close()


def write_unit_markdown(pdf_path, source: str, unit: int, out_root="corpus/md") -> Path:
    """Render a unit and persist it to ``<out_root>/<source>/unit-NN.md``.

    Thin batch wrapper around :func:`render_unit_markdown`. Writes LF line endings so
    re-running on an unchanged PDF yields a byte-identical file on every platform.
    """
    out_path = Path(out_root) / source / f"unit-{unit:02d}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_unit_markdown(pdf_path, source, unit), encoding="utf-8", newline="\n")
    return out_path
