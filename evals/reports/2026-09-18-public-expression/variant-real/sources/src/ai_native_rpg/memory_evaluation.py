"""Bounded memory experiments using the production store and NPC harness.

Fixtures and labels never enter the model together. Retrieval and answer screening
are separate; a literal answer hit is NOT a semantic or disclosure judgement.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .agent.embedding import HashingEmbedder
from .agent.harness import Harness
from .agent.memory_store import IMPORTANCE_WEIGHT, MemoryStore
from .agent.tools import build_npc_tools
from .config import Settings
from .evaluation import PLAYER, ROOT, RecordingClient, RecordingTransport
from .llm.openai_compatible import OpenAICompatibleClient
from .scenario import load_personas, load_scenario, load_seed_memories
from .schemas.memory import EpisodicMemory
from .schemas.world_state import WorldState
from .world.manager import WorldStateManager

DATASET = ROOT / "evals/memory_cases.yaml"
MODEL_DIR = ROOT / "data/runtime/models/Qwen3-Embedding-0.6B"


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest():
    files = [
        DATASET,
        Path(__file__),
        ROOT / "src/ai_native_rpg/agent/memory_store.py",
        ROOT / "src/ai_native_rpg/agent/embedding.py",
        ROOT / "src/ai_native_rpg/agent/embedding_qwen.py",
        ROOT / "src/ai_native_rpg/agent/harness.py",
        ROOT / "src/ai_native_rpg/evaluation.py",
        ROOT / "src/ai_native_rpg/llm/openai_compatible.py",
        ROOT / "scenarios/village_disappearance/world.yaml",
        *sorted((ROOT / "prompts").glob("*.txt")),
    ]
    versions = {}
    for package in ("sentence-transformers", "transformers", "torch", "numpy", "httpx"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": {p.relative_to(ROOT).as_posix(): fingerprint(p) for p in files},
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": versions,
    }


def load_dataset(path=DATASET):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    groups = data["groups"]
    group_ids = [g["id"] for g in groups]
    if len(set(group_ids)) != len(group_ids):
        raise ValueError("duplicate event group")
    corpus = []
    cases = []
    for group in groups:
        if group["split"] not in {"dev", "test"}:
            raise ValueError("each event needs exactly one split")
        for memory in group["memories"]:
            corpus.append(
                {
                    "memory_id": memory["id"],
                    "npc_id": group["npc"],
                    "event_description": memory["text"],
                    "importance": memory.get("importance", 0.4),
                    "occurred_at_day": memory["day"],
                }
            )
        for index, query in enumerate(group["queries"]):
            cases.append(
                {
                    **{k: v for k, v in group.items() if k not in {"memories", "queries"}},
                    "case_id": f"{group['id']}_{index + 1}",
                    "query": query,
                }
            )
    # Deterministic background events, identical for both encoders. They are
    # synthetic village activity, not additional annotated queries or live content.
    places = ["广场", "磨坊", "井边", "河岸", "工具棚", "酒馆", "菜园", "仓库", "石桥", "面包铺"]
    activities = [
        "邻居整理了散落的木柴",
        "邮差清点了空袋子",
        "木匠擦净了窗台",
        "守卫搬开了旧木箱",
        "药师晾晒了空竹筐",
        "村民扫去了落叶",
        "磨坊主换了灯芯",
        "商贩收起了遮雨布",
        "孩子们捡起了石子",
        "农夫检查了扁担",
    ]
    for npc in sorted({g["npc"] for g in groups}):
        missing = data["pool_size_per_npc"] - sum(m["npc_id"] == npc for m in corpus)
        if not 0 <= missing <= 100:
            raise ValueError("invalid fixture pool size")
        for index in range(missing):
            corpus.append(
                {
                    "memory_id": f"{npc}_background_{index:03d}",
                    "npc_id": npc,
                    "event_description": f"第{index % 6 + 1}天在{places[index % 10]}，"
                    f"{activities[index // 10]}。",
                    "importance": 0.4,
                    "occurred_at_day": index % 6 + 1,
                }
            )
    ids = [m["memory_id"] for m in corpus]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate memory id")
    owners = {m["memory_id"]: m["npc_id"] for m in corpus}
    for case in cases:
        if any(owners.get(mid) != case["npc"] for mid in case["gold"]):
            raise ValueError("gold must exist in this NPC's own store")
        if len(set(case["gold"])) != len(case["gold"]):
            raise ValueError("duplicate gold")
        if any(
            mid not in owners or owners[mid] == case["npc"] for mid in case.get("forbidden_ids", [])
        ):
            raise ValueError("cross-NPC labels must exist in a different store")
    return data, corpus, cases


def make_embedder(name, model_path=MODEL_DIR):
    if name == "hashing":
        return HashingEmbedder(64), {"name": "HashingEmbedder", "dim": 64}
    if name != "qwen":
        raise ValueError(f"unknown backend: {name}")
    # Fail closed: neither a download nor a Hashing fallback can masquerade as Qwen.
    source = Path(model_path).resolve()
    if not source.is_dir() or not list(source.glob("*.safetensors")):
        raise FileNotFoundError("local Qwen safetensors directory is required")
    import torch
    from sentence_transformers import SentenceTransformer

    from .agent.embedding_qwen import Qwen3Embedder

    torch.set_num_threads(4)
    encoder = SentenceTransformer(str(source), device="cpu", local_files_only=True)
    embedder = Qwen3Embedder(encoder=encoder, dim=256, cache_size=0)
    info = {
        "name": "Qwen3Embedder",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "dim": 256,
        "device": "cpu",
        "torch_threads": 4,
        "cache_size": 0,
        "model_files": {
            p.relative_to(source).as_posix(): fingerprint(p)
            for p in sorted(source.rglob("*"))
            if p.is_file()
            and ".cache" not in p.parts
            and p.suffix in {".json", ".safetensors", ".txt", ".jinja"}
        },
    }
    revision = source / "eval-revision.txt"
    info["revision"] = revision.read_text().strip() if revision.exists() else None
    return embedder, info


def make_store(npc, corpus, embedder):
    store = MemoryStore(npc, embedder=embedder)
    for item in corpus:
        if item["npc_id"] == npc:
            store.add_episodic(EpisodicMemory(**item))
    return store


def retrieval_scores(ranked, gold, k=3):
    """Full-pool MRR and macro/micro recall ingredients; empty gold is unanswerable."""
    if len(set(ranked)) != len(ranked):
        raise ValueError("ranked IDs must be unique")
    if not gold:
        return {
            "hits": None,
            "gold_count": 0,
            "recall_at_3": None,
            "reciprocal_rank": None,
            "all_relevant_at_3": None,
            "no_answer_nonempty": int(bool(ranked[:k])),
        }
    targets = set(gold)
    hits = len(set(ranked[:k]) & targets)
    rank = next((i for i, mid in enumerate(ranked, 1) if mid in targets), None)
    return {
        "hits": hits,
        "gold_count": len(targets),
        "recall_at_3": hits / len(targets),
        "reciprocal_rank": 1 / rank if rank else 0,
        "all_relevant_at_3": int(hits == len(targets)),
        "no_answer_nonempty": None,
    }


def average(values):
    return statistics.mean(values) if values else None


def latency_summary(values):
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean_ms": average(values),
        "p50_ms": statistics.median(values) if values else None,
        "p95_ms": ordered[max(0, (95 * len(values) + 99) // 100 - 1)] if values else None,
    }


def summarize_retrieval(rows):
    labelled = [r for r in rows if r["scores"]["gold_count"]]
    unknown = [r for r in rows if not r["scores"]["gold_count"]]
    gold_count = sum(r["scores"]["gold_count"] for r in labelled)
    return {
        "queries": len(rows),
        "labelled_queries": len(labelled),
        "gold_count": gold_count,
        "hits": sum(r["scores"]["hits"] for r in labelled),
        "macro_recall_at_3": average([r["scores"]["recall_at_3"] for r in labelled]),
        "micro_recall_at_3": (
            sum(r["scores"]["hits"] for r in labelled) / gold_count if gold_count else None
        ),
        "mrr_full_pool": average([r["scores"]["reciprocal_rank"] for r in labelled]),
        "all_relevant_at_3": average([r["scores"]["all_relevant_at_3"] for r in labelled]),
        "no_answer_queries": len(unknown),
        "no_answer_nonempty_rate": average([r["scores"]["no_answer_nonempty"] for r in unknown]),
        "foreign_memory_hits": sum(len(r["foreign_ids"]) for r in rows),
        "query_latency": latency_summary([r["latency_ms"] for r in rows]),
    }


def run_retrieval(*, output, backend, split, model_path=MODEL_DIR):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    data, corpus, cases = load_dataset()
    selected = [c for c in cases if c["split"] == split]
    report = {
        "experiment": "retrieval",
        "manifest": manifest(),
        "dataset_version": data["version"],
        "backend_requested": backend,
        "split": split,
        "top_k": 3,
        "importance_weight": IMPORTANCE_WEIGHT,
        "rows": [],
        "status": "unavailable",
        "limitations": [
            "Synthetic authored labels; no independent human annotation.",
            "MRR uses the full 100-memory pool, recall uses top 3 episodic entries.",
            "No-answer nonempty measures returned noise, not an acceptance decision.",
            "Foreign-memory isolation is a storage contract, not dialogue safety.",
            "One deterministic retrieval pass; no significance claim.",
        ],
    }
    write_json(output / "corpus.json", corpus)
    try:
        start = time.perf_counter()
        embedder, report["backend"] = make_embedder(backend, model_path)
        report["load_ms"] = (time.perf_counter() - start) * 1000
        embedder.encode_query("回忆最近的村庄日常")  # excluded warm-up
        start = time.perf_counter()
        stores = {npc: make_store(npc, corpus, embedder) for npc in {c["npc"] for c in selected}}
        report["index_ms"] = (time.perf_counter() - start) * 1000
        owners = {m["memory_id"]: m["npc_id"] for m in corpus}
        for case in selected:
            start = time.perf_counter()
            ranked = [
                m.memory_id
                for m in stores[case["npc"]]
                .retrieve(case["query"], top_k=data["pool_size_per_npc"])
                .episodic
            ]
            elapsed = (time.perf_counter() - start) * 1000
            report["rows"].append(
                {
                    "case": case,
                    "ranked_ids": ranked,
                    "scores": retrieval_scores(ranked, case["gold"]),
                    "latency_ms": elapsed,
                    "foreign_ids": [mid for mid in ranked[:3] if owners[mid] != case["npc"]],
                }
            )
        report["summary"] = summarize_retrieval(report["rows"])
        report["by_category"] = {
            category: summarize_retrieval(
                [r for r in report["rows"] if r["case"]["category"] == category]
            )
            for category in sorted({c["category"] for c in selected})
        }
        report["status"] = "completed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    write_json(output / "report.json", report)
    return report


class EvidenceClient(RecordingClient):
    """Capture synthetic evaluation inputs to audit the final generation context."""

    def complete(self, messages, **kwargs):
        index = len(self.calls)
        try:
            return super().complete(messages, **kwargs)
        finally:
            self.calls[index]["messages"] = [m.model_dump(mode="json") for m in messages]
            self.calls[index]["tools"] = kwargs.get("tools")


def harness_for(npc, manager, store, client, *, evidence=False):
    return Harness(
        npc_state=load_personas("village_disappearance")[npc],
        manager=manager,
        memory=store,
        llm=client,
        top_k=3,
        max_tool_iterations=1,
        retain_dialogue_evidence=evidence,
        tools=build_npc_tools(npc_id=npc, manager=manager, memory=store, player_id=PLAYER),
    )


def wire_cost(records):
    known = [r["usage"] for r in records if r.get("usage") is not None]
    return {
        "http_requests": len(records),
        "missing_usage": len(records) - len(known),
        "tokens": {
            key: sum(u.get(key, 0) for u in known)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        if known
        else None,
        "returned_models": sorted({r["model"] for r in records if r.get("model")}),
        "http_latency": latency_summary([r["latency_ms"] for r in records]),
    }


def run_sequences(*, output, model_path=MODEL_DIR, repeats=3, max_requests=180, max_seconds=1200):
    """Paired encoders share real written prefixes; lag probes branch independently.

    Filler exchanges use remember_exchange (authored path), not model-generated
    player behaviour. No earlier probe answer is allowed to seed a later one.
    """
    if repeats < 1 or max_requests < 1 or max_seconds <= 0:
        raise ValueError("budgets and repeats must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    data, _, _ = load_dataset()
    report = {
        "experiment": "sequences",
        "manifest": manifest(),
        "status": "unavailable",
        "repeats": repeats,
        "temperature": 0,
        "lags": [0, 10, 30],
        "top_k_per_bucket": 3,
        "retain_dialogue_evidence": False,
        "max_requests": max_requests,
        "max_seconds": max_seconds,
        "expected_probes": len(data["sequences"]) * repeats * 2 * 3,
        "prefixes": [],
        "probes": [],
        "limitations": [
            "Two supplemental synthetic events, not a held-out population estimate.",
            "Intervening exchanges are authored; these are not 30 real LLM turns.",
            "Backends share a real prefix written using Hashing; only recall differs.",
            "Each lag branches from that prefix; earlier answers cannot refresh memory.",
            "Literal hits and prompt presence require semantic review; no automatic judge.",
            "Lag 30 resumes a saved world/memory in fresh objects, not a Web/browser session.",
        ],
    }
    settings = Settings.from_env()
    if not settings.has_real_backend:
        report["error_type"] = "RealBackendUnavailable"
        write_json(output / "report.json", report)
        return report
    try:
        hashing, hash_info = make_embedder("hashing")
        qwen, qwen_info = make_embedder("qwen", model_path)
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        write_json(output / "report.json", report)
        return report
    report.update(backends={"hashing": hash_info, "qwen": qwen_info}, model=settings.model)
    transport = RecordingTransport(max_requests=max_requests, max_seconds=max_seconds)
    backend = OpenAICompatibleClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
        timeout=25,
        max_attempts=1,
        transport=transport,
    )
    encoders = {"hashing": hashing, "qwen": qwen}
    try:
        for repeat in range(repeats):
            for sequence in data["sequences"]:
                npc = sequence["npc"]
                prefix_id = f"{sequence['id']}_r{repeat + 1}"
                directory = output / prefix_id
                directory.mkdir()
                manager = WorldStateManager(load_scenario("village_disappearance"))
                memory = MemoryStore(npc, embedder=hashing)
                load_seed_memories("village_disappearance")[npc].load_into(memory)
                client = EvidenceClient(backend)
                harness = harness_for(npc, manager, memory, client)
                before_ids = {m["memory_id"] for m in memory.snapshot()["episodic"]}
                prefix = {"id": prefix_id, "sequence": sequence}
                report["prefixes"].append(prefix)
                wire_start = len(transport.records)
                try:
                    response, trace = harness.respond(sequence["tell"], player_id=PLAYER)
                    prefix.update(
                        response=response.model_dump(mode="json"),
                        trace=trace.model_dump(mode="json"),
                    )
                    gold = [
                        m["memory_id"]
                        for m in memory.snapshot()["episodic"]
                        if m["memory_id"] not in before_ids
                    ]
                    prefix["written_ids"] = gold
                    prefix["write_contains_answer"] = any(
                        sequence["answer"] in m["event_description"]
                        for m in memory.snapshot()["episodic"]
                        if m["memory_id"] in gold
                    )
                    memory.save(directory / "prefix_memory.json")
                    world = manager.snapshot().model_dump(mode="json")
                    write_json(directory / "prefix_world.json", world)
                except Exception as exc:
                    prefix["error_type"] = type(exc).__name__
                    continue
                finally:
                    prefix.update(
                        calls=client.calls, cost=wire_cost(transport.records[wire_start:])
                    )
                    write_json(output / "report.json", report)
                    write_json(output / "wire.json", transport.records)
                order = ["hashing", "qwen"] if repeat % 2 == 0 else ["qwen", "hashing"]
                for lag in report["lags"]:
                    for name in order:
                        case_id = f"{prefix_id}_{name}_lag{lag}"
                        probe = {
                            "id": case_id,
                            "prefix_id": prefix_id,
                            "backend": name,
                            "lag": lag,
                            "repeat": repeat + 1,
                            "gold_ids": gold,
                            "question": sequence["ask"],
                            "expected_answer": sequence["answer"],
                        }
                        report["probes"].append(probe)
                        wire_start = len(transport.records)
                        client = EvidenceClient(backend)
                        start = time.perf_counter()
                        try:
                            store = MemoryStore(npc, embedder=encoders[name])
                            store.load(directory / "prefix_memory.json")
                            branch_manager = WorldStateManager(WorldState.model_validate(world))
                            branch = harness_for(npc, branch_manager, store, client)
                            for index in range(lag):
                                branch.remember_exchange(
                                    f"这是第{index + 1}次闲聊，今天广场有人整理木柴。",
                                    "嗯，村里每天都有些杂事。",
                                )
                            if lag == 30:
                                saved = directory / f"{name}_lag30_memory.json"
                                snapshot = store.snapshot()
                                store.save(saved)
                                store = MemoryStore(npc, embedder=encoders[name])
                                store.load(saved)
                                probe["restore_equal"] = store.snapshot() == snapshot
                                # Re-read the actual serialized world, not an in-memory alias.
                                branch_manager = WorldStateManager(
                                    WorldState.model_validate_json(
                                        (directory / "prefix_world.json").read_text(
                                            encoding="utf-8"
                                        )
                                    )
                                )
                                branch = harness_for(npc, branch_manager, store, client)
                            probe["memory_before"] = store.snapshot()
                            probe["world_before"] = branch_manager.snapshot().model_dump(
                                mode="json"
                            )
                            query_start = time.perf_counter()
                            response, trace = branch.respond(sequence["ask"], player_id=PLAYER)
                            probe["turn_latency_ms"] = (time.perf_counter() - query_start) * 1000
                            retrieved = next(
                                s for s in trace.steps if s.step_name == "memory_retrieval"
                            )
                            ids = [m["id"] for m in retrieved.output_summary["episodic"]]
                            last_prompt = "\n".join(
                                m["content"] for m in client.calls[-1]["messages"]
                            )
                            probe.update(
                                response=response.model_dump(mode="json"),
                                trace=trace.model_dump(mode="json"),
                                world_after=branch_manager.snapshot().model_dump(mode="json"),
                                retrieved_ids=ids,
                                gold_recalled=bool(set(ids) & set(gold)),
                                answer_in_final_prompt=sequence["answer"] in last_prompt,
                                literal_answer_hit=sequence["answer"] in response.dialogue,
                                semantic_review=None,
                            )
                        except Exception as exc:
                            probe["error_type"] = type(exc).__name__
                        finally:
                            probe.update(
                                calls=client.calls,
                                cost=wire_cost(transport.records[wire_start:]),
                                setup_and_turn_ms=(time.perf_counter() - start) * 1000,
                            )
                            write_json(output / "report.json", report)
                            write_json(output / "wire.json", transport.records)
                            result = probe.get("literal_answer_hit", probe.get("error_type"))
                            print(f"{case_id}: {result}", flush=True)
    finally:
        backend.close()
        report["cost"] = wire_cost(transport.records)
        completed = [p for p in report["probes"] if "response" in p]
        report["completed_probes"] = len(completed)
        report["status"] = "completed" if len(completed) == report["expected_probes"] else "partial"
        report["summary"] = {
            name: {
                "completed": sum(p["backend"] == name for p in completed),
                "literal_hits": sum(
                    p["literal_answer_hit"] for p in completed if p["backend"] == name
                ),
                "gold_recalled": sum(p["gold_recalled"] for p in completed if p["backend"] == name),
                "answer_in_final_prompt": sum(
                    p["answer_in_final_prompt"] for p in completed if p["backend"] == name
                ),
                "turn_latency": latency_summary(
                    [p["turn_latency_ms"] for p in completed if p["backend"] == name]
                ),
            }
            for name in encoders
        }
        write_json(output / "report.json", report)
        write_json(output / "wire.json", transport.records)
    return report


def compare_retrieval(paths, output):
    """A compact report; raw evidence remains in the individual run directories."""
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    if any(r["status"] != "completed" for r in reports):
        raise ValueError("cannot compare incomplete retrieval runs")
    signatures = {
        (r["manifest"]["files"]["evals/memory_cases.yaml"], r["top_k"], r["importance_weight"])
        for r in reports
    }
    if len(signatures) != 1:
        raise ValueError("dataset or ranking configuration differs")
    lines = [
        "# Memory retrieval comparison",
        "",
        "Synthetic fixtures; one deterministic pass. Answer quality is evaluated separately.",
        "",
        "| Split | Backend | Hits / gold | Macro Recall@3 | Full-pool MRR | "
        "All relevant@3 | No-answer nonempty | Query p50 ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        s = r["summary"]
        lines.append(
            f"| {r['split']} | {r['backend_requested']} | {s['hits']}/{s['gold_count']} | "
            f"{s['macro_recall_at_3']:.3f} | {s['mrr_full_pool']:.3f} | "
            f"{s['all_relevant_at_3']:.3f} | {s['no_answer_nonempty_rate']:.3f} | "
            f"{s['query_latency']['p50_ms']:.2f} |"
        )
    lines += [
        "",
        "No-answer nonempty is not false acceptance: this store always returns K "
        "entries and has no abstention threshold. Corpus encoding and model loading "
        "are excluded from query latency and reported separately in JSON.",
    ]
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with Path(output).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def describe_dataset():
    _, corpus, cases = load_dataset()
    return {
        "queries": len(cases),
        "pool": dict(Counter(m["npc_id"] for m in corpus)),
        "splits": dict(Counter(c["split"] for c in cases)),
        "categories": dict(Counter(c["category"] for c in cases)),
    }
