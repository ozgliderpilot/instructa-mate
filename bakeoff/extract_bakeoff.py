#!/usr/bin/env python3
"""Extractor bake-off for the InstructaMate stage-1 parser.

Compares three PDF text-layer extractors on the *worst* real pages so we can
pick the primary extractor before committing (see parser-build.md):

  1. PyMuPDF (fitz)        - get_text("text"), the deterministic primary candidate
  2. pdfplumber            - extract_text() + extract_tables() (table/coords sidekick)
  3. pdftotext -layout     - poppler, the existing oracle to diff against

For each (sample, extractor) it writes the raw extracted text to out/ and prints
a metrics table scoring the things parser-build.md / ADR 0002 actually care about:

  - U+FFFD replacement chars (mangled bullets/dashes/footer glyphs)
  - spurious in-word spaces  (ligature damage: `f light`, `saf ety`, `ef f ective`)
  - real unicode ligatures   (ﬁ ﬂ ...) left unexpanded
  - footer recovery          (can we read `Page U - P` for the citation marker?)
  - table reconstruction      (pdfplumber line-detection on the competency table)

Run:  python bakeoff/extract_bakeoff.py
Output dumps land in bakeoff/out/<sample>__<extractor>.txt for eyeballing/diffing.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console is often cp125x

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
OUT = Path(__file__).resolve().parent / "out"

TRAINER = CORPUS / "00 Combined Trainer Guides units 1-26 Solo  BBB.pdf"
PILOT = CORPUS / "00-Combined Pilot Guides 1-26 Solo.pdf"


@dataclass(frozen=True)
class Sample:
    name: str
    pdf: Path
    page: int  # 0-indexed physical page
    note: str


# Golden hard pages located empirically (see parser-build.md bake-off step).
SAMPLES = [
    Sample("trainer-u5-p1-footer", TRAINER, 30, "Unit-5 first page; footer mangled to U+FFFD"),
    Sample("trainer-u5-competency", TRAINER, 31, "scrambled COMPETENCY ELEMENTS table"),
    Sample("trainer-u5-patter", TRAINER, 34, "Suggested Patter block (Page 5-5)"),
    Sample("pilot-u1-p1", PILOT, 2, "ligature-heavy first content page (Page 1-1)"),
    Sample("pilot-u1-p2", PILOT, 3, "ligature-heavy (Page 1-2)"),
]

# Table-heavy pages, to decide whether the LLM table-fallback is needed at all.
# verdict: does PyMuPDF reading-order text + pdfplumber line-detection recover the
# table verbatim & coherently, or is it genuinely scrambled (=> LLM fallback)?
TABLE_SAMPLES = [
    Sample("tbl-competency", TRAINER, 31, "bordered 2-col ELEMENT/STANDARDS table"),
    Sample("tbl-problem-cause", TRAINER, 39, "2-col COMMON PROBLEMS Problem/Probable Cause"),
    Sample("tbl-control-colour", TRAINER, 79, "colour-coded control table (CANOPY/TRIM/...)"),
    Sample("tbl-control-pilot", PILOT, 69, "colour-coded control table (Pilot side)"),
]


# A single lowercase letter (not 'a'/'i', the only real one-letter words) floating
# between spaces => almost always ligature/word-split damage: `saf ety`, `f light`.
SPURIOUS_SPACE = re.compile(r"(?<=[a-z]) (?![ai] )[b-hj-z] (?=[a-z])")
# Also catch leading-fragment form `f light` (letter, space, word).
SPURIOUS_LEAD = re.compile(r"(?<![a-zA-Z])(?![ai]\b)[b-hj-z] (?=[a-z]{2,})")
LIGATURES = re.compile(r"[ﬀ-ﬆ]")
FOOTER = re.compile(r"Page\s*\d+\s*-\s*\d+")


def metrics(text: str) -> dict:
    fted = FOOTER.search(text)
    return {
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "ufffd": text.count("�"),
        "spurious_space": len(SPURIOUS_SPACE.findall(text)) + len(SPURIOUS_LEAD.findall(text)),
        "ligatures": len(LIGATURES.findall(text)),
        "footer": fted.group(0) if fted else "MANGLED",
    }


def extract_pymupdf(pdf: Path, page: int) -> str:
    doc = fitz.open(pdf)
    try:
        return doc[page].get_text("text")
    finally:
        doc.close()


def extract_pdfplumber(pdf: Path, page: int) -> str:
    with pdfplumber.open(pdf) as doc:
        return doc.pages[page].extract_text() or ""


def extract_pdftotext(pdf: Path, page: int) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return "<<pdftotext not on PATH>>"
    # pdftotext pages are 1-indexed; physical page i -> i+1
    res = subprocess.run(
        [exe, "-layout", "-f", str(page + 1), "-l", str(page + 1), str(pdf), "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return res.stdout


EXTRACTORS = {
    "pymupdf": extract_pymupdf,
    "pdfplumber": extract_pdfplumber,
    "pdftotext": extract_pdftotext,
}


def pdfplumber_tables(pdf: Path, page: int) -> int:
    """How many tables does pdfplumber's line/edge detection find? (0 == none)."""
    with pdfplumber.open(pdf) as doc:
        return len(doc.pages[page].extract_tables())


