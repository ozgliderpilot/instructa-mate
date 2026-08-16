"""Polish formatting in corpus/md/other/*.md (and optionally at end of render scripts).

Transforms (structure-preserving, no wording changes beyond whitespace/join):
- join bare list markers ``(a)`` / ``(i)`` / ``1.`` with the following line
- join orphan ``-`` / ``●`` bullet markers with the following line
- fix ``- \\ntext`` bullets left by PDF extraction
- restore common acronyms in headings (GPC, AEI, …)
- merge appendix title fragments wrongly promoted as ``### Instructor Training``
- collapse 3+ blank lines to at most 2
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OTHER = ROOT / "corpus" / "md" / "other"

_BARE_MARKER = re.compile(
    r"^(?:"
    r"\([a-z]\)"  # (a)
    r"|\([A-Z]\)"  # (A)
    r"|\([ivxlcdm]+\)"  # (i) (iv) (xi)
    r"|\([IVXLCDM]+\)"
    r"|\d+\."  # 1. 12.
    r"|[a-z]\."  # a.
    r")$"
)

_BARE_BULLET = re.compile(r"^(?:[-*●•]|\-\s*)$")

_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")
_PAGE = re.compile(r"^<!-- page: \d+ -->\s*$")
_CT = re.compile(r"^<!-- content_type: \S+ -->\s*$")

# Whole-token title-case fixes only (never substring replace — that mangles Radio/Appendix).
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
    "Tmgs": "TMGs",
    "Cfi": "CFI",
    "Sms": "SMS",
    "Mosp": "MOSP",
    "Irm": "IRM",
    "Rrm": "RRM",
    "Elt": "ELT",
    "Plb": "PLB",
    "Vhf": "VHF",
    "Ats": "ATS",
    "Atsb": "ATSB",
    "Soar": "SOAR",
    "Pic": "PIC",
    "Ddm": "DDM",
    "Ecf": "EC",
}


def _fix_heading_acronyms(title: str) -> str:
    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        return _TITLE_TOKEN_FIXES.get(word, word)

    out = re.sub(r"[A-Za-z][A-Za-z0-9]*", repl, title)
    # Soften small words in Title Case headings.
    out = re.sub(r"\bOf\b", "of", out)
    out = re.sub(r"\bAnd\b", "and", out)
    out = re.sub(r"\bOr\b", "or", out)
    out = re.sub(r"\bFor\b", "for", out)
    out = re.sub(r"\bThe\b", "the", out)
    out = re.sub(r"\bTo\b", "to", out)
    out = re.sub(r"\bIn\b", "in", out)
    out = re.sub(r"\bOn\b", "on", out)
    out = re.sub(r"\bWith\b", "with", out)
    out = re.sub(r"\bAn\b", "an", out)
    if out.startswith("the "):
        out = "The " + out[4:]
    if out.upper().startswith("MODULE "):
        m = re.match(r"^(MODULE\s+\d+)\s*[–\-]\s*(.*)$", out, re.I)
        if m:
            out = f"{m.group(1).upper()} – {m.group(2)}"
    return out

def _is_structural(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _PAGE.match(line)
        or _CT.match(line)
        or line.startswith("---")
    )


def _join_markers(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if (
            nxt is not None
            and not _is_structural(nxt)
            and nxt.strip()
            and (_BARE_MARKER.fullmatch(line.strip()) or _BARE_BULLET.fullmatch(line.strip()))
        ):
            marker = line.strip()
            body = nxt.strip()
            if _BARE_BULLET.fullmatch(marker):
                out.append(f"- {body}")
            else:
                out.append(f"{marker} {body}")
            i += 2
            continue
        # "- " alone with trailing spaces already covered; also "-\\ntext" where line is "-"
        out.append(line)
        i += 1
    return out


def _merge_appendix_instructor_fragments(lines: list[str]) -> list[str]:
    """Fold ``### Instructor Training`` back into the preceding Appendix ## title."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEADING.match(line)
        if (
            m
            and m.group(1) == "###"
            and m.group(2).strip() in {"Instructor Training", "Instructor training"}
        ):
            # Find previous ## Appendix heading in out
            for j in range(len(out) - 1, -1, -1):
                hm = _HEADING.match(out[j])
                if hm and hm.group(1) == "##" and "Appendix" in hm.group(2):
                    title = hm.group(2).rstrip()
                    if not title.endswith("Instructor Training"):
                        out[j] = f"## {title} Instructor Training"
                    # Drop this ### and its following content_type if present
                    i += 1
                    if i < len(lines) and _CT.match(lines[i]):
                        i += 1
                    # Drop one blank after
                    if i < len(lines) and not lines[i].strip():
                        i += 1
                    break
            else:
                out.append(line)
                i += 1
            continue
        out.append(line)
        i += 1
    return out


def _fix_headings(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        m = _HEADING.match(line)
        if m:
            out.append(f"{m.group(1)} {_fix_heading_acronyms(m.group(2))}")
        else:
            out.append(line)
    return out


def _collapse_blanks(lines: list[str]) -> list[str]:
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                out.append("")
            continue
        blank_run = 0
        out.append(line.rstrip())
    # Trim leading/trailing blanks in body (keep final newline via join)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _promote_simple_checklist_rows(lines: list[str]) -> list[str]:
    """Turn ``Exercise\\nBrief\\nComp\\nDate`` header noise into a single GFM header when seen.

    Only applied to tight AEI-style four-column checklist headers; leaves complex
    multi-column syllabus grids alone (too lossy to auto-rebuild).
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        window = [lines[j].strip() for j in range(i, min(i + 4, len(lines)))]
        if window == ["Exercise", "Brief", "Comp", "Date"]:
            out.append("| Exercise | Brief | Comp | Date |")
            out.append("| --- | --- | --- | --- |")
            i += 4
            continue
        out.append(lines[i])
        i += 1
    return out


def polish(md: str) -> str:
    # Preserve frontmatter exactly
    if not md.startswith("---\n"):
        body = md
        fm = ""
    else:
        try:
            _, fm_block, body = md.split("---\n", 2)
            fm = f"---\n{fm_block}---\n"
        except ValueError:
            fm, body = "", md

    lines = body.splitlines()
    lines = _join_markers(lines)
    lines = _join_markers(lines)  # second pass for stacked markers
    lines = _merge_appendix_instructor_fragments(lines)
    lines = _fix_headings(lines)
    lines = _promote_simple_checklist_rows(lines)
    lines = _collapse_blanks(lines)
    return fm + "\n".join(lines) + "\n"


def polish_path(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = polish(original)
    if updated != original:
        path.write_text(updated, encoding="utf-8", newline="\n")
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Markdown files to polish (default: corpus/md/other/unit-*.md)",
    )
    args = parser.parse_args()
    paths = args.paths or sorted(OTHER.glob("unit-*.md"))
    changed = 0
    for path in paths:
        if polish_path(path):
            print(f"polished {path}")
            changed += 1
        else:
            print(f"unchanged {path}")
    print(f"{changed}/{len(paths)} files changed")


if __name__ == "__main__":
    main()
