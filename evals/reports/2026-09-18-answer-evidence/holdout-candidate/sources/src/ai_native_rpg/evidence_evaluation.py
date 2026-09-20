"""Replay fixed generation inputs, isolating evidence transfer from tool decisions."""

from __future__ import annotations

import json
from pathlib import Path

from .agent.harness import DialogueOutput
from .agent.memory_store import MemoryStore
from .config import Settings
from .evaluation import RecordingTransport
from .llm.base import Message
from .llm.openai_compatible import OpenAICompatibleClient
from .memory_evaluation import (
    EvidenceClient,
    fingerprint,
    harness_for,
    manifest,
    wire_cost,
    write_json,
)
from .scenario import load_scenario, load_seed_memories
from .schemas.npc_agent import AgentPlan, NPCAgentResponse
from .world.manager import WorldStateManager


def evidence_from_trace(trace):
    retrieval = next(s for s in trace["steps"] if s["step_name"] == "memory_retrieval")
    return {
        "episodic": [m["text"] for m in retrieval["output_summary"]["episodic"]],
        "semantic": [m["text"] for m in retrieval["output_summary"]["semantic"]],
        "工具返回": [
            {"tool": s["input_summary"]["tool"], "result": s["output_summary"]["result"]}
            for s in trace["steps"]
            if s["step_name"] == "tool_call" and s["output_summary"].get("ok") is True
        ],
    }


def replay_cases(source):
    """Predeclared subset: all completed repeat-1, lag-0 regeneration probes.

    Selection never tests the answer score. These failure-informed probes are
    development diagnostics, not a held-out generation evaluation.
    """
    cases = []
    for probe in source["probes"]:
        if probe["repeat"] != 1 or probe["lag"] != 0 or "response" not in probe:
            continue
        if probe["calls"][-1]["schema"] != "DialogueOutput":
            continue
        npc = probe["response"]["npc_id"]
        harness = harness_for(
            npc, WorldStateManager(load_scenario("village_disappearance")), MemoryStore(npc), None
        )
        verdict = next(
            s["output_summary"]["reason"]
            for s in probe["trace"]["steps"]
            if s["step_name"] == "action_validation"
        )
        response = NPCAgentResponse.model_validate(probe["response"])
        baseline = harness._dialogue_messages(response.plan, verdict, probe["question"])
        original = probe["calls"][-1]["messages"]
        if [m.model_dump(mode="json") for m in baseline] != original:
            raise ValueError("reconstructed baseline differs from saved generation context")
        candidate = harness._dialogue_messages(
            response.plan, verdict, probe["question"], evidence=evidence_from_trace(probe["trace"])
        )
        cases.append(
            {
                "id": probe["id"],
                "kind": "answer",
                "source_probe": probe["id"],
                "expected_answer": probe["expected_answer"],
                "baseline": original,
                "candidate": [m.model_dump(mode="json") for m in candidate],
            }
        )
    if not cases:
        raise ValueError("no eligible regeneration probes")
    for npc, question in [
        ("npc_a", "你那晚看见的人是谁？他后来对你儿子说了什么？"),
        ("npc_b", "那晚林子里发生了什么？你为什么这么害怕？"),
    ]:
        store = MemoryStore(npc)
        load_seed_memories("village_disappearance")[npc].load_into(store)
        snapshot = store.snapshot()
        harness = harness_for(
            npc, WorldStateManager(load_scenario("village_disappearance")), store, None
        )
        plan = AgentPlan(
            reasoning="当前关系不足以交代私密案件经历，应回避提问。", strategy="deflect"
        )
        verdict = "披露被拒绝：不得透露私密经历、相关人物身份或威胁细节；也不要编造新线索。"
        evidence = {
            "episodic": [m["event_description"] for m in snapshot["episodic"]],
            "semantic": [m["fact"] for m in snapshot["semantic"]],
            "工具返回": [],
        }
        cases.append(
            {
                "id": f"privacy_{npc}",
                "kind": "privacy",
                "expected_answer": None,
                "prohibited_evidence": evidence,
                "baseline": [
                    m.model_dump(mode="json")
                    for m in harness._dialogue_messages(plan, verdict, question)
                ],
                "candidate": [
                    m.model_dump(mode="json")
                    for m in harness._dialogue_messages(plan, verdict, question, evidence=evidence)
                ],
            }
        )
    return cases


def run_replay(*, source, output, repeats=3, max_requests=96, max_seconds=600):
    if repeats < 1 or max_requests < 1 or max_seconds <= 0:
        raise ValueError("budgets and repeats must be positive")
    source = Path(source)
    original = json.loads(source.read_text(encoding="utf-8"))
    cases = replay_cases(original)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    provenance = manifest()
    provenance["files"]["src/ai_native_rpg/evidence_evaluation.py"] = fingerprint(__file__)
    report = {
        "experiment": "fixed_evidence_replay",
        "manifest": provenance,
        "source_sha256": fingerprint(source),
        "source": source.as_posix(),
        "cases": cases,
        "repeats": repeats,
        "temperature": 0,
        "expected_outputs": len(cases) * 2 * repeats,
        "max_requests": max_requests,
        "max_seconds": max_seconds,
        "status": "unavailable",
        "rows": [],
        "limitations": [
            "Development diagnostics selected by repeat/lag/regeneration, not by outcome.",
            "All upstream inputs and the verdict are fixed; only evidence inclusion changes.",
            "Privacy controls are explicit rejected-disclosure fixtures, not full agent turns.",
            "Literal answer hits need semantic review; no persona/safety score is inferred.",
            "Three samples per condition cannot establish generalization or significance.",
        ],
    }
    settings = Settings.from_env()
    if not settings.has_real_backend:
        report["error_type"] = "RealBackendUnavailable"
        write_json(output / "report.json", report)
        return report
    report["model"] = settings.model
    transport = RecordingTransport(max_requests=max_requests, max_seconds=max_seconds)
    backend = OpenAICompatibleClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
        timeout=25,
        max_attempts=1,
        transport=transport,
    )
    try:
        for repeat in range(repeats):
            for case in cases:
                order = ["baseline", "candidate"] if repeat % 2 == 0 else ["candidate", "baseline"]
                for variant in order:
                    row = {
                        "id": f"{case['id']}_{variant}_r{repeat + 1}",
                        "case_id": case["id"],
                        "variant": variant,
                        "repeat": repeat + 1,
                        "kind": case["kind"],
                    }
                    report["rows"].append(row)
                    start = len(transport.records)
                    client = EvidenceClient(backend)
                    try:
                        response = client.complete(
                            [Message.model_validate(m) for m in case[variant]],
                            schema=DialogueOutput,
                        )
                        row["dialogue"] = response.parsed.dialogue
                        row["literal_answer_hit"] = (
                            case["expected_answer"] in row["dialogue"]
                            if case["expected_answer"]
                            else None
                        )
                        row["semantic_review"] = None
                    except Exception as exc:
                        row["error_type"] = type(exc).__name__
                    finally:
                        row.update(calls=client.calls, cost=wire_cost(transport.records[start:]))
                        write_json(output / "report.json", report)
                        write_json(output / "wire.json", transport.records)
                        print(
                            f"{row['id']}: {row.get('literal_answer_hit', row.get('error_type'))}",
                            flush=True,
                        )
    finally:
        backend.close()
        report["completed_outputs"] = sum("dialogue" in r for r in report["rows"])
        report["status"] = (
            "completed" if report["completed_outputs"] == report["expected_outputs"] else "partial"
        )
        report["cost"] = wire_cost(transport.records)
        write_json(output / "report.json", report)
        write_json(output / "wire.json", transport.records)
    return report
