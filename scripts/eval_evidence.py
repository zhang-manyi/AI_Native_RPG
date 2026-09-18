"""Compare evidence inclusion with identical upstream inputs and disclosure controls."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.evidence_evaluation import run_replay

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--max-requests", type=int, default=96)
parser.add_argument("--max-seconds", type=float, default=600)
args = parser.parse_args()
report = run_replay(
    source=args.source,
    output=args.output,
    repeats=args.repeats,
    max_requests=args.max_requests,
    max_seconds=args.max_seconds,
)
print(report["status"])
sys.exit(0 if report["status"] == "completed" else 2)
