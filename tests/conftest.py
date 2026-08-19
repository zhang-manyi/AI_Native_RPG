"""Shared fixtures. The world here mirrors docs/09_Reference_Scenario.md
(village disappearance case) so tests read like the scenario they encode."""

from __future__ import annotations

import pytest

from ai_native_rpg.scenario import NarrativeDirectives, PacedClue
from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.world_state import (
    Fact,
    Location,
    NPCWorldState,
    QuestState,
    RelationshipState,
    Visibility,
    WorldState,
)

PLAYER = "player_1"
NPC_A = "npc_a"
NPC_B = "npc_b"


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    """Pin USE_MOCK_LLM=1 for every test.

    The suite must make no network call even on a machine with a real key in
    ``.env``; relying on each test to remember that would eventually fail. Tests
    that need to exercise real-client behaviour construct it explicitly and serve
    it through ``httpx.MockTransport``, which is offline by construction.
    """
    monkeypatch.setenv("USE_MOCK_LLM", "1")


@pytest.fixture
def world() -> WorldState:
    return WorldState(
        world_id="test_village",
        time_day=1,
        locations={
            "village_square": Location(
                location_id="village_square",
                name="村庄广场",
                connected_to=["tavern", "forest_edge"],
            ),
            "tavern": Location(location_id="tavern", name="酒馆", connected_to=["village_square"]),
            "forest_edge": Location(
                location_id="forest_edge", name="森林入口", connected_to=["village_square"]
            ),
        },
        npcs={
            NPC_A: NPCWorldState(npc_id=NPC_A, name="玛尔塔", location="tavern"),
            NPC_B: NPCWorldState(npc_id=NPC_B, name="洛伦", location="forest_edge"),
        },
        quests={"investigation": QuestState(quest_id="investigation", stage=0, status="active")},
        facts={
            "victim_name": Fact(
                fact_id="victim_name", value="磨坊主的女儿", visibility=Visibility.REVEALED
            ),
            "clue_1": Fact(
                fact_id="clue_1",
                value="失踪当晚有人在森林入口见过 NPC_B",
                visibility=Visibility.HIDDEN,
                reveal_condition=Condition(
                    mode="any",
                    clauses=[
                        ConditionClause(
                            path=f"relationships.{NPC_A}.{PLAYER}.trust",
                            op=ConditionOp.GTE,
                            value=40,
                        )
                    ],
                ),
            ),
            "killer_identity": Fact(
                fact_id="killer_identity",
                value=NPC_B,
                visibility=Visibility.HIDDEN,
                partial_value="村里有个嫌疑人",
                reveal_condition=Condition(
                    mode="any",
                    clauses=[
                        ConditionClause(
                            path=f"relationships.{NPC_A}.{PLAYER}.trust",
                            op=ConditionOp.GT,
                            value=70,
                        ),
                        ConditionClause(
                            path="quests.investigation.stage", op=ConditionOp.GTE, value=3
                        ),
                    ],
                ),
            ),
            # The reversal channel: knowing the threat re-reads NPC_A's evasions as
            # shielding her son rather than the killer (docs/10 §5 priority 2).
            "npc_a_threatened": Fact(
                fact_id="npc_a_threatened",
                value="洛伦警告过她，说出去她儿子会是下一个",
                visibility=Visibility.HIDDEN,
                reveal_condition=Condition(
                    mode="all",
                    clauses=[
                        ConditionClause(
                            path=f"relationships.{NPC_A}.{PLAYER}.trust",
                            op=ConditionOp.GTE,
                            value=70,
                        ),
                        ConditionClause(
                            path=f"relationships.{NPC_A}.{PLAYER}.fear",
                            op=ConditionOp.LTE,
                            value=20,
                        ),
                    ],
                ),
            ),
        },
        relationships={
            NPC_A: {PLAYER: RelationshipState(trust=20.0, fear=10.0)},
            NPC_B: {PLAYER: RelationshipState(trust=0.0)},
        },
        player_locations={PLAYER: "tavern"},
    )


@pytest.fixture
def directives() -> NarrativeDirectives:
    """The authored narrative content for the ``world`` fixture above.

    A test-local counterpart to the pack's ``narrative:`` block, naming this
    world's fact ids. It exists because the engine no longer holds any story's
    clue list — which is the point of the split, and means tests have to supply
    one just as a pack does.
    """
    return NarrativeDirectives(
        language="中文",
        paced_clues=[
            PacedClue(fact_id="clue_1", constraint="只说你看见了有人，不要说出那个人是谁")
        ],
        reversal_fact="npc_a_threatened",
        universal_constraints=["不要写任何人的内心独白"],
    )
