# Checkpoint verification — 2026-09-18

This checkpoint preserves the existing report-conclusion flow, evaluation datasets,
all historical experiment outcomes, and the default-off filtered expression candidate.
It does not promote either expression candidate or claim new model-quality results.

Before committing, the stale SceneOption contract test was updated to allow the
existing public `final_report` boolean while still excluding resolution details.
Ordinary dialogue options must leave that boolean false; existing playthrough
tests cover final report options. Three active files were formatted. Runtime test
directories are ignored. Archived source snapshots are excluded from Ruff and
all report artifacts use Git `-text` to preserve their original bytes and hashes.
Historical reports and failed test evidence were not rewritten.

## Validation

- Full offline Python suite: **932 passed, 6 skipped, 0 failed**, 938 collected,
  120.247 seconds. Raw result: [pytest-offline.xml](pytest-offline.xml).
- Four skips require real embedding weights; the other two are existing scenarios
  with no partial fact / no initial generated hook. This run explicitly selects
  the no-model fallback and does not revalidate Qwen quality or startup latency.
- Frontend: **8 passed**.
- Ruff lint and format checks passed for the active tree; archived snapshots remain immutable.
- Review of versionable files found no configured credential values or excluded
  purpose descriptions. No model requests were made for this checkpoint.

## Reproduce from the repository root (PowerShell)

Use a fresh temporary directory and XML filename for each later run; do not
overwrite this evidence. The embedding path below must remain nonexistent.

```powershell
$env:EMBEDDING_MODEL_PATH='D:\Projects\AI_Native_RPG\data\runtime\offline-check-20260918\absent.gguf'
.venv\Scripts\python.exe -m pytest tests -q --basetemp data/runtime/checkpoint-20260918-pytest --junitxml=evals/reports/2026-09-18-checkpoint/pytest-offline.xml
node --test tests/web_frontend.test.cjs
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
git -c core.whitespace=cr-at-eol diff --check
```

The previous real-model experiments and their adoption decisions remain in
[the evaluation overview](../../OVERVIEW.md). Current next-step priorities are
maintained in [the reference scenario](../../../docs/09_Reference_Scenario.md).
