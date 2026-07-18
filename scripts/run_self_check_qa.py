"""Run refuse-or-cite Q&A over all Pilot Guide self_check questions.

Writes/updates evals/self_check_pilot_qa_runs.json incrementally so a partial
run can be resumed. Requires MONGODB_URI, VOYAGE_API_KEY, ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection
from instructamate.stage3_retrieve import VoyageReranker
from instructamate.stage4_qa import AnthropicCompleter, answer_question

PILOT = ROOT / "corpus" / "md" / "pilot"
OUT = ROOT / "evals" / "self_check_pilot_qa_runs.json"

INTRO = re.compile(r"^Use these questions to test your knowledge of the unit\.?\s*$", re.I)
NUMBERED = re.compile(r"^(\d+)\.\s+(.+)$")
BULLET = re.compile(r"^[-*]\s+(.+)$")
Q_PREFIX = re.compile(r"^Q\s*(\d+)\.?\s*(.*)$", re.I)
CHOICE = re.compile(r"^[A-F]\.\s+")
SUBPART = re.compile(r"^[a-c]\.\s+")
CONTENT_TYPE = re.compile(r"<!--\s*content_type:\s*(\w+)\s*-->")
PAGE = re.compile(r"<!--\s*page:\s*[^>]+-->")
FRONT_MATTER_UNIT = re.compile(r"^unit:\s*(.+)$", re.M)


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _unit_from_path(path: Path, text: str) -> str:
    match = FRONT_MATTER_UNIT.search(text)
    if match:
        return match.group(1).strip().strip('"')
    return path.stem.removeprefix("unit-").lstrip("0") or "0"


def _iter_content_sections(text: str):
    lines = text.splitlines()
    current_ct: str | None = None
    buf: list[str] = []
    for line in lines:
        match = CONTENT_TYPE.search(line)
        if match:
            if current_ct is not None:
                yield current_ct, "\n".join(buf)
            current_ct = match.group(1)
            buf = []
            continue
        if current_ct is not None and line.startswith("## "):
            yield current_ct, "\n".join(buf)
            current_ct = None
            buf = []
            continue
        if current_ct is not None:
            buf.append(line)
    if current_ct is not None:
        yield current_ct, "\n".join(buf)


def _parse_questions(body: str) -> list[str]:
    questions: list[str] = []
    pending: list[str] | None = None

    def flush() -> None:
        nonlocal pending
        if pending:
            text = " ".join(pending).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                questions.append(text)
        pending = None

    for raw in body.splitlines():
        line = PAGE.sub("", raw).rstrip()
        stripped = line.strip()
        if not stripped or INTRO.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        if CHOICE.match(stripped):
            # MCQ options are not part of the open question stem.
            continue
        if SUBPART.match(stripped) and pending is not None:
            # Keep a./b. sub-prompts on multi-part stems (e.g. unit 7 Q6).
            pending.append(stripped)
            continue

        numbered = NUMBERED.match(stripped)
        bullet = BULLET.match(stripped) if numbered is None else None
        q_only = Q_PREFIX.match(stripped) if numbered is None and bullet is None else None
        if numbered or bullet or q_only:
            flush()
            if numbered:
                text = numbered.group(2).strip()
            elif bullet:
                text = bullet.group(1).strip()
            else:
                text = q_only.group(2).strip()
            qmatch = Q_PREFIX.match(text)
            if qmatch:
                text = qmatch.group(2).strip() or text
            pending = [text]
            continue

        if pending is not None:
            pending.append(stripped)

    flush()
    return questions


def collect_questions() -> list[dict]:
    items: list[dict] = []
    for path in sorted(PILOT.glob("unit-*.md")):
        text = path.read_text(encoding="utf-8")
        unit = _unit_from_path(path, text)
        for content_type, body in _iter_content_sections(text):
            if content_type != "self_check":
                continue
            for index, question in enumerate(_parse_questions(body), start=1):
                items.append(
                    {
                        "id": f"self_check-pilot-{unit}-{index:02d}",
                        "source": "pilot",
                        "unit": str(unit),
                        "n": index,
                        "question": question,
                        "path": path.relative_to(ROOT).as_posix(),
                    }
                )
    return items


def _result_payload(item: dict, result) -> dict:
    return {
        **item,
        "grounded": result.grounded,
        "answer": result.answer,
        "citations": [
            {"source": c.source, "unit": c.unit, "page": c.page}
            for c in result.citations
        ],
    }


def main() -> int:
    _load_dotenv()
    missing = [
        key
        for key in ("MONGODB_URI", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY")
        if not os.environ.get(key)
    ]
    if missing:
        print(f"missing env: {', '.join(missing)}", file=sys.stderr)
        return 1

    questions = collect_questions()
    by_id = {item["id"]: item for item in questions}

    existing: dict[str, dict] = {}
    if OUT.exists():
        prior = json.loads(OUT.read_text(encoding="utf-8"))
        for row in prior.get("items", []):
            if row.get("id") and "answer" in row:
                existing[row["id"]] = row

    collection = chunks_collection(os.environ["MONGODB_URI"])
    embedder = VoyageEmbedder()
    completer = AnthropicCompleter()
    reranker = VoyageReranker()

    items_out: list[dict] = []
    done = 0
    skipped = 0
    failed = 0
    total = len(questions)
    started = time.time()

    for item in questions:
        item_id = item["id"]
        if item_id in existing and existing[item_id].get("answer") is not None:
            items_out.append(existing[item_id])
            skipped += 1
            continue

        try:
            result = answer_question(
                item["question"],
                collection,
                embedder=embedder,
                completer=completer,
                reranker=reranker,
            )
            row = _result_payload(item, result)
        except Exception as exc:  # noqa: BLE001 — record and continue the batch
            failed += 1
            row = {
                **item,
                "grounded": None,
                "answer": None,
                "citations": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"FAIL {item_id}: {row['error']}", flush=True)

        items_out.append(row)
        done += 1
        existing[item_id] = row

        payload = {
            "version": 1,
            "description": (
                "Pilot Guide self_check questions run through answer_question "
                "(hybrid retrieve + refuse-or-cite). Draft for #39 review — not gold."
            ),
            "source": "corpus/md/pilot **/SELF-CHECK QUESTIONS",
            "total": total,
            "completed": len([r for r in items_out if r.get("answer") is not None]),
            "failed": len([r for r in items_out if r.get("error")]),
            "items": items_out,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        elapsed = time.time() - started
        rate = done / elapsed if elapsed else 0
        remaining = total - skipped - done
        eta = remaining / rate if rate else 0
        flag = "ok" if row.get("grounded") else ("refuse" if row.get("answer") else "err")
        print(
            f"[{skipped + done}/{total}] {item_id} {flag} "
            f"({elapsed:.0f}s elapsed, ~{eta:.0f}s left)",
            flush=True,
        )

    grounded = sum(1 for r in items_out if r.get("grounded") is True)
    refused = sum(1 for r in items_out if r.get("grounded") is False)
    print(
        f"done: wrote {OUT.relative_to(ROOT)} "
        f"(grounded={grounded} refused={refused} failed={failed} resumed_skip={skipped})",
        flush=True,
    )
    # unused guard for linters
    _ = by_id
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
