"""Run the frozen, bounded tool/task suite (never overwrites an existing run)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.tool_evaluation import run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--split", choices=["dev", "holdout"], required=True)
parser.add_argument("--variant", choices=["baseline", "candidate"], required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--offline", action="store_true")
args = parser.parse_args()
report = run(**vars(args))
print(report["status"])
sys.exit(0 if report["status"] in {"completed", "offline"} else 2)
