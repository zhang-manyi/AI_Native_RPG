"""Player movement: the path that existed, validated, and moved nobody.

``ActionType.MOVE`` was in the vocabulary, ``_move_must_be_adjacent`` guarded it, and
``_apply_move`` wrote ``npcs[actor_id].location`` — but a player is not in ``npcs``, so
every player move either raised or silently moved an NPC that happened to share the id.
docs/13 §12 records it as never having existed.

The content consequence is what makes it worth its own file. ``loren_that_night`` is
gated on ``trust >= 85`` **or** ``stage >= 3``, and the second channel is the
"independent investigation" route (docs/14 §1.2.1). The clues that raise the stage live
in the tavern and at the forest edge, so a player who cannot leave Marta's doorstep has
exactly one channel — an author wrote two roads and one of them was rubble. The last
test here is the one that would have caught that.
"""

from __future__ import annotations

import uuid

import pytest

from ai_native_rpg.narrative.player_actions import end_conversation, move_player
from ai_native_rpg.schemas.narrative import SPENDABLE_SLOTS, TimeSlot
from ai_native_rpg.schemas.world_state import ActionProposal, WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"


def _proposal(actor: str, action_type: str, target: str | None = None, **payload) -> ActionProposal:
    """A proposal with a fresh id every time.

    Ids must be unique: the Manager is idempotent per ``proposal_id``, so a helper that
    derived the id from its arguments would make the second identical action a no-op and
    the test would read that as the rule rejecting it.
    """
    return ActionProposal(
        proposal_id=uuid.uuid4().hex,
        actor_id=actor,
        action_type=action_type,
        target_id=target,
        payload=payload,
    )


@pytest.fixture
def manager(world: WorldState) -> WorldStateManager:
    world.player_locations[PLAYER] = "village_square"
    world.story_beats.visited_locations = ["village_square"]
    return WorldStateManager(world)


class TestThePlayerCanMove:
    """The bug itself: an approved move must move the player."""

    def test_an_approved_move_changes_where_the_player_is(self, manager):
        result = manager.submit(_proposal(PLAYER, "move", "tavern"))

        assert result.approved
        assert manager.snapshot().player_locations[PLAYER] == "tavern"

    def test_the_move_is_reported_against_the_player_not_an_npc(self, manager):
        """The regression that hid the bug: it reported success either way.

        Writing ``npcs.<id>.location`` for a player id is the original defect, so the
        applied changes have to name ``player_locations`` — otherwise a caller reading
        the trace would see a successful move and the world would disagree.
        """
        result = manager.submit(_proposal(PLAYER, "move", "tavern"))

        assert result.applied_changes is not None
        assert f"player_locations.{PLAYER}" in result.applied_changes
        assert not any(key.startswith("npcs.") for key in result.applied_changes)

    def test_moving_an_npc_still_works(self, manager):
        """The old path is unchanged; this widens it rather than replacing it."""
        before = manager.snapshot().npcs[NPC_A].location
        assert before == "tavern"

        result = manager.submit(_proposal(NPC_A, "move", "village_square"))

        assert result.approved
        assert manager.snapshot().npcs[NPC_A].location == "village_square"

    def test_a_player_who_does_not_exist_is_still_rejected(self, manager):
        """Widening the actor check must not make it vacuous."""
        result = manager.submit(_proposal("player_99", "move", "tavern"))

        assert not result.approved
        assert result.rule_name == "actor_must_exist"


