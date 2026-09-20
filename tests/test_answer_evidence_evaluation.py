import importlib.util
import json

import pytest

from ai_native_rpg.evaluation import ROOT
from ai_native_rpg.memory_evaluation import fingerprint


def load_summary():
    spec = importlib.util.spec_from_file_location(
        "answer_summary", ROOT / "scripts/summarize_answer_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_split_has_new_events_and_no_fixed_full_actions():
    experiment = ROOT / "evals/reports/2026-09-18-answer-evidence"
    freeze = json.loads((experiment / "freeze.json").read_text(encoding="utf-8"))
    assert fingerprint(ROOT / "evals/answer_evidence.json") == freeze["dataset_sha256"]
    assert fingerprint(experiment / "protocol.md") == freeze["protocol_sha256"]
    data = json.loads((ROOT / "evals/answer_evidence.json").read_text(encoding="utf-8"))
    for case in data["cases"]:
        assert "expect" not in case["input"]
        assert ("fixed_action" in case["input"]["turns"][0]) == (case["mode"] == "local")
    assert len([c for c in data["cases"] if c["split"] == "holdout"]) == 8


def test_summary_rejects_missing_and_stale_assistant_review():
    summarize = load_summary().summarize
    directory = ROOT / "evals/reports/2026-09-18-answer-evidence/dev-baseline-authorized"
    row = json.loads((directory / "report.json").read_text(encoding="utf-8"))["rows"][0]
    with pytest.raises(ValueError, match="missing or stale review"):
        summarize(directory, {})
    with pytest.raises(ValueError, match="missing or stale review"):
        summarize(directory, {row["id"]: {"dialogue_sha256": "wrong"}})


def test_deny_with_answer_requires_both_assessments():
    import hashlib

    summarize = load_summary().summarize
    directory = ROOT / "evals/reports/2026-09-18-answer-evidence/dev-baseline-authorized"
    row = json.loads((directory / "report.json").read_text(encoding="utf-8"))["rows"][0]
    label = {
        "dialogue_sha256": hashlib.sha256(row["dialogue"].encode()).hexdigest(),
        "review_source": "Codex assistant review",
        "reason": "test only",
        "unsupported": False,
        "disclosure": False,
        "false_success": False,
        "expression_consistent": True,
        "refusal_appropriate": True,
    }
    with pytest.raises(ValueError, match="answer_correct"):
        summarize(directory, {row["id"]: label})
