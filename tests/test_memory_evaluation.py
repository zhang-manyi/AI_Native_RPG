"""Offline tests of labels, experimental isolation and missing evidence handling."""

import json
from collections import Counter

import pytest

from ai_native_rpg import memory_evaluation as evaluation
from ai_native_rpg.agent.embedding import HashingEmbedder
from ai_native_rpg.agent.harness import PlanningOutput
from ai_native_rpg.config import Settings
from ai_native_rpg.llm.base import LLMResponse


def test_fixture_has_grouped_splits_and_real_cross_owner_negatives():
    _, corpus, cases = evaluation.load_dataset()
    assert len(cases) == 48
    assert Counter(m["npc_id"] for m in corpus) == {"npc_a": 100, "npc_b": 100}
    assert Counter(c["split"] for c in cases) == {"dev": 24, "test": 24}
    dev = {c["id"] for c in cases if c["split"] == "dev"}
    test = {c["id"] for c in cases if c["split"] == "test"}
    assert dev.isdisjoint(test)
    for case in cases:
        if case["category"] == "cross_npc":
            store = evaluation.make_store(case["npc"], corpus, HashingEmbedder())
            all_ids = {m.memory_id for m in store.retrieve(case["query"], top_k=100).episodic}
            assert not all_ids.intersection(case["forbidden_ids"])


def test_metrics_distinguish_partial_recall_full_rank_and_no_answer():
    result = evaluation.retrieval_scores(["noise", "a", "noise2", "b"], ["a", "b"])
    assert result["recall_at_3"] == 0.5
    assert result["reciprocal_rank"] == 0.5
    assert result["all_relevant_at_3"] == 0
    assert evaluation.retrieval_scores(["noise", "a"], ["a"], k=1)["reciprocal_rank"] == 0.5
    unknown = evaluation.retrieval_scores(["noise"], [])
    assert unknown["recall_at_3"] is None
    assert unknown["no_answer_nonempty"] == 1
    assert evaluation.retrieval_scores([], ["a"])["reciprocal_rank"] == 0


def test_retrieval_report_preserves_denominators_and_never_overwrites(tmp_path):
    output = tmp_path / "hashing"
    report = evaluation.run_retrieval(output=output, backend="hashing", split="dev")
    assert report["status"] == "completed"
    assert report["summary"]["labelled_queries"] == 20
    assert report["summary"]["gold_count"] == 22
    assert report["summary"]["no_answer_queries"] == 4
    assert report["summary"]["no_answer_nonempty_rate"] == 1
    assert all(len(row["ranked_ids"]) == 100 for row in report["rows"])
    with pytest.raises(FileExistsError):
        evaluation.run_retrieval(output=output, backend="hashing", split="dev")


def test_unavailable_qwen_is_not_hashing_success(tmp_path):
    report = evaluation.run_retrieval(
        output=tmp_path / "missing", backend="qwen", split="dev", model_path=tmp_path / "no-model"
    )
    assert report["status"] == "unavailable"
    assert report["error_type"] == "FileNotFoundError"
    assert report["rows"] == []
    assert "summary" not in report


def test_comparison_refuses_incomplete_or_changed_dataset(tmp_path):
    report = evaluation.run_retrieval(output=tmp_path / "run", backend="hashing", split="dev")
    bad = tmp_path / "bad.json"
    report["manifest"]["files"]["evals/memory_cases.yaml"] = "changed"
    evaluation.write_json(bad, report)
    with pytest.raises(ValueError, match="differs"):
        evaluation.compare_retrieval([tmp_path / "run/report.json", bad], tmp_path / "compare.md")
    report["status"] = "unavailable"
    evaluation.write_json(bad, report)
    with pytest.raises(ValueError, match="incomplete"):
        evaluation.compare_retrieval([bad], tmp_path / "compare.md")


def test_sequences_do_not_call_real_backend_when_disabled(tmp_path):
    report = evaluation.run_sequences(output=tmp_path / "off")
    assert report["status"] == "unavailable"
    assert report["error_type"] == "RealBackendUnavailable"
    assert report["probes"] == []


def test_lag_branches_share_prefix_without_refreshing_from_previous_answers(tmp_path, monkeypatch):
    """Fake generation validates the experiment, not model ability."""
    monkeypatch.setattr(
        evaluation.Settings,
        "from_env",
        lambda: Settings(api_key="test-key", use_mock=False, model="offline-contract"),
    )
    monkeypatch.setattr(
        evaluation,
        "make_embedder",
        lambda name, path=None: (
            HashingEmbedder(64 if name == "hashing" else 32),
            {"name": "test-double"},
        ),
    )

    class OfflineClient:
        def __init__(self, **kwargs):
            pass

        def complete(self, messages, *, schema, **kwargs):
            assert schema is PlanningOutput
            return LLMResponse(
                parsed=PlanningOutput(reasoning="contract", strategy="answer", dialogue="测试回答"),
                model="offline-contract",
            )

        def close(self):
            pass

    monkeypatch.setattr(evaluation, "OpenAICompatibleClient", OfflineClient)
    report = evaluation.run_sequences(output=tmp_path / "branches", repeats=1)
    assert report["status"] == "completed"
    assert report["completed_probes"] == 12
    assert report["cost"]["http_requests"] == 0
    assert report["cost"]["tokens"] is None
    for prefix in report["prefixes"]:
        assert prefix["write_contains_answer"]
        probes = [p for p in report["probes"] if p["prefix_id"] == prefix["id"]]
        baseline_count = len(probes[0]["memory_before"]["episodic"])
        for probe in probes:
            assert probe["gold_ids"] == prefix["written_ids"]
            assert len(probe["memory_before"]["episodic"]) == baseline_count + probe["lag"]
            assert all(
                probe["question"] not in m["event_description"]
                for m in probe["memory_before"]["episodic"]
            )
            if probe["lag"] == 30:
                assert probe["restore_equal"]
            assert probe["calls"][-1]["messages"]
    saved = json.loads((tmp_path / "branches/report.json").read_text(encoding="utf-8"))
    assert saved["completed_probes"] == 12
