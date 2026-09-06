"""Tests for seed memories loaded from the scenario pack.

An NPC that starts with an empty memory has nothing to recall, so ``query_memory``
returns nothing and the tool is dead weight in a fresh conversation. Seed memories
are what the NPC already knew before the player arrived — Martha's sighting, the
threat she is keeping quiet about — and they are the reason retrieval has anything
to find on turn one.

They belong to the pack rather than to code for the same reason personas do: this
is story content, and swapping the story must not require a code change
(docs/09 §3). They are Agent-private state, so they load through
``load_personas``, never into ``WorldState``.
"""

from __future__ import annotations

import textwrap

import pytest

from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.scenario import ScenarioError, load_personas, load_seed_memories

SCENARIO = "village_disappearance"
NPC_A = "npc_a"
NPC_B = "npc_b"
NPC_C = "npc_c"


class TestLoadSeedMemories:
    def test_loads_memories_for_the_reference_scenario(self):
        seeds = load_seed_memories(SCENARIO)
        assert set(seeds) == {NPC_A, NPC_B, NPC_C}

    def test_martha_remembers_what_she_saw(self):
        episodic = load_seed_memories(SCENARIO)[NPC_A].episodic
        assert episodic, "Martha's sighting is the clue chain's root; she must recall it"
        assert any("森林" in m.event_description for m in episodic)

    def test_martha_holds_the_belief_that_keeps_her_quiet(self):
        semantic = load_seed_memories(SCENARIO)[NPC_A].semantic
        assert any("儿子" in m.fact for m in semantic)

    def test_the_sighting_is_high_importance(self):
        """Importance drives retrieval ranking: the memory the whole case turns on
        must outrank small talk written later."""
        episodic = load_seed_memories(SCENARIO)[NPC_A].episodic
        assert max(m.importance for m in episodic) >= 0.8

    def test_memories_are_attributed_to_their_owner(self):
        for npc_id, seeds in load_seed_memories(SCENARIO).items():
            for memory in [*seeds.episodic, *seeds.semantic]:
                assert memory.npc_id == npc_id

    def test_memory_ids_are_unique_within_an_npc(self):
        for seeds in load_seed_memories(SCENARIO).values():
            ids = [m.memory_id for m in [*seeds.episodic, *seeds.semantic]]
            assert len(ids) == len(set(ids))

    def test_loren_knows_his_own_version(self):
        """Both NPCs carry private knowledge, and Loren's is what makes him evasive."""
        seeds = load_seed_memories(SCENARIO)[NPC_B]
        assert seeds.episodic or seeds.semantic

    def test_npcs_without_seed_memories_are_allowed(self):
        """Unlike personas, memories are optional: a bystander NPC may simply have
        nothing relevant to recall."""
        seeds = load_seed_memories(SCENARIO)
        assert all(hasattr(s, "episodic") for s in seeds.values())


class TestSeedMemoriesLoadIntoAStore:
    def test_store_can_be_populated_and_retrieved_from(self):
        seeds = load_seed_memories(SCENARIO)[NPC_A]
        store = MemoryStore(NPC_A)
        seeds.load_into(store)

        assert store.episodic_count == len(seeds.episodic)
        assert store.semantic_count == len(seeds.semantic)

        hits = store.retrieve("那晚 森林 有人", top_k=3)
        assert hits.episodic, "the seeded sighting must be retrievable on turn one"

    def test_store_rejects_seeds_belonging_to_another_npc(self):
        seeds = load_seed_memories(SCENARIO)[NPC_A]
        with pytest.raises(ValueError, match="private"):
            seeds.load_into(MemoryStore(NPC_B))


def _write_pack(tmp_path, *blocks: str):
    """Write a pack from dedented YAML blocks.

    Each block is dedented independently before joining: concatenating first would
    leave the appended blocks indented relative to the base and produce invalid
    YAML rather than the validation error under test.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    body = "".join(textwrap.dedent(block) for block in blocks)
    (pack / "world.yaml").write_text(body, encoding="utf-8")
    return pack


_MINIMAL = """\
    world_id: t
    locations:
      sq: {name: 广场}
    players:
      player_1: {location: sq}
    npcs:
      npc_a: {location: sq}
    npc_personas:
      npc_a:
        persona: {traits: {honest: 0.5}, background: b}
        goal: {primary: g}
"""


class TestValidation:
    def test_memories_for_unknown_npc_fail(self, tmp_path):
        pack = _write_pack(
            tmp_path,
            _MINIMAL,
            """\
            npc_seed_memories:
              ghost:
                episodic:
                  - id: m1
                    description: d
                    importance: 0.5
            """,
        )
        with pytest.raises(ScenarioError, match="ghost"):
            load_seed_memories(pack)

    def test_importance_out_of_range_fails(self, tmp_path):
        pack = _write_pack(
            tmp_path,
            _MINIMAL,
            """\
            npc_seed_memories:
              npc_a:
                episodic:
                  - id: m1
                    description: d
                    importance: 5.0
            """,
        )
        with pytest.raises(ScenarioError, match=r"importance|schema"):
            load_seed_memories(pack)

    def test_duplicate_memory_id_fails(self, tmp_path):
        """A duplicate id would make forget/update silently act on one of two
        memories, so it must fail at load rather than surprise later."""
        pack = _write_pack(
            tmp_path,
            _MINIMAL,
            """\
            npc_seed_memories:
              npc_a:
                episodic:
                  - id: m1
                    description: first
                    importance: 0.5
                  - id: m1
                    description: second
                    importance: 0.5
            """,
        )
        with pytest.raises(ScenarioError, match="m1"):
            load_seed_memories(pack)

    def test_missing_required_field_fails(self, tmp_path):
        pack = _write_pack(
            tmp_path,
            _MINIMAL,
            """\
            npc_seed_memories:
              npc_a:
                episodic:
                  - id: m1
                    importance: 0.5
            """,
        )
        with pytest.raises(ScenarioError, match="description"):
            load_seed_memories(pack)

    def test_absent_section_yields_empty_seeds(self, tmp_path):
        pack = _write_pack(tmp_path, _MINIMAL)
        seeds = load_seed_memories(pack)
        assert seeds["npc_a"].episodic == []
        assert seeds["npc_a"].semantic == []

    def test_personas_still_load_from_a_pack_with_memories(self):
        """The two loaders read the same file and must not interfere."""
        personas = load_personas(SCENARIO)
        assert set(personas) == {NPC_A, NPC_B, NPC_C}
