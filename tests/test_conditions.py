"""Condition evaluator: the replacement for string-expression reveal_condition.

Its whole job is to be boring and total — an unknown path must raise, not
silently evaluate False, because a typo in a scenario file would otherwise mean
a clue that can never be revealed and no error anywhere.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.world.conditions import UnknownPathError, evaluate, resolve_path

PLAYER = "player_1"
NPC_A = "npc_a"


class TestResolvePath:
    def test_resolves_nested_model_attribute(self, world):
        assert resolve_path(world, "quests.investigation.stage") == 0

    def test_resolves_dict_then_model(self, world):
        assert resolve_path(world, f"relationships.{NPC_A}.{PLAYER}.trust") == 20.0

    def test_resolves_top_level_scalar(self, world):
        assert resolve_path(world, "time_day") == 1

    def test_unknown_top_level_field_raises(self, world):
        with pytest.raises(UnknownPathError, match="nope"):
            resolve_path(world, "nope.foo")

    def test_unknown_dict_key_raises(self, world):
        with pytest.raises(UnknownPathError):
            resolve_path(world, "relationships.npc_zzz.player_1.trust")

    def test_traversing_into_scalar_raises(self, world):
        with pytest.raises(UnknownPathError):
            resolve_path(world, "time_day.nonsense")

    def test_empty_path_raises(self, world):
        with pytest.raises(UnknownPathError):
            resolve_path(world, "")


class TestOperators:
    @pytest.mark.parametrize(
        ("op", "value", "expected"),
        [
            (ConditionOp.EQ, 20.0, True),
            (ConditionOp.EQ, 21.0, False),
            (ConditionOp.NE, 21.0, True),
            (ConditionOp.GT, 19.0, True),
            (ConditionOp.GT, 20.0, False),
            (ConditionOp.GTE, 20.0, True),
            (ConditionOp.LT, 21.0, True),
            (ConditionOp.LTE, 20.0, True),
            (ConditionOp.IN, [10.0, 20.0], True),
            (ConditionOp.IN, [10.0, 30.0], False),
        ],
    )
    def test_operator_semantics(self, world, op, value, expected):
        cond = Condition(
            clauses=[
                ConditionClause(path=f"relationships.{NPC_A}.{PLAYER}.trust", op=op, value=value)
            ]
        )
        assert evaluate(cond, world) is expected


class TestModes:
    def _cond(self, mode: str, trust_gate: float, stage_gate: int) -> Condition:
        return Condition(
            mode=mode,
            clauses=[
                ConditionClause(
                    path=f"relationships.{NPC_A}.{PLAYER}.trust",
                    op=ConditionOp.GTE,
                    value=trust_gate,
                ),
                ConditionClause(
                    path="quests.investigation.stage", op=ConditionOp.GTE, value=stage_gate
                ),
            ],
        )

    def test_any_true_when_one_clause_holds(self, world):
        assert evaluate(self._cond("any", trust_gate=10, stage_gate=99), world) is True

    def test_any_false_when_no_clause_holds(self, world):
        assert evaluate(self._cond("any", trust_gate=99, stage_gate=99), world) is False

    def test_all_requires_every_clause(self, world):
        assert evaluate(self._cond("all", trust_gate=10, stage_gate=0), world) is True
        assert evaluate(self._cond("all", trust_gate=10, stage_gate=99), world) is False

    def test_empty_clauses_is_false_for_any(self, world):
        """No conditions means 'nothing can unlock this', not 'always unlocked' —
        a Fact with an empty condition list should stay hidden rather than leak."""
        assert evaluate(Condition(mode="any", clauses=[]), world) is False

    def test_empty_clauses_is_false_for_all(self, world):
        """Vacuous-truth would silently reveal every hidden fact, so 'all' over an
        empty list is defined as False here, diverging from mathematical convention."""
        assert evaluate(Condition(mode="all", clauses=[]), world) is False


class TestTypeMismatch:
    def test_comparing_number_to_string_raises(self, world):
        cond = Condition(
            clauses=[
                ConditionClause(
                    path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GT, value="high"
                )
            ]
        )
        with pytest.raises(TypeError):
            evaluate(cond, world)

    def test_in_operator_requires_iterable_value(self, world):
        cond = Condition(
            clauses=[
                ConditionClause(
                    path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.IN, value=20.0
                )
            ]
        )
        with pytest.raises(TypeError):
            evaluate(cond, world)


class TestContains:
    """``contains`` is ``in`` with the operands the other way round.

    Both are needed and neither substitutes for the other: ``in`` asks whether a
    scalar in the world belongs to a set the author wrote, while ``contains`` asks
    whether a *list in the world* holds a value the author named. The event layer
    needs the second — "has M1 been completed" reads a list living in the world
    (docs/13 §7, where every new state must be condition-readable).
    """

    def _cond(self, value) -> Condition:
        return Condition(
            clauses=[
                ConditionClause(
                    path="story_beats.recent_operators", op=ConditionOp.CONTAINS, value=value
                )
            ]
        )

    def test_true_when_the_list_at_the_path_holds_the_value(self, world):
        world.story_beats.recent_operators = ["relieve", "reveal"]

        assert evaluate(self._cond("reveal"), world) is True

    def test_false_when_it_does_not(self, world):
        world.story_beats.recent_operators = ["relieve"]

        assert evaluate(self._cond("reveal"), world) is False

    def test_false_on_an_empty_list_rather_than_raising(self, world):
        world.story_beats.recent_operators = []

        assert evaluate(self._cond("reveal"), world) is False

    def test_contains_against_a_non_collection_raises(self, world):
        """Fails loudly, per this module's stance: a silent False here would be an
        event whose trigger can never fire, with nothing in the logs to say why."""
        cond = Condition(
            clauses=[
                ConditionClause(
                    path=f"relationships.{NPC_A}.{PLAYER}.trust",
                    op=ConditionOp.CONTAINS,
                    value="reveal",
                )
            ]
        )
        with pytest.raises(TypeError):
            evaluate(cond, world)

    def test_a_string_at_the_path_is_not_treated_as_a_collection(self, world):
        """Substring matching is never what an author means by ``contains``.

        Same reasoning as ``in`` rejecting a string value: "npc_b" containing "npc"
        would silently satisfy a trigger nobody wrote.
        """
        cond = Condition(
            clauses=[ConditionClause(path="world_id", op=ConditionOp.CONTAINS, value="vil")]
        )
        with pytest.raises(TypeError):
            evaluate(cond, world)
