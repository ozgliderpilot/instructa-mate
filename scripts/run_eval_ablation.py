"""Run Stage 3 ablation curve on the golden set (#40).

Scores recall@k, automated refusal, and (optional) LLM-as-judge citation
faithfulness / groundedness for vector → hybrid → hybrid+rerank.

Requires MONGODB_URI and VOYAGE_API_KEY. ANTHROPIC_API_KEY enables refusal +
judge metrics. LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY enable Langfuse traces
(install optional ``eval`` extra).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from instructamate.stage3_ingest import VoyageEmbedder, chunks_collection
from instructamate.stage3_retrieve import VoyageReranker
from instructamate.stage4_qa import AnthropicCompleter
from instructamate.stage5_eval import (
    CompleterJudge,
    LangfuseTracer,
    NullTracer,
    load_golden_set,
    run_ablation_curve,
)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Evaluate only the first N golden items (0 = all)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=10,
        help="recall@k cutoff (default: 10 = DEFAULT_P)",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip LLM-as-judge faithfulness/groundedness",
    )
    parser.add_argument(
        "--no-qa",
        action="store_true",
        help="Skip refuse-or-cite Q&A (recall-only curve)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON report to this path",
    )
    args = parser.parse_args()
    _load_dotenv()

    if not os.environ.get("MONGODB_URI") or not os.environ.get("VOYAGE_API_KEY"):
        print("MONGODB_URI and VOYAGE_API_KEY are required", file=sys.stderr)
        return 2

    items = load_golden_set()
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    completer = None
    judge = None
    if not args.no_qa:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY required unless --no-qa", file=sys.stderr)
            return 2
        completer = AnthropicCompleter()
        if not args.no_judge:
            judge = CompleterJudge(completer)

    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        tracer = LangfuseTracer()
    else:
        tracer = NullTracer()
        print("Langfuse keys absent — tracing disabled", file=sys.stderr)

    collection = chunks_collection(os.environ["MONGODB_URI"])
    points = run_ablation_curve(
        items,
        collection,
        embedder=VoyageEmbedder(),
        completer=completer,
        judge=judge,
        reranker=VoyageReranker(),
        k=args.k,
        tracer=tracer,
    )

    report = {
        "items": len(items),
        "k": args.k,
        "points": [
            {
                "step": p.step,
                "recall_at_k": p.recall_at_k,
                "refusal_accuracy": p.refusal_accuracy,
                "faithfulness": p.faithfulness,
                "groundedness": p.groundedness,
            }
            for p in points
        ],
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
