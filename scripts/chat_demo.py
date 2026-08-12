"""Interactive demo: say something to an NPC and watch the whole chain run.

    uv run python scripts/chat_demo.py            # real model if .env has a key
    USE_MOCK_LLM=1 uv run python scripts/chat_demo.py   # offline, scripted replies

This is the manual entry point the slices otherwise lack: everything else is
exercised through tests, which prove the chain works but do not let you *watch* it.
Each turn prints a debug summary — retrieved memories, tool calls, the plan, trust
before/after, one or two LLM calls, latency, tokens — because for this project the
visible reasoning chain is the deliverable, not just the line of dialogue
(docs/07 §5).

Real embeddings are used when a backend is available and HashingEmbedder otherwise,
so the script runs either way; the header says which one is live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Force UTF-8 on stdio. A Windows console defaults to a legacy code page (GBK
# here), which cannot encode this scenario's Chinese text and turns a normal turn
# into a UnicodeEncodeError. Done before any import that might print.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Allow running straight from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.agent import Harness, HashingEmbedder, MemoryStore, build_npc_tools
from ai_native_rpg.agent.embedding import Embedder
from ai_native_rpg.config import Settings, build_llm_client
from ai_native_rpg.observability import TraceStore
from ai_native_rpg.agent.harness import PromptLibrary
from ai_native_rpg.scenario import (
    list_scenarios,
    load_personas,
    load_scenario,
    load_seed_memories,
    pack_prompts_dir,
)
from ai_native_rpg.schemas.agent_trace import AgentTrace
from ai_native_rpg.schemas.npc_agent import NPCAgentResponse
from ai_native_rpg.world import WorldStateManager

DEFAULT_SCENARIO = "village_disappearance"
TRACE_DIR = Path("traces")

#: Scripted replies for USE_MOCK_LLM=1, so the chain is watchable with no key.
_MOCK_SCRIPT = [
    {
        "reasoning": "他在打探那天晚上的事，我不能直说，但也不想撒谎。",
        "strategy": "deflect",
        "dialogue": "……那晚我睡得早。你问这些做什么？",
    },
    {
        "reasoning": "他看起来是真心想帮忙，可以稍微松一点。",
        "strategy": "warm_up",
        "dialogue": "你要是真想帮忙……算了，我不知道该不该说。",
    },
]


def build_embedder() -> tuple[Embedder, str]:
    """Prefer real semantic retrieval, fall back to hashing.

    The fallback is not a silent one: which embedder is live changes retrieval
    quality substantially, so the header reports it.
    """
    try:
        from ai_native_rpg.agent.embedding_qwen import Qwen3Embedder

        if Qwen3Embedder.is_available():
            embedder = Qwen3Embedder()
            return embedder, f"Qwen3-Embedding ({embedder.dim}d, 语义检索)"
    except Exception as exc:  # a demo must not die because model setup failed
        print(f"[!] 真实 embedding 不可用，回退到 HashingEmbedder：{exc}\n")
    return HashingEmbedder(), "HashingEmbedder (字面匹配，非语义)"


def print_header(
    settings: Settings,
    scenario: str,
    npc_id: str,
    player_id: str,
    embedder_label: str,
    prompt_source: str,
    tool_names: list[str],
):
    backend = (
        f"DeepSeek {settings.model}" if settings.has_real_backend else "MockLLMClient (离线脚本)"
    )
    print("=" * 72)
    print(f"剧本：{scenario}    NPC：{npc_id}    玩家：{player_id}")
    print(f"模型：{backend}")
    print(f"检索：{embedder_label}")
    print(f"提示词：{prompt_source}")
    print(f"工具：{', '.join(tool_names)}")
    print("=" * 72)
    print("直接输入你想说的话。输入 /quit 退出，/mem 查看记忆，/trust 查看关系值。\n")


def print_turn(
    response: NPCAgentResponse,
    trace: AgentTrace,
    trust_before: float,
    trust_after: float,
    trace_path: Path,
) -> None:
    """Print the NPC's line, then the reasoning chain behind it."""
    print(f"\n【{response.npc_id}】{response.dialogue}\n")

    steps = {step.step_name: step for step in trace.steps}
    llm_calls = sum(1 for s in trace.steps if s.model_used is not None)

    retrieval = steps.get("memory_retrieval")
    if retrieval:
        out = retrieval.output_summary
        print(
            f"  检索      episodic={out.get('episodic_hits', 0)} "
            f"semantic={out.get('semantic_hits', 0)}  ({retrieval.latency_ms:.0f}ms)"
        )
        for m in out.get("episodic", []):
            print(f"            - [{m['importance']:.2f}] {m['id']}  {m['text']}")
        for m in out.get("semantic", []):
            print(f"            ~ [{m['confidence']:.2f}] {m['id']}  {m['text']}")

    for step in [s for s in trace.steps if s.step_name == "tool_call"]:
        ok = step.output_summary.get("ok")
        mark = "OK " if ok else "ERR"
        detail = "" if ok else f"  <- {step.output_summary.get('error', '')}"
        print(
            f"  工具 {mark}  {step.input_summary.get('tool')}"
            f"({step.input_summary.get('arguments')}){detail}  ({step.latency_ms:.0f}ms)"
        )

    print(f"  计划      策略={response.plan.strategy}  推理={response.plan.reasoning}")

    validation = steps.get("action_validation")
    if validation:
        out = validation.output_summary
        verdict = "通过" if out.get("approved") else "被拒"
        print(f"  行动      {validation.input_summary.get('action_type')} -> {verdict}")
        if out.get("reason"):
            print(f"            理由：{out['reason']}")

    if trust_before != trust_after:
        print(f"  信任      {trust_before:.1f} -> {trust_after:.1f}")

    tokens = sum(
        (s.token_usage or {}).get("total_tokens", 0)
        or (s.token_usage or {}).get("prompt_tokens", 0)
        + (s.token_usage or {}).get("completion_tokens", 0)
        for s in trace.steps
    )
    print(
        f"  开销      LLM 调用={llm_calls}  tokens={tokens}  总延迟={trace.total_latency_ms:.0f}ms"
    )
    print(f"  Trace     {trace_path}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with one NPC end to end.")
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO,
        help="scenario pack under scenarios/ (or a path to one)",
    )
    parser.add_argument("--npc", default=None, help="npc id, e.g. npc_a; defaults to the pack's first")
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the available scenario packs and exit",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="force the offline mock client (same as USE_MOCK_LLM=1, but a flag "
        "survives shells that do not propagate env vars)",
    )
    args = parser.parse_args()

    if args.list:
        available = list_scenarios()
        print("可用剧本：" + ("、".join(available) if available else "（scenarios/ 下没有剧本包）"))
        return 0

    settings = Settings.from_env()
    if args.mock:
        settings = settings.model_copy(update={"use_mock": True})

    try:
        world = load_scenario(args.scenario)
        personas = load_personas(args.scenario)
        seeds = load_seed_memories(args.scenario)
    except Exception as exc:
        print(f"无法加载剧本 {args.scenario!r}：{exc}")
        print("用 --list 查看可用剧本。")
        return 2

    # The pack, not the code, decides who the player is. Fall back to a sane
    # default only when the pack names no player at all.
    player_id = next(iter(world.player_locations), "player_1")

    npc_id = args.npc or next(iter(personas), None)
    if npc_id not in personas:
        print(f"未知 NPC {npc_id!r}，可选：{', '.join(personas)}")
        return 2

    npc_state = personas[npc_id]
    manager = WorldStateManager(world)

    embedder, embedder_label = build_embedder()
    memory = MemoryStore(npc_id, embedder=embedder)
    seeds[npc_id].load_into(memory)

    # A pack may ship its own prompts/ to restyle NPC voice; otherwise the shared
    # templates are used. The header reports which is live.
    overlay = pack_prompts_dir(args.scenario)
    prompts = PromptLibrary(overlay=overlay if overlay.is_dir() else None)
    prompt_source = f"{args.scenario} 覆盖 + 全局回退" if overlay.is_dir() else "全局默认"

    tools = build_npc_tools(npc_id=npc_id, manager=manager, memory=memory, player_id=player_id)
    # The mock needs a script; the real client ignores these kwargs.
    llm = build_llm_client(settings, responses=list(_MOCK_SCRIPT) * 20)
    harness = Harness(
        npc_state=npc_state, manager=manager, llm=llm, memory=memory, prompts=prompts, tools=tools
    )
    traces = TraceStore(TRACE_DIR)

    print_header(
        settings, args.scenario, npc_id, player_id, embedder_label, prompt_source, tools.names
    )

    while True:
        try:
            said = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not said:
            continue
        if said in {"/quit", "/exit", "/q"}:
            return 0
        if said == "/mem":
            result = memory.retrieve("", top_k=20)
            print(f"  episodic={memory.episodic_count} semantic={memory.semantic_count}")
            for m in result.episodic:
                print(f"   - [{m.importance:.2f}] {m.event_description}")
            for m in result.semantic:
                print(f"   ~ [{m.confidence:.2f}] {m.fact}")
            print()
            continue
        if said == "/trust":
            rel = manager.get_relationship(npc_id, player_id)
            print(f"  trust={rel.trust:.1f} fear={rel.fear:.1f} respect={rel.respect:.1f}\n")
            continue

        trust_before = manager.get_trust(npc_id, player_id)
        try:
            response, trace = harness.respond(said, player_id=player_id)
        except Exception as exc:  # keep the REPL alive across API errors
            print(f"\n[!] 这一回合失败了：{type(exc).__name__}: {exc}\n")
            continue

        trace_path = traces.save(trace)
        print_turn(response, trace, trust_before, manager.get_trust(npc_id, player_id), trace_path)


if __name__ == "__main__":
    raise SystemExit(main())
