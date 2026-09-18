"""Run a bounded fixed-suite eval or compare two retained reports."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.evaluation import compare_reports, rescore_report, run_suite

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mode", choices=["mock", "real"], default="mock")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--label", default="baseline")
parser.add_argument("--variant", choices=["baseline", "candidate"], default="candidate")
parser.add_argument("--max-requests", type=int, default=32)
parser.add_argument("--max-seconds", type=float, default=240)
operations = parser.add_mutually_exclusive_group()
operations.add_argument("--compare", nargs=2, metavar=("BASELINE_JSON", "CANDIDATE_JSON"))
operations.add_argument("--rescore", type=Path, metavar="REPORT_JSON")
args = parser.parse_args()
if args.rescore:
    rescore_report(args.rescore, args.output)
elif args.compare:
    compare_reports(*args.compare, args.output)
else:
    if args.max_requests < 1 or args.max_seconds <= 0:
        parser.error("budgets must be positive")
    result = run_suite(
        mode=args.mode,
        output=args.output,
        label=args.label,
        max_requests=args.max_requests,
        max_seconds=args.max_seconds,
        variant=args.variant,
    )
    print(f"{result['mode']}: {len(result['turns'])} turns; failures={result['failed_cases']}")
    print(f"Report: {args.output / 'report.md'}")
    if result["metrics"]["completed_turns"]["value"] != 1:
        sys.exit(2)
