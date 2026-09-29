"""Layout repairs for the one-shot other-corpus renderers.

Three PDF habits were surviving into the Markdown as lost structure:

- ruled grids (GPC unit / element / performance standards, tick matrices, forms)
  extracted as a flat reading-order stream, so empty cells disappeared
- diagrams drawn as images on a page that also has body text, with no pointer
- line-break hyphens and nested ``o`` bullets left on their own line

Table geometry comes from PyMuPDF. Prose pages that the table finder
mis-reads as a grid are left as lines.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import NamedTuple

import fitz

FIGURE_NOTE = "*[Diagram / figure — see source PDF.]*"

_SUP = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")

_SYLLABUS_HEADER = {"GPC", "Unit", "ELEMENT", "PERFORMANCE STANDARDS"}
_UNIT_ID = re.compile(r"^\d{1,2}[A-Z]?$")
_ELEMENT_START = re.compile(r"^\d+\.")
_CHROME = re.compile(
    r"^(?:"
    r"Gliding Australia"
    r"|Training Manual"
    r"|Training Principles(?:\s*&\s*Techniques Manual)?"
    r"|Manual of Standard Procedures.*"
    r"|Document OPS 00\d"
    r"|Revision \d.*"
    r"|Initial Issue.*"
    r"|Page \d+ of \d+"
    r"|UNCONTROLLED WHEN PRINTED"
    r")$",
    re.I,
)
_LEAD_LABEL = re.compile(r"^(Describe|Demonstrate):?$")


class LayoutState:
    """Carries a GPC unit id across a page break (rowspan does not repeat it)."""

    def __init__(self) -> None:
        self.gpc_unit = ""


class PLine(NamedTuple):
    x0: float
    y0: float
    y1: float
    text: str


def join_spans(spans: list[dict]) -> str:
    """Join a visual line, lifting superscript footnote markers off the word."""
    parts: list[str] = []
    for span in spans:
        text = span["text"]
        if span.get("flags", 0) & 1 and text.strip():
            text = text.strip().translate(_SUP)
        parts.append(text)
    return "".join(parts).strip()


def _positioned_lines(page: fitz.Page) -> list[PLine]:
    out: list[PLine] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            text = join_spans(spans)
            if not text or _CHROME.match(text):
                continue
            x0, y0, _x1, y1 = line["bbox"]
            out.append(PLine(x0, y0, y1, text))
    return out


def _cell_lines(text: str) -> list[str]:
    """Rejoin column-wrapped fragments inside one table cell."""
    raw = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    merged: list[str] = []
    for ln in raw:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        if prev.endswith("-") and ln[:1].islower():
            merged[-1] = prev + ln
            continue
        if (
            len(ln) == 1
            and ln.isalpha()
            and prev[-1:].isalpha()
            and " " not in prev
            and len(prev) <= 20
        ):
            merged[-1] = prev + ln  # Competen + t
            continue
        if (
            " " not in prev
            and " " not in ln
            and len(prev) <= 14
            and len(ln) <= 10
            and prev[-1:].isalpha()
            and not prev.endswith((".", ":", ";"))
        ):
            merged[-1] = f"{prev} {ln}"  # Briefed + by, GAus + No
            continue
        if (
            ln[:1].islower()
            and not prev.endswith((".", ":", ";"))
            and not re.match(r"^\([a-z0-9]+\)", ln)
            and not ln.startswith(("•", "●", "-", "o "))
        ):
            merged[-1] = f"{prev} {ln}"
            continue
        merged.append(ln)
    return merged


def _normalize_cell(text: str) -> str:
    return "<br>".join(_cell_lines(text))


def _collapse_columns(rows: list[list[str]]) -> list[list[str]]:
    """Drop empty columns and merge colspan duplicates."""
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [list(r) + [""] * (width - len(r)) for r in rows]
    keep = [
        j
        for j in range(width)
        if any(rows[i][j].strip() for i in range(len(rows)))
    ]
    rows = [[r[j] for j in keep] for r in rows]
    changed = True
    while changed and rows and len(rows[0]) > 1:
        changed = False
        for j in range(len(rows[0]) - 1):
            if all(
                not (r[j].strip() and r[j + 1].strip() and r[j].strip() != r[j + 1].strip())
                for r in rows
            ) and any(r[j + 1].strip() for r in rows):
                rows = [
                    r[:j] + [r[j].strip() or r[j + 1].strip()] + r[j + 2 :]
                    for r in rows
                ]
                changed = True
                break
    return [r for r in rows if any(c.strip() for c in r)]


def _gfm(rows: list[list[str]]) -> list[str]:
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]

    def cell(value: str) -> str:
        return value.replace("|", "\\|")

    header = "| " + " | ".join(cell(c) for c in padded[0]) + " |"
    rule = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = [
        "| " + " | ".join(cell(c) for c in r) + " |"
        for r in padded[1:]
    ]
    return [header, rule, *body]


def _short_cells(row: list[str]) -> int:
    return sum(1 for c in row if c.strip() and len(c) < 40)


def _accept_gfm(rows: list[list[str]], bbox: tuple[float, float, float, float]) -> bool:
    """Reject prose the finder sliced into fake cells."""
    if bbox[3] - bbox[1] < 60 or len(rows) < 2 or len(rows[0]) < 2:
        return False
    if any(_short_cells(r) >= 3 for r in rows[:4]):
        return True
    cells = [c for r in rows for c in r if c.strip()]
    if not cells:
        return False
    long_by_col = [0] * len(rows[0])
    for r in rows:
        for j, c in enumerate(r):
            if len(c) > 40:
                long_by_col[j] += 1
    long_total = sum(long_by_col)
    if long_total >= 3 and max(long_by_col) / long_total > 0.8:
        return False
    avg = sum(len(c) for c in cells) / len(cells)
    return avg < 55 and len(rows) >= 3


def _split_preamble(rows: list[list[str]]) -> tuple[str | None, list[list[str]]]:
    """A title merged across the header row is prose, not a column name."""
    if len(rows) < 2:
        return None, rows
    if (
        any(len(c) > 80 for c in rows[0])
        and _short_cells(rows[1]) >= 3
        and _short_cells(rows[0]) < _short_cells(rows[1])
    ):
        preamble = " ".join(c.strip() for c in rows[0] if c.strip())
        return preamble, rows[1:]
    return None, rows


def _has_rotated_text(page: fitz.Page, bbox: tuple[float, ...]) -> bool:
    """Vertical margin notes (find_tables concatenates them out of order)."""
    x0, y0, x1, y1 = bbox
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            direction = line.get("dir") or (1, 0)
            if abs(direction[0]) > 0.8:
                continue
            cx = (line["bbox"][0] + line["bbox"][2]) / 2
            cy = (line["bbox"][1] + line["bbox"][3]) / 2
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                continue
            if "".join(span["text"] for span in line.get("spans", [])).strip():
                return True
    return False


def _gfm_regions(page: fitz.Page) -> list[tuple[float, float, list[str]]]:
    try:
        tables = page.find_tables().tables
    except Exception:
        return []
    regions: list[tuple[float, float, list[str]]] = []
    for table in tables:
        bbox = tuple(table.bbox)
        if _has_rotated_text(page, bbox):
            continue
        try:
            extracted = table.extract()
        except Exception:
            continue
        rows = _collapse_columns(
            [[_normalize_cell(c or "") for c in row] for row in extracted]
        )
        if not _accept_gfm(rows, bbox):
            continue
        preamble, rows = _split_preamble(rows)
        rows = _collapse_columns(rows)
        if len(rows) < 2 or len(rows[0]) < 2:
            continue
        lines: list[str] = []
        if preamble:
            # A title and the field labels under it are separate lines. One
            # ``<br>``-joined string gets swallowed into an appendix heading.
            lines.extend(part.strip() for part in preamble.split("<br>") if part.strip())
            lines.append("")
        lines.extend(_gfm(rows))
        y0, y1 = bbox[1], bbox[3]
        src = " ".join(
            ln.text
            for ln in _positioned_lines(page)
            if y0 - 2 <= (ln.y0 + ln.y1) / 2 <= y1 + 2
        )
        # find_tables can slice a word ("trainee" -> "train"). Keep the lines.
        if not _words_preserved(src, "\n".join(lines)):
            continue
        regions.append((y0, y1, lines))
    return regions


def _alpha_counts(text: str) -> Counter[str]:
    # A lone ``o`` is the nested-bullet glyph, rendered as a Markdown sub-bullet.
    return Counter(
        word
        for word in re.findall(r"[A-Za-z']+", text.lower())
        if word != "o"
    )


def _words_preserved(src: str, out: str) -> bool:
    """Every source word is still present. Footnote digits glued on a word count."""
    return not (_alpha_counts(src) - _alpha_counts(out))


def _assemble_standards(parts: list[str]) -> str:
    items: list[tuple[str, str]] = []

    def append_wrap(text: str) -> None:
        kind, body = items[-1]
        if body.endswith("-") and text[:1].islower():
            items[-1] = (kind, body + text)
        elif not body:
            items[-1] = (kind, text)
        else:
            items[-1] = (kind, f"{body} {text}".strip())

    for part in parts:
        text = re.sub(r"\s+", " ", part).strip()
        if not text:
            continue
        if _LEAD_LABEL.fullmatch(text):
            items.append(("text", text))
            continue
        if text in {"•", "●", "-"}:
            items.append(("bullet", ""))
            continue
        if text in {"o", "◦"}:
            items.append(("sub", ""))
            continue
        if text.startswith(("• ", "● ", "- ")):
            items.append(("bullet", text[2:].strip()))
            continue
        if text.startswith("o "):
            items.append(("sub", text[2:].strip()))
            continue
        if items and items[-1][0] in {"bullet", "sub"}:
            append_wrap(text)
            continue
        items.append(("text", text))

    rendered: list[str] = []
    for kind, body in items:
        if kind == "bullet":
            rendered.append(f"- {body}".rstrip())
        elif kind == "sub":
            rendered.append(f"  - {body}".rstrip())
        else:
            rendered.append(body)
    return "<br>".join(rendered)


def _join_element(parts: list[str]) -> str:
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([A-Za-z])- ([a-z])", r"\1-\2", text)
    return text


def _syllabus_region(
    page: fitz.Page, state: LayoutState
) -> tuple[float, float, list[str]] | None:
    lines = _positioned_lines(page)
    perf = next((ln for ln in lines if ln.text == "PERFORMANCE STANDARDS"), None)
    elem = next((ln for ln in lines if ln.text == "ELEMENT"), None)
    if perf is None or elem is None or abs(perf.y0 - elem.y0) > 25:
        return None
    try:
        tables = page.find_tables().tables
    except Exception:
        tables = []
    host = next((t for t in tables if t.bbox[1] - 2 <= perf.y0 <= t.bbox[3]), None)
    if host is not None:
        y0, y1 = host.bbox[1], host.bbox[3]
    else:
        y0 = min(perf.y0, elem.y0) - 2
        y1 = page.rect.height - 36

    element_x = elem.x0
    standards_x = perf.x0
    body = [
        ln
        for ln in lines
        if y0 - 1 <= ln.y0 <= y1 and ln.text not in _SYLLABUS_HEADER
    ]
    body.sort(key=lambda ln: (ln.y0, ln.x0))

    def column(ln: PLine) -> str:
        if ln.x0 >= standards_x - 25:
            return "std"
        if ln.x0 >= element_x - 12:
            return "el"
        return "unit"

    # The unit number is centred in a rowspan, so it does not sit on the first
    # element. Group elements by number (a new unit restarts the count), then
    # give each group the unit id whose mark falls inside it.
    def is_element_start(ln: PLine) -> bool:
        if column(ln) != "el":
            return False
        text = ln.text.strip()
        if _ELEMENT_START.match(text):
            return True
        # Unnumbered title at the element gutter. Indented wraps sit further right;
        # lowercase lines continue the title above.
        return abs(ln.x0 - element_x) <= 4 and text[:1].isupper()

    def element_num(text: str) -> int:
        match = _ELEMENT_START.match(text.strip())
        if not match:
            return 0
        return int(match.group(0)[:-1])

    starts = [ln for ln in body if is_element_start(ln)]
    if not starts:
        return None
    rows: list[dict[str, object]] = [
        {
            "element": [ln.text.strip()],
            "std": [],
            "y0": ln.y0,
            "y1": ln.y1,
            "num": element_num(ln.text),
            "unit": "",
        }
        for ln in starts
    ]

    def row_index(y: float) -> int:
        chosen = 0
        for i, start in enumerate(starts):
            # A lead label can sit a few points above a vertically centred title.
            if start.y0 <= y + 8:
                chosen = i
            else:
                break
        return chosen

    marks: list[tuple[float, str]] = []
    for ln in body:
        kind = column(ln)
        if kind == "unit" and _UNIT_ID.fullmatch(ln.text.strip()):
            marks.append((ln.y0, ln.text.strip()))
            continue
        if is_element_start(ln):
            continue
        row = rows[row_index(ln.y0)]
        row["y0"] = min(float(row["y0"]), ln.y0)
        row["y1"] = max(float(row["y1"]), ln.y1)
        if kind == "el":
            row["element"].append(ln.text.strip())
        else:
            row["std"].append(ln.text.strip())

    groups: list[list[dict[str, object]]] = []
    current_group: list[dict[str, object]] = []
    prev_num: int | None = None
    for row in rows:
        num = int(row["num"])
        if prev_num is not None and num <= prev_num and current_group:
            groups.append(current_group)
            current_group = []
        current_group.append(row)
        prev_num = num
    if current_group:
        groups.append(current_group)

    carry = state.gpc_unit
    unused = list(marks)
    for group in groups:
        gy0 = min(float(row["y0"]) for row in group)
        gy1 = max(float(row["y1"]) for row in group)
        inside = [(y, token) for y, token in unused if gy0 - 8 <= y <= gy1 + 8]
        if inside:
            mid = (gy0 + gy1) / 2
            _y, token = min(inside, key=lambda mark: abs(mark[0] - mid))
            unused.remove((_y, token))
            unit = token
        else:
            unit = carry
        for row in group:
            row["unit"] = unit
        if unit:
            carry = unit
    # A unit id centred in the cell padding belongs to the nearest element group.
    for y, token in unused:
        nearest: list[dict[str, object]] | None = None
        best = 1e9
        for group in groups:
            gy0 = min(float(row["y0"]) for row in group)
            gy1 = max(float(row["y1"]) for row in group)
            if y < gy0:
                dist = gy0 - y
            elif y > gy1:
                dist = y - gy1
            else:
                dist = 0.0
            if dist < best:
                best = dist
                nearest = group
        if nearest is not None and best <= 40:
            for row in nearest:
                row["unit"] = token
    if groups and groups[-1][0]["unit"]:
        carry = str(groups[-1][0]["unit"])
    state.gpc_unit = carry

    md = [
        "| GPC Unit | Element | Performance standards |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        element = _join_element(row["element"])  # type: ignore[arg-type]
        standards = _assemble_standards(row["std"])  # type: ignore[arg-type]
        unit = str(row["unit"])
        md.append(
            "| "
            + " | ".join(
                part.replace("|", "\\|")
                for part in (unit, element, standards)
            )
            + " |"
        )
    src = " ".join(
        ln.text
        for ln in _positioned_lines(page)
        if y0 - 2 <= (ln.y0 + ln.y1) / 2 <= y1 + 2
    )
    if not _words_preserved(src, "\n".join(md)):
        return None
    return (y0, y1, md)


def _figure_ys(page: fitz.Page) -> list[float]:
    """Content diagrams. Skip logos, and skip watermarks that sit on top of text."""
    words = page.get_text("words")
    found: list[float] = []
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        if (x1 - x0) * (y1 - y0) < 20000 or (y1 - y0) < 100:
            continue
        inside = sum(
            1
            for w in words
            if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1
        )
        if inside > 8:
            continue
        found.append(y0)
    return found


def _covered(y: float, regions: list[tuple[float, float, list[str]]]) -> bool:
    return any(y0 - 1 <= y <= y1 + 1 for y0, y1, _lines in regions)


def _join_stacked_fragments(
    lines: list[tuple[float, float, float, str, bool, float]],
) -> list[tuple[float, float, float, str, bool, float]]:
    """Join ``Competen`` / ``t`` and ``Briefed`` / ``by`` inside a narrow column.

    The continuation is not the next line in reading order. ``Briefed`` and
    ``by`` share an x, with ``Competent`` and ``Date`` between them on the row.
    Anchor order is preserved so a vertical margin note stays where it was read.
    """
    used: set[int] = set()
    out: list[tuple[float, float, float, str, bool, float]] = []
    for i, item in enumerate(lines):
        if i in used:
            continue
        x0, y0, y1, text, bold, size = item
        used.add(i)
        while True:
            best: int | None = None
            best_gap = 99.0
            for j, other in enumerate(lines):
                if j in used:
                    continue
                ox, oy0, _oy1, otext, _ob, _os = other
                gap = oy0 - y1
                if abs(ox - x0) >= 8 or gap < -0.5 or gap >= 8 or gap >= best_gap:
                    continue
                if " " in text or " " in otext:
                    continue
                single = len(otext) == 1 and otext.isalpha() and text[-1:].isalpha()
                short = (
                    len(text) <= 14
                    and len(otext) <= 10
                    and text[-1:].isalpha()
                    and not text.endswith((".", ";", ":"))
                )
                if not (single or short):
                    continue
                best = j
                best_gap = gap
            if best is None:
                break
            _ox, _oy0, oy1, otext, obold, osize = lines[best]
            if len(otext) == 1 and otext.isalpha():
                text = text + otext
            else:
                text = f"{text} {otext}"
            y1 = oy1
            bold = bold or obold
            size = max(size, osize)
            used.add(best)
        out.append((x0, y0, y1, text, bold, size))
    return out


def _join_baselines(
    lines: list[tuple[float, float, float, str, bool, float]],
    rotated: set[str],
) -> list[tuple[float, float, float, str, bool, float]]:
    """Put short labels that share a baseline on one line, left to right.

    ``Name`` and ``Date of birth`` are separate text objects at the same y.
    Long prose stays on its own line so a section number can still join its
    paragraph the way the renderer already does.
    """
    out: list[tuple[float, float, float, str, bool, float]] = []
    i = 0
    while i < len(lines):
        if lines[i][3] in rotated:
            run = [lines[i]]
            j = i + 1
            while j < len(lines) and lines[j][3] in rotated:
                run.append(lines[j])
                j += 1
            buf = run[0]
            for item in run[1:]:
                prev = buf[3].rstrip()
                if prev.endswith((".", "!", "?")) or item[3].lstrip().startswith("NOTE"):
                    out.append(buf)
                    buf = item
                    continue
                buf = (
                    buf[0],
                    buf[1],
                    max(buf[2], item[2]),
                    prev + " " + item[3].lstrip(),
                    buf[4] or item[4],
                    max(buf[5], item[5]),
                )
            out.append(buf)
            i = j
            continue
        group = [lines[i]]
        j = i + 1
        while (
            j < len(lines)
            and lines[j][3] not in rotated
            and abs(lines[j][1] - group[0][1]) <= 2.5
        ):
            group.append(lines[j])
            j += 1
        group.sort(key=lambda item: item[0])
        short = all(len(item[3]) < 45 and item[5] < 13 for item in group)
        if len(group) == 1 or not short:
            out.extend(group)
            i = j
            continue
        x0, y0, y1, text, bold, size = group[0]
        for ox, _oy0, oy1, otext, obold, osize in group[1:]:
            sep = " " if re.fullmatch(r"[\d.]+", text.strip()) else "    "
            text = f"{text}{sep}{otext}"
            x0 = min(x0, ox)
            y1 = max(y1, oy1)
            bold = bold or obold
            size = max(size, osize)
        out.append((x0, y0, y1, text, bold, size))
        i = j
    return out


def _rotated_texts(page: fitz.Page) -> set[str]:
    found: set[str] = set()
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            direction = line.get("dir") or (1, 0)
            if abs(direction[0]) > 0.8:
                continue
            text = join_spans(line.get("spans") or [])
            if text:
                found.add(text)
    return found


def merge_layout(
    page: fitz.Page,
    lines: list[tuple[float, float, float, str, bool, float]],
    state: LayoutState,
) -> list[tuple[str, bool, float]]:
    """Replace ruled regions and drop in figure notes.

    ``lines`` are ``(x0, y0, y1, text, bold, size)`` in extraction order.
    That order is kept: a vertical margin note's bbox y is not its reading position.
    """
    rotated = _rotated_texts(page)
    lines = _join_stacked_fragments(lines)
    regions: list[tuple[float, float, list[str]]] = []
    syllabus = _syllabus_region(page, state)
    if syllabus is not None:
        regions.append(syllabus)
    for y0, y1, md_lines in _gfm_regions(page):
        if any(not (y1 < ry0 or y0 > ry1) for ry0, ry1, _ in regions):
            continue
        regions.append((y0, y1, md_lines))

    kept = [
        item
        for item in lines
        if item[3] in rotated or not _covered((item[1] + item[2]) / 2, regions)
    ]
    kept = _join_baselines(kept, rotated)

    inserts: list[tuple[float, list[str]]] = [(y0, md) for y0, _y1, md in regions]
    for y in _figure_ys(page):
        if not _covered(y, regions):
            inserts.append((y, [FIGURE_NOTE]))
    inserts.sort(key=lambda item: item[0])

    out: list[tuple[str, bool, float]] = []
    ii = 0

    def flush_until(y: float) -> None:
        nonlocal ii
        while ii < len(inserts) and inserts[ii][0] <= y:
            for text in inserts[ii][1]:
                out.append((text, False, 10.0))
            ii += 1

    for _x0, y0, _y1, text, bold, size in kept:
        # Rotated margin text stays in extraction order. Its bbox y would
        # otherwise pull the sentence apart and drop it into the form.
        if text in rotated:
            out.append((text, bold, size))
            continue
        flush_until(y0 + 0.5)
        out.append((text, bold, size))
    flush_until(1e9)
    return out
