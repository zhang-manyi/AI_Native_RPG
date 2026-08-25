"""Narrative state: operators, the foreshadowing ledger, story beats, candidates.

See docs/05_Narrative_Engine.md and docs/10_Narrative_Operators.md. Supersedes the
design-time draft in ``docs/schemas/narrative_event.py`` with four changes, each
forced by something the drafts left uncomputable:

* ``StoryBeats`` gains ``turn``. docs/04 §4.1 lists four fields, but a ledger
  entry stores ``planted_at_turn`` and the debug panel shows "owed 9 turns"
  (docs/07 §2.3) — neither is derivable from ``time_day``, which counts in-world
  days, not interactions.
* ``EventCandidate`` gains ``operator`` (docs/05 §4 requires it), plus
  ``constraints`` and ``pays_off``: the explicit "what you may not say this scene"
  list from docs/10 §2.3, and the ledger entry a reveal is settling.
* ``NarrativeEvent.reveal_timing`` (a free string) is replaced by the ledger's
  structured ``payoff_condition``, per docs/05 §2.3 — a string cannot be checked
  by an evaluator, so the ledger could never answer "is it time yet?".
* ``PlayerProfile`` is ported here from the draft ``player_model.py`` because the
  Experience Controller needs it now; ``PlayerRawStats`` and the Behavior Tracker
  stay unported until slice 4 has a writer for them.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .common import Condition, utc_now

#: How many turns of operator history the pacing rules can see. Five covers the
#: longest rule ("no two reveals in a row", "escalate needs a turn between")
#: with room to read a trend, and keeps the world snapshot small.
RECENT_OPERATOR_WINDOW = 5

#: Turns a foreshadowing may stay open before the panel flags it. Not enforced:
#: docs/10 §7 scopes the ledger to "record + overdue warning + visualise", and
#: auto-selecting a payoff moment is explicitly left for later.
DEFAULT_OVERDUE_AFTER_TURNS = 8


class NarrativeOperator(str, Enum):
    """The discrete dramatic moves the deterministic layer can schedule.

    Four implemented operators plus ``relieve``. ``relieve`` is not a fifth
    generator: docs/10 §2.1 defines it as "no operator fired this turn", and it
    exists here as a name so that quiet turns can be *recorded*. Without a name
    for them, ``recent_operators`` would hold only firings, and a reveal on turn 3
    would look adjacent to one on turn 9 — the pacing rules in docs/10 §3.1 all
    reason about adjacency, so they would silently misjudge.

    ``payoff`` is deliberately absent: it is the completed form of a ``reveal``
    (the reveal that settles a ledger entry), so keeping it out holds the operator
    table to the four of docs/10 §2.1 rather than inventing a fifth.
    """

    FORESHADOW = "foreshadow"
    REVEAL = "reveal"
    ESCALATE = "escalate"
    REVERSE = "reverse"
    RELIEVE = "relieve"


class Foreshadowing(BaseModel):
    """One open loop: something planted that owes the player a payoff.

    ``foreshadow``/``payoff`` is the only operator pair that must occur together,
    and the thing LLMs reliably fail at — they plant and then forget. Making the
    debt an explicit, countable record is what turns "did the story pay off?" from
    a question you answer by re-reading transcripts into one the system answers
    (docs/05 §4, docs/10 §4).
    """

    fact_id: str = Field(description="the hidden Fact this loop planted")
    planted_at_turn: int = Field(ge=0)
    payoff_condition: Condition = Field(
        description="structured, so an evaluator can answer 'is it time yet?'; a free-text "
        "timing note would leave that to a human reader (docs/05 §2.3)"
    )
    overdue_after_turns: int = Field(default=DEFAULT_OVERDUE_AFTER_TURNS, gt=0)
    note: str = Field(default="", description="human-readable label for the debug panel")

    def turns_owed(self, *, current_turn: int) -> int:
        return max(0, current_turn - self.planted_at_turn)

    def is_overdue(self, *, current_turn: int) -> bool:
        return self.turns_owed(current_turn=current_turn) > self.overdue_after_turns


class ActiveEvent(BaseModel):
    """The event currently in progress, and how far into it we are (docs/13 §3.2).

    The world had no way to say this before: turns were independent and
    ``pending_event`` carried a single hook that then vanished. An event spanning
    several exchanges has nowhere to live without it.

    ``max_exchanges`` is copied from the definition rather than looked up each turn,
    so the budget the event opened under is the budget it runs on. Re-reading the
    script mid-event would let a pack edit change the patience of a conversation
    already underway — and would make a resumed save behave differently from the
    session that wrote it.
    """

    event_id: str
    exchanges: int = Field(default=0, ge=0, description="player turns spent inside this event")
    max_exchanges: int = Field(gt=0)

    @property
    def is_out_of_patience(self) -> bool:
        """Whether the default outcome is now due (docs/13 §3.1).

        The limit lands the *authored* default outcome; it never asks the NPC to
        advance the plot. An NPC that volunteered the truth on line five because a
        counter tripped would be deciding an outcome, which changes what is unlocked
        — the thing docs/13 §3 forbids — and players notice it besides.
        """
        return self.exchanges >= self.max_exchanges


class StoryBeats(BaseModel):
    """Narrative progress: the only state the Narrative Engine may advance.

    docs/04 §3.3 is the load-bearing constraint: the Engine advances beats and the
    condition table decides what that unlocks, so the Engine never knows what it
    revealed. That is why ``chapter`` and ``tension`` are here rather than the
    Engine holding a "reveal this fact" power.

    The event-layer fields (``active_event``, ``completed_events``, ``closed_events``,
    ``flags``) all hang here for one reason: docs/13 §7 requires every new state to be
    a legal ``Condition`` path, and ``story_beats.*`` is already whitelisted. A new
    top-level field would have meant authors writing triggers against something the
    evaluator cannot reach — which is how the last two content bugs happened.
    """

    chapter: int = Field(default=1, ge=1)
    turn: int = Field(default=0, ge=0, description="player interactions so far, not in-world days")
    tension: float = Field(default=0.0, ge=0.0, le=1.0)

    open_foreshadowings: dict[str, Foreshadowing] = Field(
        default_factory=dict, description="fact_id -> entry; settled loops are removed"
    )
    planted_total: int = Field(
        default=0,
        ge=0,
        description="foreshadowings ever planted, settled ones included. Distinct from "
        "len(open_foreshadowings) because settling removes the debt but leaves the fact "
        "in the world, so an empty ledger does not mean there is room for another loop.",
    )
    recent_operators: list[str] = Field(
        default_factory=list,
        description=f"one entry per turn including 'relieve', newest last, "
        f"capped at {RECENT_OPERATOR_WINDOW}",
    )
    spent_one_shots: list[str] = Field(
        default_factory=list,
        description="keys of dramatic moves that may happen only once, e.g. "
        "'reverse:npc_a_threatened'. Unbounded (unlike recent_operators) because "
        "forgetting one would let the beat fire a second time.",
    )

    # --- event layer (docs/13 §3.2, §7) ------------------------------------

    active_event: ActiveEvent | None = Field(
        default=None, description="the event in progress, or None between events"
    )
    completed_events: list[str] = Field(
        default_factory=list,
        description="events that have finished at least once. Most may run again — M1 "
        "is expected to, since a slot spent knocking without success is not a "
        "permanent loss (docs/15 §4).",
    )
    closed_events: list[str] = Field(
        default_factory=list,
        description="events that will never trigger again (docs/15 §3.3). Kept separate "
        "from completed because the two mean opposite things for re-triggering: only M3 "
        "and M5 close, and both have another route to the same information, so closure "
        "is a cost rather than a dead end.",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="named state an outcome raised, e.g. 'probed_once', "
        "'promised_silence'. A list rather than one field per flag so a pack can "
        "introduce its own without a schema change, and readable by a Condition "
        "through the 'contains' operator.",
    )

    def open_event(self, event_id: str, *, max_exchanges: int) -> None:
        """Begin an event, replacing any that was running.

        Replacement rather than a stack: docs/13 §5.2 allows one conversation partner
        at a time, so nesting has nothing to represent, and a stack would leave an
        abandoned event able to resurface turns later out of context.
        """
        self.active_event = ActiveEvent(event_id=event_id, max_exchanges=max_exchanges)

    def record_exchange(self) -> None:
        """Count one player turn against the active event's patience."""
        if self.active_event is not None:
            self.active_event.exchanges += 1

    def finish_event(self, *, close: bool = False) -> None:
        """End the active event, recording it as completed and optionally closed."""
        if self.active_event is None:
            return
        event_id = self.active_event.event_id
        if event_id not in self.completed_events:
            self.completed_events.append(event_id)
        if close and event_id not in self.closed_events:
            self.closed_events.append(event_id)
        self.active_event = None

    def has_completed(self, event_id: str) -> bool:
        return event_id in self.completed_events

    def is_closed(self, event_id: str) -> bool:
        return event_id in self.closed_events

    def raise_flag(self, flag: str) -> None:
        if flag not in self.flags:
            self.flags.append(flag)

    def has_flag(self, flag: str) -> bool:
        return flag in self.flags

    def spend_one_shot(self, key: str) -> None:
        if key not in self.spent_one_shots:
            self.spent_one_shots.append(key)

    def is_spent(self, key: str) -> bool:
        """Whether a once-only beat has already fired.

        ``reverse`` is the operator that needs this: it adds no information and
        changes no visibility, so unlike ``reveal`` (which flips a fact to
        ``revealed`` and thereby stops re-triggering) it leaves no trace in the
        world to stop it recurring every turn.
        """
        return key in self.spent_one_shots

    def record_operator(self, operator: NarrativeOperator | str) -> None:
        """Append this turn's operator, trimming to the window.

        Every turn records something. See the class note on why quiet turns are
        stored as ``relieve`` rather than omitted.
        """
        value = operator.value if isinstance(operator, NarrativeOperator) else str(operator)
        self.recent_operators.append(value)
        del self.recent_operators[:-RECENT_OPERATOR_WINDOW]

    @property
    def last_operator(self) -> str | None:
        return self.recent_operators[-1] if self.recent_operators else None

    def turns_since(self, operator: NarrativeOperator | str) -> int | None:
        """Turns back to the most recent occurrence, or None if not in the window.

        The most recent turn is distance 1, so "no two reveals in a row" reads as
        ``turns_since(REVEAL) != 1`` and stays readable at the call site.
        """
        value = operator.value if isinstance(operator, NarrativeOperator) else str(operator)
        for distance, recorded in enumerate(reversed(self.recent_operators), start=1):
            if recorded == value:
                return distance
        return None


