"""Evaluator for structured ``Condition`` objects against a ``WorldState``.

Replaces string expressions like ``"player.trust[NPC_A] > 60 OR stage >= 3"``.
Rationale in docs/04_World_State_Manager.md#32: a parser is over-engineering and
``eval()`` on scenario data is a code-execution hole, while these seven operators
cover every condition the reference scenario needs.

Design stance: this module fails loudly. A bad path or a mismatched type raises
instead of returning False, because the failure mode of a silent False is a clue
that can never be revealed with nothing in the logs to explain why.
"""

from __future__ import annotations

from collections.abc import Container, Sized
from typing import Any

from pydantic import BaseModel

from ..schemas.common import Condition, ConditionClause, ConditionOp
from ..schemas.world_state import WorldState


class UnknownPathError(KeyError):
    """A condition referenced a path that does not exist in the world state."""


def resolve_path(state: WorldState, path: str) -> Any:
    """Resolve a dotted path such as ``relationships.npc_a.player_1.trust``.

    Traverses Pydantic model fields and dict keys uniformly, so scenario authors
    do not need to know which is which.
    """
    if not path:
        raise UnknownPathError("empty condition path")

    current: Any = state
    walked: list[str] = []

    for part in path.split("."):
        walked.append(part)
        if isinstance(current, BaseModel):
            if part not in type(current).model_fields:
                raise UnknownPathError(
                    f"{'.'.join(walked)!r} does not exist on {type(current).__name__}"
                )
            current = getattr(current, part)
        elif isinstance(current, dict):
            if part not in current:
                raise UnknownPathError(f"key {part!r} missing at {'.'.join(walked[:-1]) or 'root'}")
            current = current[part]
        else:
            raise UnknownPathError(
                f"cannot traverse into {type(current).__name__} at {'.'.join(walked)!r}"
            )

    return current


_ORDERED_OPS = {ConditionOp.GT, ConditionOp.GTE, ConditionOp.LT, ConditionOp.LTE}


def _compare(actual: Any, op: ConditionOp, expected: Any) -> bool:
    if op is ConditionOp.EQ:
        return bool(actual == expected)
    if op is ConditionOp.NE:
        return bool(actual != expected)

    if op is ConditionOp.IN:
        # str is a Container but membership means substring, which is never what a
        # scenario author means by `in`; require a real collection.
        if isinstance(expected, str) or not isinstance(expected, Container | Sized):
            raise TypeError(f"op 'in' needs a collection value, got {type(expected).__name__}")
        return actual in expected

    if op is ConditionOp.CONTAINS:
        # Mirror image of `in`, so the same guard applies to the other operand: a
        # string at the path would match substrings, letting "npc_b" satisfy a
        # trigger written for "npc".
        if isinstance(actual, str) or not isinstance(actual, Container | Sized):
            raise TypeError(
                f"op 'contains' needs a collection at the path, got {type(actual).__name__}"
            )
        return expected in actual

    if op in _ORDERED_OPS:
        # bool is an int subclass, so guard it out of numeric comparisons.
        numeric = (int, float)
        if isinstance(actual, bool) or isinstance(expected, bool):
            raise TypeError(f"cannot order-compare booleans: {actual!r} {op.value} {expected!r}")
        if not (isinstance(actual, numeric) and isinstance(expected, numeric)):
            raise TypeError(
                f"cannot compare {type(actual).__name__} with {type(expected).__name__} "
                f"using {op.value!r}"
            )
        if op is ConditionOp.GT:
            return actual > expected
        if op is ConditionOp.GTE:
            return actual >= expected
        if op is ConditionOp.LT:
            return actual < expected
        return actual <= expected

    raise ValueError(f"unsupported operator {op!r}")


def evaluate_clause(clause: ConditionClause, state: WorldState) -> bool:
    return _compare(resolve_path(state, clause.path), clause.op, clause.value)


def clause_holds_for(actual: Any, clause: ConditionClause) -> bool:
    """Whether ``actual`` satisfies ``clause``, reporting False instead of raising.

    The loud-failure stance above is right for gameplay: a silent False becomes a
    clue that never unlocks with nothing in the logs. It is wrong for the unlock
    progress board, which shows every gated fact at once — there, one malformed
    clause raising would hide the twenty well-formed rows around it. So the
    tolerant variant is separate and named, rather than a flag on the strict path.
    """
    try:
        return _compare(actual, clause.op, clause.value)
    except (TypeError, ValueError):
        return False


def evaluate(condition: Condition, state: WorldState) -> bool:
    """Evaluate a condition. An empty clause list is always False.

    Note the deliberate divergence from vacuous truth for ``mode="all"``: an empty
    ``all`` would classically be True, which here would reveal every hidden fact
    that forgot to declare a condition. Failing closed is the safer default for an
    information-asymmetry mechanism.
    """
    if not condition.clauses:
        return False

    results = (evaluate_clause(c, state) for c in condition.clauses)
    return any(results) if condition.mode == "any" else all(results)
