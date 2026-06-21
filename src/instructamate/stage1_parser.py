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

FOOTER_RE = re.compile(r"Page\s*(\d+)\s*-\s*(\d+)")
REVISION_RE = re.compile(r"Revision\s+([0-9.]+)")
UNIT_TITLE_RE = re.compile(r"Unit\s+\d+\s*[-–]?\s*(.+)")

_BOLD_FLAG = 1 << 4  # PyMuPDF span flag bit for bold
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
    "trainer": {},  # Populated in a later slice.
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


def _page_blocks(page) -> list[list[Line]]:
    """Group a page into PyMuPDF blocks of reading-order :class:`Line` records.

    A block is PyMuPDF's paragraph-ish grouping: a bullet (marker line + wrapped text
    lines), a wrapped paragraph, or a single heading line. Blank lines are dropped;
    empty blocks are omitted.
    """
    blocks: list[list[Line]] = []
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
            blocks.append(lines)
    return blocks


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


def _is_section_header(line: Line) -> bool:
    return line.bold and line.size >= _SECTION_MIN_SIZE


def _is_subheading(line: Line) -> bool:
    # A whole-line-bold label below section size. An inline bold fragment inside a
    # sentence does not qualify: its line also carries non-bold spans, so Line.bold
    # (all spans bold) is False.
    return line.bold and line.size < _SECTION_MIN_SIZE


def _marker_kind(line: Line) -> str | None:
    """Classify a block's leading line as a list marker, by glyph font."""
    if line.font == "SymbolMT" or line.text == "•":
        return "bullet"
    if line.font == "CourierNewPSMT" or line.text == "o":
        return "subbullet"
    return None


# Element = (kind, text). Kinds: h1, marker, section (text="## H\n<!-- ... -->"),
# subheading, paragraph, bullet, subbullet.
Element = tuple[str, str]


def _classify_block(block: list[Line], source: str) -> Element | None:
    """Map a page block to a rendered element, or ``None`` for stripped chrome."""
    head = block[0]
    if all(line.size <= _FOOTER_MAX_SIZE for line in block):
        return None  # footer chrome (Revision / date / Page) — already parsed
    if not head.bold and head.size >= _BANNER_MIN_SIZE:
        return None  # running header banner (manual title / unit title)

    kind = _marker_kind(head)
    if kind:  # list item: marker line, then wrapped text lines joined by space
        return (kind, " ".join(line.text for line in block[1:]))
    if _is_section_header(head):
        role = _content_type(source, head.text)
        return ("section", f"## {head.text}\n<!-- content_type: {role} -->")
    if _is_subheading(head):
        return ("subheading", f"### {head.text}")
    return ("paragraph", " ".join(line.text for line in block))


# Kinds that stay visually tight against a preceding list/marker. A page marker is
# here so a page break falling mid-list doesn't split the list with blank lines.
_TIGHT_KINDS = {"bullet", "subbullet", "marker"}


def _blank_between(prev: str, cur: str) -> bool:
    """Whether a blank line separates a *prev*-kind element from a *cur*-kind one."""
    if cur in ("section", "subheading", "paragraph", "h1"):
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


def render_unit_markdown(pdf_path, source: str, unit: int) -> str:
    """Render the Markdown for a single ``(source, unit)`` of *pdf_path*."""
    doc = fitz.open(pdf_path)
    try:
        pages = _unit_pages(doc, unit)
        meta = _unit_metadata(doc, unit, pages)
        elements: list[Element] = [("h1", f"# Unit {unit} — {meta['unit_name']}")]
        for idx, label in pages:
            elements.append(("marker", label))
            for block in _page_blocks(doc[idx]):
                element = _classify_block(block, source)
                if element is not None:
                    elements.append(element)
        return _frontmatter(source, unit, meta) + "\n" + _assemble(elements)
    finally:
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