class EventCandidate(BaseModel):
    """A triggered possibility, before any content exists for it.

    Produced by the deterministic trigger rules and consumed by the Experience
    Controller. Carries an ``operator`` because the dramatic function — not the
    topic — is what the pacing rules admit or reject (docs/05 §4).
    """

    operator: NarrativeOperator
    event_type: str = Field(description="what it is about, e.g. 'clue_disclosure'")
    intensity: float = Field(ge=0.0, le=1.0)
    preference_tag: str = Field(
        description="a PreferenceTag value; looked up in PlayerProfile.play_style for ranking"
    )
    trigger_reason: str = Field(description="e.g. 'relationships.npc_a.player_1.trust >= 40'")

    constraints: list[str] = Field(
        default_factory=list,
        description="explicit prohibitions for this scene, e.g. '不能说出洛伦的名字'. Passed to "
        "the generator: stating what may not be said is more reliable than hoping the model "
        "infers it (docs/05 §2.3, docs/10 §2.3).",
    )
    pays_off: str | None = Field(
        default=None,
        description="fact_id of the ledger entry this settles, when the candidate is a payoff",
    )
    event_id: str | None = Field(
        default=None,
        description="the authored event this candidate came from (docs/13 §2). Present for "
        "every candidate the event layer produces; the field is optional only so that "
        "``EventCandidate`` stays constructible in tests that do not need a script.",
    )
    plants_toward: str | None = Field(
        default=None,
        description="for a foreshadow event: the fact whose eventual disclosure settles what "
        "this plants. The mirror image of ``pays_off``, not a synonym — that one settles a "
        "debt now, this one opens one. Authored rather than model-written, which is what "
        "reconnects the ledger to the clue chain (docs/13 §9): a generated payoff condition "
        "could point anywhere, and in play it did.",
    )


