"""Offline evidence audit for this experiment; no provider calls."""

# This standalone report script adds the repository source tree before imports.
# ruff: noqa: E402

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from ai_native_rpg.config import Settings
from ai_native_rpg.memory_evaluation import fingerprint, write_json
from ai_native_rpg.schemas.agent_trace import AgentTrace
from ai_native_rpg.schemas.world_state import ActionProposal, WorldState
from ai_native_rpg.tool_evaluation import DATASET, EXPERIMENT, state_consistent
from ai_native_rpg.world.manager import WorldStateManager


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify():
    freeze = json.loads((EXPERIMENT / "freeze.json").read_text(encoding="utf-8"))
    assert fingerprint(DATASET) == freeze["dataset_sha256"]
    assert fingerprint(EXPERIMENT / "protocol.md") == freeze["protocol_sha256"]
    assert fingerprint(ROOT / "src/ai_native_rpg/tool_evaluation.py") == freeze["scorer_sha256"]
    summaries = load_module("tool_summary_audit", ROOT / "scripts/summarize_tools.py")
    reviews = load_module("tool_review_audit", EXPERIMENT / "assisted_review.py")
    labels = json.loads((EXPERIMENT / "review_labels.json").read_text(encoding="utf-8"))
    assert reviews.build() == labels
    by_id = {r["id"]: r for r in labels["rows"]}
    summary = json.loads((EXPERIMENT / "summary.json").read_text(encoding="utf-8"))
    result = {
        "summary_recomputes": True,
        "explicit_reviews_recompute": True,
        "frozen_dataset_protocol_scorer_match": True,
        "runs": {},
    }
    for run in labels["runs"]:
        directory = EXPERIMENT / run
        actual = summaries.summarize(directory, by_id)
        assert json.loads(json.dumps(actual)) == summary["runs"][run]
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        wire = json.loads((directory / "wire.json").read_text(encoding="utf-8"))
        assert len(report["rows"]) == 27
        assert len(wire) <= report["budget"]["requests"]
        assert report["budget_charged_tokens"] <= report["budget"]["tokens"]
        for relative, sha in report["manifest"]["files"].items():
            assert fingerprint(directory / "sources" / relative) == sha
        assert report["configuration"] == {
            "embedding": "Hashing(64)",
            "top_k": 3,
            "max_tool_iterations": 3,
            "temperature": 0,
            "retain_dialogue_evidence": False,
            "filtered_dialogue_evidence": False,
        }
        for call in wire:
            assert not {"headers", "url", "api_key"} & call.keys()
            body = call["request_body"]
            assert body["max_tokens"] == 600 and body["temperature"] == 0
            encoded = json.dumps(body, ensure_ascii=False)
            for forbidden in (
                '"private_topics"',
                '"expect"',
                '"answer_patterns"',
                '"expected_answer"',
                '"fixed_action"',
                '"goal"',
            ):
                assert forbidden not in encoded
        for row in report["rows"]:
            if "trace" in row:
                trace = AgentTrace.model_validate(row["trace"])
                assert trace.final_dialogue == row["dialogue"]
            if row["scores"]["state_consistency"] is not None:
                assert state_consistent(row)
            for entry in row["journal"]:
                before = WorldState.model_validate(entry["before"])
                manager = WorldStateManager(before)
                proposal = ActionProposal.model_validate(entry["proposal"])
                assert proposal.actor_id == row["npc"]
                verdict = manager.dry_run(proposal)
                assert verdict.approved == entry["verdict"]["approved"]
                assert verdict.rule_name == entry["verdict"]["rule_name"]
            # Inspect authority, including public projection after relation changes.
            view = WorldStateManager(WorldState.model_validate(row["world_after"])).player_view(
                "player_1"
            )
            for secret in ("npc_a_threatened", "innkeeper_own_night", "innkeeper_timeline"):
                assert secret not in view.visible_facts
            if row["turn"] == 1:
                previous = next(
                    r
                    for r in report["rows"]
                    if r["case_id"] == row["case_id"]
                    and r["repeat"] == row["repeat"]
                    and r["turn"] == 0
                )
                assert previous["world_after"] == row["world_before"]
        result["runs"][run] = {
            "rows": 27,
            "completed": sum("dialogue" in r for r in report["rows"]),
            "wire_requests": len(wire),
            "charged_tokens": report["budget_charged_tokens"],
            "archived_source_hashes_match": True,
            "no_label_fields_in_requests": True,
            "verdict_replay_matches": True,
            "secret_projection_stays_hidden": True,
            "dependent_state_continuity": True,
        }
    # Check actual configured secret value without ever printing it or Settings.
    settings = Settings.from_env()
    credential = settings.api_key.get_secret_value().encode() if settings.api_key else None
    scanned = 0
    for path in EXPERIMENT.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            scanned += 1
            assert not credential or credential not in path.read_bytes()
    result["credential_scan"] = {"files": scanned, "matches": 0}
    old_reports = [
        "evals/reports/2026-09-16",
        "evals/reports/2026-09-17-memory",
        "evals/reports/2026-09-17-expression",
        "evals/reports/2026-09-18-checkpoint",
    ]
    assert (
        subprocess.run(
            ["git", "diff", "--exit-code", "HEAD", "--", *old_reports],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )
    result["historical_reports_unchanged"] = True
    assert (
        subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                "HEAD",
                "--",
                "prompts",
                "src/ai_native_rpg/agent",
                "src/ai_native_rpg/world",
            ],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )
    result["runtime_and_default_prompts_unchanged"] = True
    result["final_artifacts"] = {
        path.relative_to(ROOT).as_posix(): fingerprint(path)
        for path in [
            ROOT / "scripts/summarize_tools.py",
            ROOT / "tests/test_tool_evaluation.py",
            ROOT / "tests/test_tool_summary.py",
            EXPERIMENT / "assisted_review.py",
            EXPERIMENT / "review_labels.json",
            EXPERIMENT / "summary.json",
        ]
    }
    return result


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else EXPERIMENT / "verification.json"
    if path.exists():
        raise FileExistsError(path)
    result = verify()
    write_json(path, result)
    print("Offline evidence verification passed.")
