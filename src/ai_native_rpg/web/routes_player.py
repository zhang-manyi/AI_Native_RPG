"""Player-facing routes (docs/12 §4.1).

**This module must not import ``WorldState``.** The player's response models are typed
against ``SceneView``, which is built from ``VisibleState`` plus static public labels,
and a test asserts the absence of that import (docs/07 §2.4, docs/12 §7 item 2). A
type signature is a mechanism; "remember to project first" is not.

The turn endpoint returns ``202`` and no dialogue. Only *admission* failures answer
synchronously — no such session, empty text, a turn already in flight. Everything
renderable arrives on the event stream, because the narrative tick has no request of
its own to fail (docs/12 §3.2).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..scenario import ScenarioError, list_scenarios
from .events import KEEPALIVE_SECONDS, EventType, HelloPayload, keepalive_frame
from .registry import SessionRegistry
from .scene import SceneView
from .session import SessionError, list_saves
from .wrap_up_view import WrapUpView

router = APIRouter(prefix="/api", tags=["player"])


class CreateSessionRequest(BaseModel):
    scenario: str | None = Field(
        default=None, description="pack under scenarios/; defaults to the first available"
    )
    npc_id: str | None = Field(
        default=None,
        description="initial focus hint; dialogue still follows the player's location",
    )
    resume_from: str | None = Field(
        default=None,
        description="save id to continue from (see GET /api/saves). The scenario must "
        "match the save; omitted starts the pack from its opening state.",
    )


class CreateSessionResponse(BaseModel):
    session_id: str
    scenario: str
    npc_id: str | None = Field(
        default=None, description="NPC co-located with the player when the session opens"
    )
    dev_mode: bool
    resumed_from: str | None = Field(
        default=None, description="the save this session continues, if any"
    )
    turn: int = Field(default=0, description="turns already played, non-zero on a resume")


class TurnRequest(BaseModel):
    text: str
    option_id: str | None = Field(
        default=None,
        description="set when the player clicked a tagged option rather than typing. Same "
        "endpoint either way (docs/12 §13.7): two submit paths would let clicking and "
        "typing be judged differently, and docs/15 §1.1 exists so typing is not the worse "
        "deal. An id the active event does not offer is dropped downstream, which lands the "
        "authored default rather than inventing an outcome.",
    )


class MoveRequest(BaseModel):
    destination: str = Field(
        description="location id to walk to. Legality is the Validator's answer, given on "
        "the ``move`` event — this endpoint does not pre-screen it, because a check here "
        "would be a second copy of the adjacency rule (docs/12 §13.2)."
    )


class TurnResponse(BaseModel):
    turn_id: str


def _registry(request: Request) -> SessionRegistry:
    return request.app.state.registry


def _session(request: Request, session_id: str):
    try:
        return _registry(request).get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no session {session_id!r}") from None


@router.get("/scenarios")
def get_scenarios() -> dict[str, list[str]]:
    """Packs available to start. Dropping a directory in ``scenarios/`` is enough."""
    return {"scenarios": list_scenarios()}


@router.post("/session", response_model=CreateSessionResponse)
def create_session(body: CreateSessionRequest, request: Request) -> CreateSessionResponse:
    registry = _registry(request)
    scenario = body.scenario or next(iter(list_scenarios()), None)
    if scenario is None:
        raise HTTPException(status_code=400, detail="no scenario packs found under scenarios/")

    try:
        session = registry.create(
            scenario=scenario,
            npc_id=body.npc_id,
            resume_from=body.resume_from,
            # Captured at startup: this handler is sync, so it runs in a threadpool
            # where there is no running loop to ask for.
            loop=getattr(request.app.state, "loop", None),
        )
    except (ScenarioError, SessionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CreateSessionResponse(
        session_id=session.session_id,
        scenario=session.scenario,
        npc_id=session.npc_id,
        dev_mode=session.dev_mode,
        resumed_from=session.resumed_from,
        # From the scene, not the world: this module may not import WorldState.
        turn=session.scene().turn,
    )


@router.get("/saves")
def get_saves() -> dict[str, list[dict]]:
    """Resumable playthroughs, newest first.

    Player-facing rather than under ``/debug`` because picking up a story where you
    left it is playing, not inspecting. Only the pack, the NPC and how far the run got
    are listed — nothing here is gated content.
    """
    return {"saves": list_saves()}


@router.get("/session/{session_id}/scene", response_model=SceneView)
def get_scene(session_id: str, request: Request) -> SceneView:
    """Scene snapshot. Also the no-JS way to see what the player would see."""
    return _session(request, session_id).scene()


@router.post("/session/{session_id}/turn", response_model=TurnResponse, status_code=202)
def post_turn(session_id: str, body: TurnRequest, request: Request) -> TurnResponse:
    """Admit one utterance. The line arrives on the event stream, not here."""
    session = _session(request, session_id)
    try:
        turn_id = session.submit_turn(body.text, option_id=body.option_id)
    except SessionError as exc:
        # 409 for "already talking": the front end also disables the input, but the
        # server stays the authority on whether a turn is in flight (docs/12 §5.3).
        status = 409 if "in flight" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return TurnResponse(turn_id=turn_id)


@router.get("/session/{session_id}/wrap_up", response_model=WrapUpView | None)
def get_wrap_up(session_id: str, request: Request) -> WrapUpView | None:
    """The day's review and reading, or ``null`` outside the wrap-up.

    ``null`` rather than a 404: the session exists and the question is legitimate, the
    answer is simply "not right now". Once a day is what makes it a ritual instead of a
    screen (docs/13 §4.2), and that cadence belongs to the engine — this endpoint reports
    it rather than deciding it.
    """
    return _session(request, session_id).wrap_up()


@router.post("/session/{session_id}/wrap_up/close", response_model=TurnResponse, status_code=202)
def post_close_out_day(session_id: str, request: Request) -> TurnResponse:
    """Dismiss the wrap-up and start the next morning.

    Separate from reading it, so that seeing the interlude and leaving it are two acts: a
    call that advanced the clock as a side effect of computing the review could never show
    the review.
    """
    session = _session(request, session_id)
    try:
        turn_id = session.submit_close_out_day()
    except SessionError as exc:
        status = 409 if "in flight" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return TurnResponse(turn_id=turn_id)


@router.post("/session/{session_id}/move", response_model=TurnResponse, status_code=202)
def post_move(session_id: str, body: MoveRequest, request: Request) -> TurnResponse:
    """Admit a move. The outcome arrives as a ``move`` event, refusals included.

    Same shape as ``/turn`` because it is the same kind of thing: a player action that
    writes world state, so it queues on the session's single thread and answers ``202``.
    A move that the Validator refuses is **not** a 4xx here — it was admitted, it ran, and
    it has an answer the player should read ("那边过不去"). Only admission failures answer
    synchronously (docs/12 §3.2).
    """
    session = _session(request, session_id)
    try:
        turn_id = session.submit_move(body.destination)
    except SessionError as exc:
        status = 409 if "in flight" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return TurnResponse(turn_id=turn_id)


@router.post("/session/{session_id}/conclude", response_model=TurnResponse, status_code=202)
def post_conclude_case(session_id: str, request: Request) -> TurnResponse:
    """Open the conclusion event on the player's own initiative (docs/15 §4 M7).

    Same shape as ``/move``: a refusal (another conversation already running, or the pack
    declaring no conclusion event) is not a 4xx — it was admitted and answered on the
    stream — only "a turn is already in flight" is (docs/12 §3.2).
    """
    session = _session(request, session_id)
    try:
        turn_id = session.submit_conclude_case()
    except SessionError as exc:
        status = 409 if "in flight" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return TurnResponse(turn_id=turn_id)


@router.get("/session/{session_id}/events")
async def get_events(session_id: str, request: Request):
    """The SSE stream. One channel for dialogue, ticks, scene and panel refreshes."""
    session = _session(request, session_id)
    registry = _registry(request)

    raw_last = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    try:
        last_event_id = int(raw_last) if raw_last else None
    except ValueError:
        last_event_id = None

    queue_ = session.subscribe(last_event_id=last_event_id)

    hello = HelloPayload(
        session_id=session.session_id,
        dev_mode=session.dev_mode,
        turn=session.scene().turn,
        scene=session.scene().model_dump(),
        backend={**session.backend, "embedder": registry.embedder_label},
        panel=session.panel() if session.dev_mode else None,
    )

    # Set during lifespan shutdown. Without watching it, Ctrl+C hangs: uvicorn waits
    # for open connections to finish, an SSE stream by design never finishes, and each
    # is waiting for the other. The stream has to volunteer to end.
    shutting_down = getattr(request.app.state, "shutdown_event", None)

    # Watching the flag inside the generator is necessary but not sufficient: Starlette
    # runs the body against ``listen_for_disconnect``, which waits on the client and so
    # never wakes, leaving the generator unresumed. Registering the task lets lifespan
    # cancel it outright, which is what actually releases the connection.
    live_streams = getattr(request.app.state, "live_streams", None)

    async def stream():
        # Register this stream's task so shutdown can cancel it. Done from inside the
        # generator because that is where the task actually running the body is current.
        task = asyncio.current_task()
        if live_streams is not None and task is not None:
            live_streams.add(task)

        # The greeting is written directly rather than emitted, so that each
        # connection gets its own current snapshot instead of replaying someone
        # else's.
        yield _frame(EventType.HELLO, hello.model_dump())
        try:
            while True:
                if await request.is_disconnected():
                    return
                if shutting_down is not None and shutting_down.is_set():
                    return
                # Wait on the next event *or* on shutdown, whichever comes first.
                # Waiting on the queue alone would leave Ctrl+C up to 15s of keepalive
                # timeout away from being noticed.
                event = await _next_event(queue_, shutting_down)
                if event is None:
                    if shutting_down is not None and shutting_down.is_set():
                        return
                    yield keepalive_frame()
                    continue
                yield event.frame()
        except asyncio.CancelledError:
            # Shutdown cancels the task outright; ending quietly lets uvicorn proceed
            # instead of logging a traceback per open stream.
            raise
        finally:
            session.unsubscribe(queue_)
            if live_streams is not None and task is not None:
                live_streams.discard(task)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Defeats proxy buffering, which otherwise holds frames until the
            # response ends — i.e. defeats the entire point of streaming.
            "X-Accel-Buffering": "no",
        },
    )


async def _next_event(queue_, shutting_down):
    """The next event, or ``None`` on keepalive timeout or shutdown.

    Races the queue against the shutdown flag so a quiet stream reacts to Ctrl+C at
    once rather than after a full keepalive interval. The loser is cancelled, so a
    shutdown does not leave a pending ``queue.get()`` behind.
    """
    getter = asyncio.ensure_future(queue_.get())
    waiters = [getter]
    if shutting_down is not None:
        waiters.append(asyncio.ensure_future(shutting_down.wait()))

    done, pending = await asyncio.wait(
        waiters, timeout=KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()

    if getter in done:
        return getter.result()
    return None


def _frame(type_: EventType, data: dict) -> str:
    """Frame an event that was never queued (the per-connection greeting)."""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {type_.value}\ndata: {body}\n\n"
