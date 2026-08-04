"""WorldStateManager: the only writer of world state.

The encapsulation tests here are the point of the module. "No Agent may mutate
the world directly" is a claim the architecture makes everywhere; these tests are
what make it true in code rather than by convention.
"""

from __future__ import annotations

import json

import pytest

from ai_native_rpg.schemas.world_state import ActionProposal, Visibility
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"
NPC_B = "npc_b"


@pytest.fixture
def manager(world) -> WorldStateManager:
    return WorldStateManager(world)


def proposal(actor=NPC_A, action_type="reveal_fact", target=None, pid="p1", **payload):
    return ActionProposal(
        proposal_id=pid, actor_id=actor, action_type=action_type, target_id=target, payload=payload
    )


class TestEncapsulation:
    def test_snapshot_is_a_copy_not_the_live_object(self, manager):
        snapshot = manager.snapshot()
        snapshot.time_day = 999
        snapshot.npcs[NPC_A].location = "hacked"
        assert manager.snapshot().time_day == 1
        assert manager.snapshot().npcs[NPC_A].location == "tavern"

    def test_mutating_a_nested_relationship_on_a_snapshot_does_not_leak(self, manager):
        snapshot = manager.snapshot()
        snapshot.relationships[NPC_A][PLAYER].trust = 100.0
        assert manager.get_trust(NPC_A, PLAYER) == 20.0

    def test_constructor_copies_the_input_world(self, world):
        mgr = WorldStateManager(world)
        world.time_day = 42
        assert mgr.snapshot().time_day == 1


class TestRelationshipAccess:
    def test_get_trust_returns_stored_value(self, manager):
        assert manager.get_trust(NPC_A, PLAYER) == 20.0

    def test_get_trust_defaults_to_zero_for_unknown_pair(self, manager):
        assert manager.get_trust(NPC_A, "stranger") == 0.0

    def test_relationship_projection_matches_world_state(self, manager):
        rel = manager.get_relationship(NPC_A, PLAYER)
        assert rel.trust == 20.0
        assert rel.fear == 10.0

    def test_projection_is_read_only_copy(self, manager):
        rel = manager.get_relationship(NPC_A, PLAYER)
        rel.trust = 90.0
        assert manager.get_trust(NPC_A, PLAYER) == 20.0


class TestSubmitProposal:
    def test_approved_reveal_updates_visibility(self, manager):
        manager.submit(proposal(action_type="adjust_relationship", target=PLAYER, trust=15.0))
        manager.submit(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=15.0, pid="p2")
        )
        result = manager.submit(proposal(target="clue_1", pid="p3"))
        assert result.approved
        assert manager.snapshot().facts["clue_1"].visibility is Visibility.REVEALED

    def test_rejected_proposal_leaves_state_untouched(self, manager):
        before = manager.snapshot().model_dump_json()
        result = manager.submit(proposal(target="killer_identity"))
        assert not result.approved
        assert manager.snapshot().model_dump_json() == before

    def test_rejection_carries_a_reason_for_the_dialogue_prompt(self, manager):
        result = manager.submit(proposal(target="killer_identity"))
        assert result.reason
        assert result.rule_name == "reveal_requires_condition_met"

    def test_relationship_adjustment_is_additive(self, manager):
        manager.submit(proposal(action_type="adjust_relationship", target=PLAYER, trust=10.0))
        assert manager.get_trust(NPC_A, PLAYER) == 30.0

    def test_relationship_adjustment_clamps_to_schema_bounds(self, manager):
        """Repeated legal steps must not be able to push past +/-100 and raise a
        validation error deep inside the write path."""
        for i in range(20):
            manager.submit(
                proposal(action_type="adjust_relationship", target=PLAYER, trust=15.0, pid=f"p{i}")
            )
        assert manager.get_trust(NPC_A, PLAYER) == 100.0

    def test_adjustment_creates_missing_relationship_entry(self, manager):
        manager.submit(
            proposal(actor=NPC_B, action_type="adjust_relationship", target="stranger", fear=5.0)
        )
        assert manager.get_relationship(NPC_B, "stranger").fear == 5.0

    def test_move_updates_location(self, manager):
        result = manager.submit(proposal(action_type="move", target="village_square"))
        assert result.approved
        assert manager.snapshot().npcs[NPC_A].location == "village_square"

    def test_advance_quest_increments_stage(self, manager):
        result = manager.submit(
            proposal(actor="narrative_engine", action_type="advance_quest", target="investigation")
        )
        assert result.approved
        assert manager.snapshot().quests["investigation"].stage == 1

    def test_applied_changes_are_reported(self, manager):
        result = manager.submit(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0)
        )
        assert result.applied_changes == {f"relationships.{NPC_A}.{PLAYER}.trust": 25.0}

    def test_last_updated_does_not_go_backward_on_write(self, manager):
        # >= not >: two utc_now() calls this close together can read the same value
        # on a coarse OS clock (notably Windows, ~1-15ms resolution), which made a
        # strict > assertion flaky. The invariant we actually care about is that a
        # write never moves last_updated backward.
        before = manager.snapshot().last_updated
        manager.submit(proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0))
        assert manager.snapshot().last_updated >= before

    def test_duplicate_proposal_id_is_rejected(self, manager):
        """Idempotence guard: a retried LLM call must not apply a relationship
        delta twice."""
        first = manager.submit(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0)
        )
        second = manager.submit(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0)
        )
        assert first.approved
        assert not second.approved
        assert manager.get_trust(NPC_A, PLAYER) == 25.0