class TestAdjacencyAppliesToThePlayer:
    """One adjacency rule, not a second one written for the player.

    This is what routing player moves through the Validator buys (docs/13 §12): the
    rule was already there and reading the wrong field, so the fix is to teach it where
    a player lives rather than to write a parallel check.
    """

    def test_a_connected_destination_is_allowed(self, manager):
        result = manager.submit(_proposal(PLAYER, "move", "tavern"))
        assert result.approved

    def test_a_disconnected_destination_is_rejected(self, manager):
        """The tavern and the forest both connect to the square, not to each other."""
        assert manager.submit(_proposal(PLAYER, "move", "tavern")).approved

        result = manager.submit(_proposal(PLAYER, "move", "forest_edge"))

        assert not result.approved
        assert result.rule_name == "move_must_be_adjacent"
        assert manager.snapshot().player_locations[PLAYER] == "tavern"

    def test_an_unknown_destination_is_rejected(self, manager):
        result = manager.submit(_proposal(PLAYER, "move", "atlantis"))
        assert not result.approved

    def test_the_rejection_names_where_the_player_actually_is(self, manager):
        """The reason string is shown to the player (docs/02 §4.1), so it has to be true.

        Before the fix this branch read an NPC's location, so a player standing in the
        square could be told his move was unreachable from the tavern.
        """
        assert manager.submit(_proposal(PLAYER, "move", "tavern")).approved

        result = manager.submit(_proposal(PLAYER, "move", "forest_edge"))

        assert result.reason is not None
        assert "tavern" in result.reason


class TestPlayersMayOnlyMove:
    """The restriction that has to arrive with the permission.

    Letting player ids past ``_actor_must_exist`` also exposes every other action type
    to them, and two of those matter: ``reveal_fact`` would let the player unlock his
    own clues and ``adjust_relationship`` would let him set what an NPC feels about him.
    Both are the disclosure bypass docs/04 §3.3 forbids; both were previously blocked
    only by accident.
    """

    @pytest.mark.parametrize(
        ("action_type", "target"),
        [
            ("reveal_fact", "clue_1"),
            ("adjust_relationship", NPC_A),
            ("advance_quest", "investigation"),
        ],
    )
    def test_a_player_cannot_propose_other_actions(self, manager, action_type, target):
        payload = {"trust": 10.0} if action_type == "adjust_relationship" else {}
        result = manager.submit(_proposal(PLAYER, action_type, target, **payload))

        assert not result.approved
        assert result.rule_name == "players_may_only_move"

    def test_a_player_cannot_advance_the_clock_directly(self, manager):
        """Otherwise a player could skip a night he did not want to spend."""
        result = manager.submit(_proposal(PLAYER, "advance_story_beat", None, advance_slot=True))

        assert not result.approved

    def test_the_player_cannot_reveal_a_clue_he_has_not_earned(self, manager):
        """Stated as the content consequence, since that is why the rule exists."""
        manager.submit(_proposal(PLAYER, "reveal_fact", "clue_1"))

        assert manager.snapshot().facts["clue_1"].visibility.value == "hidden"

    def test_the_engine_may_still_do_all_of_it(self, manager):
        """The restriction is about the player, not about the action types."""
        result = manager.submit(_proposal("narrative_engine", "advance_quest", "investigation"))
        assert result.approved


class TestFirstArrivalIsRecorded:
    """ "首次到达酒馆" is a trigger M4 and F2 are written against (docs/15 §4).

    It is only expressible while the visit is being written: afterwards the world cannot
    tell a first visit from a fifth. So the visit list is the state docs/13 §7 asks for,
    and this is what makes those triggers writable at all.
    """

    def test_arriving_somewhere_new_records_the_visit(self, manager):
        manager.submit(_proposal(PLAYER, "move", "tavern"))

        assert manager.snapshot().story_beats.has_visited("tavern")

    def test_the_starting_location_counts_as_visited(self, manager):
        """He is standing there on turn one; a trigger for it must not fire on return."""
        assert manager.snapshot().story_beats.has_visited("village_square")

    def test_returning_is_not_a_first_visit(self, manager):
        result = move_player(manager, player_id=PLAYER, destination="tavern")
        assert result.first_visit

        move_player(manager, player_id=PLAYER, destination="village_square")
        again = move_player(manager, player_id=PLAYER, destination="tavern")

        assert not again.first_visit

    def test_the_visit_list_does_not_duplicate(self, manager):
        for destination in ("tavern", "village_square", "tavern", "village_square"):
            move_player(manager, player_id=PLAYER, destination=destination)

        visited = manager.snapshot().story_beats.visited_locations
        assert sorted(visited) == ["tavern", "village_square"]

    def test_a_rejected_move_records_nothing(self, manager):
        """From the tavern the forest is two hops away, so this move is refused."""
        assert manager.submit(_proposal(PLAYER, "move", "tavern")).approved

        manager.submit(_proposal(PLAYER, "move", "forest_edge"))

        assert not manager.snapshot().story_beats.has_visited("forest_edge")


