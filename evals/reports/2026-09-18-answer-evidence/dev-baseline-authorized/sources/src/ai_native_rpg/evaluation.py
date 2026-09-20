"""Small fixed-suite evaluator; no judge, search, database or runtime scheduling.

Reports retain observations, labelled expectations, traces and before/after state.
Mock scripts exercise contracts only. Literal checks are screening, not semantic truth.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .agent.harness import Harness, PromptLibrary
from .agent.memory_store import MemoryStore
from .agent.tools import build_npc_tools
from .config import Settings
from .llm.base import ToolCall
from .llm.mock import MockLLMClient
from .llm.openai_compatible import OpenAICompatibleClient
from .observability.trace_store import TraceStore
from .scenario import load_personas, load_scenario, load_seed_memories
from .schemas.agent_trace import AgentTrace
from .schemas.npc_agent import NPCAgentResponse
from .schemas.world_state import ActionProposal
from .world.manager import WorldStateManager

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals/cases.json"
PLAYER = "player_1"


def recall(retrieved, labels, k):
    """Micro recall counts; absent evidence/empty gold is missing, never a pass."""
    if retrieved is None or not labels:
        return None
    gold = set(labels)
    return len(set(retrieved[:k]) & gold), len(gold)


def aggregate(rows):
    result = {}
    for name in sorted({key for row in rows for key in row}):
        values = [row[name] for row in rows if name in row]
        known = [value for value in values if value is not None]
        numerator = sum(value[0] for value in known)
        denominator = sum(value[1] for value in known)
        result[name] = {
            "numerator": numerator,
            "denominator": denominator,
            "value": numerator / denominator if denominator else None,
            "missing": len(values) - len(known),
        }
    return result


class BudgetExceeded(RuntimeError):
    pass


class RecordingTransport(httpx.BaseTransport):
    """Count every wire attempt (including reparses); never persist headers/URLs."""

    def __init__(self, *, max_requests=32, max_seconds=240, inner=None):
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.started = time.monotonic()
        self.records = []

    def handle_request(self, request):
        if len(self.records) >= self.max_requests:
            raise BudgetExceeded("request budget exhausted")
        if time.monotonic() - self.started >= self.max_seconds:
            raise BudgetExceeded("elapsed budget exhausted before next request")
        body = json.loads(request.content)
        body["max_tokens"] = 600
        headers = dict(request.headers)
        headers.pop("content-length", None)
        bounded = httpx.Request(
            request.method, request.url, headers=headers, json=body, extensions=request.extensions
        )
        record = {"model": None, "usage": None, "status": None}
        self.records.append(record)
        start = time.perf_counter()
        try:
            response = self.inner.handle_request(bounded)
            response.read()
            record["status"] = response.status_code
            if response.status_code == 200:
                raw = response.json()
                record["model"] = raw.get("model")
                record["usage"] = raw.get("usage") or None
                choices = raw.get("choices") or [{}]
                record["response_message"] = choices[0].get("message")
                record["finish_reason"] = choices[0].get("finish_reason")
            return response
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["latency_ms"] = round((time.perf_counter() - start) * 1000, 3)

    def close(self):
        self.inner.close()


class RecordingClient:
    """Persist parsed outputs and safe request fingerprints, never credentials."""

    def __init__(self, client):
        self.client = client
        self.calls = []

    def complete(self, messages, *, schema, temperature=0.7, tools=None):
        prompt = "\n".join(message.content for message in messages)
        record = {
            "schema": schema.__name__,
            "temperature": 0,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
        self.calls.append(record)
        start = time.perf_counter()
        try:
            response = self.client.complete(messages, schema=schema, temperature=0, tools=tools)
            record.update(response.model_dump(mode="json", serialize_as_any=True))
            return response
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["latency_ms"] = round((time.perf_counter() - start) * 1000, 3)


def _script(turn):
    responses = []
    if "options" in turn:
        responses.append({"matched_option_id": turn["expected_option"]})
    if "resolved_outcome" not in turn:
        if turn.get("mock_tools"):
            responses.append(
                [ToolCall(id=f"call_{i}", **call) for i, call in enumerate(turn["mock_tools"])]
            )
        responses.append(
            {
                "reasoning": "Contract fixture: use recorded information and obey validation.",
                "strategy": "fixture",
                "dialogue": turn["mock_dialogue"],
                "action": turn.get("mock_action"),
            }
        )
    if "resolved_outcome" in turn or turn.get("mock_action"):
        responses.append({"dialogue": turn["mock_dialogue"]})
    return responses


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in {"last_updated", "timestamp"}}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _state_consistent(before, after, response, trace):
    """Independent allowed-delta oracle for these fixtures: no writes except relations."""
    expected = _stable(before)
    proposal = response.plan.action_proposal
    validations = [s for s in trace.steps if s.step_name == "action_validation"]
    if proposal is not None:
        if len(validations) != 1:
            return False
        approved = validations[0].output_summary.get("approved")
        if not isinstance(approved, bool) or bool(response.action_proposal_id) != approved:
            return False
        if approved:
            if proposal.action_type == "reveal_fact":
                fact = expected["facts"].get(proposal.target_id)
                # Reaffirming an already public fact is an idempotent authorized action.
                return bool(
                    fact and fact["visibility"] == "revealed" and expected == _stable(after)
                )
            if proposal.action_type != "adjust_relationship" or proposal.target_id != PLAYER:
                return False
            relation = expected["relationships"][response.npc_id][PLAYER]
            for dimension, delta in proposal.payload.items():
                if dimension not in {"trust", "fear", "respect"} or abs(float(delta)) > 15:
                    return False
                relation[dimension] = max(-100, min(100, relation[dimension] + float(delta)))
    return expected == _stable(after)


def score_turn(turn, trace, response, before, after, labels):
    scores = {"completed_turns": (1, 1)}
    retrieval = next((s for s in trace.steps if s.step_name == "memory_retrieval"), None)
    if turn.get("recall_ids") or turn.get("recall_previous"):
        retrieved = (
            None
            if retrieval is None or "episodic" not in retrieval.output_summary
            else [m["id"] for m in retrieval.output_summary.get("episodic", [])]
        )
        scores["memory_recall_at_3"] = recall(retrieved, labels, 3)
    tools = [s for s in trace.steps if s.step_name == "tool_call"]
    scores["tool_execution"] = (
        (sum(s.output_summary.get("ok") is True for s in tools), len(tools))
        if all("ok" in s.output_summary for s in tools)
        else None
    )
    required = set(turn.get("required_tools", []))
    if required:
        called = {s.input_summary.get("tool") for s in tools if s.output_summary.get("ok") is True}
        scores["required_tools"] = (len(required & called), len(required))
    if turn.get("must_contain"):
        scores["literal_answer"] = (
            int(all(s in response.dialogue for s in turn["must_contain"])),
            1,
        )
    if turn.get("forbidden_patterns"):
        scores["literal_disclosure_guard"] = (
            int(not any(re.search(p, response.dialogue) for p in turn["forbidden_patterns"])),
            1,
        )
    scores["state_consistency"] = (int(_state_consistent(before, after, response, trace)), 1)
    return scores


def missing_scores(turn):
    scores = {"completed_turns": (0, 1), "tool_execution": None, "state_consistency": None}
    for key, applicable in {
        "memory_recall_at_3": turn.get("recall_ids") or turn.get("recall_previous"),
        "required_tools": turn.get("required_tools"),
        "literal_answer": turn.get("must_contain"),
        "literal_disclosure_guard": turn.get("forbidden_patterns"),
        "classification": "expected_option" in turn,
    }.items():
        if applicable:
            scores[key] = None
    return scores


def _environment_probe():
    manager = WorldStateManager(load_scenario("village_disappearance"))
    results = []
    # Start at Marta's house: reject a nonadjacent move, then take two real edges.
    for i, (target, approved) in enumerate(
        [("forest_edge", False), ("village_square", True), ("forest_edge", True)]
    ):
        before = manager.snapshot()
        verdict = manager.submit(
            ActionProposal(
                proposal_id=f"eval_move_{i}", actor_id=PLAYER, action_type="move", target_id=target
            )
        )
        after = manager.snapshot()
        correct = verdict.approved == approved and (
            after.player_locations[PLAYER] == target if approved else after == before
        )
        results.append(
            {
                "target": target,
                "expected_approved": approved,
                "verdict": verdict.model_dump(mode="json"),
                "passed": correct,
            }
        )
    return results


def _fingerprints():
    files = [
        CASES,
        ROOT / "scenarios/village_disappearance/world.yaml",
        ROOT / "src/ai_native_rpg/agent/harness.py",
        *sorted((ROOT / "prompts").glob("*.txt")),
    ]
    return {
        str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
    }


def run_suite(
    *, mode="mock", output, label="baseline", max_requests=32, max_seconds=240, variant="candidate"
):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    dataset = json.loads(CASES.read_text(encoding="utf-8"))
    settings = Settings.from_env()
    transport = None
    backend = None
    limitation = None
    if mode == "real":
        if not settings.has_real_backend:
            limitation = (
                "Real backend unavailable: missing usable credentials/config or USE_MOCK_LLM."
            )
        else:
            transport = RecordingTransport(max_requests=max_requests, max_seconds=max_seconds)
            backend = OpenAICompatibleClient(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
                timeout=20,
                max_attempts=1,
                transport=transport,
            )
    report = {
        "format_version": 1,
        "label": label,
        "variant": variant,
        "mode": "real_model" if mode == "real" else "mock_contract",
        "created_at": datetime.now(UTC).isoformat(),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "fingerprints": _fingerprints(),
        "dataset": dataset,
        "config": {
            "requested_model": settings.model if mode == "real" else "scripted-mock",
            "provider": settings.provider if mode == "real" else "offline",
            "temperature": 0,
            "repeats": 1,
            "embedder": "HashingEmbedder(dim=64)",
            "top_k_per_memory_bucket": 3,
            "max_tool_iterations": 2,
            "max_http_requests": max_requests,
            "max_elapsed_seconds": max_seconds,
            "http_timeout_seconds": 20,
            "max_output_tokens_per_request": 600,
        },
        "limitations": [
            "N=1 smoke evaluation; no statistical or semantic quality claim.",
            "Literal disclosure checks need human review; paraphrases can escape.",
            "Authored outcomes are supplied fixtures, not full Web resolution tests.",
            "Time budget checked before requests; in-flight I/O uses HTTP timeout.",
        ],
        "turns": [],
        "environment_contract": _environment_probe(),
    }
    if limitation:
        report["limitations"].append(limitation)
    all_calls = []
    try:
        for session in dataset["sessions"]:
            npc_id = session["npc_id"]
            manager = WorldStateManager(load_scenario(dataset["scenario"]))
            memory = MemoryStore(npc_id)
            load_seed_memories(dataset["scenario"])[npc_id].load_into(memory)
            previous_ids = []
            blocked = limitation
            for index, turn in enumerate(session["turns"]):
                turn_id = f"{session['id']}_{index + 1}"
                entry = {"id": turn_id, "input": turn, "scores": missing_scores(turn)}
                report["turns"].append(entry)
                if blocked:
                    entry["error"] = blocked
                    continue
                client = RecordingClient(backend or MockLLMClient(_script(turn)))
                harness = Harness(
                    npc_state=load_personas(dataset["scenario"])[npc_id],
                    manager=manager,
                    llm=client,
                    memory=memory,
                    prompts=PromptLibrary(),
                    tools=build_npc_tools(
                        npc_id=npc_id, manager=manager, memory=memory, player_id=PLAYER
                    ),
                    max_tool_iterations=2,
                    retain_dialogue_evidence=variant == "candidate",
                )
                before = manager.snapshot().model_dump(mode="json")
                before_ids = {m["memory_id"] for m in memory.snapshot()["episodic"]}
                labels = previous_ids if turn.get("recall_previous") else turn.get("recall_ids", [])
                entry.update(before=before, memory_before=memory.snapshot(), recall_labels=labels)
                try:
                    match_step = None
                    if "options" in turn:
                        matched, match_step = harness.classify_option(turn["text"], turn["options"])
                        entry["matched_option"] = matched
                    response, trace = harness.respond(
                        turn["text"],
                        player_id=PLAYER,
                        session_id=session["id"],
                        resolved_outcome=turn.get("resolved_outcome"),
                    )
                    if match_step is not None:
                        trace.steps.insert(0, match_step)
                    after = manager.snapshot().model_dump(mode="json")
                    entry.update(
                        after=after,
                        dialogue=response.dialogue,
                        response=response.model_dump(mode="json"),
                        trace_file=f"traces/{trace.trace_id}.json",
                    )
                    entry["scores"] = score_turn(turn, trace, response, before, after, labels)
                    if "expected_option" in turn:
                        entry["scores"]["classification"] = (
                            int(entry["matched_option"] == turn["expected_option"]),
                            1,
                        )
                    TraceStore(output / "traces").save(trace)
                    previous_ids = [
                        m["memory_id"]
                        for m in memory.snapshot()["episodic"]
                        if m["memory_id"] not in before_ids
                    ]
                except Exception as exc:
                    # Exception text can contain upstream output; retain type + safe wire status.
                    entry["error"] = type(exc).__name__
                    entry["after"] = manager.snapshot().model_dump(mode="json")
                    blocked = "Previous turn failed; dependent continuation skipped."
                finally:
                    entry["calls"] = client.calls
                    all_calls.extend(client.calls)
    finally:
        if backend is not None:
            backend.close()
    report["metrics"] = aggregate([t["scores"] for t in report["turns"]])
    report["failed_cases"] = [
        t["id"]
        for t in report["turns"]
        if "error" in t
        or any(value is None or value[0] < value[1] for value in t["scores"].values())
    ]
    wire = transport.records if transport else []
    totals = defaultdict(int)
    for record in wire:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = (record.get("usage") or {}).get(key)
            if isinstance(value, int):
                totals[key] += value
    report["wire_requests"] = wire
    report["cost"] = {
        "logical_calls": len(all_calls),
        "http_requests": len(wire),
        "tokens": dict(totals) or None,
        "requests_missing_usage": sum(not r.get("usage") for r in wire),
        "returned_models": sorted({r["model"] for r in wire if r.get("model")}),
        "logical_call_latency_ms": round(sum(c["latency_ms"] for c in all_calls), 3),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "report.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(report):
    lines = [
        f"# Minimal Eval: {report['label']}",
        "",
        f"Mode: **{report['mode']}**; model: `{report['config']['requested_model']}`; "
        f"temperature=0; N=1; dataset={report['dataset']['version']}.",
        "",
        "| Metric | Numerator / denominator | Value | Missing |",
        "|---|---:|---:|---:|",
    ]
    for name, metric in report["metrics"].items():
        lines.append(
            f"| {name} | {metric['numerator']} / {metric['denominator']} | "
            f"{metric['value']} | {metric['missing']} |"
        )
    lines.extend(
        [
            "",
            f"Cost: `{json.dumps(report['cost'], ensure_ascii=False)}`",
            "",
            "Failed cases: " + (", ".join(report["failed_cases"]) or "none"),
            "",
        ]
    )
    for turn in report["turns"]:
        lines.extend(
            [
                f"## {turn['id']}",
                "",
                turn.get("dialogue", turn.get("error", "missing")),
                "",
                f"Scores: `{json.dumps(turn['scores'])}`",
                "",
            ]
        )
    lines.extend(["## Limits", "", *[f"- {s}" for s in report["limitations"]], ""])
    return "\n".join(lines)


def compare_reports(baseline_path, candidate_path, output):
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    if baseline["mode"] != candidate["mode"] or baseline["dataset"] != candidate["dataset"]:
        raise ValueError("Compare only the same mode and fixed dataset")
    if baseline["config"] != candidate["config"]:
        raise ValueError("Compare only identical configurations")
    if baseline["cost"]["returned_models"] != candidate["cost"]["returned_models"]:
        raise ValueError("Compare only identical returned model identifiers")
    lines = [
        "# Fixed-suite comparison",
        "",
        f"Mode: {baseline['mode']}; N=1 per variant.",
        "",
        "Positive deltas are observations, not statistical evidence of better model quality.",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "|---|---:|---:|---:|",
    ]
    for name in sorted(baseline["metrics"].keys() | candidate["metrics"].keys()):
        left = baseline["metrics"].get(name, {}).get("value")
        right = candidate["metrics"].get(name, {}).get("value")
        delta = right - left if left is not None and right is not None else None
        lines.append(f"| {name} | {left} | {right} | {delta} |")
    for title, report in (("Baseline", baseline), ("Candidate", candidate)):
        lines.extend(
            [
                "",
                f"{title} cost: `{json.dumps(report['cost'])}`",
                f"{title} failures: {', '.join(report['failed_cases']) or 'none'}",
            ]
        )
    Path(output).write_text("\n".join(lines) + "\n", encoding="utf-8")


def rescore_report(source, output):
    """Re-score retained evidence without another model call; preserve the source."""
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("Rescoring must preserve the original report")
    report = json.loads(source.read_text(encoding="utf-8"))
    for entry in report["turns"]:
        if "error" in entry:
            entry["scores"] = missing_scores(entry["input"])
            continue
        trace = AgentTrace.model_validate_json(
            (source.parent / entry["trace_file"]).read_text(encoding="utf-8")
        )
        entry["scores"] = score_turn(
            entry["input"],
            trace,
            NPCAgentResponse.model_validate(entry["response"]),
            entry["before"],
            entry["after"],
            entry["recall_labels"],
        )
        if "expected_option" in entry["input"]:
            entry["scores"]["classification"] = (
                int(entry["matched_option"] == entry["input"]["expected_option"]),
                1,
            )
    report["rescoring"] = {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reason": "Allow idempotent reveal of already public facts; retain missing evidence.",
        "model_calls_added": 0,
    }
    report["metrics"] = aggregate([t["scores"] for t in report["turns"]])
    report["failed_cases"] = [
        t["id"]
        for t in report["turns"]
        if "error" in t
        or any(value is None or value[0] < value[1] for value in t["scores"].values())
    ]
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text(render_report(report), encoding="utf-8")
