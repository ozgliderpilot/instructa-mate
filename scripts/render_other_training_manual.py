"""One-shot: OPS 002 Training Manual PDF → corpus/md/other/unit-training-manual.md.

Not the stage-1 guide parser — this document has a different layout (numbered
chapters, appendices, multi-column syllabus tables). Output follows ADR 0002
conventions so stage 2 can chunk it: YAML frontmatter, bare ``<!-- page: N -->``
markers, ``##``/``###``/``####`` headings with ``content_type`` tags.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "corpus" / "Training Manual-v2.4-20251029.pdf"
OUT = ROOT / "corpus" / "md" / "other" / "unit-training-manual.md"

_CHROME = re.compile(
    r"^(?:"
    r"Gliding Australia"
    r"|Training Manual"
    r"|Document OPS 002"
    r"|Revision 2\.4(?:, May 2025)?"
    r"|Page \d+ of \d+"
    r"|UNCONTROLLED WHEN PRINTED"
    r"|\[\s*This page intentionally blank\s*\]"
    r")\s*$",
    re.I,
)

_CHAPTER_NUM = re.compile(r"^(\d{1,2})$")
_NUM_ONLY = re.compile(r"^(\d+(?:\.\d+)+\.?)$")  # "6.1." / "15.11.2."
_SUBSECTION = re.compile(r"^(\d+(?:\.\d+)+\.?)\s+(.+)$")
_APPENDIX = re.compile(r"^Appendix\s+(\d+)\s*[–\-]\s*(.+)$", re.I)
_APPENDIX_CONT = re.compile(r"^Appendix\s+(\d+)\s*[–\-]\s*$", re.I)
_LETTERED = re.compile(r"^\([a-zivx]+\)$", re.I)

# Cover chrome, blank, pure TOC.
_SKIP_PAGES = {2, 5, 6}

_ADMIN_HINTS = re.compile(
    r"revision history|table of contents|feedback|change proposal|cover|"
    r"introduction|overview|definitions|application form|"
    r"recording or documentation|amendment procedures|document history|"
    r"record of amendments",
    re.I,
)
_COMPETENCY_HINTS = re.compile(
    r"competenc|assessment|performance criteria|progressive competency|"
    r"syllabus|theoretical knowledge|elements and performance",
    re.I,
)
_EXERCISE_HINTS = re.compile(
    r"flight instruction|flying training|details of exercises|"
    r"aerobatic|launching methods|soaring training",
    re.I,
)
_BRIEFING_HINTS = re.compile(r"\bbriefing\b|\bdebriefing\b", re.I)
_AIRMANSHIP_HINTS = re.compile(r"threat and error|airmanship|human factors", re.I)


_TITLE_TOKEN_FIXES = {
    "Gpc": "GPC",
    "Aei": "AEI",
    "Ato": "ATO",
    "Atos": "ATOs",
    "Gfa": "GFA",
    "Casa": "CASA",
    "Tem": "TEM",
    "Tmg": "TMG",
    "Cfi": "CFI",
    "Sms": "SMS",
    "Mosp": "MOSP",
}


def _title_case(text: str) -> str:
    if text.isupper() and len(text) > 3:
        titled = text.title()
        titled = re.sub(
            r"[A-Za-z][A-Za-z0-9]*",
            lambda m: _TITLE_TOKEN_FIXES.get(m.group(0), m.group(0)),
            titled,
        )
        return titled
    return text


def _content_type(heading: str) -> str:
    if _ADMIN_HINTS.search(heading):
        return "admin"
    if _AIRMANSHIP_HINTS.search(heading):
        return "airmanship"
    if _BRIEFING_HINTS.search(heading):
        return "briefing"
    if _EXERCISE_HINTS.search(heading):
        return "exercise"
    if _COMPETENCY_HINTS.search(heading):
        return "competency"
    return "theory"


def _heading_level_for_number(number: str) -> int:
    number = number.rstrip(".")
    dots = number.count(".")
    if dots == 0:
        return 2
    return min(2 + dots, 4)


def _page_lines(page: fitz.Page) -> list[tuple[str, bool, float]]:
    out: list[tuple[str, bool, float]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text or _CHROME.match(text):
                continue
            bold = any(s.get("flags", 0) & (1 << 4) for s in spans)
            size = max(s["size"] for s in spans)
            out.append((text, bold, size))
    return out


def _looks_like_title(text: str, bold: bool) -> bool:
    if not text or _LETTERED.match(text):
        return False
    if bold:
        return True
    # Title-ish: starts with capital, not a long prose sentence.
    if text[0].islower():
        return False
    if len(text) > 90:
        return False
    if text.endswith((".", ";", ",")) and len(text) > 40:
        return False
    return True


def render() -> str:
    doc = fitz.open(PDF)
    parts: list[str] = [
        "---",
        "source: other",
        "unit: training-manual",
        "unit_name: Training Manual",
        'revision: "2.4"',
        "---",
        "",
        "# Training Manual (OPS 002)",
        "",
    ]

    pending_num: tuple[str, bool] | None = None  # (number, as_heading)
    appendix_buf: list[str] = []
    open_heading = False

    def emit_heading(level: int, title: str) -> None:
        nonlocal open_heading, appendix_buf, pending_num
        pending_num = None
        appendix_buf = []
        parts.append("")
        parts.append(f"{'#' * level} {title}")
        parts.append(f"<!-- content_type: {_content_type(title)} -->")
        parts.append("")
        open_heading = True

    def emit_body(text: str) -> None:
        nonlocal open_heading
        if not open_heading:
            emit_heading(2, "Cover")
        parts.append(text)

    def flush_pending_as_body() -> None:
        nonlocal pending_num
        if pending_num is not None:
            emit_body(pending_num[0])
            pending_num = None

    for page_index in range(len(doc)):
        page_no = page_index + 1
        if page_no in _SKIP_PAGES:
            continue

        lines = _page_lines(doc[page_index])
        if not lines:
            continue

        parts.append(f"<!-- page: {page_no} -->")
        parts.append("")

        i = 0
        while i < len(lines):
            text, bold, size = lines[i]

            # Multi-line appendix title continuation.
            if appendix_buf:
                if bold and size >= 14 and _APPENDIX.match(text) is None:
                    appendix_buf.append(text)
                    nxt = lines[i + 1] if i + 1 < len(lines) else None
                    more = (
                        nxt is not None
                        and nxt[1]
                        and nxt[2] >= 14
                        and _APPENDIX.match(nxt[0]) is None
                        and _CHAPTER_NUM.fullmatch(nxt[0]) is None
                    )
                    if not more:
                        emit_heading(2, " ".join(appendix_buf))
                    i += 1
                    continue
                emit_heading(2, " ".join(appendix_buf))
                # fall through to classify current line

            # Resolve pending number + this line as title or body.
            if pending_num is not None:
                number, as_heading = pending_num
                if as_heading and _looks_like_title(text, bold):
                    num = number.rstrip(".")
                    level = _heading_level_for_number(num)
                    title = (
                        f"{num}. {_title_case(text)}"
                        if "." in num
                        else f"{num} {_title_case(text)}"
                    )
                    emit_heading(level, title)
                    i += 1
                    continue
                # Numbered paragraph body: "6.1.1." + "Applicants for..."
                prefix = number if number.endswith(".") else number + "."
                emit_body(f"{prefix} {text}")
                pending_num = None
                i += 1
                continue

            # Chapter number alone (bold, large).
            if _CHAPTER_NUM.fullmatch(text) and (bold or size >= 11.5):
                pending_num = (text, True)
                i += 1
                continue

            # Appendix headings.
            m_app = _APPENDIX.match(text)
            if m_app:
                emit_heading(
                    2, f"Appendix {m_app.group(1)} — {_title_case(m_app.group(2).strip())}"
                )
                i += 1
                continue
            m_app_c = _APPENDIX_CONT.match(text)
            if m_app_c:
                appendix_buf = [f"Appendix {m_app_c.group(1)} —"]
                i += 1
                continue

            # Subsection number+title on one line (bold).
            m_sub = _SUBSECTION.match(text)
            if m_sub and bold:
                number, rest = m_sub.group(1), m_sub.group(2).strip()
                level = _heading_level_for_number(number)
                emit_heading(level, f"{number.rstrip('.')}. {_title_case(rest)}")
                i += 1
                continue

            # Number-only line: "6.1." / "1.1." / "6.1.1."
            # Bold → section heading; non-bold → numbered paragraph body.
            m_num = _NUM_ONLY.fullmatch(text)
            if m_num:
                number = m_num.group(1)
                pending_num = (number if number.endswith(".") else number + ".", bold)
                i += 1
                continue

            # Standalone form / display titles.
            if bold and size >= 14 and len(text) > 8 and not text.endswith((".", ";", ",")):
                emit_heading(3 if open_heading else 2, _title_case(text))
                i += 1
                continue

            emit_body(text)
            i += 1

        flush_pending_as_body()

    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    # Soft-join broken words across newlines: "Organisa-\ntion" → keep as-is
    # (verbatim rule). Collapse "6.1.  Title" artifacts already handled above.
    return text


def main() -> None:
    from polish_other_md import polish

    OUT.parent.mkdir(parents=True, exist_ok=True)
    md = polish(render())
    OUT.write_text(md, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(md):,} chars, {md.count(chr(10)):,} lines)")


if __name__ == "__main__":
    main()