class TestMovingCostsASlot:
    """docs/13 §4.1: going somewhere is what a slot is spent on."""

    def test_an_approved_move_spends_a_slot(self, manager):
        result = move_player(manager, player_id=PLAYER, destination="tavern")

        assert result.approved
        assert result.slot_spent
        assert result.slot is TimeSlot.AFTERNOON

    def test_a_rejected_move_spends_nothing(self, manager):
        """Nothing happened, so nothing was spent — and the trace should not imply it."""
        assert move_player(manager, player_id=PLAYER, destination="tavern").approved
        before = manager.snapshot().story_beats.model_copy(deep=True)

        result = move_player(manager, player_id=PLAYER, destination="forest_edge")

        after = manager.snapshot().story_beats
        assert not result.approved
        assert not result.slot_spent
        assert after.time_slot is before.time_slot
        assert after.slots_spent_today == before.slots_spent_today

    def test_three_moves_reach_the_wrap_up(self, manager):
        for _ in range(len(SPENDABLE_SLOTS)):
            move_player(manager, player_id=PLAYER, destination="tavern")
            move_player(manager, player_id=PLAYER, destination="village_square")

        beats = manager.snapshot().story_beats
        assert beats.time_slot is not TimeSlot.MORNING

    def test_ending_a_conversation_does_not_cost_a_slot_by_default(self, manager):
        """The slot went on the journey; leaving a dead conversation is not a second one."""
        before = manager.snapshot().story_beats.time_slot

        result = end_conversation(manager)

        assert result.approved
        assert not result.slot_spent
        assert manager.snapshot().story_beats.time_slot is before


class TestTheSecondChannelIsReachable:
    """The content bug the movement bug caused (docs/13 §11, docs/14 §1.2.1).

    ``loren_that_night`` declares two channels and the second one — ``stage >= 3``, the
    independent-investigation route — was unreachable, because the clues that raise the
    stage are in the tavern and at the forest edge and the player could not get to
    either. The point of asserting it here is that reachability be *checked* rather than
    documented: docs/15 §6 records this same shape of bug twice.
    """

    def test_the_forest_edge_is_reachable_from_the_start(self, manager):
        """Two moves, since the square is the hub. Both must be approved."""
        first = move_player(manager, player_id=PLAYER, destination="forest_edge")

        assert first.approved
        assert manager.snapshot().player_locations[PLAYER] == "forest_edge"

    def test_every_location_is_reachable_from_where_the_player_starts(self, manager):
        """A walk of the graph, using only approved moves.

        This is the loader-style check applied to geography: a location no player can
        stand in is content that silently never happens, whatever is authored there.
        """
        world = manager.snapshot()
        start = world.player_locations[PLAYER]

        reached = {start}
        frontier = [start]
        while frontier:
            here = frontier.pop()
            for neighbour in world.locations[here].connected_to:
                if neighbour not in reached:
                    reached.add(neighbour)
                    frontier.append(neighbour)

        assert reached == set(world.locations)

    def test_the_stage_channel_can_be_reached_while_trust_stays_low(self, manager):
        """Both of ``loren_that_night``'s channels must be independently satisfiable.

        Trust is held at its starting value throughout: if the only way to the answer
        ran through Marta, the "independent investigation" route would be decoration.
        """
        for _ in range(3):
            manager.submit(_proposal("narrative_engine", "advance_quest", "investigation"))

        world = manager.snapshot()
        assert world.quests["investigation"].stage >= 3
        assert manager.get_trust(NPC_A, PLAYER) < 85

        from ai_native_rpg.world.conditions import evaluate

        condition = world.facts["loren_that_night"].reveal_condition
        assert condition is not None
        assert evaluate(condition, world)
