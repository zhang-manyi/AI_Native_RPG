"""One play session: the assembled runtime plus the thread that owns it (docs/12 §5, §6.1).

``WorldStateManager`` is not thread-safe. ``submit()`` reads, validates and writes
with nothing between the three, and a turn has two writers: the Harness (relationship
and reveal actions) and the narrative tick (**at least one** ``advance_turn`` every
turn). ``snapshot()`` deep-copies, so a concurrent read cannot return half an object,
but it can copy a logically inconsistent world — ledger entry added, ``planted_total``
not yet — and a panel showing that is a panel that lies.

So every call that touches the manager, harness, engine or memory runs on **one
thread per session**. The world therefore has a single writer, which is the
assumption the Manager was written under; nothing in ``world/`` changes.

Why a thread and not asyncio: ``Harness.respond``, ``NarrativeEngine.tick`` and
``httpx.Client`` are all synchronous and blocking. Awaiting them in a handler would
stall the event loop; making them async would mean a second copy of the client,
harness and engine call paths — a doubled API surface for a single-player debug tool.
A global lock would be correct but would make two sessions wait 8 seconds on each
other, and sessions share nothing (their own manager, memory, engine).

A turn enqueues **two** jobs rather than one so the dialogue event can be emitted the
moment the line is ready, without waiting on the tick. The queue keeps them in the
order the architecture requires, which is the order ``chat_demo.py`` already used:
``take_pending_event`` -> ``respond`` -> ``tick``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..agent import Harness, HashingEmbedder, MemoryStore, build_npc_tools
from ..agent.embedding import Embedder
from ..agent.expression import ResolvedPublicOutcome
from ..agent.harness import PromptLibrary
from ..config import Settings, build_llm_client
from ..narrative.engine import NarrativeEngine
from ..narrative.player_actions import PlayerActionResult
from ..observability import NarrativeTickStore, TraceStore
from ..observability.panels import (
    PanelView,
    RejectedProposal,
    TurnCost,
    build_beats,
    build_ledger,
    build_slot_budget,
    build_unlock_board,
    memory_hits,
    operator_entry,
    rejected_from_tick,
    rejected_from_trace,
    turn_cost,
)
from ..scenario import (
    load_endings,
    load_event_script,
    load_intro,
    load_narrative_directives,
    load_personas,
    load_scenario,
    load_seed_memories,
    pack_prompts_dir,
)
from ..schemas.agent_trace import AgentTrace, TraceStep
from ..schemas.common import utc_now
from ..schemas.world_state import WorldState
from ..world import WorldStateManager
from ..world.actions import ActionType
from .events import (
    DialoguePayload,
    Event,
    EventType,
    MovePayload,
    NarrativeTickPayload,
    TurnAcceptedPayload,
    TurnFailedPayload,
)
from .scene import SceneLine, SceneOption, SceneView, build_pack_display, build_scene
from .wrap_up_view import WrapUpView, build_wrap_up

logger = logging.getLogger(__name__)

TRACE_DIR = Path("traces")
NARRATIVE_TRACE_DIR = TRACE_DIR / "narrative"

#: Where a session's resumable state lands, one directory per session.
#:
#: Separate from ``traces/``: a trace is an immutable record of one interaction, while
#: this is mutable current state, overwritten every turn. Mixing them would put a
#: file that changes into a directory whose contents are supposed to be history.
SAVE_DIR = Path("saves")

#: Scripted mock replies, shared with the terminal demo.
#:
#: The Harness and the Engine share one client and the mock replays in order
#: regardless of who asks, so each entry has to satisfy whichever schema comes next:
#: NPC entries carry reasoning/strategy/dialogue, narrative entries carry
#: summary/dialogue_hook. Extra keys are ignored by validation, so entries that serve
#: both are merged rather than interleaved — guessing the call order would break the
#: moment a turn takes the 2-call path.
MOCK_SCRIPT: list[dict[str, Any]] = [
    {
        "reasoning": "他在打探那天晚上的事，我不能直说，但也不想撒谎。",
        "strategy": "deflect",
        "dialogue": "……那晚我睡得早。你问这些做什么？",
        "summary": "玛尔塔提到那晚雨停得早",
        "dialogue_hook": "……那天的雨，停得比往常早些。",
        "planted_fact_id": "rain_stopped_early",
        "planted_value": "失踪那晚的雨停得比往常早，地上还没干透",
        "payoff_condition": {
            "mode": "any",
            "clauses": [{"path": "relationships.npc_a.player_1.trust", "op": "gte", "value": 45}],
        },
    },
    {
        "reasoning": "他看起来是真心想帮忙，可以稍微松一点。",
        "strategy": "warm_up",
        "dialogue": "你要是真想帮忙……算了，我不知道该不该说。",
        "summary": "玛尔塔看了一眼儿子的房间",
        "dialogue_hook": "（她朝里屋看了一眼，没说话。）",
        "planted_fact_id": "curtain_moved",
        "planted_value": "那晚玛尔塔家的窗帘动过一下",
        "payoff_condition": {
            "mode": "any",
            "clauses": [{"path": "relationships.npc_a.player_1.trust", "op": "gte", "value": 45}],
        },
    },
]


class SessionError(RuntimeError):
    """A session could not be created or a turn could not be admitted."""


@dataclass(frozen=True)
class SaveGame:
    """A previous playthrough, read off disk before a session is assembled.

    Read early on purpose. The world has to exist before ``WorldStateManager`` is
    constructed, because the Harness, the Engine and every tool take that manager at
    construction and keep it — swapping in a restored manager afterwards would leave
    them writing to a world nobody reads. So resuming loads the world *instead of* the
    pack's initial one rather than over it.

    Only what play produced is here. Personas, prompts, tools and narrative directives
    all come from the pack every time: saving them would mean a resumed session
    silently ignoring an edited scenario, which for a debug tool is the opposite of
    useful.
    """

    save_id: str
    scenario: str
    npc_id: str
    turn: int
    world: WorldState | None
    memory_paths: dict[str, Path]
    transcript: list[dict[str, str]]
    day_start: dict[str, Any] | None = None

    @property
    def memory_path(self) -> Path | None:
        """Legacy compatibility: the focused NPC's file, when present."""
        return self.memory_paths.get(self.npc_id)


@dataclass
class _NPCRuntime:
    memory: MemoryStore
    harness: Harness
    tool_names: list[str]


