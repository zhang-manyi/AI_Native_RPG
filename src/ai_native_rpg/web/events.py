"""Server-sent events: the one channel everything renderable travels on (docs/12 §3).

``POST /turn`` returns ``202`` and no dialogue. The line, the narrative tick and the
panel refreshes all arrive here instead, so the front end has a single render entry
point and one ordered log. "the line is here", "the tick is here 8 seconds later"
and "the panels refreshed" become three events of one kind rather than an HTTP
response plus two pushes.

Framing is hand-rolled rather than pulled from ``sse-starlette``: the whole of the
wire format is an ``id:``/``event:``/``data:`` triple, a blank line, and a comment
line for keepalive. One fewer dependency is worth thirty lines.

Two event kinds (``narrative_tick``, ``panel``) are developer-only and are filtered
**server-side per connection** (§7). Hiding them in the front end would make F12 a
complete spoiler tool.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..observability.panels import OperatorEntry, PanelView, RejectedProposal, TurnCost


class EventType(str, Enum):
    """Every event the stream can carry. See docs/12 §3.3 for the table."""

    HELLO = "hello"
    TURN_ACCEPTED = "turn_accepted"
    DIALOGUE = "dialogue"
    NARRATIVE_TICK = "narrative_tick"
    PANEL = "panel"
    SCENE = "scene"
    TURN_FAILED = "turn_failed"


#: Events a non-developer connection may receive. The complement of this set is
#: what ``DEV_ONLY_EVENTS`` withholds, and one test asserts a plain stream carries
#: nothing outside it (docs/12 §7 item 5).
PLAYER_EVENTS = frozenset(
    {
        EventType.HELLO,
        EventType.TURN_ACCEPTED,
        EventType.DIALOGUE,
        EventType.SCENE,
        EventType.TURN_FAILED,
    }
)

#: Events that carry the full world, and therefore never reach a player.
DEV_ONLY_EVENTS = frozenset({EventType.NARRATIVE_TICK, EventType.PANEL})

#: Seconds of silence after which a comment line is sent to hold the connection
#: open. Comfortably under the 60s that proxies and browsers tend to assume.
KEEPALIVE_SECONDS = 15.0


class DialoguePayload(BaseModel):
    """The NPC's line, ready to render. Carries no hidden state.

    ``trust_before``/``trust_after`` are the one relationship figure the player-side
    payload includes; they are already visible to the player in the sense that the
    NPC's warmth is what they are reading, and the terminal demo printed them too.
    Gated behind dev mode anyway by the caller when the panel is off.
    """

    turn_id: str
    turn: int
    npc_id: str
    name: str = Field(description="public display name, from NPCWorldState.name")
    dialogue: str
    strategy: str = ""
    trust_before: float | None = None
    trust_after: float | None = None
    latency_ms: float = 0.0
    used_pending_hook: bool = Field(
        default=False,
        description="whether this turn was handed content the Engine generated last turn",
    )


class TurnAcceptedPayload(BaseModel):
    """Echo of what the player said, so the front end need not insert it optimistically."""

    turn_id: str
    text: str
    turn: int


class NarrativeTickPayload(BaseModel):
    """The narrative decision for a turn — developer-only.

    Wraps ``OperatorEntry`` (the timeline row) plus the proposals the Validator
    refused, which docs/07 §2.3 calls the most under-rated block: it is direct
    evidence that a deterministic layer stopped the model.
    """

    turn_id: str
    entry: OperatorEntry
    rejected_proposals: list[RejectedProposal] = Field(default_factory=list)


class TurnFailedPayload(BaseModel):
    """A stage of the turn raised.

    ``stage`` matters: a failed ``dialogue`` means this turn is lost and the player
    should say something again, while a failed ``tick`` only means the *next* turn
    gets no setup. The terminal demo drew the same distinction with two ``except``
    blocks, and flattening it here would lose it.
    """

    turn_id: str
    stage: Literal["dialogue", "tick"]
    error_type: str
    message: str


class HelloPayload(BaseModel):
    """First frame: everything needed to render before any turn happens."""

    session_id: str
    dev_mode: bool
    turn: int
    scene: dict[str, Any]
    backend: dict[str, str] = Field(
        default_factory=dict,
        description="provider/model/embedder/prompt source/tools — the header the "
        "terminal demo printed. 'why is it slow' is usually answered by which "
        "backend answered.",
    )
    panel: PanelView | None = None
    last_turn_cost: TurnCost | None = None


class Event(BaseModel):
    """One frame on the stream.

    ``seq`` is the SSE ``id:``, monotonic per session, which is what makes
    ``Last-Event-ID`` replay possible after a reconnect.
    """

    seq: int
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def dev_only(self) -> bool:
        return self.type in DEV_ONLY_EVENTS

    def frame(self) -> str:
        """Serialise to the SSE wire format.

        ``ensure_ascii=False`` keeps Chinese readable in ``curl -N`` output, which is
        the debugging path docs/12 §3.1 chose SSE partly to preserve. Newlines inside
        the payload are impossible because ``json.dumps`` escapes them, so a single
        ``data:`` line is always enough.
        """
        body = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"id: {self.seq}\nevent: {self.type.value}\ndata: {body}\n\n"


def keepalive_frame() -> str:
    """A comment line. Holds the connection without entering the event log."""
    return ": ping\n\n"
