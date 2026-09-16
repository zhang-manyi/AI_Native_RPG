"""Walk the event chain by hand, printing world state at each step.

A throwaway inspection tool for slice 5 batch 1: it shows what the tests assert, in
a form you can read. Offline by default (mock LLM), so it costs nothing and always
gives the same numbers.

    python scripts/event_walkthrough.py
    python scripts/event_walkthrough.py --option press_that_night

The point is to see the five links of the chain separately: which event triggered and
why, what the operator handed the generator, which outcome the response landed on, and
what moved in the world as a result.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Force UTF-8 on stdout before anything prints. Every line of output here is Chinese,
# and a Windows console defaults to a legacy code page that renders it as mojibake.
# Doing it in-process rather than asking the operator to set PYTHONIOENCODING keeps the
# invocation identical across PowerShell, cmd and bash — and the env-var form is itself
# shell-specific, so the instruction would have been wrong for two of the three.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import NarrativeEngine
from ai_native_rpg.scenario import (
    load_event_script,
    load_narrative_directives,
    load_scenario,
)
from ai_native_rpg.world.manager import WorldStateManager

PACK = "village_disappearance"
PLAYER = "player_1"
NPC_A = "npc_a"


def _mock_content() -> dict:
    return {
        "summary": "（mock）玛尔塔在门后停了很久",
        "dialogue_hook": "（mock）……你先说你是谁。",
        "participants": ["玛尔塔"],
    }


def _show_state(manager: WorldStateManager, label: str) -> None:
    world = manager.snapshot()
    beats = world.story_beats
    rel = manager.get_relationship(NPC_A, PLAYER)
    active = beats.active_event

    print(f"\n--- {label} ---")
    print(f"  turn={beats.turn}  最近算子={beats.recent_operators}")
    print(f"  玛尔塔→玩家：trust={rel.trust:.0f}  fear={rel.fear:.0f}")
    print(
        "  active_event="
        + (f"{active.event_id} ({active.exchanges}/{active.max_exchanges} 句)" if active else "无")
    )
    print(f"  已完成={beats.completed_events}  已关闭={beats.closed_events}  flags={beats.flags}")
    revealed = [f_id for f_id, f in world.facts.items() if f.visibility.value == "revealed"]
    print(f"  已揭露的 fact（{len(revealed)}）：{revealed}")
    if beats.open_foreshadowings:
        for fact_id, entry in beats.open_foreshadowings.items():
            print(f"  账本：{fact_id} — {entry.note}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--option",
        default="goodwill",
        help="M1 里选哪个选项：goodwill / press_that_night",
    )
    parser.add_argument("--trust", type=float, default=None, help="开局把 trust 设成这个值")
    parser.add_argument("--seed", type=int, default=0, help="随机带的种子，同种子结果可复现")
    args = parser.parse_args()

    world = load_scenario(PACK)
    if args.trust is not None:
        world.relationships[NPC_A][PLAYER].trust = args.trust

    manager = WorldStateManager(world)
    engine = NarrativeEngine(
        manager=manager,
        llm=MockLLMClient([_mock_content() for _ in range(12)]),
        directives=load_narrative_directives(PACK),
        script=load_event_script(PACK),
    )

    script = engine.script
    print(f"剧本载入：{sorted(script.events)}")
    _show_state(manager, "开局")

    # === 链条第 1 环：事件被选中 ===========================================
    print("\n=== 1. Director 选事件（触发是硬 Condition，不走三段式）===")
    tick = engine.tick(player_id=PLAYER)
    print(f"  候选：{[c.event_id for c in tick.candidates]}")
    for r in tick.rejected:
        print(f"  被节奏规则挡下：{r}")
    if tick.selected is None:
        print("  没有事件到条件 → 这一回合安静（docs/13 §2.1：不退化出算子级小 beat）")
        _show_state(manager, "tick 之后")
        return 0
    print(f"  选中：{tick.selected.event_id}")
    print(f"  触发理由：{tick.selected.trigger_reason}")

    # === 第 2 环：算子表达 =================================================
    print("\n=== 2. 算子表达（算子降级为'这件事怎么讲'）===")
    print(f"  算子：{tick.selected.operator.value}")
    print(f"  本场禁止：{tick.selected.constraints}")
    if tick.event is not None:
        print(f"  生成内容（mock）：{tick.event.generated_content.get('dialogue_hook')}")
    _show_state(manager, "事件已开启，但还没有任何结果落地")

    # === 第 3 环：玩家看到什么 =============================================
    definition = engine.active_event()
    if definition is not None:
        print("\n=== 3. 玩家侧 ===")
        for line in definition.scripted_lines:
            print(f"  剧本台词：{line}")
        for option in definition.options:
            check = option.check
            gate = (
                f"（判定：{check.dimension} 门槛 {check.threshold:.0f}，"
                f"随机带 ±{check.effective_band(option.tag):.0f}）"
                if check
                else "（不判定）"
            )
            print(f"  [{option.tag.value}] {option.text} {gate}")

    # === 第 4、5 环：回应 → 结果 → 数值 ====================================
    print(f"\n=== 4-5. 玩家选 {args.option!r} → 映射到结果 → 数值变化 ===")
    record = engine.resolve_player_response(
        player_id=PLAYER, option_id=args.option, rng=random.Random(args.seed)
    )
    if record is None:
        print("  没有进行中的事件")
        return 0
    print(f"  判定落在哪一段：{record.band}")
    if record.threshold is not None:
        print(
            f"  当前值 {record.current_value:.0f} vs 门槛 {record.threshold:.0f}"
            f"（差 {record.margin:+.0f}）"
        )
    print(f"  结果：{record.outcome_id}")
    print(f"  事件结束了吗：{record.finished}  永久关闭：{record.closed}")
    for proposal in record.proposals:
        verdict = "通过" if proposal.approved else f"被拒（{proposal.rule_name}）"
        print(f"  提案 {verdict}：{proposal.applied_changes or proposal.reason}")

    _show_state(manager, "结果落地之后")

    # 后续还有什么可触发的
    from ai_native_rpg.narrative.rules import triggerable_events

    upcoming = [e.event_id for e in triggerable_events(manager.snapshot(), script)]
    print(f"\n下一轮可触发：{upcoming or '（无——需要先把 trust 抬上去）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
