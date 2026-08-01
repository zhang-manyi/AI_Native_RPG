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
from pydantic import ValidationError

from .schemas.common import Condition
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


def load_scenario(name_or_path: str | Path) -> WorldState:
    """Load and validate a scenario pack into an initial ``WorldState``.

    Accepts either a pack name under ``scenarios/`` or a path to a pack directory.
    """
    world_file = _resolve_pack(name_or_path)

    try:
        raw = yaml.safe_load(world_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{world_file} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScenarioError(f"{world_file} must contain a mapping at the top level")

    try:
        world = _build_world(raw)
    except ValidationError as exc:
        raise ScenarioError(f"{world_file} does not match the world schema: {exc}") from exc

    _validate_world(world)
    return world
