"""Shared fixtures. The world here mirrors docs/09_Reference_Scenario.md
(village disappearance case) so tests read like the scenario they encode."""

from __future__ import annotations

import pytest

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
            NPC_A: NPCWorldState(npc_id=NPC_A, location="tavern"),
            NPC_B: NPCWorldState(npc_id=NPC_B, location="forest_edge"),
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
        },
        relationships={
            NPC_A: {PLAYER: RelationshipState(trust=20.0, fear=10.0)},
            NPC_B: {PLAYER: RelationshipState(trust=0.0)},
        },
        player_locations={PLAYER: "tavern"},
    )
