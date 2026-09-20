"""World State schema. Single Source of Truth for the game world.
See docs/04_World_State_Manager.md for design rationale."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import Condition, utc_now
from .narrative import StoryBeats, TimeSlot


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
    name: str = Field(
        default="",
        description="public display name, e.g. '玛尔塔'. Objective and externally "
        "observable, exactly like Location.name — anyone in the village knows what "
        "the midwife is called. Distinct from persona.background, which opens with "
        "the same name but continues into things the player has not earned. Blank "
        "falls back to npc_id.",
    )
    public_note: str = Field(
        default="",
        description="one line about this character that anyone would know on sight, "
        "e.g. '村里的接生婆，独自带大一个儿子'. Same nature as Location.description: "
        "objective, ungated, safe to show before the player has earned anything. "
        "Deliberately a separate authored field rather than a slice of "
        "persona.background — that text continues into what she saw that night, so "
        "excerpting it would leak the mystery by construction. Blank means show nothing.",
    )
    alive: bool = True
    location: str
    faction_id: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.npc_id


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
    backdrop: str = Field(
        default="",
        description="which kind of place this is, for a renderer to draw: 'interior', "
        "'forest', 'square', 'tavern'. A *type*, not art — the pack names the kind and "
        "the front end owns the pixels, so a scenario ships no assets. Unknown or blank "
        "values render as a neutral background rather than failing.",
    )
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

    story_beats: StoryBeats = Field(
        default_factory=StoryBeats,
        description="narrative progress; the only thing the Narrative Engine may advance "
        "(docs/04 §3.3). Defaulted so that worlds saved before slice 3 still load.",
    )

    last_updated: datetime = Field(default_factory=utc_now)


class VisibleState(BaseModel):
    """Output of ``PlayerView()``: the subset of the world a player can see."""

    player_id: str
    time_day: int
    time_slot: TimeSlot = Field(
        default=TimeSlot.MORNING,
        description="which slot of the day it is. Not hidden information — the cost of an "
        "action is only a cost if the player can see the clock (docs/13 §4.1), and a "
        "detective knows whether it is morning or dark out.",
    )
    visible_facts: dict[str, Any] = Field(
        default_factory=dict, description="fact_id -> value (or partial_value)"
    )
    known_npc_locations: dict[str, str] = Field(default_factory=dict)
    current_location: str | None = None
    reachable_locations: list[str] = Field(
        default_factory=list,
        description="where the player may go from here, i.e. the current location's "
        "``connected_to``. Projected rather than left to the caller so that adjacency has "
        "one copy: a front end deriving it would be a second rule free to drift from the "
        "Validator's (docs/12 §13.2).",
    )
    visited_locations: list[str] = Field(
        default_factory=list,
        description="places the player has already been, from ``story_beats.visited_locations``. "
        "Where he has walked is his own memory, so it is his to see — and it is the only "
        "record of it, never copied.",
    )
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
