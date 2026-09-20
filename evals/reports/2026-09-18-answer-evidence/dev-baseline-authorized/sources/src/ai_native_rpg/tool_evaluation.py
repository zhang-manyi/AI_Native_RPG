"""Small tool decisions and dependent tasks, using the real Harness and rules.

Generation receives only case['input']; labels are consulted after execution.
Observer subclasses preserve partial steps and state even if generation fails.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .agent.embedding import HashingEmbedder
from .agent.harness import Harness, PlanningOutput, PromptLibrary
from .agent.memory_store import MemoryStore
from .agent.tools import build_npc_tools
from .config import Settings
from .evaluation import PLAYER, ROOT, BudgetExceeded, _stable, aggregate
from .llm.openai_compatible import OpenAICompatibleClient
from .memory_evaluation import EvidenceClient, fingerprint, manifest, wire_cost, write_json
from .scenario import load_personas, load_scenario, load_seed_memories
from .world.manager import WorldStateManager

DATASET = ROOT / "evals/tool_tasks.json"
EXPERIMENT = ROOT / "evals/reports/2026-09-18-tools"
BUDGET = {"requests": 120, "tokens": 300_000, "seconds": 600, "output_tokens": 600}


class TaskTransport(httpx.BaseTransport):
    """Reserve a conservative UTF-8 byte bound before every HTTP attempt.

    Unknown usage keeps its reservation; failed/blocked attempts are never free.
    Only the JSON body and response body are archived, never headers or URLs.
    """

    def __init__(self, *, inner=None, budget=None):
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.budget = dict(budget or BUDGET)
        self.started = time.monotonic()
        self.records = []
        self.charged_tokens = 0
        self.stopped = None

    def handle_request(self, request):
        if self.stopped:
            raise BudgetExceeded(self.stopped)
        body = json.loads(request.content)
        body["max_tokens"] = self.budget["output_tokens"]
        # Includes tools/schema plus 512 tokens of framing allowance. This is a
        # conservative engineering bound, not a provider tokenizer or billing rate.
        reservation = len(json.dumps(body, ensure_ascii=False).encode()) + 512 + body["max_tokens"]
        if (
            len(self.records) >= self.budget["requests"]
            or time.monotonic() - self.started >= self.budget["seconds"]
            or self.charged_tokens + reservation > self.budget["tokens"]
            or reservation > 40_000
        ):
            self.stopped = "budget exhausted before request"
            raise BudgetExceeded(self.stopped)
        headers = dict(request.headers)
        headers.pop("content-length", None)
        bounded = httpx.Request(
            request.method, request.url, headers=headers, json=body, extensions=request.extensions
        )
        record = {"request_body": body, "model": None, "usage": None, "status": None}
        self.records.append(record)
        self.charged_tokens += reservation
        started = time.perf_counter()
        try:
            response = self.inner.handle_request(bounded)
            response.read()
            record["status"] = response.status_code
            try:
                raw = response.json()
            except ValueError:
                raw = {"non_json_body": response.text}
            record["response_body"] = raw
            record["model"] = raw.get("model")
            record["usage"] = raw.get("usage") or None
            if record["usage"] and "total_tokens" in record["usage"]:
                self.charged_tokens += record["usage"]["total_tokens"] - reservation
            record["finish_reason"] = (raw.get("choices") or [{}])[0].get("finish_reason")
            return response
        except httpx.ConnectError:
            # One infrastructure failure is enough; keep remaining planned rows.
            self.stopped = "connection unavailable"
            record["error_type"] = "ConnectError"
            raise
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["reserved_tokens"] = reservation
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)

    def close(self):
        self.inner.close()


class ObservedManager(WorldStateManager):
    def __init__(self, state):
        super().__init__(state)
        self.journal = []

    def submit(self, proposal):
        before = self.snapshot().model_dump(mode="json")
        verdict = super().submit(proposal)
        self.journal.append(
            {
                "proposal": proposal.model_dump(mode="json"),
                "verdict": verdict.model_dump(mode="json"),
                "before": before,
                "after": self.snapshot().model_dump(mode="json"),
            }
        )
        return verdict


class ObservedTools:
    def __init__(self, registry, manager):
        self.registry, self.manager = registry, manager
        self.reads = []

    def specs(self):
        return self.registry.specs()

    def call(self, name, arguments):
        before = self.manager.snapshot().model_dump(mode="json")
        row = {"tool": name, "arguments": arguments}
        self.reads.append(row)
        try:
            result = self.registry.call(name, arguments)
            row.update(ok=True, result=result)
            return result
        except Exception as exc:
            row.update(ok=False, error_type=type(exc).__name__)
            raise
        finally:
            row["state_unchanged"] = _stable(before) == _stable(
                self.manager.snapshot().model_dump(mode="json")
            )


class ObservedHarness(Harness):
    def _retrieve(self, observation, npc_id, player_id, steps):
        self.partial_steps = steps
        return super()._retrieve(observation, npc_id, player_id, steps)


def prepare(inputs, client, variant="baseline"):
    """No expected answer, scoring label or desired action is read here."""
    npc = inputs["npc"]
    manager = ObservedManager(load_scenario("village_disappearance"))
    memory = MemoryStore(npc, embedder=HashingEmbedder(64))
    load_seed_memories("village_disappearance")[npc].load_into(memory)
    tools = ObservedTools(
        build_npc_tools(npc_id=npc, manager=manager, memory=memory, player_id=PLAYER), manager
    )
    overlay = EXPERIMENT / "candidate" if variant == "candidate" else None
    if overlay is not None and not (overlay / "npc_planning.txt").is_file():
        raise ValueError("candidate has not been selected")
    harness = ObservedHarness(
        npc_state=load_personas("village_disappearance")[npc],
        manager=manager,
        memory=memory,
        llm=client,
        tools=tools,
        prompts=PromptLibrary(overlay=overlay),
        top_k=3,
        max_tool_iterations=3,
        retain_dialogue_evidence=False,
        filtered_dialogue_evidence=False,
    )
    for i, statement in enumerate(inputs.get("prefix", [])):
        harness.remember_exchange(statement, "听见了。", player_id=PLAYER)
        memory._episodic[-1] = memory._episodic[-1].model_copy(update={"memory_id": f"prefix_{i}"})
    return harness, manager, memory, tools


def state_consistent(row):
    """Independent delta oracle for this suite; approval alone never passes."""
    expected = _stable(row["world_before"])
    for item in row["journal"]:
        if expected != _stable(item["before"]):
            return False
        p, v = item["proposal"], item["verdict"]
        if v["approved"]:
            actor, target = p["actor_id"], p["target_id"]
            if p["action_type"] == "move":
                origin = expected["npcs"][actor]["location"]
                if target != origin and target not in expected["locations"][origin]["connected_to"]:
                    return False
                expected["npcs"][actor]["location"] = target
            elif p["action_type"] == "adjust_relationship":
                relation = (
                    expected["relationships"]
                    .setdefault(actor, {})
                    .setdefault(target, {"trust": 0, "fear": 0, "respect": 0})
                )
                for dimension, delta in p["payload"].items():
                    if dimension not in {"trust", "fear", "respect"} or abs(delta) > 15:
                        return False
                    relation[dimension] = max(-100, min(100, relation[dimension] + delta))
            elif p["action_type"] == "reveal_fact":
                expected["facts"][target]["visibility"] = "revealed"
            elif p["action_type"] == "advance_quest":
                quest = expected["quests"][target]
                quest["stage"] += 1
                if quest["status"] == "not_started":
                    quest["status"] = "active"
            else:
                return False
        if expected != _stable(item["after"]):
            return False
    if "response" in row:
        applied = [j["proposal"]["proposal_id"] for j in row["journal"] if j["verdict"]["approved"]]
        if row["response"]["action_proposal_id"] != (applied[-1] if applied else None):
            return False
    return expected == _stable(row["world_after"]) and all(
        r["state_unchanged"] for r in row["tools"]
    )


def structural_scores(row, expect):
    completed = "dialogue" in row
    scores = {
        "completion": (int(completed), 1),
        "state_consistency": (int(state_consistent(row)), 1),
    }
    if row.get("error_type") in {
        "Offline",
        "DependencyFailed",
        "BackendUnavailable",
        "connection unavailable",
        "budget exhausted before request",
    }:
        scores["state_consistency"] = None
    calls = row["tools"]
    scores["tool_execution"] = (sum(c["ok"] for c in calls), len(calls))
    scores["readonly_tools"] = (sum(c["state_unchanged"] for c in calls), len(calls))
    initial = next((s for s in row["steps"] if s["step_name"] == "memory_retrieval"), None)
    initial_ids = [m["id"] for m in initial["output_summary"]["episodic"]] if initial else []
    all_ids = set(initial_ids)
    for call in calls:
        all_ids.update(call.get("result", {}).get("episodic_ids", []))
    row["initial_memory_ids"], row["all_memory_ids"] = initial_ids, sorted(all_ids)
    gold = expect.get("memory_id")
    if gold:
        scores["initial_recall"] = (int(gold in initial_ids), 1) if initial else None
        scores["whole_turn_recall"] = (int(gold in all_ids), 1) if initial else None
    policy = expect.get("tools")
    if policy is not None:
        needed = set(policy["required"])
        if gold and gold in initial_ids:
            needed.discard("query_memory")
        called = {c["tool"] for c in calls}
        allowed = set(policy["allowed"])
        scores["tool_selection"] = (
            (int(needed <= called and called <= allowed), 1) if completed else None
        )
        seen, good, assessed = set(), 0, 0
        for call in calls:
            key = json.dumps([call["tool"], call["arguments"]], sort_keys=True, ensure_ascii=False)
            args = call["arguments"]
            valid = call["ok"] and call["tool"] in allowed and key not in seen
            if call["tool"] == "check_public_fact":
                valid &= args.get("fact_id") == expect.get("fact_id")
            elif call["tool"] == "query_relationship":
                valid &= args.get("target_id") == PLAYER
            elif call["tool"] == "query_memory":
                if not gold and valid:
                    # Private recollection relevance needs semantic review; no
                    # labelled answer ID exists for these boundary probes.
                    seen.add(key)
                    continue
                valid &= bool(gold and gold in call.get("result", {}).get("episodic_ids", []))
                valid &= isinstance(args.get("query"), str) and bool(args["query"].strip())
            good += bool(valid)
            assessed += 1
            seen.add(key)
        scores["tool_parameters"] = (good, assessed)
        row["parameters_needing_review"] = len(calls) - assessed
        scores["no_duplicate_calls"] = (int(len(seen) == len(calls)), 1) if completed else None
    goal = expect["goal"]
    if goal == "move":
        achieved = row["world_after"]["npcs"][row["npc"]]["location"] == expect["target"]
        scores["state_goal"] = (int(achieved), 1)
        scores["action_selection"] = (
            (
                int(
                    any(
                        j["proposal"]["action_type"] == "move"
                        and j["proposal"]["target_id"] == expect["target"]
                        for j in row["journal"]
                    )
                ),
                1,
            )
            if completed
            else None
        )
    elif goal == "deny":
        matched = [
            j
            for j in row["journal"]
            if j["proposal"]["action_type"] == expect["action_type"]
            and j["proposal"]["target_id"] == expect["target"]
        ]
        scores["actual_rejection_path"] = (
            int(any(not j["verdict"]["approved"] for j in matched)),
            1,
        )
        scores["action_selection"] = (int(bool(matched)), 1) if completed else None
        if expect["action_type"] == "move":
            achieved = (
                row["world_after"]["npcs"][row["npc"]]["location"]
                == row["world_before"]["npcs"][row["npc"]]["location"]
            )
        else:
            achieved = (
                row["world_after"]["facts"][expect["target"]]["visibility"]
                == row["world_before"]["facts"][expect["target"]]["visibility"]
            )
        scores["state_goal"] = (int(achieved), 1)
    return scores


def execute_turn(harness, manager, memory, tools, client, turn, *, local=False):
    row = {
        "npc": harness._npc.npc_id,
        "question": turn["question"],
        "world_before": manager.snapshot().model_dump(mode="json"),
        "memory_before": memory.snapshot(),
    }
    started = time.perf_counter()
    ji, ti, ci = len(manager.journal), len(tools.reads), len(client.calls)
    harness.partial_steps = []
    try:
        if local:
            steps = harness.partial_steps
            retrieval = harness._retrieve(turn["question"], harness._npc.npc_id, PLAYER, steps)
            planning = PlanningOutput(
                reasoning="尝试玩家要求的行动，遵守裁决。",
                strategy="attempt",
                dialogue="",
                action=turn["fixed_action"],
            )
            plan, dialogue, applied = harness._act_and_regenerate(
                planning,
                harness._npc.npc_id,
                steps,
                turn["question"],
                player_id=PLAYER,
                retrieval=retrieval,
            )
            row.update(
                dialogue=dialogue, local_plan=plan.model_dump(mode="json"), applied_id=applied
            )
        else:
            response, trace = harness.respond(turn["question"], player_id=PLAYER)
            row.update(
                dialogue=response.dialogue,
                response=response.model_dump(mode="json"),
                trace=trace.model_dump(mode="json"),
            )
    except Exception as exc:
        row["error_type"] = type(exc).__name__
    finally:
        row.update(
            world_after=manager.snapshot().model_dump(mode="json"),
            memory_after=memory.snapshot(),
            journal=manager.journal[ji:],
            tools=tools.reads[ti:],
            calls=client.calls[ci:],
            steps=[s.model_dump(mode="json") for s in harness.partial_steps],
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
    return row


def source_manifest(output, *, dataset=DATASET, experiment=EXPERIMENT, extra_sources=()):
    provenance = manifest()
    paths = [
        dataset,
        Path(__file__),
        ROOT / "scripts/eval_tools.py",
        experiment / "protocol.md",
        experiment / "freeze.json",
        *sorted((ROOT / "src/ai_native_rpg").rglob("*.py")),
        *sorted((experiment / "candidate").glob("*.txt")),
        *extra_sources,
    ]
    for path in paths:
        provenance["files"][path.relative_to(ROOT).as_posix()] = fingerprint(path)
    for relative in provenance["files"]:
        dest = output / "sources" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / relative).read_bytes())
    return provenance


def run(
    *, split, variant, output, offline=False, dataset=DATASET, experiment=EXPERIMENT,
    budget=None, prepare_case=prepare, extra_sources=(), configuration=None,
):
    data = json.loads(dataset.read_text(encoding="utf-8"))
    freeze = json.loads((experiment / "freeze.json").read_text(encoding="utf-8"))
    if fingerprint(dataset) != freeze["dataset_sha256"]:
        raise ValueError("frozen dataset changed")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = [c for c in data["cases"] if c["split"] == split]
    report = {
        "split": split,
        "variant": variant,
        "status": "running",
        "cases": cases,
        "manifest": source_manifest(
            output, dataset=dataset, experiment=experiment, extra_sources=extra_sources
        ),
        "budget": dict(budget or BUDGET),
        "repeats": 3,
        "configuration": {
            "embedding": "Hashing(64)",
            "top_k": 3,
            "max_tool_iterations": 3,
            "temperature": 0,
            "retain_dialogue_evidence": False,
            "filtered_dialogue_evidence": False,
            **(configuration or {}),
        },
        "rows": [],
        "currency_cost": None,
        "currency_cost_reason": "No verified tariff",
    }
    settings = Settings.from_env()
    report["requested_model"] = settings.model
    transport = TaskTransport(budget=budget)
    backend = None
    if not offline and settings.has_real_backend:
        backend = OpenAICompatibleClient(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            timeout=25,
            max_attempts=1,
            transport=transport,
        )
    try:
        for repeat in range(1, 4):
            for case in cases:
                client = EvidenceClient(backend)
                harness, manager, memory, tools = prepare_case(case["input"], client, variant)
                dependency_ok = True
                for index, turn in enumerate(case["input"]["turns"]):
                    meta = {
                        "id": f"{case['id']}_{variant}_r{repeat}_t{index + 1}",
                        "case_id": case["id"],
                        "repeat": repeat,
                        "turn": index,
                        "mode": case["mode"],
                    }
                    wi = len(transport.records)
                    if offline or backend is None or not dependency_ok or transport.stopped:
                        row = {
                            "npc": case["input"]["npc"],
                            "question": turn["question"],
                            "world_before": manager.snapshot().model_dump(mode="json"),
                            "world_after": manager.snapshot().model_dump(mode="json"),
                            "memory_before": memory.snapshot(),
                            "steps": [],
                            "journal": [],
                            "tools": [],
                            "calls": [],
                            "latency_ms": 0,
                            "error_type": "Offline"
                            if offline
                            else transport.stopped
                            or ("DependencyFailed" if not dependency_ok else "BackendUnavailable"),
                        }
                    else:
                        row = execute_turn(
                            harness,
                            manager,
                            memory,
                            tools,
                            client,
                            turn,
                            local=case["mode"] == "local",
                        )
                    row.update(meta)
                    row["wire_range"] = [wi, len(transport.records)]
                    row["cost"] = wire_cost(transport.records[wi:])
                    row["scores"] = structural_scores(row, case["expect"][index])
                    dependency_ok = (
                        "dialogue" in row and row["scores"].get("state_goal", (1, 1))[0] == 1
                    )
                    report["rows"].append(row)
                    write_json(output / "report.json", report)
                    write_json(output / "wire.json", transport.records)
                    print(f"{row['id']}: {row.get('error_type', 'completed')}", flush=True)
    finally:
        if backend:
            backend.close()
        else:
            transport.close()
        report["cost"] = wire_cost(transport.records)
        report["budget_charged_tokens"] = transport.charged_tokens
        report["metrics"] = {
            mode: aggregate([r["scores"] for r in report["rows"] if r["mode"] == mode])
            for mode in ("full", "local")
        }
        report["status"] = (
            "offline"
            if offline
            else ("completed" if all("dialogue" in r for r in report["rows"]) else "partial")
        )
        write_json(output / "report.json", report)
        write_json(output / "wire.json", transport.records)
    return report
