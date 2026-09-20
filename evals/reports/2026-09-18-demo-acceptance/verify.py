"""Offline integrity/state verification and rendering; semantic labels are assistant review."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def verify():
    report = read(HERE / "authorized-run/report.json")
    wire = read(HERE / "authorized-run/wire.json")
    initial = read(HERE / "initial_manifest.json")
    steps = report["steps"]
    labels = read(HERE / "review_labels.json")
    dialogue = {
        s["index"]: e["data"]["dialogue"]
        for s in steps
        for e in s["events"]
        if e["type"] == "dialogue"
    }
    checks = {
        "historical_reports_unchanged": all(
            digest(ROOT / name) == value for name, value in initial["historical_reports"].items()
        ),
        "protocol_route_unchanged": all(
            digest(ROOT / name) == value for name, value in initial["frozen"].items()
        ),
        "source_archive_matches_run": all(
            digest(HERE / "authorized-run/sources" / name) == value
            for name, value in report["source_hashes"].items()
        ),
        "current_runtime_matches_run": all(
            digest(ROOT / name) == value
            for name, value in report["source_hashes"].items()
            if name.startswith(("src/", "prompts/", "scenarios/"))
        ),
        "real_backend_only": not report["settings"]["use_mock"]
        and all(w["status"] == 200 and w["model"] == "deepseek-flash" for w in wire),
        "all_route_steps_executed": len(steps) == len(read(HERE / "route.json")),
        "denied_move_state_unchanged": steps[0]["before"]["world"] == steps[0]["after"]["world"],
        "denied_move_not_claimed_success": any(
            e["type"] == "move" and not e["data"]["approved"] and not e["data"]["slot_spent"]
            for e in steps[0]["events"]
        ),
        "both_restores_exact_world_memory_transcript": all(
            steps[i]["before"][key] == steps[i]["after"][key]
            for i in (6, 20)
            for key in ("world", "memories", "transcript")
        ),
        "resumed_game_continued": steps[7]["after"]["world"]["story_beats"]["turn"]
        > steps[6]["after"]["world"]["story_beats"]["turn"],
        "truth_ending": report["ending_id"] == "truth_uncovered",
        "ending_requires_evidence_obtained": all(
            report["final"]["world"]["facts"][key]["visibility"] == "revealed"
            for key in (
                "ella_things_missing",
                "ella_asked_the_road",
                "timeline_mismatch",
                "forest_traces",
            )
        ),
        "core_private_truth_stays_hidden_in_state": all(
            report["final"]["world"]["facts"][key]["visibility"] == "hidden"
            for key in ("ella_whereabouts", "loren_that_night", "npc_a_threatened")
        ),
        "terminal_input_rejected_without_state_change": steps[21]["http_response"]["status"] == 400
        and steps[21]["before"]["world"] == steps[21]["after"]["world"],
        "no_turn_failed": all(e["type"] != "turn_failed" for s in steps for e in s["events"]),
        "all_model_dialogues_reviewed_with_hash": set(dialogue) == {x["step"] for x in labels}
        and all(
            hashlib.sha256(dialogue[x["step"]].encode()).hexdigest() == x["dialogue_sha256"]
            for x in labels
        ),
        "usage_matches_report": sum(w["usage"]["total_tokens"] for w in wire)
        == report["cost"]["tokens"]["total_tokens"],
    }
    traces = [read(p) for p in (HERE / "authorized-run/traces").glob("*.json")]
    checks["no_expression_fallback"] = all(
        s["step_name"] != "expression_fallback" for t in traces for s in t["steps"]
    )
    # For each ordinary conversation, independently compare the wire proposal to
    # actual relationship changes. Rule-selected choices have separate resolution traces.
    action_checks = []
    for index in (5, 7, 11):
        step = steps[index]
        start, end = step["wire_range"]
        proposal = None
        for response in wire[start:end]:
            content = response["response_body"]["choices"][0]["message"].get("content")
            if content:
                data = json.loads(content)
                proposal = data.get("action") or proposal
        npc = step["before"]["scene"]["current_npc_id"]
        before = step["before"]["world"]["relationships"][npc]["player_1"]
        after = step["after"]["world"]["relationships"][npc]["player_1"]
        action_checks.append(
            proposal["action_type"] == "adjust_relationship"
            and all(
                after[k] - before[k] == proposal["payload"].get(k, 0)
                for k in ("trust", "fear", "respect")
            )
        )
    checks["all_three_free_turn_relation_deltas_match_proposals"] = all(action_checks)
    write(
        "verification.json",
        {
            "checks": checks,
            "all_structural_checks_pass": all(checks.values()),
            "historical_files_checked": len(initial["historical_reports"]),
            "semantic_review": (
                "assistant review, not keyword scoring or independent human calibration"
            ),
            "semantic_acceptance_passed": False,
            "cost_money": None,
            "cost_money_reason": "no verified pricing",
            "total_attempts_including_infrastructure_failure": len(wire) + 1,
            "actual_known_tokens": report["cost"]["tokens"]["total_tokens"],
            "unknown_usage_reservation": read(HERE / "initial-run/report.json")[
                "budget_charged_tokens"
            ],
        },
    )
    lines = [
        "# 完整玩家记录（生成内容与固定叙事分别标注）",
        "",
        "模型语义结论见 review_labels.json；规则达到终局不代表质量通过。",
        "",
        "## 普通开局",
        "",
    ]
    for p in report["initial"]["scene"]["passages"]:
        lines.append(f"- 固定 {p['speaker']}：{p['text']}")
    for step in steps:
        lines.extend(
            [
                "",
                f"## 步骤 {step['index']:02d}",
                "",
                "输入：`" + json.dumps(step["action"], ensure_ascii=False) + "`",
                "",
            ]
        )
        if step["index"] in dialogue:
            lines.append("真实模型：" + dialogue[step["index"]])
        for p in step["after"]["scene"]["passages"]:
            if p["text"] != dialogue.get(step["index"]):
                prefix = (
                    "恢复/拒绝后保留"
                    if step["action"]["kind"] in ("resume", "turn")
                    else "固定叙事"
                )
                lines.append(f"- {prefix} {p['speaker']}：{p['text']}")
        for e in step["events"]:
            if e["type"] == "move":
                lines.append("- 规则移动结果：`" + json.dumps(e["data"], ensure_ascii=False) + "`")
        lines.append(
            f"- HTTP {step['http_response']['status']}；模型请求范围 {step['wire_range']}"
            "（从0起，右端不含）"
        )
    (HERE / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    verify()