def list_saves(root: Path | None = None) -> list[dict[str, Any]]:
    """Every resumable save, newest first. Unreadable ones are skipped, not raised."""
    directory = root or SAVE_DIR
    if not directory.is_dir():
        return []
    saves = []
    for child in sorted(directory.iterdir()):
        meta_path = child / "session.json"
        if not (child.is_dir() and meta_path.is_file()):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        saves.append(
            {
                "save_id": child.name,
                "scenario": meta.get("scenario", ""),
                "npc_id": meta.get("npc_id", ""),
                "turn": meta.get("turn", 0),
                "saved_at": meta.get("saved_at", ""),
                "lines": len(meta.get("transcript") or []),
            }
        )
    saves.sort(key=lambda s: s["saved_at"], reverse=True)
    return saves


def load_save(save_id: str, root: Path | None = None) -> SaveGame:
    """Read a save off disk, or explain why it cannot be resumed."""
    directory = (root or SAVE_DIR) / save_id
    meta_path = directory / "session.json"
    if not meta_path.is_file():
        raise SessionError(f"no save to resume: {meta_path} does not exist")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"save {save_id!r} could not be read: {exc}") from exc

    world_path = directory / "world.json"
    world = None
    if world_path.is_file():
        try:
            world = WorldState.model_validate_json(world_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise SessionError(f"save {save_id!r} has an unreadable world: {exc}") from exc

    npc_id = str(meta.get("npc_id") or "")
    memory_paths: dict[str, Path] = {}
    # New saves contain one file per persona. Read the scenario's cast so a missing
    # file simply means "use seed memory" during assembly.
    try:
        persona_ids = load_personas(str(meta.get("scenario", ""))).keys()
    except Exception:
        persona_ids = ()
    for candidate in persona_ids:
        path = directory / f"memory_{candidate}.json"
        if path.is_file():
            memory_paths[candidate] = path
    # Legacy saves had one file named for the focused NPC; retain it even when the
    # metadata/scenario cannot be loaded yet.
    if npc_id and npc_id not in memory_paths:
        legacy = directory / f"memory_{npc_id}.json"
        if legacy.is_file():
            memory_paths[npc_id] = legacy
    return SaveGame(
        save_id=save_id,
        scenario=str(meta.get("scenario", "")),
        npc_id=npc_id,
        turn=int(meta.get("turn", 0) or 0),
        world=world,
        memory_paths=memory_paths,
        transcript=[dict(line) for line in meta.get("transcript") or []],
        day_start=meta.get("day_start"),
    )


@dataclass
class _Turn:
    """Bookkeeping for one player utterance in flight."""

    turn_id: str
    text: str
    #: The option the player clicked, if they clicked one rather than typed.
    #:
    #: Both arrive here and both travel the same path from here on (docs/15 §1.1): if
    #: clicking had a defined check and typing did not, typing would be strictly worse and
    #: everyone would click, which kills the open-input path docs/03 §5 exists to keep.
    option_id: str | None = None


def _rejected_from_action(
    result: PlayerActionResult, *, actor: str, turn: int
) -> list[RejectedProposal]:
    """Panel rows for whatever the Validator refused during a player action.

    The player's own refused proposals belong on the same block as an NPC's: "a rule
    stopped this" is the block's subject, and leaving the player out would make his moves
    the one write path with no record of being checked.
    """
    return [
        RejectedProposal(
            turn=turn,
            actor=actor,
            action_type=ActionType.MOVE.value,
            rule_name=outcome.rule_name,
            reason=outcome.reason,
        )
        for outcome in result.proposals
        if not outcome.approved
    ]


class Session:
    """A scenario, its NPC runtimes, and the single thread allowed to touch them."""

    def __init__(
        self,
        *,
        session_id: str,
        scenario: str,
        npc_id: str | None = None,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        dev_mode: bool = True,
        loop: asyncio.AbstractEventLoop | None = None,
        trace_dir: Path | None = None,
        save_dir: Path | None = None,
        resume: SaveGame | None = None,
    ) -> None:
        self.session_id = session_id
        self.scenario = scenario
        self.dev_mode = dev_mode
        self._loop = loop
        self._settings = settings or Settings.from_env()
        self._save_root = save_dir or SAVE_DIR
        self.resumed_from = resume.save_id if resume is not None else None

        # --- assembly, identical to chat_demo.main() (docs/12 §6.1) -----------
        world = load_scenario(scenario)
        personas = load_personas(scenario)
        seeds = load_seed_memories(scenario)
        self.intro = load_intro(scenario)
        directives = load_narrative_directives(scenario)

        if resume is not None:
            if resume.scenario != scenario:
                raise SessionError(
                    f"save {resume.save_id!r} belongs to scenario {resume.scenario!r}, "
                    f"not {scenario!r}"
                )
            if npc_id is None:
                npc_id = resume.npc_id or next(iter(personas), None)
            # Replaces the pack's opening world rather than being applied over it: see
            # SaveGame on why this has to happen before the manager is built.
            if resume.world is not None:
                world = resume.world

        # The pack, not the code, decides who the player is.
        self.player_id = next(iter(world.player_locations), "player_1")

        resolved_npc = npc_id or next(iter(personas), None)
        if resolved_npc not in personas:
            raise SessionError(
                f"unknown npc {npc_id!r} in scenario {scenario!r}; available: {', '.join(personas)}"
            )
        self._default_npc_id = resolved_npc

        self.manager = WorldStateManager(world)
        # Public display data, lifted once. The render path never sees WorldState.
        self.display = build_pack_display(locations=world.locations, npcs=world.npcs)
        # The embedder is shared across sessions by the caller: it is read-only and
        # costs hundreds of MB to load, so one instance serves everyone.
        overlay = pack_prompts_dir(scenario)
        self.prompt_source = f"{scenario} 覆盖 + 全局回退" if overlay.is_dir() else "全局默认"
        prompts = PromptLibrary(overlay=overlay if overlay.is_dir() else None)
        llm = build_llm_client(self._settings, responses=list(MOCK_SCRIPT) * 40)
        self._npcs: dict[str, _NPCRuntime] = {}
        shared_embedder = embedder or HashingEmbedder()
        for persona_id, npc_state in personas.items():
            memory = MemoryStore(persona_id, embedder=shared_embedder)
            memory_path = resume.memory_paths.get(persona_id) if resume is not None else None
            if memory_path is not None and memory_path.is_file():
                memory.load(memory_path)
            else:
                seeds[persona_id].load_into(memory)
            tools = build_npc_tools(
                npc_id=persona_id,
                manager=self.manager,
                memory=memory,
                player_id=self.player_id,
            )
            self._npcs[persona_id] = _NPCRuntime(
                memory=memory,
                harness=Harness(
                    npc_state=npc_state,
                    manager=self.manager,
                    llm=llm,
                    memory=memory,
                    prompts=prompts,
                    tools=tools,
                    public_expression=(
                        self._settings.public_expression and self._settings.has_real_backend
                    ),
                ),
                tool_names=list(tools.names),
            )
        self.engine = NarrativeEngine(
            manager=self.manager,
            llm=llm,
            prompts=prompts,
            directives=directives,
            # The pack's event network. Without it there is never an ``active_event``, so
            # no scripted line and no option could ever reach the page — docs/12 §13.7 has
            # nothing to render until this is passed. Loaded from the pack every time, like
            # personas and prompts: a resumed save must not sail on a stale script.
            script=load_event_script(scenario),
            # Without this the case never ends, however far the player gets: nothing
            # would ever write ``story_beats.ended_at`` (docs/14 §4).
            endings=load_endings(scenario),
        )

        root = trace_dir or TRACE_DIR
        self.traces = TraceStore(root)
        self.narrative_traces = NarrativeTickStore(root / "narrative")

        # --- event plumbing ---------------------------------------------------
        self._seq = 0
        self._history: list[Event] = []
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = threading.Lock()

        # --- panel accumulators ----------------------------------------------
        # Per-turn records the panel shows as a series. Kept here rather than
        # recomputed from disk: a trace is one interaction and holds no turn number,
        # so the correlation only exists while the session is alive.
        self._timeline: list[Any] = []
        self._rejected: list[RejectedProposal] = []
        self._relationship_trend: list[dict[str, float]] = []
        self._last_cost: Any = None
        self._last_memory: Any = []
        self._npc_activity: dict[str, dict] = {}
        self._npc_trends: dict[str, list] = {npc_id: [] for npc_id in world.npcs}
        self._day_start = resume.day_start if resume else self._day_baseline()
        self._sample_relationship()
        # Restored so the page opens on the conversation the player left, rather than
        # an empty screen in front of an NPC who remembers them. The panel series
        # (timeline, rejected proposals, trust trend) deliberately start empty: those
        # are per-turn observability records, and a resumed session has no turns yet.
        self.transcript: list[dict[str, str]] = (
            [dict(line) for line in resume.transcript] if resume is not None else []
        )
        self._passages: list[dict[str, str]] = []

        # A fresh game gets one tick before anyone has said anything, so the opening
        # event's scripted lines (docs/15 §1's "我受人所托……") are already in the very
        # first `scene`/`hello` payload. Without this, `check_triggers` never runs until
        # the player's first turn closes — M1's trigger is `turn >= 0`, true from the
        # start, but nothing evaluated it — so the page opened on an empty composer
        # waiting for a line that had nothing to answer, and only the player's throwaway
        # opener actually put M1 on the board a turn late. A resumed save skips this: it
        # already has turns behind it, and re-ticking here would run the pacing rules
        # against a `recent_operators` window the resume did not itself produce.
        #
        # Run after the accumulators above exist (`_timeline` etc. mirror what
        # `_job_tick` records) but before the worker thread starts, so nothing else can
        # be touching `self.manager` concurrently — the single-writer contract
        # (docs/04) is intact, and this read of its own result populates the panel's
        # timeline the same way a played turn would.
        #
        # Runs synchronously on the request thread, unlike every other tick — there is
        # no turn to answer with a `turn_failed` event yet, since the player has not
        # submitted one. So a real backend's LLMAPIError (a slow or unreachable
        # endpoint, not a mock) must not escape and turn session creation itself into a
        # 500: the opening tick is an optimisation (M1's line arriving a turn early),
        # and losing it costs nothing but that — the player's first submit_turn runs
        # its own tick and reaches the same event then, exactly as it did before this
        # existed.
        if resume is None:
            try:
                opening_tick = self.engine.tick(player_id=self.player_id)
            except Exception:
                logger.warning(
                    "opening tick failed for session %s; the first submit_turn will retry it",
                    session_id,
                    exc_info=True,
                )
            else:
                self.narrative_traces.save(opening_tick)
                self._timeline.append(operator_entry(opening_tick))
                self._rejected.extend(rejected_from_tick(opening_tick))
                self._collect_presentation()

        if resume is not None:
            # Old saves may hold a conversation from a different location. Refresh
            # authored arrivals without advancing the clock or replaying their effects.
            refreshed = self.engine.refresh_authored(self.player_id)
            if refreshed:
                self.narrative_traces.save(refreshed)
                self._collect_presentation()
            if self.engine.is_reporting():
                # Re-open the current report presentation, including saves made with
                # the former face-to-face conclusion script.
                self._passages = [
                    line.model_dump() for line in self.engine.active_event().presentation
                ]
            if not self._passages and self.transcript:
                self._passages = [dict(self.transcript[-1])]

        # --- the executor -----------------------------------------------------
        self._jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        # Jobs enqueued but not finished. The authority for "a turn is in flight",
        # and the reason ``submit_turn`` can refuse a second turn: both jobs of a turn
        # count, so the session stays busy until the tick is done, not just the line.
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._closed = False
        self._worker = threading.Thread(
            target=self._run_jobs, name=f"session-{session_id[:8]}", daemon=True
        )
        self._worker.start()

        self._sample_relationship()

    def _resolve_current_npc_id(self) -> str | None:
        """Return the co-located NPC that should receive the next line."""
        return next(iter(self.manager.player_view(self.player_id).known_npc_locations), None)

    @property
    def npc_id(self) -> str | None:
        """The NPC co-located with the player, if this scene has one."""
        return self._resolve_current_npc_id()

    @property
    def memory(self) -> MemoryStore:
        runtime = self._npcs.get(self._resolve_current_npc_id())
        if runtime is None:
            raise SessionError("no NPC runtime is available")
        return runtime.memory

    @property
    def harness(self) -> Harness:
        runtime = self._npcs.get(self._resolve_current_npc_id())
        if runtime is None:
            raise SessionError("no NPC runtime is available")
        return runtime.harness

    @property
    def tool_names(self) -> list[str]:
        runtime = self._npcs.get(self._resolve_current_npc_id())
        return list(runtime.tool_names) if runtime is not None else []

    # --- backend description -------------------------------------------------

    @property
    def backend(self) -> dict[str, str]:
        """What is actually live, for the page header (``print_header``'s content).

        Names the provider, not just the model: with a relay the model string alone
        does not say which endpoint is being billed.
        """
        if self._settings.has_real_backend:
            model = f"{self._settings.provider} {self._settings.model}"
        else:
            model = "MockLLMClient (离线脚本)"
        current_npc_id = self._resolve_current_npc_id()
        runtime = self._npcs.get(current_npc_id)
        return {
            "scenario": self.scenario,
            "npc_id": current_npc_id or "",
            "npc_ids": ", ".join(self._npcs),
            "player_id": self.player_id,
            "model": model,
            "embedder": (type(runtime.memory._embedder).__name__ if runtime is not None else ""),
            "prompt_source": self.prompt_source,
            "tools": ", ".join(runtime.tool_names) if runtime is not None else "",
        }

    # --- reads (safe from any thread) ----------------------------------------

    def scene(self) -> SceneView:
        """The player's view of the world.

        The visibility decision is entirely ``player_view()``'s; the only other input
        is ``self.display``, which holds static public labels. ``turn`` is read from
        ``story_beats`` — a turn count is not hidden information, and the player is
        shown it as "第 N 轮" anyway.
        """
        definition = self.engine.active_event()
        scene = build_scene(
            view=self.manager.player_view(self.player_id),
            display=self.display,
            turn=self.manager.snapshot().story_beats.turn,
            intro=self.intro,
            transcript=self.transcript[-12:],
            # Straight from the pack's event definition (docs/12 §13.7). Which form a
            # moment takes was decided when the pack loaded — docs/15 §1's criterion is a
            # schema validator — so nothing here judges it.
            scripted_lines=list(definition.scripted_lines) if definition else [],
            # Filtered by requires (docs/15 §7 M7): the same list the classifier sees,
            # so a gated option cannot show as a button while being invisible to typing.
            options=[
                SceneOption(
                    option_id=o.option_id,
                    tag=o.tag.value,
                    text=o.text,
                    final_report=bool(
                        self.engine.is_reporting()
                        and definition.outcome_for(o.on_success).closes_event
                    ),
                )
                for o in self.engine.visible_event_options()
            ],
            event_in_progress=definition is not None,
        )
        scene.passages = [
            SceneLine(
                speaker=line["speaker"],
                text=line["text"],
                name=(
                    self.display.npcs[line["speaker"]].name
                    if line["speaker"] in self.display.npcs
                    else ("你" if line["speaker"] == "player" else "旁白")
                ),
            )
            for line in self._passages
        ]
        scene.passage_id = f"{scene.turn}:{len(self.transcript)}"
        scene.ready = not self.busy
        ending = self.engine.reached_ending()
        if ending:
            scene.ending = {
                "title": f"结局：{ending.player_title}",
                "text": ending.player_text,
            }
        scene.conclude_reason = self.engine.conclude_reason(self.player_id)
        scene.can_conclude = not scene.conclude_reason
        scene.report_active = self.engine.is_reporting()
        return scene

    def panel(self) -> PanelView:
        """The developer panel. Reads ``WorldState`` directly, by design (docs/07 §2.4)."""
        world = self.manager.snapshot()
        runtime = self._npcs.get(self._resolve_current_npc_id())
        return PanelView(
            beats=build_beats(world),
            npcs=self._npc_panels(world),
            slot_budget=build_slot_budget(world),
            ledger=build_ledger(world),
            unlock_board=build_unlock_board(world),
            rejected_proposals=list(reversed(self._rejected[-20:])),
            operator_timeline=list(reversed(self._timeline[-20:])),
            relationship_trend=self._npc_trends.get(self._resolve_current_npc_id(), [])[-40:],
            memory=self._last_memory,
            memory_counts={
                "episodic": runtime.memory.episodic_count if runtime is not None else 0,
                "semantic": runtime.memory.semantic_count if runtime is not None else 0,
            },
            last_turn_cost=self._last_cost,
            pending_hook=str((self.engine.pending_event or {}).get("dialogue_hook", "")),
        )

    def wrap_up(self) -> WrapUpView | None:
        """The day's investigation review, or ``None`` outside the wrap-up.

        ``None`` is passed through rather than smoothed over: ``engine.wrap_up()`` returns
        it deliberately, because a review available on demand every turn is just a screen,
        and once a day it is a ritual (docs/13 §4.2).

        A read, so it runs off the executor like ``scene()`` and ``panel()``.
        """
        day = self.engine.wrap_up(player_id=self.player_id)
        if day is None:
            return None
        baseline = self._day_start
        valid = baseline is not None and baseline.get("day") == day.day
        changes = []
        if valid:
            for npc_id, npc in self.manager.snapshot().npcs.items():
                current = self.manager.get_relationship(npc_id, self.player_id)
                before = baseline["relationships"].get(npc_id, {})
                delta = {
                    k: round(getattr(current, k) - before.get(k, 0), 2)
                    for k in ("trust", "fear", "respect")
                }
                if any(delta.values()):
                    changes.append({"npc_id": npc_id, "name": npc.display_name, **delta})
        return build_wrap_up(
            review=day.review,
            baseline=baseline["known"] if valid else None,
            relationship_changes=changes,
        )

    @property
    def busy(self) -> bool:
        """Whether a turn is in flight. The authority for the 409 in ``submit_turn``.

        Stays true until the *tick* finishes, not just the line: the tick writes world
        state (at least one ``advance_turn``), so admitting the next turn while it runs
        is precisely the race the single-thread executor exists to prevent.
        """
        with self._inflight_lock:
            return self._inflight > 0

    # --- event stream --------------------------------------------------------

    def subscribe(self, *, last_event_id: int | None = None) -> asyncio.Queue[Event]:
        """Attach a stream, optionally replaying what was missed.

        Replay is what makes ``EventSource``'s automatic reconnect lossless: the
        browser sends ``Last-Event-ID`` and everything after it is re-delivered.
        """
        q: asyncio.Queue[Event] = asyncio.Queue()
        with self._lock:
            if last_event_id is not None:
                for event in self._history:
                    if event.seq > last_event_id and self._visible(event):
                        q.put_nowait(event)
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _visible(self, event: Event) -> bool:
        """Developer-only events never reach a non-developer session (docs/12 §7)."""
        return self.dev_mode or not event.dev_only

    def emit(self, type_: EventType, data: dict[str, Any]) -> Event | None:
        """Record an event and fan it out. Returns None if it was withheld.

        Filtering happens **here**, server-side: a dev event on a plain session is
        never created, rather than sent and hidden by the front end.
        """
        with self._lock:
            candidate = Event(seq=self._seq + 1, type=type_, data=data)
            if not self._visible(candidate):
                return None
            self._seq += 1
            event = candidate
            self._history.append(event)
            subscribers = list(self._subscribers)

        for q in subscribers:
            self._deliver(q, event)
        return event

    def _deliver(self, q: asyncio.Queue[Event], event: Event) -> None:
        """Hand an event to a queue from whichever thread produced it.

        The worker thread is not the event loop's thread, so the put has to be
        marshalled; this is the only crossing point between the two worlds.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            q.put_nowait(event)
            return
        # A loop closed mid-shutdown raises here; the stream it fed is gone anyway.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(q.put_nowait, event)

    # --- the executor --------------------------------------------------------

    def _run_jobs(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                self._jobs.task_done()
                return
            try:
                job()
            except Exception:
                # A job reports its own failure as an event; anything escaping here
                # must still not kill the worker, or the session would go silently
                # deaf for the rest of its life.
                pass
            finally:
                # Decrement under the lock rather than inferring business from the
                # queue's ``unfinished_tasks``: that counter drops as the last job is
                # *taken*, so a turn looked idle while its tick was still running and
                # the next turn was admitted alongside it — exactly the concurrency
                # this executor exists to prevent.
                with self._inflight_lock:
                    self._inflight = max(0, self._inflight - 1)
                    idle = self._inflight == 0
                if idle:
                    self._emit_state()
                self._jobs.task_done()

    def submit_turn(self, text: str, *, option_id: str | None = None) -> str:
        """Admit one utterance and enqueue its two jobs. Returns the turn id.

        Returns immediately: the caller answers ``202`` and the line arrives on the
        stream. Rejecting a turn while one is in flight is deliberate — see
        docs/12 §5.3 for why queueing plus a disabled input beat preemption.

        ``option_id`` is set when the player clicked a tagged option instead of typing.
        It is the *same* endpoint and the same executor either way (docs/12 §13.7): two
        submit paths would let the two forms be judged differently, and docs/15 §1.1
        exists precisely so typing is not the worse deal.
        """
        if self._closed:
            raise SessionError("session is closed")
        if self.engine.reached_ending() or self.engine.is_wrapping_up():
            raise SessionError("当前调查已经结束或正在整理一天的线索。")
        if option_id and option_id not in {
            o.option_id for o in self.engine.visible_event_options()
        }:
            raise SessionError("这个选项已不在当前事件中。")
        text = text.strip()
        if not text:
            raise SessionError("empty input")

        if self.engine.is_reporting() and option_id is None:
            option_id = next(
                (o.option_id for o in self.engine.visible_event_options() if o.text == text),
                None,
            )
            if option_id is None:
                raise SessionError("请选择报告中的判断，或合上报告继续调查。")

        turn = _Turn(turn_id=uuid.uuid4().hex, text=text, option_id=option_id)
        # Count both jobs before either is queued: a turn is in flight from here
        # until its tick lands, so a check-then-submit from another request cannot
        # slip between the two.
        with self._inflight_lock:
            if self._inflight > 0:
                raise SessionError("a turn is already in flight for this session")
            self._inflight = 2
        self.emit(
            EventType.TURN_ACCEPTED,
            TurnAcceptedPayload(
                turn_id=turn.turn_id,
                text=turn.text,
                turn=self.manager.snapshot().story_beats.turn,
            ).model_dump(),
        )
        self._jobs.put(lambda: self._job_dialogue(turn))
        self._jobs.put(lambda: self._job_tick(turn))
        return turn.turn_id

    def submit_move(self, destination: str) -> str:
        """Admit a move and enqueue it. Returns the turn id.

        Goes through this executor rather than calling ``move_player`` from the request
        thread, because a move **writes world state** — the ``move`` proposal itself and
        then a slot charge. ``move_player`` guarantees the Validator is consulted, not
        that nothing else is mid-write; a direct call from a handler would be exactly the
        bypass docs/12 §13.2 rules out.

        Only *one* job is enqueued here. Whether a narrative tick follows depends on
        whether the move landed, and only the move knows: a refused move spends nothing
        and must not advance the story a turn (see ``_job_move``).
        """
        if self._closed:
            raise SessionError("session is closed")
        destination = destination.strip()
        if not destination:
            raise SessionError("empty destination")

        turn = _Turn(turn_id=uuid.uuid4().hex, text=destination)
        with self._inflight_lock:
            if self._inflight > 0:
                raise SessionError("a turn is already in flight for this session")
            self._inflight = 1
        self._jobs.put(lambda: self._job_move(turn))
        return turn.turn_id

    def submit_conclude_case(self) -> str:
        """Open the conclusion event on the player's own initiative. Returns the turn id.

        On the executor for the same reason ``submit_move`` is: opening an event writes
        ``story_beats.active_event``. One job, no tick queued directly — ``end_conversation``
        does not spend a slot either, and the conclusion event's own options land through
        the ordinary dialogue path once it is open (docs/13 §12, docs/15 §4 M7).
        """
        if self._closed:
            raise SessionError("session is closed")

        turn = _Turn(turn_id=uuid.uuid4().hex, text="")
        with self._inflight_lock:
            if self._inflight > 0:
                raise SessionError("a turn is already in flight for this session")
            self._inflight = 1
        self._jobs.put(lambda: self._job_conclude_case(turn))
        return turn.turn_id

    def submit_close_out_day(self) -> str:
        """Step from the wrap-up into the next morning. Returns the turn id.

        On the executor because it writes: closing out the day advances the slot clock,
        which is world state. Refusing it outside the wrap-up is
        ``advance_past_wrap_up``'s job, not this one's — a caller able to skip a slot it
        did not want to spend is the thing that function guards against, and duplicating
        the check here would be a second copy of it.

        One job, no tick: the interlude is not a turn. It costs no slot (docs/13 §4.2), so
        charging the story a turn for dismissing it would misreport how long ago the last
        beat fired.
        """
        if self._closed:
            raise SessionError("session is closed")

        turn = _Turn(turn_id=uuid.uuid4().hex, text="")
        with self._inflight_lock:
            if self._inflight > 0:
                raise SessionError("a turn is already in flight for this session")
            self._inflight = 1
        self._jobs.put(lambda: self._job_close_out_day(turn))
        return turn.turn_id

    def close(self) -> None:
        self._closed = True
        self._jobs.put(None)

    def join(self, timeout: float | None = None) -> None:
        """Block until the in-flight turn drains. For tests and shutdown."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.busy:
            if deadline is not None and time.monotonic() > deadline:
                return
            time.sleep(0.01)

    # --- the two jobs of a turn ----------------------------------------------

    def _job_dialogue(self, turn: _Turn) -> None:
        """LLM call(s) for the line. Emits ``dialogue`` as soon as it is ready."""
        started = time.perf_counter()
        self._passages = []
        definition = self.engine.active_event()
        if definition and definition.delivery == "choice":
            self._job_choice(turn)
            return
        npc_id = self._resolve_current_npc_id()
        if npc_id is None or npc_id not in self._npcs:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type="NoConversationNPC",
                    message="当前场景没有可对话的 NPC。",
                ).model_dump(),
            )
            return
        runtime = self._npcs[npc_id]
        trust_before = self.manager.get_trust(npc_id, self.player_id)
        rel_before = self.manager.get_relationship(npc_id, self.player_id)

        # Content generated after the previous turn is woven into this one, so the
        # player never waits on the narrative call (docs/02 §4).
        pending = self.engine.take_pending_event()

        # The active event's options, handed to the NPC call so free text can be classified
        # against them **in that same call** (docs/15 §1.1) — no extra request, which is
        # what makes typing cost the player nothing relative to clicking.
        options = self.engine.active_event_options()

        try:
            response, trace = runtime.harness.respond(
                turn.text,
                player_id=self.player_id,
                session_id=self.session_id,
                narrative_event=pending,
                event_options=options,
            )
        except Exception as exc:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type=type(exc).__name__,
                    message=str(exc),
                ).model_dump(),
            )
            return

        self.traces.save(trace)

        # A clicked option wins over the classifier; otherwise the label the model assigned
        # to free text stands in for one. Either way the *same* call lands the outcome, so
        # a typed equivalent of an option resolves exactly as the button would have.
        chosen = turn.option_id or response.matched_option_id
        try:
            self.engine.resolve_player_response(player_id=self.player_id, option_id=chosen)
        except Exception as exc:
            # The line already exists and the player should see it; a resolution that blew
            # up costs this turn its consequences, not its dialogue. Reported under the
            # dialogue stage because that is the turn it belongs to.
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type=type(exc).__name__,
                    message=f"事件结算失败：{exc}",
                ).model_dump(),
            )

        trust_after = self.manager.get_trust(npc_id, self.player_id)
        world = self.manager.snapshot()

        self.transcript.append({"speaker": "player", "text": turn.text})
        self.transcript.append({"speaker": npc_id, "text": response.dialogue})

        self._last_cost = turn_cost(trace)
        self._last_memory = memory_hits(trace)
        self._record_npc_activity(trace)
        self._rejected.extend(rejected_from_trace(trace, turn=world.story_beats.turn))
        self._sample_relationship()

        self.emit(
            EventType.DIALOGUE,
            DialoguePayload(
                turn_id=turn.turn_id,
                turn=world.story_beats.turn,
                npc_id=npc_id,
                name=(
                    self.display.npcs.get(npc_id).name if self.display.npcs.get(npc_id) else npc_id
                ),
                dialogue=response.dialogue,
                strategy=response.plan.strategy,
                trust_before=trust_before,
                trust_after=trust_after,
                relationship_changes={
                    k: round(
                        getattr(self.manager.get_relationship(npc_id, self.player_id), k)
                        - getattr(rel_before, k),
                        2,
                    )
                    for k in ("trust", "fear", "respect")
                    if getattr(self.manager.get_relationship(npc_id, self.player_id), k)
                    != getattr(rel_before, k)
                },
                latency_ms=(time.perf_counter() - started) * 1000.0,
                used_pending_hook=bool(pending),
                option_id=chosen,
            ).model_dump(),
        )
        self._emit_state()

    def _job_choice(self, turn: _Turn) -> None:
        """Resolve once before expression. A generation failure cannot reroll a choice."""
        started = time.perf_counter()
        self._last_cost = TurnCost()
        self._last_memory = []
        definition = self.engine.active_event()
        if definition is None:
            return
        npc_id = None if self.engine.is_reporting() else self._resolve_current_npc_id()
        runtime = self._npcs.get(npc_id)
        trust_before = self.manager.get_trust(npc_id, self.player_id) if npc_id else 0
        rel_before = self.manager.get_relationship(npc_id, self.player_id) if npc_id else None
        options = self.engine.visible_event_options()
        chosen = turn.option_id or next((o.option_id for o in options if o.text == turn.text), None)
        classification = None
        try:
            if chosen is None and runtime and self._settings.has_real_backend:
                chosen, classification = runtime.harness.classify_option(
                    turn.text, self.engine.active_event_options()
                )
            if chosen is None:
                if runtime is None:
                    raise SessionError("这里可以观察现场，请选择一个调查动作。")
                response, trace = runtime.harness.respond(
                    turn.text, player_id=self.player_id, session_id=self.session_id
                )
                speaker, line = npc_id, response.dialogue
                self._record_response_trace(trace, classification)
                self._sample_relationship()
            else:
                trace = None
                expression_failure = None
                record = self.engine.resolve_player_response(
                    player_id=self.player_id, option_id=chosen
                )
                if record is None or record.outcome_id is None:
                    raise SessionError("这个选项已经不可用了。")
                outcome = definition.outcome_for(record.outcome_id)
                speaker = npc_id if outcome.npc_reply and definition.npc_id else "narrator"
                line = outcome.npc_reply or outcome.summary
                # The authored consequence remains available even if expression fails.
                selected = next(o for o in options if o.option_id == chosen)
                if runtime and selected.tag.value != "observe" and self._settings.has_real_backend:
                    try:
                        response, trace = runtime.harness.respond(
                            turn.text,
                            player_id=self.player_id,
                            session_id=self.session_id,
                            resolved_outcome=outcome.summary
                            + "\n"
                            + outcome.npc_reply
                            + "\n"
                            + "\n".join(definition.constraints),
                            resolved_public_outcome=ResolvedPublicOutcome(
                                summary=outcome.summary, reply=outcome.npc_reply or ""
                            ),
                        )
                        speaker, line = npc_id, response.dialogue
                    except Exception as exc:
                        expression_failure = type(exc).__name__
                        logger.warning(
                            "NPC expression failed; using authored result", exc_info=True
                        )
                        runtime.harness.remember_exchange(turn.text, line, player_id=self.player_id)
                elif runtime:
                    runtime.harness.remember_exchange(turn.text, line, player_id=self.player_id)
                if trace is None:
                    trace = AgentTrace(
                        trace_id=uuid.uuid4().hex,
                        npc_id=npc_id or "narrator",
                        player_id=self.player_id,
                        session_id=self.session_id,
                        final_dialogue=line,
                        total_latency_ms=(time.perf_counter() - started) * 1000
                        - (classification.latency_ms if classification else 0),
                    )
                trace.steps.insert(
                    0,
                    TraceStep(
                        step_name="event_resolution",
                        input_summary={"option_id": chosen},
                        output_summary=record.model_dump(mode="json"),
                    ),
                )
                if expression_failure:
                    trace.steps.append(
                        TraceStep(
                            step_name="expression_fallback",
                            model_used=self._settings.model,
                            output_summary={
                                "error_type": expression_failure,
                                "authored_reply": True,
                            },
                        )
                    )
                self._record_response_trace(trace, classification)
                self._sample_relationship()
            self.transcript.append({"speaker": "player", "text": turn.text})
            self._add_passage(speaker, line)
            if speaker == npc_id and npc_id:
                rel_after = self.manager.get_relationship(npc_id, self.player_id)
                relationship_changes = {}
                if rel_before is not None:
                    for dimension in ("trust", "fear", "respect"):
                        delta = getattr(rel_after, dimension) - getattr(rel_before, dimension)
                        if abs(delta) >= 0.05:
                            relationship_changes[dimension] = round(delta, 1)
                self.emit(
                    EventType.DIALOGUE,
                    DialoguePayload(
                        turn_id=turn.turn_id,
                        turn=self.manager.snapshot().story_beats.turn,
                        npc_id=npc_id,
                        name=self.npc_name,
                        dialogue=line,
                        strategy="event_response",
                        trust_before=trust_before,
                        trust_after=self.manager.get_trust(npc_id, self.player_id),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        used_pending_hook=False,
                        option_id=chosen,
                        relationship_changes=relationship_changes,
                    ).model_dump(),
                )
        except Exception as exc:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type=type(exc).__name__,
                    message=str(exc),
                ).model_dump(),
            )

    def _record_response_trace(self, trace, classification=None) -> None:
        if classification:
            trace.steps.insert(0, classification)
            trace.total_latency_ms += classification.latency_ms
        self.traces.save(trace)
        self._last_cost = turn_cost(trace)
        self._last_memory = memory_hits(trace)
        self._record_npc_activity(trace)
        self._rejected.extend(
            rejected_from_trace(trace, turn=self.manager.snapshot().story_beats.turn)
        )

    def _add_passage(self, speaker: str, text: str) -> None:
        line = {"speaker": speaker, "text": text}
        self._passages.append(line)
        self.transcript.append(line)

    def _collect_presentation(self) -> None:
        for line in self.engine.presented_lines:
            self._add_passage(line.speaker, line.text)
            if line.speaker in self._npcs:
                self._npcs[line.speaker].harness.remember_exchange("场景对白", line.text)
        self.engine.presented_lines = []

    def _job_move(self, turn: _Turn) -> None:
        """Walk the player somewhere. A turn with no line (docs/12 §13.2).

        The work is entirely ``player_actions.move_player``: the proposal, the adjacency
        rule, the slot charge and the first-visit record all belong to layers below this
        one. What this adds is the two things a Web layer owes — running it on the session
        thread, and turning the result into an event.

        A landed move enqueues a tick, so it is a turn like any other: ``advance_turn``
        gets recorded, which is what the pacing rules read adjacency from, and arriving
        somewhere new can open an event. A **refused** move enqueues nothing — it spent no
        slot and nothing happened, so charging the story a turn for it would be a lie in
        the one record that says how long ago a beat fired.
        """
        started = time.perf_counter()
        self._passages = []
        try:
            result = self.engine.move_player(player_id=self.player_id, destination=turn.text)
        except Exception as exc:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type=type(exc).__name__,
                    message=str(exc),
                ).model_dump(),
            )
            return

        destination = self.display.locations.get(turn.text)
        self.emit(
            EventType.MOVE,
            MovePayload(
                turn_id=turn.turn_id,
                approved=result.approved,
                destination=turn.text,
                name=destination.name if destination is not None else turn.text,
                # Verbatim: the reason is already written for the player to read.
                reason=result.reason,
                slot_spent=result.slot_spent,
                slot=result.slot.value if result.slot is not None else None,
                day=result.day,
                first_visit=result.first_visit,
                out_of_days=result.out_of_days,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            ).model_dump(),
        )

        if not result.approved:
            # Nothing moved and nothing was spent; the scene is unchanged, so not even a
            # snapshot is owed. The front end re-enables input on this event.
            self._rejected.extend(_rejected_from_action(result, actor=self.player_id, turn=0))
            self.emit(EventType.PANEL, self.panel().model_dump())
            return

        self._rejected.extend(
            _rejected_from_action(
                result, actor=self.player_id, turn=self.manager.snapshot().story_beats.turn
            )
        )
        self._emit_state()

        # The turn continues: count the tick before queueing it so ``busy`` never dips
        # between the two jobs and lets another request in.
        with self._inflight_lock:
            self._inflight += 1
        self._jobs.put(lambda: self._job_tick(turn))

    def _job_conclude_case(self, turn: _Turn) -> None:
        """Open the conclusion event. No line of its own — the scene refresh carries it."""
        self._passages = []
        try:
            result = self.engine.conclude_case()
        except Exception as exc:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type=type(exc).__name__,
                    message=str(exc),
                ).model_dump(),
            )
            return

        world = self.manager.snapshot()
        self.emit(
            EventType.MOVE,
            MovePayload(
                turn_id=turn.turn_id,
                approved=result.approved,
                # Not travel, but the same "an act, whether it landed" shape ``_job_move``
                # and ``_job_close_out_day`` already use rather than inventing a fourth
                # event type for one more player-initiated act.
                destination=world.player_locations.get(self.player_id, ""),
                name="",
                reason=result.reason,
                slot_spent=False,
                slot=result.slot.value if result.slot is not None else None,
                day=result.day,
                out_of_days=result.out_of_days,
            ).model_dump(),
        )
        if not result.approved:
            self.emit(EventType.PANEL, self.panel().model_dump())
            return
        definition = self.engine.active_event()
        if definition:
            for line in definition.presentation:
                self._add_passage(line.speaker, line.text)
        self._emit_state()
        self.save()

    def _job_close_out_day(self, turn: _Turn) -> None:
        """Turn the day over. No line, no tick — the interlude is not a turn."""
        self._passages = []
        try:
            result = self.engine.close_out_day()
        except Exception as exc:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="dialogue",
                    error_type=type(exc).__name__,
                    message=str(exc),
                ).model_dump(),
            )
            return

        world = self.manager.snapshot()
        self.emit(
            EventType.MOVE,
            MovePayload(
                turn_id=turn.turn_id,
                approved=result.approved,
                # Not travel, but the same shape of answer: an act, whether it landed, and
                # the clock afterwards. A second near-identical event type would buy the
                # front end nothing but another branch.
                destination=world.player_locations.get(self.player_id, ""),
                name="",
                reason=result.reason,
                # The wrap-up spends nothing; this only turns the day over.
                slot_spent=False,
                slot=result.slot.value if result.slot is not None else None,
                day=result.day,
                out_of_days=result.out_of_days,
            ).model_dump(),
        )
        if result.approved:
            self._day_start = self._day_baseline()
            tick = self.engine.refresh_authored(self.player_id)
            if tick:
                self.narrative_traces.save(tick)
            self._collect_presentation()
        self._emit_state()
        # A day boundary is as much a resumable point as a turn boundary, and the world is
        # consistent here for the same reason: nothing else is mid-write on this thread.
        self.save()

    def _job_tick(self, turn: _Turn) -> None:
        """The narrative decision. Its ~8.6s land here, off the player's wait."""
        try:
            tick = self.engine.tick(player_id=self.player_id)
        except Exception as exc:
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id=turn.turn_id,
                    stage="tick",
                    error_type=type(exc).__name__,
                    message=str(exc),
                ).model_dump(),
            )
            return

        self.narrative_traces.save(tick)
        self._collect_presentation()
        entry = operator_entry(tick)
        rejected = rejected_from_tick(tick)
        self._timeline.append(entry)
        self._rejected.extend(rejected)

        self.emit(
            EventType.NARRATIVE_TICK,
            NarrativeTickPayload(
                turn_id=turn.turn_id, entry=entry, rejected_proposals=rejected
            ).model_dump(),
        )
        self._emit_state()
        # The turn is now closed and the world is consistent: the only safe point to
        # write a save, and still on the session thread.
        self.save()

    # --- save / resume -------------------------------------------------------

    def save(self) -> None:
        """Persist enough to carry this playthrough into a later session.

        Called at the end of the tick job, which is the turn boundary: the tick is the
        last writer of a turn (``advance_turn`` closes it), so saving here cannot
        capture a world that is halfway through one. It runs on the session thread for
        the same reason every other world access does — ``snapshot()`` deep-copies but
        can still copy a logically inconsistent world if a writer is mid-turn.

        Separate files because they have different owners: the world
        (``WorldStateManager``), each NPC's private memory (``MemoryStore``, which by
        design never enters ``WorldState``), and the conversation as the player read it.
        Failures are swallowed and reported as a panel-visible event rather than
        killing the turn: a save is a convenience, and losing one is not worth losing
        the interaction that just happened.
        """
        directory = self._save_dir
        try:
            self.manager.save(directory / "world.json")
            for npc_id, runtime in self._npcs.items():
                runtime.memory.save(directory / f"memory_{npc_id}.json")
            meta = {
                "session_id": self.session_id,
                "scenario": self.scenario,
                # Focus used when the save is resumed; this is not a session-wide
                # identity, since each NPC owns an independent runtime and memory.
                "npc_id": self._resolve_current_npc_id(),
                "player_id": self.player_id,
                "turn": self.manager.snapshot().story_beats.turn,
                "saved_at": utc_now().isoformat(),
                "transcript": self.transcript,
                "day_start": self._day_start,
            }
            path = directory / "session.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:  # a lost save must not cost the player their turn
            self.emit(
                EventType.TURN_FAILED,
                TurnFailedPayload(
                    turn_id="",
                    stage="tick",
                    error_type=type(exc).__name__,
                    message=f"存档失败：{exc}",
                ).model_dump(),
            )

    @property
    def _save_dir(self) -> Path:
        return self._save_root / self.session_id

    # --- helpers -------------------------------------------------------------

    def _emit_state(self) -> None:
        """Push fresh snapshots after the world moved (docs/12 §3.3: no diffs)."""
        self.emit(EventType.SCENE, self.scene().model_dump())
        self.emit(EventType.PANEL, self.panel().model_dump())

    def _day_baseline(self) -> dict[str, Any]:
        world = self.manager.snapshot()
        return {
            "day": world.time_day,
            "known": dict(self.manager.player_view(self.player_id).visible_facts),
            "relationships": {
                npc_id: self.manager.get_relationship(npc_id, self.player_id).model_dump(
                    mode="json"
                )
                for npc_id in world.npcs
            },
        }

    def _record_npc_activity(self, trace: AgentTrace) -> None:
        activity = self._npc_activity.setdefault(trace.npc_id, {"proposals": []})
        activity["retrieved_memory"] = [m.model_dump() for m in memory_hits(trace)]
        activity["last_turn_cost"] = turn_cost(trace).model_dump()
        activity["last_dialogue"] = trace.final_dialogue
        activity["last_turn"] = self.manager.snapshot().story_beats.turn
        activity["steps"] = [step.model_dump(mode="json") for step in trace.steps]
        for step in trace.steps:
            if step.step_name == "action_validation":
                activity["proposals"].append(
                    {"turn": activity["last_turn"], **step.input_summary, **step.output_summary}
                )
        activity["proposals"] = activity["proposals"][-40:]

    def _npc_panels(self, world: WorldState) -> list[dict[str, Any]]:
        rows = []
        for npc_id, npc in world.npcs.items():
            runtime = self._npcs.get(npc_id)
            relationships = {
                target: rel.model_dump(mode="json")
                for target, rel in world.relationships.get(npc_id, {}).items()
            }
            relationships.setdefault(
                self.player_id,
                self.manager.get_relationship(npc_id, self.player_id).model_dump(mode="json"),
            )
            events = []
            for event in self.engine.script.events.values():
                if event.npc_id != npc_id:
                    continue
                events.append(
                    {
                        "event_id": event.event_id,
                        "operator": event.operator.value,
                        "delivery": event.delivery,
                        "locations": event.locations,
                        "active": bool(
                            world.story_beats.active_event
                            and world.story_beats.active_event.event_id == event.event_id
                        ),
                        "completed": event.event_id in world.story_beats.completed_events,
                        "trigger": event.trigger.model_dump(mode="json"),
                    }
                )
            rows.append(
                {
                    "npc_id": npc_id,
                    "name": npc.display_name,
                    "world_state": npc.model_dump(mode="json"),
                    "agent_state": runtime.harness.state_snapshot() if runtime else {},
                    "relationships": relationships,
                    "relationship_trend": self._npc_trends.get(npc_id, [])[-40:],
                    "memory": runtime.memory.snapshot()
                    if runtime
                    else {"episodic": [], "semantic": []},
                    "tools": runtime.tool_names if runtime else [],
                    "events": events,
                    **self._npc_activity.get(npc_id, {}),
                }
            )
        return rows

    def _sample_relationship(self) -> None:
        world = self.manager.snapshot()
        for npc_id in world.npcs:
            rel = self.manager.get_relationship(npc_id, self.player_id)
            point = {
                "turn": world.story_beats.turn,
                "trust": rel.trust,
                "fear": rel.fear,
                "respect": rel.respect,
            }
            points = self._npc_trends.setdefault(npc_id, [])
            if not points or points[-1] != point:
                points.append(point)
            del points[:-40]
        self._relationship_trend = self._npc_trends.get(self._resolve_current_npc_id(), [])

    @property
    def npc_name(self) -> str:
        """The NPC's public display name (``NPCWorldState.name``)."""
        npc_id = self._resolve_current_npc_id()
        npc = self.display.npcs.get(npc_id)
        return npc.name if npc is not None else (npc_id or "")
