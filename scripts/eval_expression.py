"""Run the frozen expression-evidence comparison without overwriting earlier runs."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.expression_evaluation import run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--stage", choices=["dev", "local", "full"], required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--max-requests", type=int)
parser.add_argument("--max-seconds", type=float)
parser.add_argument("--max-tokens", type=int)
args = parser.parse_args()
report = run(**vars(args))
print(report["status"])
sys.exit(0 if report["status"] == "completed" else 2)
