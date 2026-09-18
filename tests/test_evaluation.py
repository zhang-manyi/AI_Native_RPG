"""Offline checks for evaluation denominators, failure accounting and artifacts."""

import json
from types import SimpleNamespace

import httpx
import pytest

from ai_native_rpg.evaluation import (
    BudgetExceeded,
    RecordingTransport,
    _state_consistent,
    aggregate,
    compare_reports,
    missing_scores,
    recall,
    rescore_report,
    run_suite,
)


def test_recall_counts_unique_labels_and_never_scores_unlabelled_as_zero():
    assert recall(["a", "a", "b"], ["a", "b"], 2) == (1, 2)
    assert recall([], [], 3) is None
    assert recall(None, ["a"], 3) is None
    assert recall([], ["a"], 3) == (0, 1)


def test_aggregate_exposes_missing_and_zero_denominators():
    result = aggregate([{"x": (1, 2), "empty": (0, 0)}, {"x": None}])
    assert result["x"] == {"numerator": 1, "denominator": 2, "value": 0.5, "missing": 1}
    assert result["empty"]["value"] is None


def test_mock_suite_writes_trace_evidence_without_claiming_model_quality(tmp_path):
    report = run_suite(mode="mock", output=tmp_path / "run")
    assert report["mode"] == "mock_contract"
    assert len(report["turns"]) == 6
    assert report["metrics"]["completed_turns"]["value"] == 1
    assert report["metrics"]["state_consistency"]["value"] == 1
    assert report["metrics"]["required_tools"]["value"] == 1
    assert report["metrics"]["memory_recall_at_3"]["value"] == 1
    assert report["cost"]["tokens"] is None
    assert (tmp_path / "run" / "report.md").is_file()
    assert len(list((tmp_path / "run" / "traces").glob("*.json"))) == 6
    rescore_report(tmp_path / "run" / "report.json", tmp_path / "run" / "rescored.json")
    rescored = json.loads((tmp_path / "run" / "rescored.json").read_text(encoding="utf-8"))
    assert rescored["metrics"] == report["metrics"]
    assert rescored["rescoring"]["model_calls_added"] == 0
    assert report["turns"][0]["calls"][0]["parsed"]["dialogue"]


def test_failed_turn_retains_applicable_missing_denominators():
    scores = missing_scores(
        {
            "recall_previous": True,
            "required_tools": ["query_memory"],
            "expected_option": None,
            "forbidden_patterns": ["secret"],
        }
    )
    assert scores["classification"] is None
    assert scores["memory_recall_at_3"] is None
    assert scores["required_tools"] is None
    assert scores["literal_disclosure_guard"] is None
    assert scores["completed_turns"] == (0, 1)


def test_real_unavailable_never_falls_back_to_mock(tmp_path):
    # conftest forces USE_MOCK_LLM=1 even on machines with credentials.
    report = run_suite(mode="real", output=tmp_path / "unavailable")
    assert report["mode"] == "real_model"
    assert report["metrics"]["completed_turns"]["value"] == 0
    assert report["metrics"]["memory_recall_at_3"]["missing"] == 2
    assert report["cost"]["logical_calls"] == 0
    assert report["cost"]["tokens"] is None


def test_wire_budget_and_missing_usage_are_recorded_without_credentials():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "returned-model",
                "choices": [{"message": {"content": "malformed"}, "finish_reason": "length"}],
            },
        )

    transport = RecordingTransport(max_requests=1, inner=httpx.MockTransport(handle))
    with httpx.Client(transport=transport) as client:
        client.post(
            "https://example.test",
            json={"max_tokens": 9999},
            headers={"Authorization": "Bearer test-secret"},
        )
        with pytest.raises(BudgetExceeded):
            client.post("https://example.test", json={})
    assert requests[0]["max_tokens"] == 600
    assert transport.records[0]["usage"] is None
    assert transport.records[0]["finish_reason"] == "length"
    assert transport.records[0]["response_message"]["content"] == "malformed"
    assert "test-secret" not in json.dumps(transport.records)


def test_compare_refuses_mixed_modes(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text(json.dumps({"mode": "mock_contract", "dataset": {}}))
    right.write_text(json.dumps({"mode": "real_model", "dataset": {}}))
    with pytest.raises(ValueError, match="same mode"):
        compare_reports(left, right, tmp_path / "comparison.md")


@pytest.mark.parametrize("visibility,expected", [("revealed", True), ("hidden", False)])
def test_state_oracle_distinguishes_public_reaffirmation_from_hidden_disclosure(
    visibility, expected
):
    before = {"facts": {"victim_name": {"visibility": visibility}}}
    after = {"facts": {"victim_name": {"visibility": "revealed"}}}
    response = SimpleNamespace(
        npc_id="npc_a",
        action_proposal_id="p",
        plan=SimpleNamespace(
            action_proposal=SimpleNamespace(action_type="reveal_fact", target_id="victim_name")
        ),
    )
    trace = SimpleNamespace(
        steps=[SimpleNamespace(step_name="action_validation", output_summary={"approved": True})]
    )
    assert _state_consistent(before, after, response, trace) is expected