class NarrativeEvent(BaseModel):
    """A candidate after the LLM filled its slots. Intermediate, not world state.

    ``generated_content`` is structured input for the NPC's Dialogue Generation
    stage, not text shown to the player (docs/05 §6, docs/06 §3).
    """

    event_id: str
    operator: NarrativeOperator
    event_type: str
    participants: list[str] = Field(default_factory=list, description="NPC/faction ids")
    generated_content: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PlayerProfile(BaseModel):
    """Periodic summary of who this player is, read by the Controller's ranking.

    Ported from the ``docs/schemas/player_model.py`` draft. Slice 3 only *reads*
    it; the Behavior Tracker that writes it arrives in slice 4, so a profile is
    optional everywhere it appears and its absence means "no opinion".
    """

    player_id: str
    play_style: dict[str, float] = Field(
        default_factory=dict, description="e.g. {'exploration': 0.8, 'social': 0.9}"
    )
    narrative_preference: str = Field(
        default="", description="natural-language summary for downstream prompts"
    )
    risk_tolerance: float = 0.0
    last_summarized_at: datetime | None = None
    summarized_from_session: str | None = None

    def weight_for(self, preference_tag: str) -> float:
        """Preference weight for a tag, defaulting to neutral.

        Neutral is 0.5, not 0: an untracked tag means "nothing known about this
        taste yet", whereas 0 would silently veto every candidate carrying it.
        """
        return self.play_style.get(preference_tag, 0.5)