def render_gfm(pdf: Path, page: int) -> tuple[str, str]:
    """pdfplumber line-detection -> GFM. Returns (shape_str, markdown)."""
    with pdfplumber.open(pdf) as doc:
        tables = doc.pages[page].extract_tables()
    if not tables:
        return "0 tables", ""
    t = max(tables, key=len)
    ncol = max(len(r) for r in t)
    out = []
    for ri, row in enumerate(t):
        cells = [(c or "").replace("\n", " ").strip() for c in row] + [""] * (ncol - len(row))
        out.append("| " + " | ".join(cells) + " |")
        if ri == 0:
            out.append("| " + " | ".join(["---"] * ncol) + " |")
    return f"{len(t)} rows x {ncol} cols", "\n".join(out)


def table_report() -> None:
    """Emit, per table page, the three rival renderings + a reading-order verdict.

    The question is RAG-usability, not a perfect grid: does the linear text keep
    each table cell coherent (verbatim, paired), or does it interleave (scramble)?
    Human eyeballs the dumps; the proxy below flags pdftotext-style interleave.
    """
    print(f"\n{'='*78}\nTABLE BAKE-OFF  (LLM-fallback decision)\n{'='*78}")
    for s in TABLE_SAMPLES:
        mu = extract_pymupdf(s.pdf, s.page)          # reading-order text
        pt = extract_pdftotext(s.pdf, s.page)         # -layout (visual columns)
        shape, gfm = render_gfm(s.pdf, s.page)        # pdfplumber GFM attempt
        (OUT / f"{s.name}__pymupdf.txt").write_text(mu, encoding="utf-8")
        (OUT / f"{s.name}__pdftotext.txt").write_text(pt, encoding="utf-8")
        (OUT / f"{s.name}__pdfplumber.md").write_text(gfm, encoding="utf-8")
        # interleave proxy: a "label:" line immediately followed by a line that is
        # clearly the *other* column is the scramble signature pdftotext produces.
        print(f"\n# {s.name} — {s.note}")
        print(f"  pdfplumber GFM: {shape}")
        print(f"  pymupdf reading-order chars={len(mu)}  pdftotext chars={len(pt)}")
        print(f"  -> dumps: out/{s.name}__{{pymupdf.txt, pdftotext.txt, pdfplumber.md}}")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    rows = []
    for s in SAMPLES:
        for ename, fn in EXTRACTORS.items():
            text = fn(s.pdf, s.page)
            (OUT / f"{s.name}__{ename}.txt").write_text(text, encoding="utf-8")
            m = metrics(text)
            m["sample"], m["extractor"] = s.name, ename
            rows.append(m)

    # ---- metrics table ----
    hdr = f"{'sample':28} {'extractor':11} {'chars':>6} {'fffd':>4} {'splitwd':>7} {'lig':>4}  footer"
    print("\n" + hdr)
    print("-" * len(hdr))
    last = None
    for r in rows:
        if last and last != r["sample"]:
            print()
        print(f"{r['sample']:28} {r['extractor']:11} {r['chars']:>6} {r['ufffd']:>4} "
              f"{r['spurious_space']:>7} {r['ligatures']:>4}  {r['footer']}")
        last = r["sample"]

    # ---- pdfplumber table detection on the competency page ----
    comp = next(s for s in SAMPLES if "competency" in s.name)
    n = pdfplumber_tables(comp.pdf, comp.page)
    print(f"\npdfplumber.extract_tables() on {comp.name}: {n} table(s) detected "
          f"({'usable for GFM' if n else 'none -> LLM-fallback candidate'})")

    table_report()

    print(f"\nDumps written to {OUT.relative_to(ROOT)}/  (diff side-by-side to judge quality)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
