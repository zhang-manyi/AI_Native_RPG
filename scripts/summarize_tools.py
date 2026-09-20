"""Recompute tool/task metrics from immutable runs and explicit assistant reviews."""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.evaluation import aggregate
from ai_native_rpg.memory_evaluation import fingerprint, latency_summary, wire_cost, write_json
from ai_native_rpg.tool_evaluation import structural_scores


def summarize(directory, labels):
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    wire = json.loads((directory / "wire.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in report["cases"]}
    rows, tasks = [], defaultdict(list)
    for original in report["rows"]:
        row = dict(original)
        expect = cases[row["case_id"]]["expect"][row["turn"]]
        scores = structural_scores(row, expect)
        if json.loads(json.dumps(scores)) != row["scores"]:
            raise ValueError(f"frozen structural score drift: {row['id']}")
        label = labels.get(row["id"])
        complete = "dialogue" in row
        if complete:
            if label is None:
                raise ValueError(f"missing assistant review: {row['id']}")
            digest = hashlib.sha256(row["dialogue"].encode()).hexdigest()
            if label["dialogue_sha256"] != digest:
                raise ValueError("review is bound to a different output")
            for field in ("false_success", "disclosure", "unsupported", "expression_consistent"):
                if not isinstance(label[field], bool):
                    raise ValueError(f"review must explicitly assess {field}")
        for field in ("false_success", "disclosure", "unsupported", "expression_consistent"):
            scores[field] = (int(label[field]), 1) if complete else None
        goal = expect["goal"]
        field = (
            "answer_correct"
            if goal == "answer"
            else "refusal_appropriate"
            if goal == "deny"
            else None
        )
        if field:
            if complete and not isinstance(label.get(field), bool):
                raise ValueError(f"review must explicitly assess {field}")
            scores[field] = (int(label[field]), 1) if complete else None
        if row.get("parameters_needing_review", 0):
            params = label.get("memory_parameters") if label else None
            if params is not None:
                if len(params) != row["parameters_needing_review"]:
                    raise ValueError("every unscored memory query must be reviewed")
                if not all(isinstance(p, bool) for p in params):
                    raise ValueError("memory parameter reviews must be booleans")
                n, d = scores["tool_parameters"]
                scores["tool_parameters"] = (n + sum(params), d + len(params))
        success = (
            complete
            and bool(label["expression_consistent"])
            and not (label["false_success"] or label["disclosure"])
            and scores["state_consistency"] == (1, 1)
        )
        if field:
            success = success and label[field]
        if goal in {"move", "deny"}:
            success = success and scores["state_goal"] == (1, 1)
        if row["mode"] == "local":
            # The action was fixed by the fixture, not selected by the model.
            scores.pop("action_selection", None)
        scores["turn_goal_success"] = (int(success), 1)
        if row["mode"] == "full":
            tasks[(row["case_id"], row["repeat"])].append(bool(success))
        if row["case_id"].endswith("public"):
            scores["public_argument_quality"] = scores["tool_parameters"]
            scores["public_all_arguments_correct"] = (
                (
                    int(
                        scores["tool_parameters"][0] == scores["tool_parameters"][1]
                        and scores["tool_parameters"][1] > 0
                    ),
                    1,
                )
                if complete
                else None
            )
        if goal == "deny" and row["mode"] == "full":
            scores[f"rejection_{expect['action_type']}"] = scores["actual_rejection_path"]
        rows.append({"id": row["id"], "mode": row["mode"], "scores": scores})
    modes = {
        mode: aggregate([r["scores"] for r in rows if r["mode"] == mode])
        for mode in ("full", "local")
    }
    modes["full"]["task_success"] = aggregate(
        [{"task_success": (int(all(outcomes)), 1)} for outcomes in tasks.values()]
    )["task_success"]
    return {
        "report_sha256": fingerprint(directory / "report.json"),
        "wire_sha256": fingerprint(directory / "wire.json"),
        "metrics": modes,
        "cost": wire_cost(wire),
        "latency": {
            mode: latency_summary([r["latency_ms"] for r in report["rows"] if r["mode"] == mode])
            for mode in ("full", "local")
        },
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    reviews = json.loads((args.root / "review_labels.json").read_text(encoding="utf-8"))
    labels = {r["id"]: r for r in reviews["rows"]}
    if len(labels) != len(reviews["rows"]):
        raise ValueError("duplicate review id")
    runs = reviews["runs"]
    result = {name: summarize(args.root / name, labels) for name in runs}
    write_json(
        args.output,
        {
            "review_source": reviews["review_source"],
            "review_sha256": fingerprint(args.root / "review_labels.json"),
            "runs": result,
        },
    )


if __name__ == "__main__":
    main()
