"""Validator rules. Each rule is a pure function (proposal, state) -> RuleOutcome,
per docs/04 §5 — a rule list, not a rule engine."""

from __future__ import annotations

from ai_native_rpg.schemas.world_state import ActionProposal
from ai_native_rpg.world.validator import DEFAULT_RULES, Validator

PLAYER = "player_1"
NPC_A = "npc_a"
NPC_B = "npc_b"


def proposal(actor=NPC_A, action_type="reveal_fact", target=None, **payload) -> ActionProposal:
    return ActionProposal(
        proposal_id="p1", actor_id=actor, action_type=action_type, target_id=target, payload=payload
    )


class TestActorExists:
    def test_unknown_actor_is_rejected(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(actor="npc_ghost", target="clue_1"), world
        )
        assert not result.approved
        assert result.rule_name == "actor_must_exist"

    def test_dead_actor_cannot_act(self, world):
        world.npcs[NPC_A].alive = False
        result = Validator(DEFAULT_RULES).validate(proposal(target="clue_1"), world)
        assert not result.approved
        assert result.rule_name == "actor_must_be_alive"

    def test_narrative_engine_passes_the_actor_existence_check(self, world):
        """The Narrative Engine proposes actions but is not an NPC in the world;
        it must not be rejected by the actor-exists rule. It is still subject to
        the per-action rules — see TestNoRevealBypass."""
        result = Validator(DEFAULT_RULES).validate(
            proposal(actor="narrative_engine", action_type="advance_quest", target="investigation"),
            world,
        )
        assert result.approved


class TestNoRevealBypass:
    """No actor may bypass reveal_condition — not even the Narrative Engine.

    The Engine drives plot reveals by advancing story beats and letting the
    condition table decide what that unlocks. A bypass action was considered and
    rejected: the only facts it could unlock that beats cannot are the ones an
    author deliberately gave no beat channel, i.e. the endings.
    See docs/04_World_State_Manager.md#33.
    """

    def test_narrative_engine_cannot_reveal_past_an_unmet_condition(self, world):
        world.relationships[NPC_A][PLAYER].trust = 0.0
        result = Validator(DEFAULT_RULES).validate(
            proposal(actor="narrative_engine", target="clue_1"), world
        )
        assert not result.approved
        assert result.rule_name == "reveal_requires_condition_met"

    def test_npc_cannot_reveal_past_an_unmet_condition(self, world):
        world.relationships[NPC_A][PLAYER].trust = 0.0
        result = Validator(DEFAULT_RULES).validate(proposal(target="clue_1"), world)
        assert not result.approved
        assert result.rule_name == "reveal_requires_condition_met"

    def test_no_action_type_looks_like_a_bypass(self, world):
        """Guards against a bypass being reintroduced later by name."""
        from ai_native_rpg.world.actions import KNOWN_ACTION_TYPES

        assert not any("force" in a for a in KNOWN_ACTION_TYPES)


class TestUnknownActionType:
    def test_unknown_action_type_is_rejected(self, world):
        """Fail closed: an action the Validator has no rule for must not pass
        through unchecked, or an LLM inventing an action name would bypass every
        guarantee this layer exists to provide."""
        result = Validator(DEFAULT_RULES).validate(proposal(action_type="mind_control"), world)
        assert not result.approved
        assert "unknown" in (result.reason or "").lower()


class TestRevealFact:
    def test_reveal_requires_known_fact(self, world):
        result = Validator(DEFAULT_RULES).validate(proposal(target="no_such_fact"), world)
        assert not result.approved

    def test_reveal_blocked_when_condition_unmet(self, world):
        world.relationships[NPC_A][PLAYER].trust = 10.0
        result = Validator(DEFAULT_RULES).validate(proposal(target="clue_1"), world)
        assert not result.approved
        assert result.rule_name == "reveal_requires_condition_met"

    def test_reveal_allowed_when_condition_met(self, world):
        world.relationships[NPC_A][PLAYER].trust = 55.0
        result = Validator(DEFAULT_RULES).validate(proposal(target="clue_1"), world)
        assert result.approved

    def test_already_revealed_fact_is_allowed_as_noop(self, world):
        result = Validator(DEFAULT_RULES).validate(proposal(target="victim_name"), world)
        assert result.approved


class TestAdjustRelationship:
    def test_within_step_limit_is_allowed(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0), world
        )
        assert result.approved

    def test_oversized_single_step_is_rejected(self, world):
        """Caps how fast an LLM can move relationship values: a model that decides
        trust should jump +80 in one line of dialogue would otherwise unlock the
        entire clue chain at once."""
        result = Validator(DEFAULT_RULES).validate(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=80.0), world
        )
        assert not result.approved
        assert result.rule_name == "relationship_step_limit"

    def test_unknown_dimension_is_rejected(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(action_type="adjust_relationship", target=PLAYER, admiration=1.0), world
        )
        assert not result.approved


class TestMoveNPC:
    def test_move_to_connected_location_is_allowed(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(action_type="move", target="village_square"), world
        )
        assert result.approved

    def test_move_to_disconnected_location_is_rejected(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(action_type="move", target="forest_edge"), world
        )
        assert not result.approved
        assert result.rule_name == "move_must_be_adjacent"

    def test_move_to_unknown_location_is_rejected(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(action_type="move", target="atlantis"), world
        )
        assert not result.approved


class TestAdvanceQuest:
    def test_advance_by_one_stage_is_allowed(self, world):
        result = Validator(DEFAULT_RULES).validate(
            proposal(actor="narrative_engine", action_type="advance_quest", target="investigation"),
            world,
        )
        assert result.approved

    def test_cannot_advance_completed_quest(self, world):
        world.quests["investigation"].status = "completed"
        result = Validator(DEFAULT_RULES).validate(
            proposal(actor="narrative_engine", action_type="advance_quest", target="investigation"),
            world,
        )
        assert not result.approved


class TestRuleComposition:
    def test_first_failing_rule_short_circuits(self, world):
        """Rejection reason should name the first violated rule, so the reason fed
        into Dialogue Generation is specific rather than a generic failure."""
        world.npcs[NPC_A].alive = False
        result = Validator(DEFAULT_RULES).validate(proposal(target="no_such_fact"), world)
        assert result.rule_name == "actor_must_be_alive"

    def test_validate_never_mutates_state(self, world):
        before = world.model_dump_json()
        Validator(DEFAULT_RULES).validate(
            proposal(action_type="adjust_relationship", target=PLAYER, trust=5.0), world
        )
        assert world.model_dump_json() == before
