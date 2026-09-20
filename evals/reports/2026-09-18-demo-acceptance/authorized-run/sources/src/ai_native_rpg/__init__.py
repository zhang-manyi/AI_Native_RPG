"""AI Native RPG: Agent runtime for an LLM-driven RPG.

Layering follows docs/01_System_Architecture.md — modules are split by whether
they need an LLM, not by feature name:

- ``schemas``      : shared Pydantic contracts (no logic)
- ``world``        : World State Manager   [deterministic]
- ``player``       : Player Model          [deterministic + periodic LLM]
- ``narrative``    : Narrative Engine      [rule trigger + LLM generation]
- ``agent``        : NPC Agent Runtime     [agent]
- ``observability``: Trace / Eval          [consumes the above]
"""

__version__ = "0.1.0"
