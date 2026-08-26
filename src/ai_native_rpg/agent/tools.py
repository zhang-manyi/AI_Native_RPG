"""The NPC's tool set (docs/06 §2 Tools): deterministic functions the model may
call during Planning, plus the registry that validates and dispatches them.

A tool is a hole in the world's information asymmetry. Whatever it returns lands
in the model's context, and anything in context can eventually be talked out of
the model — no prompt instruction reliably prevents that. So the read surface is
drawn narrowly:

  * ``query_relationship`` — trust/fear/respect, whose source of truth is the
    world (docs/04 §2.1), never the NPC's memory.
  * ``query_memory`` — the NPC's *own* episodic/semantic memory, which is where
    private knowledge of hidden matters lives (and may be mistaken: memory is the
    NPC's belief, not ground truth).
  * ``check_public_fact`` — only facts already visible to the player. An
    undisclosed fact such as ``loren_that_night`` therefore never enters a prompt
    at all, instead of entering it and relying on the Validator to catch the leak
    after the model has already seen it.

There is deliberately no ``propose_action`` tool, though docs/06 lists one: that
draft predates the ordering conclusion in docs/02 §4.1. Actions travel as
``PlanningOutput.action`` so validation always precedes dialogue; a tool would be
a second write path able to bypass that.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..world.manager import WorldStateManager
from .memory_store import MemoryStore

DEFAULT_TOP_K = 3


class ToolError(RuntimeError):
    """A tool call could not be executed as requested.

    Rendered short on purpose: the Harness hands the message back to the model as
    the tool result, so a bad call costs one extra iteration rather than failing
    the player's turn.
    """


# --- argument schemas ------------------------------------------------------
# Pydantic models double as validation and as the JSON Schema sent to the model.
# ``extra="forbid"`` matters: a model inventing arguments is drifting, and
# silently dropping them would hide that instead of correcting it.


class _StrictArgs(BaseModel):
    model_config = {"extra": "forbid"}


class QueryRelationshipArgs(_StrictArgs):
    target_id: str = Field(description="character id to look up, e.g. the player's id")


class QueryMemoryArgs(_StrictArgs):
    query: str = Field(description="what to recall, in natural language")
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=10)


class CheckPublicFactArgs(_StrictArgs):
    fact_id: str = Field(description="id of a fact that is public knowledge in the village")


class _Tool(BaseModel):
    """One registered tool: its declared schema plus the function that runs it."""

    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[BaseModel], dict[str, Any]]

    model_config = {"arbitrary_types_allowed": True}

    def spec(self) -> dict[str, Any]:
        """The OpenAI-compatible ``tools`` entry for this tool."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    """Name -> tool lookup with argument validation and dispatch."""

    def __init__(self, tools: list[_Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """Tool declarations to pass as the ``tools`` request parameter."""
        return [tool.spec() for tool in self._tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate ``arguments`` and run the tool, or raise ``ToolError``."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}; available: {', '.join(self._tools)}")
        try:
            parsed = tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '<args>'}: {err['msg']}"
                for err in exc.errors()[:3]
            )
            raise ToolError(f"invalid arguments for {name!r}: {problems}") from exc
        return tool.run(parsed)


def build_npc_tools(
    *,
    npc_id: str,
    manager: WorldStateManager,
    memory: MemoryStore,
    player_id: str,
) -> ToolRegistry:
    """Build the tool set for one NPC, closed over its world and memory.

    Binding ``npc_id`` here rather than accepting it as a tool argument means the
    model cannot ask about another NPC's relationships or memories: identity is
    the Harness's to assert, as with ``ActionProposal.actor_id``.
    """

    def query_relationship(args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, QueryRelationshipArgs)
        rel = manager.get_relationship(npc_id, args.target_id)
        return {
            "target_id": args.target_id,
            "trust": rel.trust,
            "fear": rel.fear,
            "respect": rel.respect,
        }

    def query_memory(args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, QueryMemoryArgs)
        result = memory.retrieve(args.query, top_k=args.top_k)
        return {
            "episodic": [m.event_description for m in result.episodic],
            "semantic": [m.fact for m in result.semantic],
        }

    def check_public_fact(args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, CheckPublicFactArgs)
        # Reuse player_view so "public" means exactly what the player can see,
        # including facts turned visible by a satisfied reveal_condition. Partial
        # visibility is a player-facing teaser, not public knowledge, so it is
        # excluded by comparing against the fact's full value.
        visible = manager.player_view(player_id).visible_facts
        fact = manager.snapshot().facts.get(args.fact_id)
        if fact is None or args.fact_id not in visible:
            # An unknown id and a hidden fact answer identically: distinguishing
            # them would itself tell the model which secrets exist.
            return {"fact_id": args.fact_id, "known": False}
        if visible[args.fact_id] != fact.value:
            return {"fact_id": args.fact_id, "known": False}
        return {"fact_id": args.fact_id, "known": True, "value": fact.value}

    return ToolRegistry(
        [
            _Tool(
                name="query_relationship",
                description=(
                    "Look up how you currently feel about someone: trust, fear and "
                    "respect, each roughly -100 to 100."
                ),
                args_model=QueryRelationshipArgs,
                run=query_relationship,
            ),
            _Tool(
                name="query_memory",
                description=(
                    "Search your own memories for something you personally "
                    "experienced or believe. May include things only you know."
                ),
                args_model=QueryMemoryArgs,
                run=query_memory,
            ),
            _Tool(
                name="check_public_fact",
                description=(
                    "Check a fact that is common knowledge in the village. Returns "
                    "known=false for anything not yet public — including secrets you "
                    "may personally know, which you should recall with query_memory "
                    "instead."
                ),
                args_model=CheckPublicFactArgs,
                run=check_public_fact,
            ),
        ]
    )
