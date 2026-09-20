"""Recompute archived evidence without generating or changing historical runs."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from ai_native_rpg.config import Settings  # noqa: E402
from ai_native_rpg.memory_evaluation import fingerprint, wire_cost  # noqa: E402

OUT = Path(__file__).resolve().parent


def main():
    checks = {}
    frozen = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
    checks["dataset_frozen"] = (
        fingerprint(ROOT / "evals/answer_evidence.json") == frozen["dataset_sha256"]
    )
    checks["protocol_frozen"] = fingerprint(OUT / "protocol.md") == frozen["protocol_sha256"]
    historical = json.loads((OUT / "historical_hashes.json").read_text(encoding="utf-8"))
    checks["historical_files_unchanged"] = all(
        fingerprint(ROOT / p) == sha for p, sha in historical.items()
    )
    checks["historical_file_count"] = len(historical)
    selection = json.loads((OUT / "candidate-selection.json").read_text(encoding="utf-8"))
    checks["candidate_current_hashes_match"] = all(
        fingerprint(ROOT / p) == sha for p, sha in selection["files"].items()
    )
    reviews = json.loads((OUT / "review_labels.json").read_text(encoding="utf-8"))
    labels = {r["id"]: r for r in reviews["rows"]}
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        "answer_summary", ROOT / "scripts/summarize_answer_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checks["offline_summary_exact"] = all(
        json.loads(json.dumps(module.summarize(OUT / name, labels))) == summary["runs"][name]
        for name in reviews["runs"]
    )
    checks["source_snapshots_valid"] = True
    checks["candidate_frozen_across_runs"] = True
    checks["no_score_fields_in_requests"] = True
    checks["planned_rows_and_completion"] = True
    checks["within_budgets"] = True
    checks["no_configured_credential_in_archive"] = True
    api_key = Settings.from_env().api_key.get_secret_value()
    records = []
    for name in ["dev-baseline", *reviews["runs"]]:
        directory = OUT / name
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        wire = json.loads((directory / "wire.json").read_text(encoding="utf-8"))
        records.extend(wire)
        for path, sha in report["manifest"]["files"].items():
            checks["source_snapshots_valid"] &= fingerprint(directory / "sources" / path) == sha
        if report["variant"] == "candidate":
            checks["candidate_frozen_across_runs"] &= all(
                report["manifest"]["files"].get(p) == sha for p, sha in selection["files"].items()
            )
        if name != "dev-baseline":
            checks["planned_rows_and_completion"] &= len(report["rows"]) == 24 and all(
                "dialogue" in r for r in report["rows"]
            )
        checks["within_budgets"] &= (
            len(wire) <= report["budget"]["requests"]
            and report["budget_charged_tokens"] <= report["budget"]["tokens"]
        )
        for record in wire:
            request = json.dumps(record["request_body"], ensure_ascii=False)
            checks["no_score_fields_in_requests"] &= all(
                f'"{field}"' not in request
                for field in [
                    "expect",
                    "answer_correct",
                    "private_topics",
                    "refusal_appropriate",
                    "fixed_action",
                ]
            )
        for p in directory.rglob("*"):
            if p.is_file() and api_key:
                checks["no_configured_credential_in_archive"] &= (
                    api_key.encode() not in p.read_bytes()
                )
    checks["all_attempts_cost"] = wire_cost(records)
    checks["infrastructure_attempt_reserved_tokens"] = json.loads(
        (OUT / "dev-baseline/report.json").read_text(encoding="utf-8")
    )["budget_charged_tokens"]
    checks["review_count"] = len(labels)
    checks["currency_cost"] = None
    checks["currency_cost_reason"] = "No verified tariff"
    assert all(v for v in checks.values() if isinstance(v, bool)), checks
    with (OUT / "verification.json").open("x", encoding="utf-8") as stream:
        json.dump(checks, stream, ensure_ascii=False, indent=2)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
