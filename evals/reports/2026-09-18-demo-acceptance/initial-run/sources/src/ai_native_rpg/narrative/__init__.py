"""Narrative Engine,叙事算子 and Experience Controller (docs/05, docs/10).

The split inside this package mirrors the one docs/05 §1 insists on:

* ``rules.py`` — trigger rules: what the world *permits* now. Deterministic.
* ``controller.py`` — Experience Controller: which permitted thing to run, via
  tension admission then preference ranking. Deterministic.
* ``engine.py`` — the Engine proper: fills the chosen operator's slots with an LLM
  call, then lands the effects as ordinary Action Proposals.

Only ``engine.py`` touches a model. That division *is* the architectural claim of
docs/01 §1 — structure is scheduled deterministically and the model only writes
content into a slot it was handed.
"""

from .controller import PacingVerdict, select_candidate
from .rules import check_triggers

__all__ = ["PacingVerdict", "check_triggers", "select_candidate"]
