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
from ai_native_rpg.agent.harness import PromptLibrary
from ai_native_rpg.config import Settings, build_llm_client
from ai_native_rpg.narrative.engine import NarrativeEngine, NarrativeTick
from ai_native_rpg.observability import NarrativeTickStore, TraceStore
from ai_native_rpg.observability.panels import build_beats, build_ledger, build_unlock_board
from ai_native_rpg.scenario import (
    list_scenarios,
    load_intro,
    load_narrative_directives,
    load_personas,
    load_scenario,
    load_seed_memories,
    pack_prompts_dir,
)
from ai_native_rpg.schemas.agent_trace import AgentTrace
from ai_native_rpg.schemas.npc_agent import NPCAgentResponse
from ai_native_rpg.schemas.world_state import WorldState
from ai_native_rpg.world import WorldStateManager

DEFAULT_SCENARIO = "village_disappearance"
TRACE_DIR = Path("traces")
NARRATIVE_TRACE_DIR = TRACE_DIR / "narrative"

#: Scripted replies for USE_MOCK_LLM=1, so the chain is watchable with no key.
#:
#: The Harness and the Narrative Engine share one client here, and the mock plays
#: its script back in order regardless of who asks. So each entry has to satisfy
#: whichever schema comes next: NPC entries carry reasoning/strategy/dialogue,
#: narrative entries carry summary/dialogue_hook. Extra keys are ignored by
#: validation, so entries that serve both are merged rather than interleaved —
#: guessing the call order would break the moment a turn takes the 2-call path.
_MOCK_SCRIPT = [
    {
        "reasoning": "他在打探那天晚上的事，我不能直说，但也不想撒谎。",
        "strategy": "deflect",
        "dialogue": "……那晚我睡得早。你问这些做什么？",
        "summary": "玛尔塔提到那晚雨停得早",
        "dialogue_hook": "……那天的雨，停得比往常早些。",
        "planted_fact_id": "rain_stopped_early",
        "planted_value": "失踪那晚的雨停得比往常早，地上还没干透",
        "payoff_condition": {
            "mode": "any",
            "clauses": [{"path": "relationships.npc_a.player_1.trust", "op": "gte", "value": 45}],
        },
    },
    {
        "reasoning": "他看起来是真心想帮忙，可以稍微松一点。",
        "strategy": "warm_up",
        "dialogue": "你要是真想帮忙……算了，我不知道该不该说。",
        "summary": "玛尔塔看了一眼儿子的房间",
        "dialogue_hook": "（她朝里屋看了一眼，没说话。）",
        # Also carried here: whichever entry the Engine happens to draw must be able
        # to satisfy a foreshadow, or the plant is rejected as incomplete for a
        # reason that says nothing about the system under demonstration.
        "planted_fact_id": "curtain_moved",
        "planted_value": "那晚玛尔塔家的窗帘动过一下",
        "payoff_condition": {
            "mode": "any",
            "clauses": [{"path": "relationships.npc_a.player_1.trust", "op": "gte", "value": 45}],
        },
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
    # Name the provider, not just the model: with a relay the model string alone
    # does not say which endpoint is being billed, and "why is it slow" is usually
    # answered by knowing which backend answered.
    backend = (
        f"{settings.provider} {settings.model}"
        if settings.has_real_backend
        else "MockLLMClient (离线脚本)"
    )
    print("=" * 72)
    print(f"剧本：{scenario}    NPC：{npc_id}    玩家：{player_id}")
    print(f"模型：{backend}")
    print(f"检索：{embedder_label}")
    print(f"提示词：{prompt_source}")
    print(f"工具：{', '.join(tool_names)}")
    print("=" * 72)
    print("直接输入你想说的话。/quit 退出，/mem 记忆，/trust 关系值，")
    print("/beats 叙事进度，/ledger 伏笔账本，/unlock 解锁进度。\n")


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


def print_narrative_tick(tick: NarrativeTick, tick_path: Path) -> None:
    """One line per narrative decision, including the decision to do nothing.

    A quiet turn is printed rather than skipped: "pacing held a candidate back" is
    the Engine's least visible and most informative outcome (docs/07 §2.3).
    """
    if tick.selected is not None:
        mark = f"{tick.selected.operator.value} · {tick.selected.event_type}"
    else:
        mark = "relieve（本轮不触发）"
    starved = "  [已停摆，放松冷却]" if tick.starved else ""
    print(f"  算子      {mark}{starved}")

    if tick.selected is not None:
        print(f"            触发：{tick.selected.trigger_reason}")
    for blocked in tick.rejected:
        print(f"            被挡：{blocked['operator']} · {blocked['reason']}")

    for result in tick.proposals:
        if not result.approved:
            print(f"            提议被拒：{result.rule_name} — {result.reason}")

    if tick.event is not None:
        hook = tick.event.generated_content.get("dialogue_hook", "")
        print(f"            生成：{hook}")
    if tick.model_used:
        print(f"            叙事调用 {tick.model_used}  ({tick.latency_ms:.0f}ms)")
    print(f"  叙事Tick  {tick_path}\n")


def print_beats(world: WorldState) -> None:
    """Chapter, tension and the operator timeline (docs/07 §2.3 block 4)."""
    view = build_beats(world)
    print(f"  第 {view.chapter} 章 · 回合 {view.turn} · 张力 {view.tension:.2f}")
    if view.recent_operators:
        print(f"  最近算子  {' → '.join(view.recent_operators)}")
    else:
        print("  最近算子  （还没有任何回合）")
    if view.spent_one_shots:
        print(f"  已用一次性 {', '.join(view.spent_one_shots)}")
    print()


def print_ledger(world: WorldState) -> None:
    """The foreshadowing ledger (docs/07 §2.3 block 1).

    Turns "the model planted something and forgot it" from a thing you find by
    re-reading transcripts into a thing you see at a glance.
    """
    rows = build_ledger(world)
    if not rows:
        print("  伏笔账本  （没有未回收的伏笔）\n")
        return

    print(f"  伏笔账本  {len(rows)} 条未回收")
    for row in rows:
        status = "⚠️ 超期" if row.overdue else "正常"
        condition = " / ".join(f"{c.path} {c.op} {c.expected}" for c in row.payoff_clauses)
        print(f"   - {row.label}")
        print(
            f"     埋于第 {row.planted_at_turn} 回合，已欠 {row.turns_owed} 轮"
            f"（阈值 {row.overdue_after_turns}）  {status}"
        )
        print(f"     回收条件：{condition}")
    print()


def print_unlock_board(world: WorldState) -> None:
    """Per-clause progress toward each hidden fact (docs/07 §2.3 block 2).

    This is the evaluator's intermediate result made visible. Tuning a threshold is
    otherwise pure guesswork — is 40 too high, will the player stall forever — and
    this is the evidence for that call.

    Reads WorldState directly rather than PlayerView, deliberately: it is a
    developer tool and must show what the player cannot see (docs/07 §2.4). The
    player-facing path never goes through here.
    """
    rows = build_unlock_board(world)
    if not rows:
        print("  解锁进度  （没有待解锁的事实）\n")
        return

    print("  解锁进度")
    for row in rows:
        parts = []
        for clause in row.clauses:
            if not clause.resolvable:
                parts.append(f"{clause.path}=?(路径无法解析)")
                continue
            met = "✓" if clause.met else "·"
            shown = f"{clause.actual:.0f}" if isinstance(clause.actual, float) else clause.actual
            parts.append(f"{met} {clause.label} {shown}/{clause.expected}")
        mode = "任一" if row.mode == "any" else "全部"
        print(f"   - {row.fact_id:<32}（{mode}）{'  '.join(parts)}")
    print()


def _display_name(npc_state, fallback: str) -> str:
    """A player-facing name for an NPC, without leaking persona internals.

    Backgrounds open with the character's name ("玛尔塔，村里的接生婆……"), so the
    span before the first separator is a safe public label — it stops before the
    parts of the background the player is not meant to know yet.
    """
    if npc_state is None:
        return fallback
    background = npc_state.persona.background.strip()
    for sep in ("，", "、", ",", "。"):
        if sep in background:
            head = background.split(sep, 1)[0].strip()
            if head:
                return head
    return fallback


def render_intro(world, view, personas, npc_id: str, intro: dict[str, str]) -> None:
    """Deterministic opening: orient the player from public state alone.

    Assembled straight from the scenario pack (public facts, the player's current
    location, who they are talking to) — no LLM. It renders only what PlayerView
    exposes, so it can never spoil a hidden clue, and it works for any pack.

    Genre-specific wording (title, headers, the goal line) comes from the pack's
    optional ``intro`` block; anything it omits falls back to a neutral default,
    so a non-detective pack is not stuck with detective phrasing.
    """
    title = intro.get("title", "当前情况")
    premise = intro.get("premise", "").strip()
    facts_header = intro.get("facts_header", "目前已知")
    goal = intro.get("goal", "你想和这个人谈谈，看看能问出些什么。")

    line = "─" * 72
    print(line)
    print(f"  {title} · 第 {view.time_day} 天")
    print(line)

    # premise (if any) frames the scene first, so the facts below read as
    # supporting detail rather than a bare list of disconnected values.
    if premise:
        print(premise)
        print()

    if view.visible_facts:
        print(f"【{facts_header}】")
        for value in view.visible_facts.values():
            print(f"  · {value}")
        print()

    location = world.locations.get(view.current_location)
    if location is not None:
        print(f"【你所在的地方】{location.name}")
        if location.description:
            print(f"  {location.description}")
        print()

    npc_world = world.npcs.get(npc_id)
    name = _display_name(personas.get(npc_id), npc_id)
    where = ""
    if npc_world is not None and npc_world.location in world.locations:
        where = f"（在{world.locations[npc_world.location].name}）"
    print(f"【眼前的人】{name}{where}")
    print(f"  {goal}")
    print(line)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with one NPC end to end.")
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO,
        help="scenario pack under scenarios/ (or a path to one)",
    )
    parser.add_argument(
        "--npc", default=None, help="npc id, e.g. npc_a; defaults to the pack's first"
    )
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
        intro = load_intro(args.scenario)
        directives = load_narrative_directives(args.scenario)
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
    engine = NarrativeEngine(manager=manager, llm=llm, prompts=prompts, directives=directives)
    traces = TraceStore(TRACE_DIR)
    narrative_traces = NarrativeTickStore(NARRATIVE_TRACE_DIR)

    render_intro(world, manager.player_view(player_id), personas, npc_id, intro)
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
            # Name the owner: memory is per-NPC and private, not a global store.
            # Each NPC has its own; try --npc npc_b to see a different one.
            owner = _display_name(npc_state, npc_id)
            print(
                f"  【{owner}】的私有记忆  "
                f"episodic={memory.episodic_count} semantic={memory.semantic_count}"
            )
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
        if said == "/beats":
            print_beats(manager.snapshot())
            continue
        if said == "/ledger":
            print_ledger(manager.snapshot())
            continue
        if said == "/unlock":
            print_unlock_board(manager.snapshot())
            continue

        trust_before = manager.get_trust(npc_id, player_id)
        # Content generated after the previous turn is woven into this one, so the
        # player never waits on the narrative call (docs/02 §4).
        pending = engine.take_pending_event()
        try:
            response, trace = harness.respond(said, player_id=player_id, narrative_event=pending)
        except Exception as exc:  # keep the REPL alive across API errors
            print(f"\n[!] 这一回合失败了：{type(exc).__name__}: {exc}\n")
            continue

        trace_path = traces.save(trace)
        print_turn(response, trace, trust_before, manager.get_trust(npc_id, player_id), trace_path)

        # Now that the world has moved, decide this turn's beat. Its content lands
        # on the next turn.
        try:
            tick = engine.tick(player_id=player_id)
        except Exception as exc:
            print(f"[!] 叙事引擎这一轮失败了：{type(exc).__name__}: {exc}\n")
            continue
        print_narrative_tick(tick, narrative_traces.save(tick))


if __name__ == "__main__":
    raise SystemExit(main())
