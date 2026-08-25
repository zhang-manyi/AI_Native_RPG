"""Every ending must be reachable, and the loader is what says so (docs/13 §11).

The same content bug has happened three times, and each time it was invisible except as a
scene that never came:

* ``quests.investigation.stage`` had an author and readers and no writer;
* ``killer_identity``'s second channel needed a player who could move, and he could not;
* ``fear`` gated the clam-up ending while NPCs only ever adjusted trust.

One shape: an author wrote a road and nobody checked it went anywhere. The loader already
refused an unresolvable ``path`` — and that is far too weak, because **all three of those
paths resolve perfectly**. The missing question is whether the value can ever change.

The test worth reading first is ``test_a_dimension_nothing_writes_is_rejected``: it is the
``fear`` bug, reproduced as a pack, and it must fail to load.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ai_native_rpg.reachability import (
    ENGINE_WRITTEN_PREFIXES,
    unreachable_clauses,
    writers_for_paths,
)
from ai_native_rpg.scenario import (
    ScenarioError,
    load_endings,
    load_event_script,
    load_scenario,
)
from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp

PACK = "village_disappearance"
PLAYER = "player_1"
NPC_A = "npc_a"


def _gate(path: str, value: float = 1) -> Condition:
    return Condition(
        mode="all", clauses=[ConditionClause(path=path, op=ConditionOp.GTE, value=value)]
    )


@pytest.fixture
def pack():
    return load_scenario(PACK), load_event_script(PACK)


class TestTheShippedPacksEndings:
    """The village pack declares its endings and they check out."""

    def test_the_pack_declares_its_endings(self):
        endings = load_endings(PACK)

        assert {e.ending_id for e in endings} == {
            "truth_uncovered",
            "accused_the_wrong_man",
            "loren_moves_first",
            "marta_clams_up",
        }

    def test_every_non_pending_ending_is_reachable(self, pack):
        world, script = pack
        live = {e.ending_id: e.condition for e in load_endings(PACK) if not e.pending}

        assert unreachable_clauses(live, world=world, script=script) == []

    def test_the_clam_up_ending_is_among_the_reachable_ones(self, pack):
        """The bug this file exists for: ``fear`` is read here and now written.

        Before the floor in ``resolution.apply_outcome``, this ending's gate depended on a
        model volunteering the right dimension, and it did not.
        """
        world, script = pack

        problems = unreachable_clauses(
            {"marta_clams_up": _gate(f"relationships.{NPC_A}.{PLAYER}.fear", 70)},
            world=world,
            script=script,
        )

        assert problems == []

    def test_the_stage_channel_is_reachable(self, pack):
        """The first of the three historical bugs, as a check."""
        world, script = pack

        problems = unreachable_clauses(
            {"truth": _gate("quests.investigation.stage", 3)}, world=world, script=script
        )

        assert problems == []

    def test_the_unfinished_ending_says_so(self):
        """Declared and flagged, rather than omitted.

        Leaving it out would make the check pass and lose the record — which is exactly
        how a road ends up written only in a document.
        """
        pending = [e for e in load_endings(PACK) if e.pending]

        assert [e.ending_id for e in pending] == ["loren_moves_first"]
        assert "M6" in pending[0].path_note

    def test_the_pending_ending_would_otherwise_fail(self, pack):
        """It is genuinely unreachable, so the flag is carrying real weight.

        Nothing raises tension until an `escalate` event exists, and M6 is the next batch.
        """
        world, script = pack

        problems = unreachable_clauses(
            {"loren_moves_first": _gate("story_beats.tension", 0.8)},
            world=world,
            script=script,
        )

        assert len(problems) == 1
        assert "story_beats.tension" in str(problems[0])


class TestResolvableIsNotEnough:
    """Why the existing loader check could not have caught any of the three bugs."""

    def test_all_three_historical_paths_resolve_cleanly(self, pack):
        """Stated directly, because it is the reason this module exists."""
        from ai_native_rpg.world.conditions import resolve_path

        world, _ = pack

        for path in (
            "quests.investigation.stage",
            f"relationships.{NPC_A}.{PLAYER}.fear",
            f"relationships.{NPC_A}.{PLAYER}.respect",
        ):
            resolve_path(world, path)  # no raise

    def test_but_one_of_them_is_still_unreachable(self, pack):
        """``respect`` resolves and nothing moves it — the shape of all three bugs."""
        world, script = pack

        problems = unreachable_clauses(
            {"needs_respect": _gate(f"relationships.{NPC_A}.{PLAYER}.respect", 50)},
            world=world,
            script=script,
        )

        assert len(problems) == 1
        assert "respect" in problems[0].reason


class TestDimensionsAreTrackedPerNpc:
    """The check has to be ``(npc, dimension)``-shaped, not two independent sets.

    The ``fear`` bug is why: ``relationships.npc_a.*`` was written on nearly every turn
    while ``fear`` specifically was not. A checker that unioned the halves would have said
    "npc_a is written, fear is written" and waved it through.
    """

    def test_an_untouched_npc_is_reported(self, pack):
        world, script = pack

        problems = unreachable_clauses(
            {"needs_npc_b": _gate(f"relationships.npc_b.{PLAYER}.trust", 50)},
            world=world,
            script=script,
        )

        assert len(problems) == 1
        assert "npc_b" in problems[0].reason

    def test_the_reason_names_what_does_move(self, pack):
        """A message that gets fixed rather than shrugged at."""
        world, script = pack

        problems = unreachable_clauses(
            {"needs_respect": _gate(f"relationships.{NPC_A}.{PLAYER}.respect", 50)},
            world=world,
            script=script,
        )

        assert "trust" in problems[0].reason
        assert "fear" in problems[0].reason

    def test_an_incomplete_relationship_path_is_reported(self, pack):
        world, script = pack

        problems = unreachable_clauses(
            {"truncated": _gate(f"relationships.{NPC_A}.trust", 50)}, world=world, script=script
        )

        assert len(problems) == 1

    def test_the_writers_come_from_the_script(self, pack):
        """Derived, not declared, so it cannot drift from what the events do."""
        _, script = pack

        writers = writers_for_paths(script)

        assert (NPC_A, "trust") in writers.relationship_pairs
        assert (NPC_A, "fear") in writers.relationship_pairs
        assert (NPC_A, "respect") not in writers.relationship_pairs

    def test_a_checked_option_makes_fear_movable(self, pack):
        """Even with no outcome naming it: the floor charges it on a failed check."""
        _, script = pack
        writers = writers_for_paths(script)

        assert (NPC_A, "fear") in writers.relationship_pairs


class TestTheEngineTableIsNarrow:
    """A broad prefix is how a dead channel passes, so the table enumerates."""

    def test_tension_is_not_claimed_by_the_engine(self):
        """The Engine has the writer but only fires it for an `escalate` event, so whether
        tension moves is a property of the pack. Claiming it here would pass a pack whose
        escalate event has not been written — the state the village pack is in."""
        assert "story_beats.tension" not in ENGINE_WRITTEN_PREFIXES

    def test_story_beats_is_not_claimed_wholesale(self):
        assert "story_beats." not in ENGINE_WRITTEN_PREFIXES

    def test_every_entry_gives_a_reason(self):
        """The reason is the audit trail: remove a writer, remove the entry."""
        assert all(reason.strip() for reason in ENGINE_WRITTEN_PREFIXES.values())

    def test_the_clock_paths_are_claimed(self):
        """They have writers as of this batch, so a pack may gate on them."""
        assert "story_beats.time_slot" in ENGINE_WRITTEN_PREFIXES
        assert "time_day" in ENGINE_WRITTEN_PREFIXES

    def test_an_escalate_event_makes_tension_movable(self, pack):
        """The pack side of the same claim."""
        _, script = pack
        assert "story_beats.tension" not in writers_for_paths(script).prefixes


class TestTheLoaderRefusesADeadEnding:
    """The whole point: a startup message rather than a mystery in play."""

    def _write_pack(self, tmp_path: Path, endings_yaml: str) -> Path:
        """A minimal pack whose only relationship writer is ``trust``.

        That is the shape of the historical bug: something moves, just not the thing the
        ending reads. Note the options are [示好] and [观察] — both unchecked — so not even
        the fear floor applies, and ``fear`` is genuinely immovable here.
        """
        head = textwrap.dedent(
            """
            world_id: broken
            time_day: 1
            locations:
              room:
                name: 房间
                connected_to: []
            players:
              player_1:
                location: room
            quests:
              investigation:
                stage: 0
                status: active
            npcs:
              npc_a:
                name: 玛尔塔
                location: room
            relationships:
              npc_a:
                player_1:
                  trust: 10
            facts:
              a_clue:
                value: 线索
                visibility: revealed
            narrative:
            """
        ).strip()

        tail = textwrap.dedent(
            """
            events:
              M1:
                operator: reveal
                npc_id: npc_a
                max_exchanges: 3
                trigger:
                  clauses:
                    - path: story_beats.turn
                      op: gte
                      value: 0
                default_outcome: nothing
                outcomes:
                  nothing:
                    summary: 没谈成
                  warmed:
                    summary: 她松一点
                    relationship_changes:
                      npc_a:
                        trust: 8
                options:
                  - option_id: kind
                    tag: goodwill
                    text: 我不是来添麻烦的
                    on_success: warmed
                  - option_id: quiet
                    tag: observe
                    text: （看一眼）
                    on_success: nothing
            """
        ).strip()

        narrative_body = textwrap.indent(textwrap.dedent(endings_yaml).strip(), "  ")

        pack = tmp_path / "broken"
        pack.mkdir()
        (pack / "world.yaml").write_text(
            "\n".join([head, narrative_body, tail]) + "\n", encoding="utf-8"
        )
        return pack

    def test_a_dimension_nothing_writes_is_rejected(self, tmp_path):
        """The ``fear`` bug reproduced as a pack. This is the test that matters.

        The events here only ever change ``trust`` — no checked option, so not even the
        fear floor applies — and the ending is gated on ``fear``. That combination shipped
        once, and it must not load.
        """
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: clams_up
                condition:
                  mode: all
                  clauses:
                    - path: relationships.npc_a.player_1.fear
                      op: gte
                      value: 70
            """,
        )

        with pytest.raises(ScenarioError) as exc:
            load_event_script(pack)

        assert "clams_up" in str(exc.value)
        assert "fear" in str(exc.value)

    def test_the_error_says_how_to_record_a_genuine_gap(self, tmp_path):
        """An author whose events really are unwritten needs a way out that keeps the record."""
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: clams_up
                condition:
                  mode: all
                  clauses:
                    - path: relationships.npc_a.player_1.fear
                      op: gte
                      value: 70
            """,
        )

        with pytest.raises(ScenarioError) as exc:
            load_event_script(pack)

        assert "pending" in str(exc.value)

    def test_marking_it_pending_lets_the_pack_load(self, tmp_path):
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: clams_up
                pending: true
                condition:
                  mode: all
                  clauses:
                    - path: relationships.npc_a.player_1.fear
                      op: gte
                      value: 70
            """,
        )

        assert load_event_script(pack) is not None

    def test_a_stale_pending_flag_is_rejected(self, tmp_path):
        """A flag left set after the events land would exempt a real ending forever.

        ``trust`` is moved by this pack's own outcome, so this ending is reachable and the
        flag is a leftover.
        """
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: warmed_up
                pending: true
                condition:
                  mode: all
                  clauses:
                    - path: relationships.npc_a.player_1.trust
                      op: gte
                      value: 50
            """,
        )

        with pytest.raises(ScenarioError) as exc:
            load_event_script(pack)

        assert "warmed_up" in str(exc.value)
        assert "reachable" in str(exc.value)

    def test_an_ending_with_no_clauses_is_rejected(self, tmp_path):
        """An empty condition evaluates False by design, so it can never fire."""
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: never
                condition:
                  mode: all
                  clauses: []
            """,
        )

        with pytest.raises(ScenarioError) as exc:
            load_event_script(pack)

        assert "never" in str(exc.value)

    def test_a_typo_in_an_ending_path_is_rejected(self, tmp_path):
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: typo
                condition:
                  mode: all
                  clauses:
                    - path: relationships.npc_a.player_1.trast
                      op: gte
                      value: 50
            """,
        )

        with pytest.raises(ScenarioError):
            load_event_script(pack)

    def test_a_reachable_ending_loads(self, tmp_path):
        pack = self._write_pack(
            tmp_path,
            """
            endings:
              - ending_id: warmed_up
                condition:
                  mode: all
                  clauses:
                    - path: relationships.npc_a.player_1.trust
                      op: gte
                      value: 50
            """,
        )

        assert load_event_script(pack) is not None

    def test_a_pack_declaring_no_endings_still_loads(self, tmp_path):
        """Absence is valid, as everywhere else in the format — but then nothing is
        verified, so the check is opt-in by having written the endings down."""
        pack = self._write_pack(tmp_path, "language: 中文")

        assert load_event_script(pack) is not None
