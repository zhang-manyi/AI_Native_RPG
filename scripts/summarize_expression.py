"""Recompute descriptive metrics from saved evidence and explicit assistant annotations."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.evaluation import _state_consistent
from ai_native_rpg.memory_evaluation import fingerprint, latency_summary, wire_cost, write_json
from ai_native_rpg.schemas.agent_trace import AgentTrace
from ai_native_rpg.schemas.npc_agent import NPCAgentResponse


def rate(values):
    known = [v for v in values if v is not None]
    return {
        "numerator": sum(bool(v) for v in known),
        "denominator": len(known),
        "missing": len(values) - len(known),
        "value": sum(bool(v) for v in known) / len(known) if known else None,
    }


def summarize(root, output):
    root, output = Path(root), Path(output)
    if output.exists():
        raise FileExistsError(output)
    labels = json.loads((root / "review_labels.json").read_text(encoding="utf-8"))
    summary = {
        "reviewer": labels["reviewer"],
        "rubric": labels["rubric"],
        "labels_sha256": fingerprint(root / "review_labels.json"),
        "stages": {},
        "reviewed_rows": [],
    }
    keys = [
        "answer_correct",
        "grounding_issue",
        "disclosure_violation",
        "action_misstatement",
        "note",
    ]
    for stage, annotations in labels["stages"].items():
        report_path = root / stage / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        wires = json.loads((root / stage / "wire.json").read_text(encoding="utf-8"))
        if set(annotations) != {r["id"] for r in report["rows"]}:
            raise ValueError(f"missing or extra review labels for {stage}")
        cases = {c["id"]: c for c in report["cases"]}
        scored = []
        for row in report["rows"]:
            review = dict(zip(keys, annotations[row["id"]], strict=True))
            if "dialogue" not in row and any(review[k] is not None for k in keys[:4]):
                raise ValueError("incomplete outputs must not be assigned behavioural success")
            case = cases[row["case_id"]]
            item = {
                "stage": stage,
                "id": row["id"],
                "variant": row["variant"],
                "case_id": row["case_id"],
                "completed": "dialogue" in row,
                "dialogue_sha256": hashlib.sha256(row.get("dialogue", "").encode()).hexdigest(),
                **review,
                "initial_recall": None,
                "any_recall": None,
                "state_consistent": None,
                "rejected_action": None,
                "required_rejection_covered": None,
            }
            if stage == "holdout-full" and "trace" in row:
                trace = AgentTrace.model_validate(row["trace"])
                response = NPCAgentResponse.model_validate(row["response"])
                item["state_consistent"] = _state_consistent(
                    row["world_before"], row["world_after"], response, trace
                )
                validations = [s for s in trace.steps if s.step_name == "action_validation"]
                item["rejected_action"] = any(
                    s.output_summary.get("approved") is False for s in validations
                )
                if case.get("fixed_action"):
                    item["required_rejection_covered"] = any(
                        s.output_summary.get("approved") is False
                        and s.input_summary.get("action_type")
                        == case["fixed_action"]["action_type"]
                        and s.input_summary.get("target_id") == case["fixed_action"]["target_id"]
                        for s in validations
                    )
                if case.get("required_memory"):
                    retrieval = next(s for s in trace.steps if s.step_name == "memory_retrieval")
                    ids = [m["id"] for m in retrieval.output_summary["episodic"]]
                    item["initial_recall"] = "player_exchange" in ids
                    tool_ids = [
                        mid
                        for s in trace.steps
                        if s.step_name == "tool_call" and s.output_summary.get("ok") is True
                        for mid in s.output_summary.get("result", {}).get("episodic_ids", [])
                    ]
                    item["any_recall"] = "player_exchange" in ids + tool_ids
            if stage == "holdout-local" and case.get("required_memory"):
                item["initial_recall"] = any(
                    m["memory_id"] == "player_exchange"
                    for m in case["upstream"]["retrieval"]["episodic"]
                )
                item["any_recall"] = item["initial_recall"]
            item["answer_in_context"] = row.get("answer_in_final_context")
            if case.get("answer_patterns") and not review["answer_correct"] and item["completed"]:
                item["failure_signals"] = []
                if item["any_recall"] is False:
                    item["failure_signals"].append("retrieval_omission")
                elif item["answer_in_context"] is False:
                    item["failure_signals"].append("context_loss_or_not_supplied")
                if item["answer_in_context"] is True:
                    item["failure_signals"].append("evidence_unused_or_misused_review_required")
            scored.append(item)
        summary["reviewed_rows"].extend(scored)
        stage_summary = {"report_sha256": fingerprint(report_path), "variants": {}}
        for variant in ("baseline", "candidate"):
            rows = [r for r in report["rows"] if r["variant"] == variant]
            items = [r for r in scored if r["variant"] == variant]
            selected_wire = [wire for r in rows for wire in wires[slice(*r["wire_range"])]]
            answer_items = [i for i in items if cases[i["case_id"]].get("answer_patterns")]
            memory_items = [i for i in items if cases[i["case_id"]].get("required_memory")]
            rejection_items = [i for i in items if cases[i["case_id"]].get("fixed_action")]
            metrics = {
                "completion": rate([i["completed"] for i in items]),
                "answer_correct": rate([i["answer_correct"] for i in answer_items]),
                "literal_answer": rate(
                    [
                        r.get("literal_answer")
                        for r in rows
                        if cases[r["case_id"]].get("answer_patterns")
                    ]
                ),
                "answer_in_context": rate([i["answer_in_context"] for i in answer_items]),
                "grounding_issue": rate([i["grounding_issue"] for i in items]),
                "disclosure_violation": rate([i["disclosure_violation"] for i in items]),
                "action_misstatement": rate([i["action_misstatement"] for i in items]),
                "initial_recall": rate([i["initial_recall"] for i in memory_items]),
                "any_recall": rate([i["any_recall"] for i in memory_items]),
                "state_consistency": rate([i["state_consistent"] for i in items]),
                "required_rejection_covered": rate(
                    [i["required_rejection_covered"] for i in rejection_items]
                ),
                "actual_rejected_actions": sum(i["rejected_action"] is True for i in items),
                "latency_all_attempted_ms": latency_summary([r["latency_ms"] for r in rows]),
                "cost": wire_cost(selected_wire),
                "currency_cost": None,
            }
            # Local fixed verdict coverage is not a model decision score.
            if stage == "holdout-local":
                metrics["required_rejection_covered"] = rate(
                    [
                        not cases[i["case_id"]]["upstream"]["verdict"]["approved"]
                        for i in rejection_items
                    ]
                )
            stage_summary["variants"][variant] = metrics
        summary["stages"][stage] = stage_summary
    write_json(output, summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.root, args.output)
    print(json.dumps(result["stages"], ensure_ascii=False, indent=2))
