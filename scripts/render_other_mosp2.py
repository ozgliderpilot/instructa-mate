"""One-shot: MOSP Part 2 Operations (OPS 001) → corpus/md/other/unit-mosp2.md.

Same numbered-chapter layout as OPS 002 Training Manual (no PDF outline).
Output follows ADR 0002 conventions for stage-2 chunking.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "corpus" / "MOSP 2 Operations V9 Jan 2024.pdf"
OUT = ROOT / "corpus" / "md" / "other" / "unit-mosp2.md"

_CHROME = re.compile(
    r"^(?:"
    r"Gliding Australia"
    r"|Manual of Standard Procedures Part 2,? Operations"
    r"|Document OPS 001"
    r"|Revision 9(?:, January 2024)?"
    r"|Page \d+ of \d+"
    r"|UNCONTROLLED WHEN PRINTED"
    r"|\[\s*This page intentionally blank\s*\]"
    r"|Contents"
    r")\s*$",
    re.I,
)

_CHAPTER_NUM = re.compile(r"^(\d{1,2})$")
_CHAPTER_COMBINED = re.compile(r"^(\d{1,2})\s+(.+)$")  # "10 Evaluation flights"
_NUM_ONLY = re.compile(r"^(\d+(?:\.\d+)+\.?)$")
_SUBSECTION = re.compile(r"^(\d+(?:\.\d+)+\.?)\s+(.+)$")
_APPENDIX = re.compile(r"^Appendix\s+(\d+)\s*[–\-]\s*(.+)$", re.I)
_APPENDIX_CONT = re.compile(r"^Appendix\s+(\d+)\s*[–\-]\s*$", re.I)
_LETTERED = re.compile(r"^\([a-zivx]+\)$", re.I)

# Cover chrome, blank, pure TOC.
_SKIP_PAGES = {2, 5, 6}

_PART_BANNERS = {
    "sailplane air operations",
    "requirements for sailplane flight crew authorisations",
    "requirements for sailplane flight crew",
    "authorisations",
    "flight instructors",
    "approved training organisations",
    "non-training flying organisations",
    "oversight, certification, and enforcement",
    "medical requirements",
}

_ADMIN_HINTS = re.compile(
    r"revision history|table of contents|feedback|change proposal|cover|"
    r"introduction|overview|definitions|application form|application and report|"
    r"record-keeping|recording|declaration|certificate for|"
    r"amendment procedures|document history|record of amendments|"
    r"notification of changes|annual internal review|annual activity report",
    re.I,
)
_AIRMANSHIP_HINTS = re.compile(
    r"safety policy|occurrence reporting|risk|threat and error|"
    r"medical|fitness|medication|psychoactive|diving and blood|"
    r"decrease in medical|validity periods for medical",
    re.I,
)
_BRIEFING_HINTS = re.compile(r"\bbriefing\b|\bdebriefing\b", re.I)
_COMPETENCY_HINTS = re.compile(
    r"competenc|assessment|skill test|proficiency check|"
    r"glider pilot certificate|flight instructor|recency|flight review|"
    r"aerobatic privileges|privileges and conditions",
    re.I,
)
_EXERCISE_HINTS = re.compile(
    r"operating procedures|charter|evaluation flights|test flying|"
    r"sailplane towing|aerobatic manoeuvres|air displays|"
    r"flights over unlandable|coaching activities",
    re.I,
)


_TITLE_TOKEN_FIXES = {
    "Gpc": "GPC",
    "Aei": "AEI",
    "Ato": "ATO",
    "Atos": "ATOs",
    "Gfa": "GFA",
    "Casa": "CASA",
    "Casr": "CASR",
    "Tem": "TEM",
    "Afm": "AFM",
    "Efb": "EFB",
    "Tmg": "TMG",
    "Cfi": "CFI",
    "Sms": "SMS",
    "Mosp": "MOSP",
    "Elt": "ELT",
    "Plb": "PLB",
    "Vhf": "VHF",
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
    if text[0].islower():
        return False
    if len(text) > 90:
        return False
    if text.endswith((".", ";", ",")) and len(text) > 40:
        return False
    return True


def _is_part_banner(text: str, bold: bool, size: float) -> bool:
    if not (bold and size >= 14):
        return False
    key = re.sub(r"\s+", " ", text).strip().casefold()
    if key in _PART_BANNERS:
        return True
    # Multi-line part titles (second line alone).
    if key in {"authorisations", "(atos)", "organisations"}:
        return True
    return False


def render() -> str:
    doc = fitz.open(PDF)
    parts: list[str] = [
        "---",
        "source: other",
        "unit: mosp2",
        "unit_name: Manual of Standard Procedures Part 2 — Operations",
        'revision: "9"',
        "---",
        "",
        "# Manual of Standard Procedures Part 2 — Operations (OPS 001)",
        "",
    ]

    pending_num: tuple[str, bool] | None = None
    appendix_buf: list[str] = []
    part_buf: list[str] = []
    open_heading = False

    def emit_heading(level: int, title: str) -> None:
        nonlocal open_heading, appendix_buf, pending_num, part_buf
        pending_num = None
        appendix_buf = []
        part_buf = []
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

            # Multi-line part banner continuation.
            if part_buf:
                if bold and size >= 14 and _CHAPTER_COMBINED.match(text) is None:
                    part_buf.append(text)
                    nxt = lines[i + 1] if i + 1 < len(lines) else None
                    more = (
                        nxt is not None
                        and nxt[1]
                        and nxt[2] >= 14
                        and _CHAPTER_NUM.fullmatch(nxt[0]) is None
                        and _CHAPTER_COMBINED.match(nxt[0]) is None
                    )
                    if not more:
                        emit_heading(2, _title_case(" ".join(part_buf)))
                    i += 1
                    continue
                emit_heading(2, _title_case(" ".join(part_buf)))

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

            # Combined chapter title on one line: "10 Evaluation flights"
            # May continue on the next bold line ("PLANE", "authorisations – persons").
            m_ch = _CHAPTER_COMBINED.match(text)
            if m_ch and bold and size >= 11.5 and "." not in m_ch.group(1):
                rest = m_ch.group(2).strip()
                if not rest[:1].islower() and len(rest) < 120:
                    title_bits = [rest]
                    j = i + 1
                    while j < len(lines):
                        nxt_t, nxt_b, nxt_s = lines[j]
                        if not nxt_b or nxt_s < 11.5:
                            break
                        if _CHAPTER_NUM.fullmatch(nxt_t) or _CHAPTER_COMBINED.match(nxt_t):
                            break
                        if _APPENDIX.match(nxt_t) or _NUM_ONLY.fullmatch(nxt_t):
                            break
                        if len(nxt_t) > 90:
                            break
                        prev = title_bits[-1].rstrip().casefold()
                        # Allow lowercase continuation after a line-break connector.
                        if nxt_t[:1].islower() and not prev.endswith(
                            (" or", " and", " the", " of", " for", " to", " –", "-")
                        ):
                            break
                        title_bits.append(nxt_t)
                        j += 1
                    emit_heading(
                        2,
                        f"{m_ch.group(1)} {_title_case(' '.join(title_bits))}",
                    )
                    i = j
                    continue

            # Appendix headings — require banner styling so body references like
            # "(Appendix 4 – Non-training…)" are not promoted.
            m_app = _APPENDIX.match(text)
            if m_app and bold and size >= 14:
                title = f"Appendix {m_app.group(1)} — {_title_case(m_app.group(2).strip())}"
                j = i + 1
                while j < len(lines):
                    nxt_t, nxt_b, nxt_s = lines[j]
                    if not (nxt_b and nxt_s >= 14):
                        break
                    if _APPENDIX.match(nxt_t) or _CHAPTER_NUM.fullmatch(nxt_t):
                        break
                    if _CHAPTER_COMBINED.match(nxt_t):
                        break
                    # Stop before form letterheads / org banners.
                    low = nxt_t.casefold()
                    if "gliding federation" in low or low.startswith("trading as"):
                        break
                    if nxt_t.isupper() and len(nxt_t) > 20:
                        break
                    title = f"{title} {_title_case(nxt_t)}"
                    j += 1
                emit_heading(2, title)
                i = j
                continue
            m_app_c = _APPENDIX_CONT.match(text)
            if m_app_c and bold and size >= 14:
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

            # Number-only line: bold → heading; non-bold → body label.
            m_num = _NUM_ONLY.fullmatch(text)
            if m_num:
                number = m_num.group(1)
                pending_num = (number if number.endswith(".") else number + ".", bold)
                i += 1
                continue

            # Part banners / form titles (large bold).
            if bold and size >= 14 and len(text) > 8 and not text.endswith((".", ";", ",")):
                key = re.sub(r"\s+", " ", text).strip().casefold()
                if key in _PART_BANNERS or size >= 15.5:
                    # May continue on next line.
                    nxt = lines[i + 1] if i + 1 < len(lines) else None
                    more = (
                        nxt is not None
                        and nxt[1]
                        and nxt[2] >= 14
                        and _CHAPTER_NUM.fullmatch(nxt[0]) is None
                        and _CHAPTER_COMBINED.match(nxt[0]) is None
                        and _APPENDIX.match(nxt[0]) is None
                    )
                    if more:
                        part_buf = [text]
                    else:
                        emit_heading(2, _title_case(text))
                    i += 1
                    continue
                emit_heading(3 if open_heading else 2, _title_case(text))
                i += 1
                continue

            emit_body(text)
            i += 1

        flush_pending_as_body()

    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    return text


def main() -> None:
    from polish_other_md import polish

    OUT.parent.mkdir(parents=True, exist_ok=True)
    md = polish(render())
    OUT.write_text(md, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(md):,} chars, {md.count(chr(10)):,} lines)")


if __name__ == "__main__":
    main()