class TestDryRun:
    def test_dry_run_reports_verdict_without_applying(self, manager):
        result = manager.dry_run(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0)
        )
        assert result.approved
        assert manager.get_trust(NPC_A, PLAYER) == 20.0

    def test_dry_run_does_not_consume_the_proposal_id(self, manager):
        p = proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0)
        manager.dry_run(p)
        assert manager.submit(p).approved


class TestPlayerViewIntegration:
    def test_manager_exposes_player_view(self, manager):
        view = manager.player_view(PLAYER)
        assert "victim_name" in view.visible_facts
        assert "clue_1" not in view.visible_facts

    def test_view_reflects_approved_reveal(self, manager):
        manager.submit(proposal(action_type="adjust_relationship", target=PLAYER, trust=15.0))
        manager.submit(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=15.0, pid="p2")
        )
        manager.submit(proposal(target="clue_1", pid="p3"))
        assert "clue_1" in manager.player_view(PLAYER).visible_facts


class TestPersistence:
    def test_round_trip_preserves_state(self, manager, tmp_path):
        manager.submit(proposal(action_type="adjust_relationship", target=PLAYER, trust=10.0))
        path = tmp_path / "world.json"
        manager.save(path)

        restored = WorldStateManager.load(path)
        assert restored.get_trust(NPC_A, PLAYER) == 30.0
        assert restored.snapshot().facts["clue_1"].visibility is Visibility.HIDDEN

    def test_saved_file_is_readable_json(self, manager, tmp_path):
        path = tmp_path / "world.json"
        manager.save(path)
        assert json.loads(path.read_text(encoding="utf-8"))["world_id"] == "test_village"

    def test_save_creates_parent_directories(self, manager, tmp_path):
        path = tmp_path / "nested" / "dir" / "world.json"
        manager.save(path)
        assert path.exists()

    def test_reveal_conditions_survive_round_trip(self, manager, tmp_path):
        """Conditions are nested models inside a dict; a sloppy serializer would
        flatten them into plain dicts and break the evaluator on reload."""
        path = tmp_path / "world.json"
        manager.save(path)
        restored = WorldStateManager.load(path)
        restored.submit(
            proposal(
                actor="narrative_engine",
                action_type="advance_quest",
                target="investigation",
                pid="q1",
            )
        )
        restored.submit(
            proposal(
                actor="narrative_engine",
                action_type="advance_quest",
                target="investigation",
                pid="q2",
            )
        )
        restored.submit(
            proposal(
                actor="narrative_engine",
                action_type="advance_quest",
                target="investigation",
                pid="q3",
            )
        )
        assert "killer_identity" in restored.player_view(PLAYER).visible_facts
