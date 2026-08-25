"""The four narrative actions and the rules that gate them (docs/10 §2.1).

An operator is "a set of Action Proposals" by design, so it travels the existing
Validator rather than getting a new permission mechanism. These tests pin the
gates that make that safe:

* only system actors may propose them — an NPC must not advance the plot;
* ``chapter`` moves by at most one, mirroring MAX_RELATIONSHIP_STEP: a jump to
  chapter 9 would satisfy every chapter-gated condition at once, which is exactly
  the pacing bypass docs/04 §3.3 exists to prevent;
* a planted foreshadowing must be *unsatisfiable right now* and reachable later,
  since a loop that is already due is not a loop;
* a payoff cannot be claimed before its condition holds.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.narrative import Foreshadowing, NarrativeOperator
from ai_native_rpg.schemas.world_state import ActionProposal, Visibility, WorldState
from ai_native_rpg.world.actions import ActionType
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"
ENGINE = "narrative_engine"


def _proposal(action_type: str, *, actor: str = ENGINE, target=None, **payload) -> ActionProposal:
    import uuid

    return ActionProposal(
        proposal_id=uuid.uuid4().hex,
        actor_id=actor,
        action_type=action_type,
        target_id=target,
        payload=payload,
    )


def _trust_condition(value: float) -> dict:
    return Condition(
        clauses=[
            ConditionClause(
                path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=value
            )
        ]
    ).model_dump(mode="json")


class TestAdvanceTurn:
    def test_records_the_operator_and_increments_the_turn(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_turn", operator="reveal"))

        assert result.approved, result.reason
        beats = manager.snapshot().story_beats
        assert beats.turn == 1
        assert beats.recent_operators == ["reveal"]

    def test_a_quiet_turn_is_recorded_as_relieve(self, world: WorldState):
        # docs/10 §2.1: relieve is expressed as "no operator fired". It still
        # occupies a turn slot, so the pacing rules can measure adjacency.
        manager = WorldStateManager(world)

        manager.submit(_proposal("advance_turn", operator="relieve"))
        manager.submit(_proposal("advance_turn", operator="relieve"))

        assert manager.snapshot().story_beats.recent_operators == ["relieve", "relieve"]
        assert manager.snapshot().story_beats.turn == 2

    def test_an_unknown_operator_is_rejected(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_turn", operator="plot_twist_supreme"))

        assert not result.approved
        assert result.rule_name == "operator_must_be_known"

    def test_an_npc_cannot_advance_the_turn(self, world: WorldState):
        # Turn/beat bookkeeping belongs to the Engine. An NPC that could write it
        # would be able to age out its own pacing cooldowns.
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_turn", actor=NPC_A, operator="reveal"))

        assert not result.approved
        assert result.rule_name == "narrative_actions_are_system_only"


class TestAdvanceStoryBeat:
    def test_advances_chapter_and_tension(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", chapter=2, tension=0.6))

        assert result.approved, result.reason
        beats = manager.snapshot().story_beats
        assert (beats.chapter, beats.tension) == (2, 0.6)
        assert result.applied_changes == {
            "story_beats.chapter": 2,
            "story_beats.tension": 0.6,
        }

    def test_tension_alone_is_a_valid_advance(self, world: WorldState):
        # `escalate` raises tension without turning a chapter.
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", tension=0.4))

        assert result.approved, result.reason
        assert manager.snapshot().story_beats.chapter == 1

    def test_chapter_cannot_move_backwards(self, world: WorldState):
        manager = WorldStateManager(world)
        manager.submit(_proposal("advance_story_beat", chapter=2))

        result = manager.submit(_proposal("advance_story_beat", chapter=1))

        assert not result.approved
        assert result.rule_name == "chapter_advances_monotonically"

    def test_chapter_cannot_skip_ahead(self, world: WorldState):
        """One chapter at a time, for the reason MAX_RELATIONSHIP_STEP exists.

        Chapters are a reveal channel (docs/04 §3.3). Jumping 1 -> 9 would satisfy
        every chapter-gated clue at once, turning the pacing system off in a single
        approved proposal.
        """
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", chapter=9))

        assert not result.approved
        assert result.rule_name == "chapter_advances_monotonically"

    @pytest.mark.parametrize("bad", [-0.2, 1.5])
    def test_tension_outside_the_unit_interval_is_rejected(self, world: WorldState, bad):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", tension=bad))

        assert not result.approved
        assert result.rule_name == "tension_must_be_a_unit_fraction"

    def test_an_empty_advance_is_rejected(self, world: WorldState):
        # A no-op proposal that reports success is the failure mode
        # _target_must_be_present exists to prevent; same reasoning here.
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat"))

        assert not result.approved
        assert result.rule_name == "beat_advance_must_change_something"


class TestEventLifecycleWrites:
    """Opening, counting and closing an event go through the same single write path.

    docs/13 §11 records the failure this avoids: ``quests.investigation.stage`` had
    an author and a reader and no writer, so three foreshadowings waited forever on a
    number nothing moved. New state needs its writer wired the same day it is added,
    and the writer has to be a proposal — a setter on the Manager would be a second
    path around the Validator, which is the one thing docs/04 forbids.
    """

    def test_opening_an_event_records_it_as_active(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal("advance_story_beat", open_event="M1_knock", max_exchanges=4)
        )

        assert result.approved, result.reason
        active = manager.snapshot().story_beats.active_event
        assert active is not None
        assert (active.event_id, active.exchanges, active.max_exchanges) == ("M1_knock", 0, 4)

    def test_opening_without_a_budget_is_rejected(self, world: WorldState):
        """An event with no patience limit could never fall to its default outcome."""
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", open_event="M1_knock"))

        assert not result.approved
        assert result.rule_name == "event_lifecycle_payload_is_coherent"

    def test_recording_an_exchange_counts_up(self, world: WorldState):
        manager = WorldStateManager(world)
        manager.submit(_proposal("advance_story_beat", open_event="M1_knock", max_exchanges=4))

        result = manager.submit(_proposal("advance_story_beat", record_exchange=True))

        assert result.approved, result.reason
        active = manager.snapshot().story_beats.active_event
        assert active is not None and active.exchanges == 1

    def test_finishing_an_event_completes_it_without_closing(self, world: WorldState):
        manager = WorldStateManager(world)
        manager.submit(_proposal("advance_story_beat", open_event="M1_knock", max_exchanges=4))

        result = manager.submit(_proposal("advance_story_beat", finish_event=True))

        assert result.approved, result.reason
        beats = manager.snapshot().story_beats
        assert beats.active_event is None
        assert beats.has_completed("M1_knock")
        assert not beats.is_closed("M1_knock")

    def test_finishing_with_closure_closes_it(self, world: WorldState):
        manager = WorldStateManager(world)
        manager.submit(_proposal("advance_story_beat", open_event="M3_witness", max_exchanges=4))

        result = manager.submit(
            _proposal("advance_story_beat", finish_event=True, close_event=True)
        )

        assert result.approved, result.reason
        assert manager.snapshot().story_beats.is_closed("M3_witness")

    def test_raising_a_flag_is_recorded(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", raise_flags=["probed_once"]))

        assert result.approved, result.reason
        assert manager.snapshot().story_beats.has_flag("probed_once")

    def test_an_npc_cannot_open_an_event(self, world: WorldState):
        """The event layer decides what happens; an NPC inside one must not.

        Same reasoning as the turn counter: an NPC able to open events could open the
        one whose outcome hands over what it wants to say.
        """
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal("advance_story_beat", actor=NPC_A, open_event="M1_knock", max_exchanges=4)
        )

        assert not result.approved
        assert result.rule_name == "narrative_actions_are_system_only"

    def test_flags_must_be_a_list_of_strings(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("advance_story_beat", raise_flags="probed_once"))

        assert not result.approved
        assert result.rule_name == "event_lifecycle_payload_is_coherent"


class TestPlantForeshadowing:
    def test_writes_a_hidden_fact_and_a_ledger_entry(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="marta_was_awake",
                value="玛尔塔那晚似乎没睡",
                payoff_condition=_trust_condition(40),
                note="玛尔塔那晚没睡",
            )
        )

        assert result.approved, result.reason
        state = manager.snapshot()
        fact = state.facts["marta_was_awake"]
        # planted hidden: the payoff condition is the disclosure channel, so the
        # fact is not visible now but can become so without a bypass.
        assert fact.visibility is Visibility.HIDDEN
        assert fact.reveal_condition is not None
        entry = state.story_beats.open_foreshadowings["marta_was_awake"]
        assert entry.planted_at_turn == state.story_beats.turn
        assert entry.note == "玛尔塔那晚没睡"

    def test_the_planted_fact_is_invisible_to_the_player(self, world: WorldState):
        manager = WorldStateManager(world)

        manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="marta_was_awake",
                value="玛尔塔那晚似乎没睡",
                payoff_condition=_trust_condition(40),
            )
        )

        assert "marta_was_awake" not in manager.player_view(PLAYER).visible_facts

    def test_the_payoff_condition_becomes_the_reveal_condition(self, world: WorldState):
        """Planting does not bypass docs/04 §3.3 — it *installs* a channel.

        The condition the ledger will check and the condition PlayerView reads are
        the same object, so "time to pay off" and "the player may see it" cannot
        drift apart.
        """
        manager = WorldStateManager(world)
        manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="marta_was_awake",
                value="v",
                payoff_condition=_trust_condition(40),
            )
        )

        state = manager.snapshot()
        entry = state.story_beats.open_foreshadowings["marta_was_awake"]
        assert state.facts["marta_was_awake"].reveal_condition == entry.payoff_condition

    def test_an_already_satisfied_condition_is_rejected(self, world: WorldState):
        # trust starts at 20 in the fixture, so a `>= 10` loop is due on arrival.
        # A loop with no waiting period is not a loop.
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="already_due",
                value="v",
                payoff_condition=_trust_condition(10),
            )
        )

        assert not result.approved
        assert result.rule_name == "foreshadowing_must_not_be_due_yet"

    def test_an_unresolvable_payoff_path_is_rejected(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="bad_path",
                value="v",
                payoff_condition=Condition(
                    clauses=[ConditionClause(path="nonsense.field", op=ConditionOp.GTE, value=1)]
                ).model_dump(mode="json"),
            )
        )

        assert not result.approved
        assert result.rule_name == "payoff_condition_must_be_checkable"

    def test_a_path_outside_the_whitelist_is_rejected(self, world: WorldState):
        """The model may not invent its own unlock channel.

        A resolvable path is not enough: ``time_day`` resolves fine and would make
        the loop pay off by simply waiting, with no player action involved.
        """
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="wrong_channel",
                value="v",
                payoff_condition=Condition(
                    clauses=[ConditionClause(path="time_day", op=ConditionOp.GTE, value=9)]
                ).model_dump(mode="json"),
            )
        )

        assert not result.approved
        assert result.rule_name == "payoff_condition_must_be_checkable"

    def test_an_empty_condition_is_rejected(self, world: WorldState):
        # An empty condition evaluates False forever (conditions.evaluate fails
        # closed), so this would plant a loop that can never be settled.
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="never",
                value="v",
                payoff_condition={"mode": "any", "clauses": []},
            )
        )

        assert not result.approved
        assert result.rule_name == "payoff_condition_must_be_checkable"

    def test_it_cannot_overwrite_an_existing_fact(self, world: WorldState):
        # Overwriting killer_identity with a "foreshadowing" would rewrite the
        # answer to the mystery through a side door.
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="killer_identity",
                value="v",
                payoff_condition=_trust_condition(90),
            )
        )

        assert not result.approved
        assert result.rule_name == "planted_fact_id_must_be_new"

    def test_an_npc_cannot_plant(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                actor=NPC_A,
                target="npc_planted",
                value="v",
                payoff_condition=_trust_condition(50),
            )
        )

        assert not result.approved
        assert result.rule_name == "narrative_actions_are_system_only"

    def test_it_cannot_name_a_character_the_world_does_not_have(self, world: WorldState):
        """Found in play: a run planted a detail about a village miller.

        The pack has two NPCs and no miller. Every other rule passed — the fact id
        was new, the condition was checkable — because nothing inspected the names,
        so the invented character entered the ledger owing the player a payoff about
        someone who does not exist.
        """
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="millwheel_rope",
                value="水轮上挂着一截湿绳子",
                payoff_condition=_trust_condition(50),
                participants=["npc_miller"],
            )
        )

        assert not result.approved
        assert result.rule_name == "participants_must_exist"
        assert "npc_miller" in result.reason

    def test_it_accepts_the_display_names_the_generator_is_given(self, world: WorldState):
        """The prompt hands out names, not ids, so names must validate.

        Ids leak into prose, and a fact's value can itself be an id
        (``killer_identity`` is ``npc_b`` here), so listing the cast by id would put
        an undisclosed value into the prompt. Rejecting names here would reject the
        vocabulary the prompt just supplied.
        """
        manager = WorldStateManager(world)

        result = manager.submit(
            _proposal(
                "plant_foreshadowing",
                target="a_quiet_detail",
                value="门口的柴堆比邻居家的都高",
                payoff_condition=_trust_condition(50),
                participants=["玛尔塔"],
            )
        )

        assert result.approved, result.reason


class TestPayOffForeshadowing:
    def _with_open_loop(self, world: WorldState, threshold: float = 40) -> WorldStateManager:
        world.story_beats.open_foreshadowings["clue_1"] = Foreshadowing(
            fact_id="clue_1",
            planted_at_turn=0,
            payoff_condition=Condition(
                clauses=[
                    ConditionClause(
                        path=f"relationships.{NPC_A}.{PLAYER}.trust",
                        op=ConditionOp.GTE,
                        value=threshold,
                    )
                ]
            ),
        )
        return WorldStateManager(world)

    def test_settling_removes_the_entry_from_the_ledger(self, world: WorldState):
        manager = self._with_open_loop(world, threshold=20)  # fixture trust is 20

        result = manager.submit(_proposal("pay_off_foreshadowing", target="clue_1"))

        assert result.approved, result.reason
        assert manager.snapshot().story_beats.open_foreshadowings == {}

    def test_it_cannot_be_claimed_before_the_condition_holds(self, world: WorldState):
        manager = self._with_open_loop(world, threshold=80)  # trust is 20

        result = manager.submit(_proposal("pay_off_foreshadowing", target="clue_1"))

        assert not result.approved
        assert result.rule_name == "payoff_must_be_due"

    def test_settling_an_unknown_loop_is_rejected(self, world: WorldState):
        manager = WorldStateManager(world)

        result = manager.submit(_proposal("pay_off_foreshadowing", target="never_planted"))

        assert not result.approved
        assert result.rule_name == "payoff_target_must_be_open"


class TestNarrativeActionsAreRegistered:
    def test_all_four_are_known_action_types(self):
        # Unknown action types are rejected by _action_type_is_known, so an
        # operator whose action is not registered fails closed rather than
        # silently doing nothing.
        from ai_native_rpg.world.actions import KNOWN_ACTION_TYPES

        assert {
            ActionType.ADVANCE_TURN.value,
            ActionType.ADVANCE_STORY_BEAT.value,
            ActionType.PLANT_FORESHADOWING.value,
            ActionType.PAY_OFF_FORESHADOWING.value,
        } <= KNOWN_ACTION_TYPES

    def test_operator_vocabulary_matches_the_schema(self):
        from ai_native_rpg.world.actions import KNOWN_OPERATORS

        assert frozenset(op.value for op in NarrativeOperator) == KNOWN_OPERATORS
