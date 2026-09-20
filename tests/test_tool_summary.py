"""The semantic review must bind to outputs and cannot overwrite structural truth."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "summarize_tools", ROOT / "scripts/summarize_tools.py"
)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)
REPORT = ROOT / "evals/reports/2026-09-18-tools"


def labels():
    data = json.loads((REPORT / "review_labels.json").read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["rows"]}


def test_saved_summaries_recompute_and_local_actions_are_not_model_selection():
    saved = json.loads((REPORT / "summary.json").read_text(encoding="utf-8"))
    for run, expected in saved["runs"].items():
        actual = summary.summarize(REPORT / run, labels())
        assert json.loads(json.dumps(actual)) == expected
        assert "action_selection" not in actual["metrics"]["local"]
        assert actual["metrics"]["full"]["actual_rejection_path"]["numerator"] == 0


def test_modified_output_review_is_rejected():
    reviewed = labels()
    reviewed["holdout_public_baseline_r1_t1"]["dialogue_sha256"] = "wrong"
    with pytest.raises(ValueError, match="different output"):
        summary.summarize(REPORT / "holdout-baseline", reviewed)


def test_review_cannot_leave_requested_answer_implicitly_true():
    reviewed = labels()
    reviewed["holdout_public_baseline_r1_t1"]["answer_correct"] = None
    with pytest.raises(ValueError, match="explicitly assess answer_correct"):
        summary.summarize(REPORT / "holdout-baseline", reviewed)


def test_cannot_silently_rescore_frozen_structural_metric(tmp_path):
    report = json.loads((REPORT / "holdout-baseline/report.json").read_text(encoding="utf-8"))
    report["rows"][0]["scores"]["tool_execution"] = [999, 999]
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "wire.json").write_bytes((REPORT / "holdout-baseline/wire.json").read_bytes())
    with pytest.raises(ValueError, match="structural score drift"):
        summary.summarize(tmp_path, labels())
