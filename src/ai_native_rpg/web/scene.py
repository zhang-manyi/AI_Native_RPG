"""The player's view of the scene (docs/12 §4.2).

Everything here is built from ``VisibleState`` plus *static public* pack data. The
type signatures are the enforcement: no function in this module accepts a
``WorldState``, and the module does not import one (docs/07 §2.4, docs/12 §7 item 2).
A test asserts the absence of that import, because "remember not to pass the world in
here" is not a mechanism.

``PackDisplay`` exists for that reason. Location names, an NPC's public name and the
intro text are objective, externally observable facts that never change during play —
anyone in the village knows what the midwife is called. Extracting them once at
assembly means the render path needs no access to the world at all, rather than being
handed the world and trusted to read only the safe fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..schemas.world_state import VisibleState

#: Built-in sprite assets, assigned by order of appearance in the pack.
#:
#: A front-end asset name, deliberately not pack content: a scenario ships no art and
#: must not need to. A pack wanting its own portraits is a later, additive change (an
#: optional ``sprite:`` field), and nothing here hardcodes a story's characters.
SPRITE_KEYS = ("sprite_a", "sprite_b", "sprite_c", "sprite_d")


class LocationDisplay(BaseModel):
    location_id: str
    name: str
    description: str = ""
    backdrop: str = Field(
        default="",
        description="place *kind* from the pack ('interior', 'forest', …); the front end "
        "decides what it looks like. Blank renders neutrally.",
    )


class NpcDisplay(BaseModel):
    npc_id: str
    name: str = Field(description="public display name, from NPCWorldState.name")
    note: str = Field(
        default="",
        description="one ungated line about this character, from NPCWorldState.public_note. "
        "Never sourced from persona.background, which continues into what the player has "
        "yet to earn.",
    )
    sprite_key: str


class PackDisplay(BaseModel):
    """Static, public presentation data lifted out of the pack once.

    Not world state: none of it is gated, none of it changes as the story moves.
    Holding it separately is what lets ``build_scene`` be typed against
    ``VisibleState`` alone.
    """

    locations: dict[str, LocationDisplay] = Field(default_factory=dict)
    npcs: dict[str, NpcDisplay] = Field(default_factory=dict)
    intro: dict[str, str] = Field(default_factory=dict)


class SceneLine(BaseModel):
    speaker: str = Field(description="'player' or an npc id")
    name: str = ""
    text: str


class SceneView(BaseModel):
    """What the player can see. The only shape the player-side routes return.

    Contains no hidden fact values by construction: ``visible_facts`` comes straight
    from ``PlayerView``'s projection, which fails closed on anything not positively
    established as visible.
    """

    turn: int
    time_day: int
    location: LocationDisplay | None = None
    npcs: list[NpcDisplay] = Field(default_factory=list)
    visible_facts: list[dict[str, Any]] = Field(default_factory=list)
    quest_stages: dict[str, int] = Field(default_factory=dict)
    intro: dict[str, str] = Field(default_factory=dict)
    transcript_tail: list[SceneLine] = Field(default_factory=list)


def build_pack_display(*, locations, npcs) -> PackDisplay:
    """Lift the public display table out of a freshly loaded pack.

    Takes the two mappings rather than the world, so this stays callable without a
    ``WorldState`` in scope. ``locations`` and ``npcs`` are
    ``WorldState.locations``/``.npcs``; only their public fields are read.
    """
    sprites = {}
    for index, npc_id in enumerate(npcs):
        sprites[npc_id] = SPRITE_KEYS[index % len(SPRITE_KEYS)]

    return PackDisplay(
        locations={
            loc_id: LocationDisplay(
                location_id=loc_id,
                name=loc.name,
                description=loc.description,
                # getattr, not attribute access: a world loaded from a snapshot saved
                # before these fields existed must still render.
                backdrop=getattr(loc, "backdrop", ""),
            )
            for loc_id, loc in locations.items()
        },
        npcs={
            npc_id: NpcDisplay(
                npc_id=npc_id,
                name=npc.display_name,
                note=getattr(npc, "public_note", ""),
                sprite_key=sprites[npc_id],
            )
            for npc_id, npc in npcs.items()
        },
    )


def build_scene(
    *,
    view: VisibleState,
    display: PackDisplay,
    turn: int,
    intro: dict[str, str] | None = None,
    transcript: list[dict[str, str]] | None = None,
) -> SceneView:
    """Project the player's visible state into something renderable.

    Only NPCs ``PlayerView`` reports as co-located appear: presence is already a
    visibility decision made upstream, and re-deriving it here would be a second
    place for it to be wrong.
    """
    npcs = [display.npcs[npc_id] for npc_id in view.known_npc_locations if npc_id in display.npcs]

    lines = []
    for entry in transcript or []:
        speaker = entry.get("speaker", "")
        name = "" if speaker == "player" else _name_of(display, speaker)
        lines.append(SceneLine(speaker=speaker, name=name, text=entry.get("text", "")))

    location = display.locations.get(view.current_location) if view.current_location else None

    return SceneView(
        turn=turn,
        time_day=view.time_day,
        location=location,
        npcs=npcs,
        # A list of {id, value} rather than a mapping: the page renders these in
        # order, and the pack's authoring order is the readable one.
        visible_facts=[{"id": k, "value": v} for k, v in view.visible_facts.items()],
        quest_stages=dict(view.quest_stages),
        intro=dict(intro or {}),
        transcript_tail=lines,
    )


def _name_of(display: PackDisplay, npc_id: str) -> str:
    npc = display.npcs.get(npc_id)
    return npc.name if npc is not None else npc_id
