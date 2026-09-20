"""Offline structural checks plus explicit, hash-bound assistant assessments."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.evaluation import aggregate
from ai_native_rpg.memory_evaluation import fingerprint, latency_summary, wire_cost, write_json
from ai_native_rpg.tool_evaluation import structural_scores


def summarize(directory, labels):
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    wire = json.loads((directory / "wire.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in report["cases"]}
    records = []
    for original in report["rows"]:
        row = dict(original)
        expect = cases[row["case_id"]]["expect"][row["turn"]]
        scores = structural_scores(row, expect)
        if json.loads(json.dumps(scores)) != row["scores"]:
            raise ValueError("structural score drift")
        label = labels.get(row["id"])
        complete = "dialogue" in row
        fields = ["unsupported", "disclosure", "false_success", "expression_consistent"]
        if "answer" in expect:
            fields.append("answer_correct")
        if expect["goal"] == "deny":
            fields.append("refusal_appropriate")
        if complete:
            if (
                not label
                or label["dialogue_sha256"] != hashlib.sha256(row["dialogue"].encode()).hexdigest()
            ):
                raise ValueError(f"missing or stale review: {row['id']}")
            if label.get("review_source") != "Codex assistant review" or not label.get("reason"):
                raise ValueError("review identity and reason required")
        for field in fields:
            if complete and not isinstance(label.get(field), bool):
                raise ValueError(f"explicit boolean required: {row['id']}/{field}")
            scores[field] = (int(label[field]), 1) if complete else None
        if "answer" in expect:
            scores["ordinary_answer" if expect["goal"] == "answer" else "answer_with_refusal"] = (
                scores["answer_correct"]
            )
        success = bool(complete and scores["state_consistency"] == (1, 1))
        if complete:
            success &= not (label["disclosure"] or label["false_success"])
            success &= label["expression_consistent"]
            for field in ("answer_correct", "refusal_appropriate"):
                if field in fields:
                    success &= label[field]
            if "state_goal" in scores:
                success &= scores["state_goal"] == (1, 1)
        scores["task_success"] = (int(success), 1)
        scores["grounded_success"] = (
            (int(success and not label["unsupported"]), 1) if success else (0, 1)
        )
        if row["mode"] == "local":
            scores.pop("action_selection", None)
        if expect["goal"] == "deny":
            scores[f"rejection_{expect['action_type']}"] = scores["actual_rejection_path"]
        branch = "after_action" if row["journal"] else "direct"
        if complete:
            scores[f"branch_{branch}"] = (1, 1)
        if "answer" in expect:
            scores[f"answer_{branch}"] = scores["answer_correct"]
        records.append({"id": row["id"], "mode": row["mode"], "scores": scores})
    return {
        "report_sha256": fingerprint(directory / "report.json"),
        "wire_sha256": fingerprint(directory / "wire.json"),
        "metrics": {
            mode: aggregate([r["scores"] for r in records if r["mode"] == mode])
            for mode in ("full", "local")
        },
        "cost": wire_cost(wire),
        "latency": {
            mode: latency_summary([r["latency_ms"] for r in report["rows"] if r["mode"] == mode])
            for mode in ("full", "local")
        },
        "rows": records,
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
        raise ValueError("duplicate review")
    write_json(
        args.output,
        {
            "review_source": "Codex assistant review",
            "review_sha256": fingerprint(args.root / "review_labels.json"),
            "runs": {name: summarize(args.root / name, labels) for name in reviews["runs"]},
        },
    )


if __name__ == "__main__":
    main()
