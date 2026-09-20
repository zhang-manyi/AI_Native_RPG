"""Bounded answer-evidence experiment; scoring labels never enter prepare."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.evaluation import ROOT
from ai_native_rpg.memory_evaluation import fingerprint
from ai_native_rpg.tool_evaluation import prepare, run

EXPERIMENT = ROOT / "evals/reports/2026-09-18-answer-evidence"
BUDGET = {"requests": 96, "tokens": 240_000, "seconds": 480, "output_tokens": 600}


def prepare_answer(inputs, client, variant):
    h, manager, memory, tools = prepare(inputs, client, "baseline")
    if variant == "candidate":
        if not (EXPERIMENT / "candidate-selection.json").exists():
            raise ValueError("candidate not selected")
        selection = json.loads(
            (EXPERIMENT / "candidate-selection.json").read_text(encoding="utf-8")
        )
        for relative, digest in selection["files"].items():
            if fingerprint(ROOT / relative) != digest:
                raise ValueError(f"frozen candidate changed: {relative}")
        h._grounded_rewrite = True
    return h, manager, memory, tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "holdout"], required=True)
    parser.add_argument("--variant", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    report = run(
        **vars(args),
        dataset=ROOT / "evals/answer_evidence.json",
        experiment=EXPERIMENT,
        budget=BUDGET,
        prepare_case=prepare_answer,
        extra_sources=[Path(__file__)],
        configuration={"grounded_rewrite": args.variant == "candidate"},
    )
    print(report["status"])
    return 0 if report["status"] in {"completed", "offline"} else 2


if __name__ == "__main__":
    sys.exit(main())
