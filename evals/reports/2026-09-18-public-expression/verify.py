"""Recompute structural checks, cost and transcript; semantic verdicts are separate review."""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_native_rpg.schemas.world_state import WorldState  # noqa: E402
from ai_native_rpg.world.player_view import player_view  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_run(name, labels):
    directory = HERE / name
    report, wire = read(directory / "report.json"), read(directory / "wire.json")
    rows = report["steps"]
    traces = [read(p) for p in (directory / "traces").glob("*.json")]
    dialogues = {
        row["index"]: event["data"]["dialogue"]
        for row in rows
        for event in row["events"]
        if event["type"] == "dialogue"
    }
    checks = {
        "all_22_steps_and_truth": len(rows) == 22 and report["ending_id"] == "truth_uncovered",
        "no_missing_requests": all(r["status"] == 200 and r["usage"] for r in wire),
        "no_fallback": report["expression_fallbacks"] == 0,
        "no_turn_failure": all(e["type"] != "turn_failed" for row in rows for e in row["events"]),
        "denied_move_unchanged": rows[0]["before"]["world"] == rows[0]["after"]["world"]
        and any(e["type"] == "move" and not e["data"]["approved"] for e in rows[0]["events"]),
        "both_restores_exact": all(
            rows[index]["before"][key] == rows[index]["after"][key]
            for index in (6, 20)
            for key in ("world", "memories", "transcript")
        ),
        "terminal_input_rejected": rows[21]["http_response"]["status"] == 400
        and rows[21]["before"]["world"] == rows[21]["after"]["world"],
        "all_npcs_use_new_boundary_only": all(
            flags
            == {
                "public_expression": True,
                "grounded_rewrite": False,
                "retain_dialogue_evidence": False,
                "filtered_dialogue_evidence": False,
            }
            for flags in report["runtime_flags"].values()
        ),
        "source_archive_valid": all(
            sha(directory / "sources" / path) == digest
            for path, digest in report["source_hashes"].items()
        ),
        "review_covers_every_dialogue": set(dialogues) == {r["step"] for r in labels}
        and all(
            hashlib.sha256(dialogues[r["step"]].encode()).hexdigest() == r["sha256"] for r in labels
        ),
        "usage_reconciles": sum(r["usage"]["total_tokens"] for r in wire)
        == report["cost"]["tokens"]["total_tokens"],
    }
    expression_payloads = []
    expression_steps = set()
    provenance_checks = []
    action_checks = []
    for row in rows:
        start, end = row["wire_range"]
        after_view = player_view(WorldState.model_validate(row["after"]["world"]), "player_1")
        for record in wire[start:end]:
            messages = record["request_body"]["messages"]
            if len(messages) < 2:
                continue
            try:
                payload = json.loads(messages[1]["content"])
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or "allowed_evidence" not in payload:
                continue
            expression_payloads.append(payload)
            expression_steps.add(row["index"])
            evidence = payload["allowed_evidence"]
            npc = row["before"]["scene"]["current_npc_id"]
            memories = {m["memory_id"]: m for m in row["before"]["memories"][npc]["episodic"]}
            provenance_checks.append(
                all(
                    p["memory_id"] in memories
                    and memories[p["memory_id"]]["source_player_id"] == "player_1"
                    and memories[p["memory_id"]]["player_statement"] == p["text"]
                    for p in evidence["player_statements"]
                )
            )
            provenance_checks.append(
                all(
                    key in after_view.visible_facts and after_view.visible_facts[key] == value
                    for key, value in evidence["public_facts"].items()
                )
            )
        if row["index"] in (5, 7, 11):
            actions = []
            for record in wire[start:end]:
                raw = record["response_body"]["choices"][0]["message"].get("content")
                if raw:
                    parsed = json.loads(raw)
                    if parsed.get("action"):
                        actions.append(parsed["action"])
            npc = row["before"]["scene"]["current_npc_id"]
            before = row["before"]["world"]["relationships"][npc]["player_1"]
            after = row["after"]["world"]["relationships"][npc]["player_1"]
            if not actions:
                action_checks.append(
                    all(before[k] == after[k] for k in ("trust", "fear", "respect"))
                )
            else:
                action = actions[-1]
                validations = [
                    s
                    for t in traces
                    if t["final_dialogue"] == dialogues[row["index"]]
                    for s in t["steps"]
                    if s["step_name"] == "action_validation"
                ]
                if (
                    action["action_type"] == "adjust_relationship"
                    and validations[0]["output_summary"]["approved"]
                ):
                    action_checks.append(
                        all(
                            after[k] == min(100, max(-100, before[k] + action["payload"].get(k, 0)))
                            for k in ("trust", "fear", "respect")
                        )
                    )
                else:
                    action_checks.append(False)  # A different action needs an explicit audit.
    checks["all_eight_dialogues_use_public_expression"] = expression_steps == set(dialogues)
    checks["public_sources_match_state_and_memory"] = all(provenance_checks)
    checks["free_turn_action_deltas_match_verdicts"] = all(action_checks)
    checks["expression_fields_allowlisted"] = all(
        set(p) == {"character", "player_question", "allowed_evidence"}
        and set(p["character"]) == {"name", "public_note", "traits"}
        and set(p["allowed_evidence"])
        <= {"player_statements", "public_facts", "action_result", "resolved_outcome"}
        and set(p["allowed_evidence"]["action_result"])
        <= {"status", "kind", "revealed_fact", "relationship_now", "actual_location", "quest_stage"}
        and set(p["allowed_evidence"].get("resolved_outcome", {})) <= {"summary", "reply"}
        for p in expression_payloads
    )
    inputs = json.dumps([r["request_body"] for r in wire], ensure_ascii=False)
    checks["review_labels_not_sent"] = all(
        label["reason"] not in inputs and label["sha256"] not in inputs for label in labels
    )
    lines = [f"# {name} 完整玩家记录", "", "模型与固定内容分开；判断见review_labels.json。", ""]
    for p in report["initial"]["scene"]["passages"]:
        lines.append(f"- 固定 {p['speaker']}：{p['text']}")
    for row in rows:
        lines += [
            "",
            f"## 步骤 {row['index']:02d}",
            "",
            json.dumps(row["action"], ensure_ascii=False),
            "",
        ]
        line = dialogues.get(row["index"])
        if line:
            lines.append("真实模型：" + line)
        for p in row["after"]["scene"]["passages"]:
            if p["text"] != line:
                lines.append(f"- 固定内容/恢复保留 {p['speaker']}：{p['text']}")
        if row["after"]["wrap_up"]:
            lines.append("规则收束：" + json.dumps(row["after"]["wrap_up"], ensure_ascii=False))
        lines.append(
            f"HTTP {row['http_response']['status']}；模型请求区间 {row['wire_range']}（右端不含）"
        )
    (HERE / f"{name}-transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "checks": checks,
        "cost": report["cost"],
        "elapsed_seconds": report["elapsed_seconds"],
        "expression_http_attempts": len(expression_payloads),
        "semantic_pass": all(r["passes_gate"] for r in labels),
    }


