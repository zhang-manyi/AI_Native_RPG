"""Run the no-build client state tests as part of the normal project checks."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_client_playback_and_composer_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to verify the plain JavaScript client")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("web_frontend.test.cjs"))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
