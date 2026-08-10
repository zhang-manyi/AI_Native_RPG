"""Scenario pack loading: story setup as data, not code.

Story content (locations, NPCs, initial facts and visibility, starting trust,
quests) lives in ``scenarios/<name>/world.yaml``. Trigger rules stay in Python and
runtime content comes from the LLM — see docs/09_Reference_Scenario.md.

The loader validates aggressively because scenario files are hand-written and
their failure modes are silent. A mistyped condition path would otherwise produce
a clue that can never unlock, with no error anywhere to explain why; catching it
at load turns a mystifying content bug into a startup message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from .agent.memory_store import MemoryStore
from .schemas.common import Condition
from .schemas.memory import EpisodicMemory, SemanticMemory
from .schemas.npc_agent import NPCGoal, NPCPersona, NPCState
from .schemas.world_state import (
    Fact,
    FactionState,
    Location,
    NPCWorldState,
    QuestState,
    RelationshipState,
    Visibility,
    WorldState,
)
from .world.conditions import UnknownPathError, resolve_path

SCENARIOS_ROOT = Path(__file__).resolve().parents[2] / "scenarios"


class ScenarioError(ValueError):
    """A scenario pack is missing, malformed, or internally inconsistent."""


def _resolve_pack(name_or_path: str | Path) -> Path:
    candidate = Path(name_or_path)
    is_direct_path = candidate.is_absolute() or candidate.exists()
    pack = candidate if is_direct_path else SCENARIOS_ROOT / candidate
    world_file = pack / "world.yaml"
    if not world_file.is_file():
        raise ScenarioError(f"scenario pack not found: no world.yaml at {pack}")
    return world_file


def _build_world(raw: dict[str, Any]) -> WorldState:
    """Map the YAML shape onto the schema.

    The YAML nests entities under id keys (``npcs: {npc_a: {...}}``) while the
    schema stores the id on the model, so ids are injected here. This keeps the
    authoring format free of repeated ids.
    """
    facts = {}
    for fact_id, spec in (raw.get("facts") or {}).items():
        condition = spec.get("reveal_condition")
        facts[fact_id] = Fact(
            fact_id=fact_id,
            value=spec.get("value"),
            visibility=Visibility(spec.get("visibility", "revealed")),
            partial_value=spec.get("partial_value"),
            reveal_condition=Condition.model_validate(condition) if condition else None,
        )

    relationships: dict[str, dict[str, RelationshipState]] = {}
    for npc_id, targets in (raw.get("relationships") or {}).items():
        relationships[npc_id] = {
            target_id: RelationshipState.model_validate(values)
            for target_id, values in (targets or {}).items()
        }

    return WorldState(
        world_id=raw.get("world_id", "unnamed_world"),
        time_day=raw.get("time_day", 0),
        locations={
            loc_id: Location(
                location_id=loc_id,
                name=spec.get("name", loc_id),
                description=spec.get("description", ""),
                connected_to=list(spec.get("connected_to") or []),
            )
            for loc_id, spec in (raw.get("locations") or {}).items()
        },
        npcs={
            npc_id: NPCWorldState(
                npc_id=npc_id,
                location=spec.get("location", ""),
                alive=spec.get("alive", True),
                faction_id=spec.get("faction_id"),
            )
            for npc_id, spec in (raw.get("npcs") or {}).items()
        },
        factions={
            f_id: FactionState(
                faction_id=f_id,
                power=spec.get("power", 0.5),
                stability=spec.get("stability", 0.5),
                relationships=dict(spec.get("relationships") or {}),
            )
            for f_id, spec in (raw.get("factions") or {}).items()
        },
        quests={
            q_id: QuestState(
                quest_id=q_id,
                stage=spec.get("stage", 0),
                status=spec.get("status", "not_started"),
            )
            for q_id, spec in (raw.get("quests") or {}).items()
        },
        facts=facts,
        relationships=relationships,
        player_locations={
            p_id: spec.get("location", "") for p_id, spec in (raw.get("players") or {}).items()
        },
    )


def _validate_world(world: WorldState) -> None:
    """Cross-reference checks the schema alone cannot express."""
    known_locations = set(world.locations)

    for loc in world.locations.values():
        for neighbour in loc.connected_to:
            if neighbour not in known_locations:
                raise ScenarioError(
                    f"location {loc.location_id!r} connects to unknown location {neighbour!r}"
                )

    for npc in world.npcs.values():
        if npc.location not in known_locations:
            raise ScenarioError(f"npc {npc.npc_id!r} starts in unknown location {npc.location!r}")

    for player_id, location in world.player_locations.items():
        if location not in known_locations:
            raise ScenarioError(f"player {player_id!r} starts in unknown location {location!r}")

    for npc in world.npcs.values():
        if npc.faction_id is not None and npc.faction_id not in world.factions:
            raise ScenarioError(f"npc {npc.npc_id!r} belongs to unknown faction {npc.faction_id!r}")

    for fact in world.facts.values():
        if fact.visibility is Visibility.PARTIAL and fact.partial_value is None:
            raise ScenarioError(
                f"fact {fact.fact_id!r} is partial but has no partial_value, so it would "
                "silently render as invisible"
            )
        if fact.reveal_condition is None:
            continue
        for clause in fact.reveal_condition.clauses:
            # Resolve against the freshly built world: the path must address
            # something that actually exists, or the clue can never unlock.
            try:
                resolve_path(world, clause.path)
            except UnknownPathError as exc:
                raise ScenarioError(
                    f"fact {fact.fact_id!r} has a reveal_condition referencing an unresolvable "
                    f"path {clause.path!r}: {exc}"
                ) from exc


def _read_pack(name_or_path: str | Path) -> tuple[Path, dict[str, Any]]:
    """Resolve a pack and parse its ``world.yaml`` into a raw mapping.

    Shared by ``load_scenario`` and ``load_personas`` so both read the same file
    through the same error handling.
    """
    world_file = _resolve_pack(name_or_path)

    try:
        raw = yaml.safe_load(world_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{world_file} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScenarioError(f"{world_file} must contain a mapping at the top level")

    return world_file, raw


def load_scenario(name_or_path: str | Path) -> WorldState:
    """Load and validate a scenario pack into an initial ``WorldState``.

    Accepts either a pack name under ``scenarios/`` or a path to a pack directory.
    """
    world_file, raw = _read_pack(name_or_path)

    try:
        world = _build_world(raw)
    except ValidationError as exc:
        raise ScenarioError(f"{world_file} does not match the world schema: {exc}") from exc

    _validate_world(world)
    return world


def load_personas(name_or_path: str | Path) -> dict[str, NPCState]:
    """Load the agent-internal ``NPCState`` for each NPC from ``npc_personas``.

    Kept separate from ``load_scenario`` on purpose: persona / goal / emotion /
    beliefs are Agent state, not objective world state, and must not live in
    ``WorldState`` (docs/04 §2.1, docs/06 §2). Every persona's ``npc_id`` is
    cross-checked against the world's ``npcs`` so a persona for a non-existent NPC
    — or an NPC left without a persona — surfaces at load, not as a mystifying
    KeyError deep in the Harness.
    """
    world_file, raw = _read_pack(name_or_path)

    world_npcs = set((raw.get("npcs") or {}).keys())
    persona_specs = raw.get("npc_personas") or {}

    personas: dict[str, NPCState] = {}
    for npc_id, spec in persona_specs.items():
        if npc_id not in world_npcs:
            raise ScenarioError(
                f"{world_file}: npc_personas has {npc_id!r}, which is not an NPC in the world"
            )
        persona_spec = spec.get("persona") or {}
        goal_spec = spec.get("goal") or {}
        try:
            personas[npc_id] = NPCState(
                npc_id=npc_id,
                persona=NPCPersona(
                    traits=dict(persona_spec.get("traits") or {}),
                    background=persona_spec.get("background", ""),
                ),
                goal=NPCGoal(
                    primary=goal_spec.get("primary", ""),
                    secondary=list(goal_spec.get("secondary") or []),
                ),
                emotion=spec.get("emotion", "neutral"),
                beliefs=dict(spec.get("beliefs") or {}),
            )
        except ValidationError as exc:
            raise ScenarioError(
                f"{world_file}: persona for {npc_id!r} does not match the schema: {exc}"
            ) from exc

    missing = world_npcs - set(personas)
    if missing:
        raise ScenarioError(
            f"{world_file}: NPCs {sorted(missing)} have no persona in npc_personas; "
            "the Agent Harness needs an NPCState for every NPC it may speak as"
        )

    return personas


class SeedMemories(BaseModel):
    """What an NPC already remembered before the player showed up.

    Agent-private state, so it loads alongside personas and never enters
    ``WorldState``. Without seeds a fresh NPC has nothing to recall and
    ``query_memory`` is dead weight on turn one; with them, retrieval has the
    NPC's own private knowledge to find — knowledge that is *belief*, and so may
    be mistaken, unlike a world fact.
    """

    episodic: list[EpisodicMemory] = Field(default_factory=list)
    semantic: list[SemanticMemory] = Field(default_factory=list)

    def load_into(self, store: MemoryStore) -> None:
        """Populate a store. Raises if the store belongs to another NPC."""
        for memory in self.episodic:
            store.add_episodic(memory)
        for memory in self.semantic:
            store.add_semantic(memory)


def load_seed_memories(name_or_path: str | Path) -> dict[str, SeedMemories]:
    """Load each NPC's starting memories from the pack's ``npc_seed_memories``.

    Unlike personas, memories are optional — a bystander NPC may genuinely have
    nothing relevant to recall — so every NPC in the world gets an entry, empty if
    the pack does not mention it. Ids are checked for uniqueness because a
    duplicate would make ``forget``/``update_semantic`` act on an arbitrary one of
    two memories, a bug that would surface far from its cause.
    """
    world_file, raw = _read_pack(name_or_path)

    world_npcs = set((raw.get("npcs") or {}).keys())
    specs = raw.get("npc_seed_memories") or {}

    seeds: dict[str, SeedMemories] = {npc_id: SeedMemories() for npc_id in world_npcs}
    for npc_id, spec in specs.items():
        if npc_id not in world_npcs:
            raise ScenarioError(
                f"{world_file}: npc_seed_memories has {npc_id!r}, which is not an NPC in the world"
            )

        day = int(raw.get("time_day", 1) or 1)
        try:
            episodic = [
                EpisodicMemory(
                    memory_id=item["id"],
                    npc_id=npc_id,
                    event_description=item["description"],
                    importance=item.get("importance", 0.5),
                    emotion=item.get("emotion"),
                    # Seeds predate play; default them to the world's start day.
                    occurred_at_day=item.get("occurred_at_day", day),
                )
                for item in spec.get("episodic") or []
            ]
            semantic = [
                SemanticMemory(
                    memory_id=item["id"],
                    npc_id=npc_id,
                    fact=item["fact"],
                    confidence=item.get("confidence", 0.7),
                )
                for item in spec.get("semantic") or []
            ]
        except KeyError as exc:
            raise ScenarioError(
                f"{world_file}: a seed memory for {npc_id!r} is missing field {exc}"
            ) from exc
        except ValidationError as exc:
            raise ScenarioError(
                f"{world_file}: seed memories for {npc_id!r} do not match the schema: {exc}"
            ) from exc

        ids = [m.memory_id for m in [*episodic, *semantic]]
        duplicates = sorted({mid for mid in ids if ids.count(mid) > 1})
        if duplicates:
            raise ScenarioError(
                f"{world_file}: seed memories for {npc_id!r} reuse id(s) {duplicates}; "
                "ids must be unique so forget/update act on exactly one memory"
            )

        seeds[npc_id] = SeedMemories(episodic=episodic, semantic=semantic)

    return seeds
