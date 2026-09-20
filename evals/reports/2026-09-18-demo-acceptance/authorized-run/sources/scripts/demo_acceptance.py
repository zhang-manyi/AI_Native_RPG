"""Record a real guided game through the existing player HTTP routes.

No grading data enters this runner. Observer patches only redirect artifact paths
and install a bounded HTTP transport; gameplay goes through FastAPI as in the UI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from ai_native_rpg.config import Settings
from ai_native_rpg.llm.openai_compatible import OpenAICompatibleClient
from ai_native_rpg.memory_evaluation import fingerprint, wire_cost, write_json
from ai_native_rpg.tool_evaluation import TaskTransport
from ai_native_rpg.web import session as session_module
from ai_native_rpg.web.app import create_app

BUDGET = {"requests": 50, "tokens": 180_000, "seconds": 600, "output_tokens": 1200}


class DurableTransport(TaskTransport):
    def __init__(self, output):
        super().__init__(budget=BUDGET)
        self.output = output

    def handle_request(self, request):
        try:
            return super().handle_request(request)
        finally:
            write_json(self.output / "wire.json", self.records)


def snapshot(game):
    return {
        "session_id": game.session_id,
        "scene": game.scene().model_dump(mode="json"),
        "world": game.manager.snapshot().model_dump(mode="json"),
        "memories": {key: npc.memory.snapshot() for key, npc in game._npcs.items()},
        "transcript": game.transcript.copy(),
    }


def archive_sources(output):
    files = [ROOT / "pyproject.toml", Path(__file__)]
    for directory in ("src", "prompts", "scenarios"):
        files.extend(
            p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    hashes = {}
    for path in files:
        relative = path.relative_to(ROOT)
        target = output / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[relative.as_posix()] = fingerprint(path)
    return hashes


def run(route_path, output):
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings.from_env()
    if not settings.has_real_backend:
        write_json(output / "blocked.json", {"reason": "real backend required; no mock fallback"})
        return 2
    route = json.loads(route_path.read_text(encoding="utf-8"))
    write_json(output / "route.json", route)
    transport = DurableTransport(output)
    clients = []

    def observed_client(config, **_kwargs):
        client = OpenAICompatibleClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            transport=transport,
        )
        clients.append(client)
        return client

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], text=True),
        "command": subprocess.list2cmdline([sys.executable, *sys.argv]),
        "settings": settings.model_dump(mode="json", exclude={"api_key"}),
        "budget": BUDGET,
        "route_sha256": fingerprint(route_path),
        "source_hashes": archive_sources(output),
        "candidate_flags": {
            "retain_dialogue_evidence": False,
            "filtered_dialogue_evidence": False,
            "grounded_rewrite": False,
        },
        "steps": [],
        "completed": False,
    }
    write_json(output / "report.json", report)
    started = time.monotonic()
    try:
        with (
            patch.object(session_module, "SAVE_DIR", output / "saves"),
            patch.object(session_module, "TRACE_DIR", output / "traces"),
            patch.object(session_module, "build_llm_client", observed_client),
            TestClient(create_app(settings=settings, dev_mode=False)) as web,
        ):
            registry = web.app.state.registry
            response = web.post("/api/session", json={"scenario": "village_disappearance"})
            report["create_response"] = {"status": response.status_code, "body": response.json()}
            response.raise_for_status()
            sid = response.json()["session_id"]
            game = registry.get(sid)
            report["embedder"] = registry.embedder_label
            report["initial"] = snapshot(game)
            for index, action in enumerate(route):
                if time.monotonic() - started >= BUDGET["seconds"]:
                    raise RuntimeError("route time budget exhausted")
                before = snapshot(game)
                row = {"index": index, "action": action, "before": before}
                report["steps"].append(row)
                wire_start, event_start = len(transport.records), len(game._history)
                step_started = time.monotonic()
                try:
                    kind = action["kind"]
                    if kind == "resume":
                        archive = output / "save_checkpoints" / f"{index:02d}"
                        shutil.copytree(output / "saves" / sid, archive)
                        registry.close(sid)
                        response = web.post("/api/session", json={"resume_from": sid})
                        response.raise_for_status()
                        sid = response.json()["session_id"]
                        game = registry.get(sid)
                        event_start = 0
                    else:
                        endpoint, body = kind, {}
                        if kind == "choice":
                            option = next(
                                o
                                for o in game.scene().options
                                if o.option_id == action["option_id"]
                            )
                            endpoint = "turn"
                            body = {"text": option.text, "option_id": option.option_id}
                        elif kind == "turn":
                            body = {"text": action["text"]}
                        elif kind == "move":
                            body = {"destination": action["destination"]}
                        elif kind == "close_day":
                            endpoint = "wrap_up/close"
                        elif kind == "conclude":
                            endpoint = "conclude"
                        row["http_request"] = {
                            "path": f"/api/session/{sid}/{endpoint}",
                            "body": body,
                        }
                        response = web.post(row["http_request"]["path"], json=body)
                    row["http_response"] = {"status": response.status_code, "body": response.json()}
                    expected_status = action.get("status", 200 if kind == "resume" else 202)
                    if response.status_code != expected_status:
                        raise RuntimeError(f"unexpected HTTP status {response.status_code}")
                    game.join(timeout=180)
                    if game.busy:
                        raise RuntimeError("session did not settle")
                    if transport.stopped:
                        raise RuntimeError(transport.stopped)
                    failures = [
                        e for e in game._history[event_start:] if e.type.value == "turn_failed"
                    ]
                    if failures:
                        raise RuntimeError("player turn_failed event; inspect saved events")
                finally:
                    row["after"] = snapshot(game)
                    row["events"] = [e.model_dump(mode="json") for e in game._history[event_start:]]
                    row["wire_range"] = [wire_start, len(transport.records)]
                    row["elapsed_seconds"] = round(time.monotonic() - step_started, 3)
                    write_json(output / "report.json", report)
                    print(
                        f"step {index:02d} {action['kind']}: {len(transport.records)} HTTP",
                        flush=True,
                    )
            report["final"] = snapshot(game)
            ending = game.engine.reached_ending()
            report["ending_id"] = ending.ending_id if ending else None
            report["completed"] = report["ending_id"] == "truth_uncovered"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        report["cost"] = wire_cost(transport.records)
        report["budget_charged_tokens"] = transport.charged_tokens
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_json(output / "report.json", report)
        write_json(output / "wire.json", transport.records)
        for client in clients:
            client.close()
    print(
        json.dumps(
            {k: report.get(k) for k in ("completed", "ending_id", "error", "cost")},
            ensure_ascii=False,
        )
    )
    return 0 if report["completed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.route, args.output))
