"""Rerun affected contracts with explicit Hashing; never alter runtime defaults."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from ai_native_rpg.agent.embedding import HashingEmbedder  # noqa: E402

if __name__ == "__main__":
    # Only the Web registry's backend factory is replaced. Real sockets, SSE,
    # persistence, Harness, Manager and all test assertions remain unchanged.
    with patch(
        "ai_native_rpg.web.registry.build_shared_embedder",
        return_value=(HashingEmbedder(), "HashingEmbedder (test fixture)"),
    ):
        raise SystemExit(
            pytest.main(
                [
                    "tests/test_expression.py",
                    "tests/test_expression_evaluation.py",
                    "tests/test_harness.py",
                    "tests/test_tools.py",
                    "tests/test_tool_loop.py",
                    "tests/test_memory_store.py",
                    "tests/test_memory_evaluation.py",
                    "tests/test_evidence_evaluation.py",
                    "tests/test_evaluation.py",
                    "tests/test_web_shutdown.py",
                    "tests/test_web_turn.py",
                    "tests/test_web_isolation.py",
                    "-q",
                    "--basetemp",
                    ".pytest-expression-focused",
                    "--junitxml=evals/reports/2026-09-17-expression/pytest-focused.xml",
                ]
            )
        )
