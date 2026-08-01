"""World State schema. Single Source of Truth for the game world.
See docs/04_World_State_Manager.md for design rationale."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import Condition, utc_now


class Visibility(str, Enum):
    HIDDEN = "hidden"
    PARTIAL = "partial"
    REVEALED = "revealed"


class Fact(BaseModel):
    """A world fact, possibly not visible to the player."""

    fact_id: str
    value: Any
    visibility: Visibility = Visibility.REVEALED
    reveal_condition: Condition | None = Field(
        default=None,
        description="structured condition evaluated against WorldState; None = never auto-reveals",
    )
    partial_value: Any | None = Field(
        default=None,
        description="shown when visibility=partial, e.g. 'there is a suspect' without the name",
    )


class NPCWorldState(BaseModel):
    """Objective, externally observable NPC data.

    Persona/goal/emotion/beliefs live in ``npc_agent.py`` — this is "where the NPC
    is", not "what the NPC thinks". Relationship values are *not* here either;
    they are ``WorldState.relationships`` because they are (npc, target) pairs
    read by the Validator, the Narrative Engine and the Agent alike.
    See docs/04_World_State_Manager.md#21.
    """

    npc_id: str
    alive: bool = True
    location: str
    faction_id: str | None = None


class RelationshipState(BaseModel):
    """Quantified relationship from one character toward another.

    Source of truth lives here in WorldState; ``memory.RelationshipMemory`` is a
    read-only projection of it.
    """

    trust: float = Field(default=0.0, ge=-100, le=100)
    fear: float = Field(default=0.0, ge=-100, le=100)
    respect: float = Field(default=0.0, ge=-100, le=100)
    last_updated: datetime = Field(default_factory=utc_now)


class FactionState(BaseModel):
    faction_id: str
    power: float = 0.5
    stability: float = 0.5
    relationships: dict[str, float] = Field(
        default_factory=dict, description="faction_id -> relationship score [-1, 1]"
    )


class QuestState(BaseModel):
    quest_id: str
    stage: int = 0
    status: Literal["not_started", "active", "completed", "failed"] = "not_started"


class Location(BaseModel):
    location_id: str
    name: str
    description: str = ""
    connected_to: list[str] = Field(default_factory=list)


class WorldState(BaseModel):
    """Reality Layer: the complete set of facts, the only source of truth.

    Mutated exclusively through ``WorldStateManager.submit_proposal`` — readers
    receive deep copies so that no Agent can mutate this in place.
    """

    world_id: str
    time_day: int = 0

    npcs: dict[str, NPCWorldState] = Field(default_factory=dict)
    factions: dict[str, FactionState] = Field(default_factory=dict)
    quests: dict[str, QuestState] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)

    relationships: dict[str, dict[str, RelationshipState]] = Field(
        default_factory=dict,
        description="npc_id -> target_id -> RelationshipState; directional, not symmetric",
    )

    player_locations: dict[str, str] = Field(
        default_factory=dict, description="player_id -> location_id"
    )

    last_updated: datetime = Field(default_factory=utc_now)


class VisibleState(BaseModel):
    """Output of ``PlayerView()``: the subset of the world a player can see."""

    player_id: str
    time_day: int
    visible_facts: dict[str, Any] = Field(
        default_factory=dict, description="fact_id -> value (or partial_value)"
    )
    known_npc_locations: dict[str, str] = Field(default_factory=dict)
    current_location: str | None = None
    quest_stages: dict[str, int] = Field(default_factory=dict)


class ActionProposal(BaseModel):
    """An Agent's request to change the world. Never written directly."""

    proposal_id: str
    actor_id: str
    action_type: str
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class ActionValidationResult(BaseModel):
    proposal_id: str
    approved: bool
    reason: str | None = Field(
        default=None,
        description="why it was rejected; fed into Dialogue Generation as a constraint "
        "so the NPC deflects in character instead of leaking a fact it may not reveal",
    )
    applied_changes: dict[str, Any] | None = None
    rule_name: str | None = Field(default=None, description="which rule rejected it")
