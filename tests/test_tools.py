"""Tests for the NPC's tool set (docs/06 §2 Tools). Deterministic, strict TDD.

The security-relevant property here is what ``check_public_fact`` refuses. A tool
is a hole in the information asymmetry: whatever it returns lands in the model's
context, and anything in context can be talked out of the model. So the tool
exposes only facts already revealed to the player — "what the whole village
knows". An NPC's private knowledge of hidden matters comes from its own Memory
instead, which means an undisclosed fact like ``loren_that_night`` never enters a
prompt at all, rather than entering it and relying on the Validator to catch the
leak afterwards.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.agent.tools import ToolError, ToolRegistry, build_npc_tools
from ai_native_rpg.schemas.memory import EpisodicMemory, SemanticMemory
from ai_native_rpg.schemas.world_state import ActionProposal, Visibility
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"


@pytest.fixture
def manager(world) -> WorldStateManager:
    return WorldStateManager(world)


@pytest.fixture
def memory() -> MemoryStore:
    store = MemoryStore(NPC_A)
    store.add_episodic(
        EpisodicMemory(
            memory_id="m1",
            npc_id=NPC_A,
            event_description="失踪那晚我看见有人从森林方向回到村里",
            importance=0.9,
            occurred_at_day=1,
        )
    )
    store.add_semantic(
        SemanticMemory(memory_id="s1", npc_id=NPC_A, fact="说出去儿子会有危险", confidence=0.8)
    )
    return store


@pytest.fixture
def registry(manager, memory) -> ToolRegistry:
    return build_npc_tools(npc_id=NPC_A, manager=manager, memory=memory, player_id=PLAYER)


class TestSchemaDeclaration:
    def test_exposes_three_tools(self, registry):
        assert set(registry.names) == {
            "query_relationship",
            "query_memory",
            "check_public_fact",
        }

    def test_schemas_are_openai_tool_shaped(self, registry):
        for spec in registry.specs():
            assert spec["type"] == "function"
            function = spec["function"]
            assert isinstance(function["name"], str)
            assert function["description"]
            assert function["parameters"]["type"] == "object"

    def test_tool_count_stays_small(self, registry):
        """docs/06 §7 caps the set at 3-4: a wide tool surface makes selection
        noisier without proving anything more about the call chain."""
        assert len(registry.names) <= 4

    def test_no_action_proposal_tool(self, registry):
        """Actions travel as PlanningOutput.action so validation precedes dialogue
        (docs/02 §4.1). A tool would be a second write path that bypasses that
        ordering."""
        assert "propose_action" not in registry.names


class TestQueryRelationship:
    def test_returns_world_values(self, registry):
        result = registry.call("query_relationship", {"target_id": PLAYER})
        # conftest seeds npc_a -> player_1 at trust 20, fear 10
        assert result["trust"] == pytest.approx(20.0)
        assert result["fear"] == pytest.approx(10.0)

    def test_unknown_target_returns_neutral_not_error(self, registry):
        """An NPC asking about someone it has no history with is a fair question;
        all-zero is the honest answer and matches get_relationship."""
        result = registry.call("query_relationship", {"target_id": "stranger"})
        assert result["trust"] == pytest.approx(0.0)

    def test_reflects_world_changes(self, registry, manager):
        """The tool reads through to the live world, so a mid-conversation trust
        change is visible on the next call rather than snapshotted at build time."""
        before = registry.call("query_relationship", {"target_id": PLAYER})["trust"]
        manager.submit(
            ActionProposal(
                proposal_id="p1",
                actor_id=NPC_A,
                action_type="adjust_relationship",
                target_id=PLAYER,
                payload={"trust": 15},
            )
        )
        after = registry.call("query_relationship", {"target_id": PLAYER})["trust"]
        assert after == pytest.approx(before + 15.0)


class TestQueryMemory:
    def test_retrieves_relevant_memories(self, registry):
        result = registry.call("query_memory", {"query": "那晚 森林", "top_k": 2})
        joined = " ".join(result["episodic"])
        assert "森林" in joined

    def test_includes_semantic_beliefs(self, registry):
        result = registry.call("query_memory", {"query": "儿子 危险"})
        assert any("儿子" in fact for fact in result["semantic"])

    def test_top_k_is_honoured(self, registry, memory):
        for i in range(5):
            memory.add_episodic(
                EpisodicMemory(
                    memory_id=f"extra{i}",
                    npc_id=NPC_A,
                    event_description=f"村里的第{i}件小事，和森林有关",
                    importance=0.3,
                    occurred_at_day=1,
                )
            )
        result = registry.call("query_memory", {"query": "森林", "top_k": 2})
        assert len(result["episodic"]) <= 2

    def test_empty_store_returns_empty_lists(self, manager):
        registry = build_npc_tools(
            npc_id=NPC_A, manager=manager, memory=MemoryStore(NPC_A), player_id=PLAYER
        )
        result = registry.call("query_memory", {"query": "任何事"})
        assert result["episodic"] == []
        assert result["semantic"] == []

    def test_missing_query_argument_is_a_tool_error(self, registry):
        with pytest.raises(ToolError, match="query"):
            registry.call("query_memory", {})


class TestCheckPublicFact:
    def test_returns_revealed_fact(self, registry):
        result = registry.call("check_public_fact", {"fact_id": "victim_name"})
        assert result["known"] is True
        assert "磨坊主" in str(result["value"])

    def test_refuses_hidden_fact_without_leaking_it(self, registry, world):
        """The refusal must not carry the value, or the "refusal" is the leak."""
        secret = str(world.facts["loren_that_night"].value)
        result = registry.call("check_public_fact", {"fact_id": "loren_that_night"})

        assert result["known"] is False
        assert secret not in str(result)

    def test_refuses_partial_fact(self, world):
        """Partial visibility is a player-facing teaser, not public knowledge."""
        world.facts["clue_1"].visibility = Visibility.PARTIAL
        world.facts["clue_1"].partial_value = "有人那晚没睡"
        manager = WorldStateManager(world)
        registry = build_npc_tools(
            npc_id=NPC_A, manager=manager, memory=MemoryStore(NPC_A), player_id=PLAYER
        )
        result = registry.call("check_public_fact", {"fact_id": "clue_1"})
        assert result["known"] is False
        assert str(world.facts["clue_1"].value) not in str(result)

    def test_condition_revealed_fact_counts_as_public(self, world):
        """A fact whose reveal_condition is satisfied is public in effect, so the
        tool must agree with player_view rather than only reading the raw flag."""
        world.relationships[NPC_A][PLAYER].trust = 90.0
        manager = WorldStateManager(world)
        registry = build_npc_tools(
            npc_id=NPC_A, manager=manager, memory=MemoryStore(NPC_A), player_id=PLAYER
        )
        result = registry.call("check_public_fact", {"fact_id": "clue_1"})
        assert result["known"] is True

    def test_unknown_fact_id_is_not_an_error(self, registry):
        """A hallucinated fact id must be indistinguishable from a hidden one, or
        the difference itself tells the model what exists."""
        result = registry.call("check_public_fact", {"fact_id": "no_such_fact"})
        assert result["known"] is False


class TestRegistryErrors:
    def test_unknown_tool_name(self, registry):
        with pytest.raises(ToolError, match="unknown tool"):
            registry.call("delete_world", {})

    def test_wrong_argument_type(self, registry):
        with pytest.raises(ToolError):
            registry.call("query_memory", {"query": "x", "top_k": "many"})

    def test_extra_arguments_rejected(self, registry):
        """A model inventing arguments is drifting; silently ignoring them hides it."""
        with pytest.raises(ToolError):
            registry.call("query_relationship", {"target_id": PLAYER, "bonus": 1})

    def test_errors_are_reportable_to_the_model(self, registry):
        """ToolError must render to a short string the Harness can hand back as a
        tool result, so a bad call costs one turn instead of failing the response."""
        try:
            registry.call("nope", {})
        except ToolError as exc:
            assert str(exc)
            assert len(str(exc)) < 300
