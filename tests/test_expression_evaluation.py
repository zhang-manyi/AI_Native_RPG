import json

import httpx
import pytest

from ai_native_rpg.evaluation import BudgetExceeded
from ai_native_rpg.expression_evaluation import (
    HOLDOUT,
    HOLDOUT_SHA,
    ExpressionTransport,
    development_cases,
    local_holdout_cases,
    prepare_holdout,
    run,
)
from ai_native_rpg.memory_evaluation import fingerprint


def test_frozen_fixture_and_actual_rejected_local_actions():
    assert fingerprint(HOLDOUT) == HOLDOUT_SHA
    cases = json.loads(HOLDOUT.read_text(encoding="utf-8"))["cases"]
    prepared = local_holdout_cases(cases)
    for case in prepared:
        if "fixed_action" in case:
            assert case["upstream"]["verdict"]["approved"] is False
        for variant in ("baseline", "candidate"):
            prompt = json.dumps(case[variant])
            assert "answer_patterns" not in prompt
            assert "prohibited_patterns" not in prompt
        if case.get("tell") and any(
            m["memory_id"] == "player_exchange" for m in case["upstream"]["retrieval"]["episodic"]
        ):
            assert case["tell"] in case["candidate"][1]["content"]


def test_development_uses_all_original_selected_cases_and_verified_provenance():
    cases = development_cases()
    assert len(cases) == 6
    for case in cases:
        if case["kind"] == "answer":
            assert case["upstream"]["verified_player_ids"]
        assert case["baseline"] != case["candidate"]


def test_input_preparation_ignores_evaluator_labels():
    case = {
        "npc": "npc_a",
        "tell": "我叫小松",
        "answer_patterns": ["LABEL CANARY"],
        "prohibited_patterns": ["LABEL CANARY"],
    }
    _, manager, memory = prepare_holdout(case)
    assert "LABEL CANARY" not in json.dumps(memory.snapshot())
    assert "LABEL CANARY" not in manager.snapshot().model_dump_json()


def test_token_budget_and_wire_evidence_have_no_credentials():
    transport = ExpressionTransport(
        max_tokens=2,
        max_requests=3,
        max_seconds=60,
        inner=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "model": "fake",
                    "usage": {"total_tokens": 2},
                    "choices": [{"message": {"content": "{}"}}],
                },
            )
        ),
    )
    request = httpx.Request(
        "POST",
        "https://example.test",
        headers={"Authorization": "SECRET"},
        json={"messages": [], "max_tokens": 9999},
    )
    transport.handle_request(request)
    assert transport.records[0]["request_body"]["max_tokens"] == 600
    assert "SECRET" not in json.dumps(transport.records)
    with pytest.raises(BudgetExceeded):
        transport.handle_request(request)


def test_unavailable_is_not_mock_and_outputs_cannot_be_overwritten(tmp_path):
    output = tmp_path / "run"
    report = run(stage="local", output=output)
    assert report["status"] == "unavailable"
    assert report["rows"] == []
    with pytest.raises(FileExistsError):
        run(stage="local", output=output)
