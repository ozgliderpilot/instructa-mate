"""One-shot: OPS 003 Training Principles & Techniques Manual → Markdown.

Uses the PDF outline (bookmarks) for heading hierarchy — this document has a
reliable TOC. Output follows ADR 0002 conventions for stage-2 chunking.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "corpus" / "Training Principles & Techniques Manual.pdf"
OUT = ROOT / "corpus" / "md" / "other" / "unit-training-principles.md"

_CHROME = re.compile(
    r"^(?:"
    r"Gliding Australia"
    r"|Training Principles(?:\s*&\s*Techniques Manual)?"
    r"|Document OPS 003"
    r"|Initial Issue(?:, December 2022)?"
    r"|Page \d+ of \d+"
    r"|UNCONTROLLED WHEN PRINTED"
    r"|\[\s*This page intentionally blank\s*\]"
    r")\s*$",
    re.I,
)

# Blank + TOC pages (body starts at INTRODUCTION on p8).
_SKIP_PAGES = {2, 5, 6, 7}

_ADMIN_HINTS = re.compile(
    r"revision history|table of contents|feedback|change proposal|cover|"
    r"amendment procedures|document history|record of amendments|"
    r"^references$|^introduction$|overview of the gliding australia training",
    re.I,
)
_AIM_HINTS = re.compile(r"learning objectives? of this module", re.I)
_AIRMANSHIP_HINTS = re.compile(
    r"threat and error|risk management|safety leadership|just culture|"
    r"incident|airmanship|human factors|thresholds of intervention",
    re.I,
)
_BRIEFING_HINTS = re.compile(
    r"pre-flight briefing|post-flight debriefing|hand-over|exchange of controls|"
    r"fault analysis|active listening",
    re.I,
)
_EXERCISE_HINTS = re.compile(
    r"demonstrate|direct and monitor|\bddm\b|standard instructional format",
    re.I,
)
_COMPETENCY_HINTS = re.compile(r"competenc|gpc pathway|gpc logbook", re.I)


def _norm(text: str) -> str:
    """Normalize for TOC ↔ body heading match."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def _content_type(heading: str) -> str:
    if _ADMIN_HINTS.search(heading):
        return "admin"
    if _AIM_HINTS.search(heading):
        return "aim"
    if _AIRMANSHIP_HINTS.search(heading):
        return "airmanship"
    if _BRIEFING_HINTS.search(heading):
        return "briefing"
    if _EXERCISE_HINTS.search(heading):
        return "exercise"
    if _COMPETENCY_HINTS.search(heading):
        return "competency"
    return "theory"


_TITLE_TOKEN_FIXES = {
    "Gpc": "GPC",
    "Aei": "AEI",
    "Ato": "ATO",
    "Tem": "TEM",
    "Ddm": "DDM",
    "Irm": "IRM",
    "Rrm": "RRM",
    "Cfi": "CFI",
    "Sms": "SMS",
}


def _smart_module(text: str) -> str:
    """Title-case ALL-CAPS module banners without mangling the MODULE token."""

    def _fix(titled: str) -> str:
        return re.sub(
            r"[A-Za-z][A-Za-z0-9]*",
            lambda m: _TITLE_TOKEN_FIXES.get(m.group(0), m.group(0)),
            titled,
        )

    m = re.match(r"^(MODULE\s+\d+)\s*[–\-]\s*(.+)$", text, re.I)
    if m:
        return f"{m.group(1).upper()} – {_fix(m.group(2).title())}"
    return _fix(text.title()) if text.isupper() else text


_FIGURE_NOTE = "*[Diagram / figure — see source PDF.]*"


def _page_lines(page: fitz.Page) -> list[str]:
    out: list[str] = []
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
            # Normalize bullets to markdown list markers.
            if text.startswith("●"):
                text = "- " + text.lstrip("●").strip()
            out.append(text)
    return out


def render() -> str:
    doc = fitz.open(PDF)
    toc = [(level, title.strip(), page) for level, title, page in doc.get_toc()]
    toc_norms = [_norm(title) for _, title, _ in toc]
    toc_i = 0

    parts: list[str] = [
        "---",
        "source: other",
        "unit: training-principles",
        "unit_name: Training Principles & Techniques Manual",
        'revision: "initial"',
        "---",
        "",
        "# Training Principles & Techniques Manual (OPS 003)",
        "",
    ]
    open_heading = False
    unmatched: list[str] = []

    def emit_heading(level: int, title: str) -> None:
        nonlocal open_heading
        # TOC level 1 → ## (H1 reserved for document title); cap at ######.
        md_level = min(level + 1, 6)
        if title.upper().startswith("MODULE"):
            display = _smart_module(title)
        elif title.isupper() and len(title) > 4:
            display = _smart_module(title)  # title-case + token fixes
        else:
            display = title
        parts.append("")
        parts.append(f"{'#' * md_level} {display}")
        parts.append(f"<!-- content_type: {_content_type(title)} -->")
        parts.append("")
        open_heading = True

    def emit_body(text: str) -> None:
        nonlocal open_heading
        if not open_heading:
            emit_heading(1, "Cover")
        parts.append(text)

    for page_index in range(len(doc)):
        page_no = page_index + 1
        if page_no in _SKIP_PAGES:
            continue

        lines = _page_lines(doc[page_index])
        has_images = bool(doc[page_index].get_images())
        if not lines and not has_images:
            continue

        parts.append(f"<!-- page: {page_no} -->")
        parts.append("")

        # Feedback form title on p4 (not in TOC).
        if page_no == 4:
            for text in lines:
                if _norm(text).startswith("gliding australia feedback"):
                    emit_heading(2, text)
                else:
                    emit_body(text)
            continue

        body_on_page = False
        for text in lines:
            key = _norm(text)
            # Sequential TOC match — document order follows the outline.
            if toc_i < len(toc) and key == toc_norms[toc_i]:
                level, title, _ = toc[toc_i]
                emit_heading(level, title)
                toc_i += 1
                continue
            emit_body(text)
            body_on_page = True

        # Figure-only (or heading+figure) pages must still own a citation page.
        if has_images and not body_on_page:
            emit_body(_FIGURE_NOTE)

    if toc_i < len(toc):
        unmatched = [f"p{p} L{lv} {t}" for lv, t, p in toc[toc_i:]]

    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    if unmatched:
        print(f"WARNING: {len(unmatched)} TOC entries not matched:")
        for row in unmatched[:20]:
            print(f"  {row}")
        if len(unmatched) > 20:
            print(f"  ... +{len(unmatched) - 20} more")
    else:
        print(f"matched all {len(toc)} TOC headings")
    return text


def main() -> None:
    from polish_other_md import polish

    OUT.parent.mkdir(parents=True, exist_ok=True)
    md = polish(render())
    OUT.write_text(md, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(md):,} chars, {md.count(chr(10)):,} lines)")


if __name__ == "__main__":
    main()