def main():
    frozen = read(HERE / "freeze.json")
    labels = read(HERE / "review_labels.json")
    result = {name: verify_run(name, labels[name]) for name in ("original-real", "variant-real")}
    result["historical_reports_unchanged"] = all(
        sha(ROOT / name) == digest for name, digest in frozen["history"].items()
    )
    result["protocol_and_routes_unchanged"] = all(
        sha(HERE / name) == digest for name, digest in frozen["frozen"].items()
    )
    result["both_runs_same_sources"] = (
        read(HERE / "original-real/report.json")["source_hashes"]
        == read(HERE / "variant-real/report.json")["source_hashes"]
    )
    result["total_real_cost"] = {
        "http_requests": sum(result[name]["cost"]["http_requests"] for name in labels),
        "tokens": sum(result[name]["cost"]["tokens"]["total_tokens"] for name in labels),
        "money": None,
        "money_reason": "no verified provider rate",
    }
    result["infrastructure_failure"] = read(HERE / "original-sandbox/report.json")["cost"]
    result["unknown_token_reservation"] = read(HERE / "original-sandbox/report.json")[
        "budget_charged_tokens"
    ]
    result["review_method"] = (
        "assistant semantic review, not keyword scoring or calibrated human judge"
    )
    passed = all(
        result[name]["semantic_pass"] and all(result[name]["checks"].values()) for name in labels
    )
    passed = passed and all(
        result[k]
        for k in (
            "historical_reports_unchanged",
            "protocol_and_routes_unchanged",
            "both_runs_same_sources",
        )
    )
    result["bounded_acceptance_passed"] = passed
    (HERE / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
