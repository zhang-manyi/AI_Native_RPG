"""Every ending must be reachable, and the loader is what says so (docs/13 §11).

The same content bug has happened three times, and each time it was invisible except as a
scene that never came:

* ``quests.investigation.stage`` had an author and readers and no writer;
* ``loren_that_night``'s second channel needed a player who could move, and he could not;
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
        """Five: the three of docs/14 §4 plus 指控错人, plus the clock's own ending.

        ``never_found_out`` is named by docs/15 §4 M7 and docs/14 §4.3 and was missing from
        this list — the same omission in a new place, since the other four were checked for
        reachability and this one was not declared at all.
        """
        endings = load_endings(PACK)

        assert {e.ending_id for e in endings} == {
            "truth_uncovered",
            "accused_the_wrong_man",
            "loren_moves_first",
            "marta_clams_up",
            "never_found_out",
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

    def test_clamming_up_is_a_milestone_not_an_ending(self):
        """docs/14 §4.3: 闭口不等于失败终局.

        It shuts Marta's social line while the tavern and the forest remain, and
        『只有天数用尽仍未查明才是终局』. Listing it as a peer of 查明真相 would have a panel
        render a **cost** as a **conclusion**, and tell the player the game is over while he
        is still in it. This is also the reason `loren_that_night`'s second channel exists.
        """
        clams_up = next(e for e in load_endings(PACK) if e.ending_id == "marta_clams_up")

        assert not clams_up.terminal

    def test_the_case_has_four_terminal_endings(self):
        terminal = {e.ending_id for e in load_endings(PACK) if e.terminal}

        assert terminal == {
            "truth_uncovered",
            "accused_the_wrong_man",
            "loren_moves_first",
            "never_found_out",
        }

    def test_terminal_defaults_to_true(self):
        """The surprising claim is the milestone, so an author has to say so explicitly."""
        for ending in load_endings(PACK):
            if ending.ending_id != "marta_clams_up":
                assert ending.terminal

    def test_a_milestone_is_still_checked_for_reachability(self, pack):
        """Not terminal does not mean not verified: ``fear`` genuinely has to reach 70."""
        world, script = pack
        clams_up = next(e for e in load_endings(PACK) if e.ending_id == "marta_clams_up")

        assert (
            unreachable_clauses({"marta_clams_up": clams_up.condition}, world=world, script=script)
            == []
        )

    def test_being_moved_on_needs_him_to_know_you_are_asking(self):
        """docs/14 §4.3 gives two clauses and the first declaration had only one.

        Tension alone is "the story got tense"; tension *and* him knowing someone is asking
        is "他知道你在查，而你还在逼" — only the second is a reason for him to act on the
        player rather than a mood.
        """
        moves_first = next(e for e in load_endings(PACK) if e.ending_id == "loren_moves_first")
        paths = {c.path for c in moves_first.condition.clauses}

        assert paths == {
            "story_beats.tension",
            "facts.npc_b_aware_of_investigation.visibility",
        }
        assert moves_first.condition.mode == "all"

    def test_the_awareness_clause_is_earned_on_the_way(self, pack):
        """It unlocks at tension 0.6, so it is not a second grind toward 0.8."""
        world, _ = pack
        condition = world.facts["npc_b_aware_of_investigation"].reveal_condition

        assert condition is not None
        assert any(c.value <= 0.8 for c in condition.clauses)

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

        M5 gave ``story_beats.tension`` a writer (a failed [追问] against Loren at the
        forest), so the pending ending's real gap moved to its other clause:
        ``npc_b_aware_of_investigation`` is still nothing's to reveal until M6 lands.
        """
        world, script = pack

        problems = unreachable_clauses(
            {
                "loren_moves_first": _gate("story_beats.tension", 0.8),
                "loren_moves_first_full": Condition(
                    mode="all",
                    clauses=[
                        ConditionClause(path="story_beats.tension", op=ConditionOp.GTE, value=0.8),
                        ConditionClause(
                            path="facts.npc_b_aware_of_investigation.visibility",
                            op=ConditionOp.EQ,
                            value="revealed",
                        ),
                    ],
                ),
            },
            world=world,
            script=script,
        )

        assert len(problems) == 1
        assert "npc_b_aware_of_investigation" in problems[0].path


class TestAClauseAlreadyTrueNeedsNoWriter:
    """The question is "can this clause hold", not "can this value move".

    The two come apart for failure endings, which are built out of clauses asking the world
    to have *not* changed. "``ella_whereabouts`` is still hidden" is satisfied at turn 0, so
    demanding a writer for it would report the one ending nobody has to earn as unreachable
    — the checker being wrong in the opposite direction from the bugs it exists to catch.
    """

    def test_the_ran_out_of_days_ending_is_reachable(self, pack):
        world, script = pack
        condition = next(
            e.condition for e in load_endings(PACK) if e.ending_id == "never_found_out"
        )

        assert unreachable_clauses({"never_found_out": condition}, world=world, script=script) == []

    def test_a_stay_hidden_clause_passes_without_a_revealer(self, pack):
        """``npc_a_threatened`` has no outcome revealing it yet (M6 is unwritten), and that
        is fine here — a clause asking it to stay hidden needs no writer at all."""
        world, script = pack
        stay_hidden = Condition(
            mode="all",
            clauses=[
                ConditionClause(
                    path="facts.npc_a_threatened.visibility", op=ConditionOp.EQ, value="hidden"
                )
            ],
        )

        assert unreachable_clauses({"x": stay_hidden}, world=world, script=script) == []

    def test_but_a_become_revealed_clause_still_needs_one(self, pack):
        """The same fact, the other direction: nothing reveals it yet, so this cannot hold.

        ``ella_whereabouts`` no longer serves as this example: M7's ``told_loren_truth``
        outcome reveals it now (docs/15 §4 M7), so a clause asking it to become revealed
        is reachable and would make a poor negative case.
        """
        world, script = pack
        must_reveal = Condition(
            mode="all",
            clauses=[
                ConditionClause(
                    path="facts.npc_a_threatened.visibility", op=ConditionOp.EQ, value="revealed"
                )
            ],
        )

        problems = unreachable_clauses({"x": must_reveal}, world=world, script=script)

        assert len(problems) == 1
        assert "npc_a_threatened" in problems[0].reason

    def test_the_clock_ending_needs_no_events_at_all(self, pack):
        """``time_day`` turns over at each wrap-up regardless of what the player does.

        That is what gives the slot budget teeth: it is the one ending careful play cannot
        avoid, so it needs no authored event behind it.
        """
        world, script = pack

        problems = unreachable_clauses({"clock": _gate("time_day", 5)}, world=world, script=script)

        assert problems == []


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
        """A checker property, not a pack fact: pick an id no event could plausibly
        write rather than a real NPC, since which cast members have writers is
        exactly what changes as the script grows (M5 gave ``npc_b`` one)."""
        world, script = pack

        problems = unreachable_clauses(
            {"needs_npc_x": _gate(f"relationships.npc_x.{PLAYER}.trust", 50)},
            world=world,
            script=script,
        )

        assert len(problems) == 1
        assert "npc_x" in problems[0].reason

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

    def test_a_tension_change_outcome_makes_tension_movable(self, pack):
        """The pack side of the same claim.

        Any outcome may carry ``tension_change`` (docs/15 §4 M5: a failed [追问] against
        Loren at the forest raises it), not only an `escalate`-operator event — so the
        writer check has to look at outcomes, not at the operator label.
        """
        _, script = pack
        assert "story_beats.tension" in writers_for_paths(script).prefixes


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
