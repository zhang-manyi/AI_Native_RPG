"""Bounded expression comparisons; labels never enter the generation path."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .agent.harness import DialogueOutput, Harness
from .agent.memory_store import MemoryStore
from .agent.tools import build_npc_tools
from .config import Settings
from .evaluation import PLAYER, ROOT, BudgetExceeded, RecordingTransport
from .evidence_evaluation import replay_cases
from .llm.base import Message
from .llm.openai_compatible import OpenAICompatibleClient
from .memory_evaluation import EvidenceClient, fingerprint, manifest, wire_cost, write_json
from .scenario import load_personas, load_scenario, load_seed_memories
from .schemas.agent_trace import TraceStep
from .schemas.memory import EpisodicMemory, MemoryRetrievalResult, SemanticMemory
from .schemas.npc_agent import AgentPlan
from .schemas.world_state import ActionProposal, WorldState
from .world.manager import WorldStateManager

HOLDOUT = ROOT / "evals/expression_holdout.json"
HOLDOUT_SHA = "ee3eab4e58304b2bca341e4d4e869a50e4376d55d4681140a82c497b8838862c"
SOURCE = ROOT / "evals/reports/2026-09-17-memory/sequences/report.json"


class ExpressionTransport(RecordingTransport):
    def __init__(self, *, max_tokens, **kwargs):
        super().__init__(**kwargs)
        self.max_tokens = max_tokens

    def handle_request(self, request):
        used = sum((r.get("usage") or {}).get("total_tokens", 0) for r in self.records)
        if used >= self.max_tokens:
            raise BudgetExceeded("known token budget exhausted before request")
        body = json.loads(request.content)
        if len(request.content) > 100_000:
            raise BudgetExceeded("request content exceeds byte cap")
        index = len(self.records)
        try:
            return super().handle_request(request)
        finally:
            if len(self.records) > index:
                body["max_tokens"] = 600
                self.records[index]["request_body"] = body


def make_harness(npc, manager, memory, client, *, candidate=False):
    return Harness(
        npc_state=load_personas("village_disappearance")[npc],
        manager=manager,
        memory=memory,
        llm=client,
        filtered_dialogue_evidence=candidate,
        top_k=3,
        max_tool_iterations=1,
        tools=build_npc_tools(npc_id=npc, manager=manager, memory=memory, player_id=PLAYER),
    )


def prepare_holdout(case, client=None, candidate=False):
    """Only input fields are read here. No answers, patterns, or review labels."""
    npc = case["npc"]
    manager = WorldStateManager(load_scenario("village_disappearance"))
    memory = MemoryStore(npc)
    load_seed_memories("village_disappearance")[npc].load_into(memory)
    harness = make_harness(npc, manager, memory, client, candidate=candidate)
    if case.get("tell"):
        harness.remember_exchange(case["tell"], "听见了。", player_id=PLAYER)
        # IDs do not carry answer labels. A stable ID makes paired snapshots auditable.
        written = memory._episodic[-1]
        memory._episodic[-1] = written.model_copy(update={"memory_id": "player_exchange"})
    return harness, manager, memory


def local_holdout_cases(cases):
    prepared = []
    for case in cases:
        harness, manager, memory = prepare_holdout(case)
        steps = []
        retrieval = harness._retrieve(case["question"], case["npc"], PLAYER, steps)
        proposal = ActionProposal(
            proposal_id="fixed_action",
            actor_id=case["npc"],
            **case.get(
                "fixed_action",
                {
                    "action_type": "adjust_relationship",
                    "target_id": PLAYER,
                    "payload": {"fear": 1},
                },
            ),
        )
        before = manager.snapshot().model_dump(mode="json")
        verdict = manager.submit(proposal)
        plan = AgentPlan(
            reasoning="谨慎回应眼前的问题。", strategy="cautious", action_proposal=proposal
        )
        baseline = harness._dialogue_messages(plan, verdict.reason, case["question"])
        candidate = harness._expression_messages(
            case["question"],
            retrieval,
            steps,
            PLAYER,
            action_status="applied" if verdict.approved else "rejected",
        )
        prepared.append(
            {
                **case,
                "baseline": [m.model_dump(mode="json") for m in baseline],
                "candidate": [m.model_dump(mode="json") for m in candidate],
                "upstream": {
                    "memory": memory.snapshot(),
                    "retrieval": retrieval.model_dump(mode="json"),
                    "world_before": before,
                    "world_after": manager.snapshot().model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "verdict": verdict.model_dump(mode="json"),
                },
            }
        )
    return prepared


def development_cases():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    old = replay_cases(source)  # also byte-checks every original baseline message
    probes = {p["id"]: p for p in source["probes"]}
    prefixes = {p["id"]: p for p in source["prefixes"]}
    output = []
    for case in old:
        if case["kind"] == "answer":
            probe = probes[case["source_probe"]]
            prefix = prefixes[probe["prefix_id"]]
            npc = probe["response"]["npc_id"]
            memory = MemoryStore(npc)
            verified = []
            for raw in probe["memory_before"]["episodic"]:
                raw = dict(raw)
                if raw["memory_id"] in prefix["written_ids"]:
                    tell = prefix["sequence"]["tell"]
                    if raw["event_description"] != Harness._reflection_text(
                        tell, prefix["response"]["dialogue"]
                    ):
                        raise ValueError("prefix provenance cannot be verified")
                    raw.update(
                        source_player_id=PLAYER, player_statement=Harness._clip_reflection(tell)
                    )
                    verified.append(raw["memory_id"])
                memory.add_episodic(EpisodicMemory.model_validate(raw))
            for raw in probe["memory_before"]["semantic"]:
                memory.add_semantic(SemanticMemory.model_validate(raw))
            steps = [TraceStep.model_validate(s) for s in probe["trace"]["steps"]]
            recalled = next(s for s in steps if s.step_name == "memory_retrieval")
            retrieval = MemoryRetrievalResult(
                episodic=memory.episodic_by_ids(
                    [m["id"] for m in recalled.output_summary["episodic"]]
                )
            )
            # Evaluation-only reconstruction from recorded successful tool output.
            # Runtime never guesses legacy provenance from strings.
            for step in steps:
                if (
                    step.step_name == "tool_call"
                    and step.input_summary.get("tool") == "query_memory"
                    and step.output_summary.get("ok") is True
                ):
                    result = step.output_summary["result"]
                    texts = result.get("episodic", [])
                    result["episodic_ids"] = [
                        m["memory_id"]
                        for m in memory.snapshot()["episodic"]
                        if m["event_description"] in texts
                    ]
            verdict = next(s.output_summary for s in steps if s.step_name == "action_validation")
            manager = WorldStateManager(WorldState.model_validate(probe["world_after"]))
            question = probe["question"]
            case["answer_patterns"] = [re.escape(probe["expected_answer"])]
            case["upstream"] = {
                "source_probe": probe["id"],
                "verified_player_ids": verified,
                "trace": probe["trace"],
                "world": probe["world_after"],
                "memory": memory.snapshot(),
            }
        else:
            npc = case["id"].removeprefix("privacy_")
            memory = MemoryStore(npc)
            load_seed_memories("village_disappearance")[npc].load_into(memory)
            manager = WorldStateManager(load_scenario("village_disappearance"))
            retrieval = memory.retrieve("那晚", top_k=10)
            steps, verdict = [], {"approved": False}
            question = (
                "你那晚看见的人是谁？他后来对你儿子说了什么？"
                if npc == "npc_a"
                else "那晚林子里发生了什么？你为什么这么害怕？"
            )
        harness = make_harness(npc, manager, memory, None)
        messages = harness._expression_messages(
            question,
            retrieval,
            steps,
            PLAYER,
            action_status="applied" if verdict["approved"] else "rejected",
        )
        case["candidate"] = [m.model_dump(mode="json") for m in messages]
        case["question"] = question
        case["npc"] = npc
        output.append(case)
    return output


def screen(case, dialogue, calls):
    """Screens only: no substring miss is interpreted as safety or grounding."""
    patterns = case.get("answer_patterns", [])
    return {
        "literal_answer": all(re.search(p, dialogue) for p in patterns) if patterns else None,
        "prohibited_literal_hits": [
            p for p in case.get("prohibited_patterns", []) if re.search(p, dialogue)
        ],
        "answer_in_final_context": all(
            re.search(p, "\n".join(m["content"] for m in calls[-1]["messages"])) for p in patterns
        )
        if patterns
        else None,
        "assistant_review": None,
    }


def run(*, stage, output, repeats=3, max_requests=None, max_seconds=None, max_tokens=None):
    if repeats != 3:
        raise ValueError("this frozen protocol requires exactly three repetitions")
    if fingerprint(HOLDOUT) != HOLDOUT_SHA:
        raise ValueError("frozen holdout changed")
    limits = {"dev": (48, 600, 150000), "local": (56, 600, 150000), "full": (180, 1200, 450000)}[
        stage
    ]
    max_requests = max_requests if max_requests is not None else limits[0]
    max_seconds = max_seconds if max_seconds is not None else limits[1]
    max_tokens = max_tokens if max_tokens is not None else limits[2]
    if min(max_requests, max_seconds, max_tokens) <= 0:
        raise ValueError("budgets must be positive")
    cases = (
        development_cases()
        if stage == "dev"
        else json.loads(HOLDOUT.read_text(encoding="utf-8"))["cases"]
    )
    if stage == "local":
        cases = local_holdout_cases(cases)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    provenance = manifest()
    for path in [
        Path(__file__),
        ROOT / "src/ai_native_rpg/agent/expression.py",
        ROOT / "src/ai_native_rpg/schemas/memory.py",
        ROOT / "src/ai_native_rpg/agent/tools.py",
        HOLDOUT,
        ROOT / "scripts/eval_expression.py",
    ]:
        provenance["files"][path.relative_to(ROOT).as_posix()] = fingerprint(path)
    # Archive exact dirty-worktree sources as well as hashes; HEAD alone is insufficient.
    for relative in provenance["files"]:
        destination = output / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    report = {
        "stage": stage,
        "status": "unavailable",
        "manifest": provenance,
        "source_sha256": fingerprint(SOURCE),
        "cases": cases,
        "repeats": repeats,
        "temperature": 0,
        "expected_outputs": len(cases) * repeats * 2,
        "budgets": {
            "requests": max_requests,
            "seconds": max_seconds,
            "known_tokens": max_tokens,
            "output_tokens_per_request": 600,
        },
        "rows": [],
        "currency_cost": None,
        "currency_cost_reason": "No verified provider tariff; HTTP token usage retained.",
        "limitations": [
            "Assistant review, not independent human annotation or calibrated judge.",
            "Authored memory prefix; full means respond with real planning/tools/action.",
            "Fixed-action cases only force rejection in local comparison.",
            "Small correlated synthetic sample; no generalization claim.",
        ],
    }
    settings = Settings.from_env()
    if not settings.has_real_backend:
        report["error_type"] = "RealBackendUnavailable"
        write_json(output / "report.json", report)
        return report
    report["requested_model"] = settings.model
    transport = ExpressionTransport(
        max_requests=max_requests, max_seconds=max_seconds, max_tokens=max_tokens
    )
    backend = OpenAICompatibleClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
        timeout=25,
        max_attempts=1,
        transport=transport,
    )
    try:
        for repeat in range(1, repeats + 1):
            for case in cases:
                order = ["baseline", "candidate"] if repeat % 2 else ["candidate", "baseline"]
                for variant in order:
                    row = {
                        "id": f"{case['id']}_{variant}_r{repeat}",
                        "case_id": case["id"],
                        "variant": variant,
                        "repeat": repeat,
                        "kind": case["kind"],
                    }
                    report["rows"].append(row)
                    client = EvidenceClient(backend)
                    start, started = len(transport.records), time.perf_counter()
                    try:
                        if stage == "full":
                            harness, manager, memory = prepare_holdout(
                                case, client, candidate=variant == "candidate"
                            )
                            row.update(
                                world_before=manager.snapshot().model_dump(mode="json"),
                                memory_before=memory.snapshot(),
                            )
                            response, trace = harness.respond(case["question"], player_id=PLAYER)
                            row.update(
                                response=response.model_dump(mode="json"),
                                trace=trace.model_dump(mode="json"),
                                world_after=manager.snapshot().model_dump(mode="json"),
                                memory_after=memory.snapshot(),
                            )
                            row["dialogue"] = response.dialogue
                        else:
                            reply = client.complete(
                                [Message.model_validate(m) for m in case[variant]],
                                schema=DialogueOutput,
                            )
                            row["dialogue"] = reply.parsed.dialogue
                        row.update(screen(case, row["dialogue"], client.calls))
                    except Exception as exc:
                        row["error_type"] = type(exc).__name__
                    finally:
                        row.update(
                            calls=client.calls,
                            cost=wire_cost(transport.records[start:]),
                            latency_ms=round((time.perf_counter() - started) * 1000, 3),
                            wire_range=[start, len(transport.records)],
                        )
                        write_json(output / "report.json", report)
                        write_json(output / "wire.json", transport.records)
                        status = "completed" if "dialogue" in row else row["error_type"]
                        print(f"{row['id']}: {status}", flush=True)
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
