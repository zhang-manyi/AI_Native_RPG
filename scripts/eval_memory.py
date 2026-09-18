"""Run memory retrieval or bounded real multi-turn probes; never silently fall back."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.memory_evaluation import (
    MODEL_DIR,
    compare_retrieval,
    describe_dataset,
    run_retrieval,
    run_sequences,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("phase", choices=["describe", "retrieval", "sequences", "compare"])
parser.add_argument("--output", type=Path)
parser.add_argument("--backend", choices=["hashing", "qwen"], default="hashing")
parser.add_argument("--split", choices=["dev", "test"], default="dev")
parser.add_argument("--model-path", type=Path, default=MODEL_DIR)
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--max-requests", type=int, default=180)
parser.add_argument("--max-seconds", type=float, default=1200)
parser.add_argument("--reports", nargs="+", type=Path)
args = parser.parse_args()
if args.phase == "describe":
    print(json.dumps(describe_dataset(), ensure_ascii=False))
    sys.exit(0)
if not args.output:
    parser.error("--output required")
if args.phase == "compare":
    if not args.reports:
        parser.error("--reports required")
    compare_retrieval(args.reports, args.output)
    sys.exit(0)
if args.phase == "retrieval":
    report = run_retrieval(
        output=args.output, backend=args.backend, split=args.split, model_path=args.model_path
    )
else:
    report = run_sequences(
        output=args.output,
        model_path=args.model_path,
        repeats=args.repeats,
        max_requests=args.max_requests,
        max_seconds=args.max_seconds,
    )
print(json.dumps({k: report[k] for k in ("status", "summary", "error_type") if k in report}))
sys.exit(0 if report["status"] == "completed" else 2)
