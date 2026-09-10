"""The runtime ending check (docs/14 §4, docs/15 §4 M7's third trigger: 天数用尽).

``load_endings``/``unreachable_clauses`` (tested in ``test_reachability.py``) only check
that a declared ending *could* fire — that some writer exists for every clause. Nothing
there exercises the code path that actually records it during play: ``NarrativeEngine.
_check_endings()``, called from every player action and from ``tick``. This file is the
missing runtime half, and it doubles as regression coverage for the bug that made every
one of these silently no-op until fixed in this change: ``end_case`` was missing from
``world/validator.py``'s ``_BEAT_ADVANCE_KEYS``, so the very ``ADVANCE_STORY_BEAT``
proposal meant to write ``story_beats.ended_at`` was rejected before the apply step ran,
however correct ``check_terminal_ending``'s own answer was.

``truth_uncovered`` and ``accused_the_wrong_man`` are covered in ``test_event_m7.py``
alongside the event mechanics that reach them. This file covers the two that do not run
through M7 at all: the clock's own ending, and the milestone that must *not* become one.
"""

from __future__ import annotations

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import NarrativeEngine
from ai_native_rpg.scenario import (
    load_endings,
    load_event_script,
    load_narrative_directives,
    load_scenario,
)
from ai_native_rpg.schemas.narrative import SLOTS_PER_DAY
from ai_native_rpg.world.manager import WorldStateManager

PACK = "village_disappearance"
PLAYER = "player_1"
NPC_A = "npc_a"

# A move that always lands: the player starts at npc_a_house, and the square connects
# back to it, so bouncing between the two spends slots without needing the forest or
# the tavern's own trigger machinery to cooperate.
_HOME = "npc_a_house"
_SQUARE = "village_square"


def _content() -> dict:
    return {"summary": "s", "dialogue_hook": "h", "participants": []}


def _engine() -> tuple[NarrativeEngine, WorldStateManager]:
    world = load_scenario(PACK)
    manager = WorldStateManager(world)
    engine = NarrativeEngine(
        manager=manager,
        llm=MockLLMClient([_content() for _ in range(80)]),
        directives=load_narrative_directives(PACK),
        script=load_event_script(PACK),
        endings=load_endings(PACK),
    )
    return engine, manager


def _spend_one_day(engine: NarrativeEngine) -> None:
    """Walk back and forth until the day's slots are gone, then step into the next."""
    destinations = [_SQUARE, _HOME, _SQUARE]
    for destination in destinations[:SLOTS_PER_DAY]:
        engine.move_player(player_id=PLAYER, destination=destination)


class TestNeverFoundOut:
    def test_running_out_of_days_records_the_ending(self):
        """docs/14 §4.3: the clock's own ending, reachable with no event at all.

        The condition is ``time_day > 4`` (docs/15 §6's slot-budget arithmetic — the
        pack's day count, not ``StoryBeats.day_limit``, which only gates ``out_of_days``
        for the wrap-up flow), so the case must run its full four days with nothing
        found before this fires.
        """
        engine, manager = _engine()

        for _ in range(5):
            _spend_one_day(engine)
            engine.close_out_day()

        # Once the deadline ends the case, further travel must not advance the clock.
        assert manager.snapshot().time_day == 5
        assert manager.snapshot().story_beats.ended_at == "never_found_out"

    def test_the_case_stays_open_while_days_remain(self):
        engine, manager = _engine()

        _spend_one_day(engine)

        assert manager.snapshot().time_day == 1
        assert manager.snapshot().story_beats.ended_at is None

    def test_travel_after_the_ending_is_refused_and_does_not_disturb_it(self):
        """``_check_endings`` runs after every ``move_player`` too (docs/14 §4.3's note
        that this ending needs no event), not only from ``close_out_day`` — this pins
        that a move made once the deadline is already past neither breaks nor re-derives
        a different answer."""
        engine, manager = _engine()
        for _ in range(5):
            _spend_one_day(engine)
            engine.close_out_day()
        assert manager.snapshot().story_beats.ended_at == "never_found_out"

        result = engine.move_player(player_id=PLAYER, destination=_SQUARE)

        assert not result.approved
        assert manager.snapshot().story_beats.ended_at == "never_found_out"


class TestMartaClamsUp:
    """docs/14 §4.3: 闭口 is a milestone (``terminal: false``), not an ending — reaching
    it must not set ``ended_at``, only the milestone flag."""

    def test_reaching_the_fear_threshold_raises_the_milestone_flag_not_ended_at(self):
        engine, manager = _engine()
        world = manager.snapshot()
        world.relationships[NPC_A][PLAYER].fear = 70.0
        manager = WorldStateManager(world)
        engine = NarrativeEngine(
            manager=manager,
            llm=MockLLMClient([_content() for _ in range(4)]),
            directives=load_narrative_directives(PACK),
            script=load_event_script(PACK),
            endings=load_endings(PACK),
        )

        engine.move_player(player_id=PLAYER, destination=_SQUARE)

        snapshot = manager.snapshot()
        assert snapshot.story_beats.ended_at is None
        assert "milestone:marta_clams_up" in snapshot.story_beats.flags

    def test_the_case_stays_playable_after_clamming_up(self):
        """The tavern and forest channels remain open — the point of the milestone
        distinction (docs/14 §4.3)."""
        engine, manager = _engine()
        world = manager.snapshot()
        world.relationships[NPC_A][PLAYER].fear = 70.0
        manager = WorldStateManager(world)
        engine = NarrativeEngine(
            manager=manager,
            llm=MockLLMClient([_content() for _ in range(4)]),
            directives=load_narrative_directives(PACK),
            script=load_event_script(PACK),
            endings=load_endings(PACK),
        )
        engine.move_player(player_id=PLAYER, destination=_SQUARE)

        # Still able to act: a further move is accepted, not refused as "game over".
        result = engine.move_player(player_id=PLAYER, destination="tavern")

        assert result.approved

    def test_the_milestone_flag_is_not_raised_twice(self):
        engine, manager = _engine()
        world = manager.snapshot()
        world.relationships[NPC_A][PLAYER].fear = 70.0
        manager = WorldStateManager(world)
        engine = NarrativeEngine(
            manager=manager,
            llm=MockLLMClient([_content() for _ in range(4)]),
            directives=load_narrative_directives(PACK),
            script=load_event_script(PACK),
            endings=load_endings(PACK),
        )
        engine.move_player(player_id=PLAYER, destination=_SQUARE)
        first_flags = list(manager.snapshot().story_beats.flags)

        engine.move_player(player_id=PLAYER, destination="tavern")

        assert manager.snapshot().story_beats.flags.count("milestone:marta_clams_up") == 1
        assert first_flags.count("milestone:marta_clams_up") == 1
