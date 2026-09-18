# Memory evaluation

This experiment measures retrieval separately from using the retrieved evidence.
The fixture is synthetic evaluation data, not additional playable story content.

Results and decisions: [2026-09-17 review](reports/2026-09-17-memory/review.md).

The subsequent [filtered expression experiment](reports/2026-09-17-expression/review.md)
implements player-source provenance and a restricted expression projection. Its
new seven-case holdout was frozen before implementation; fixed-input replay and
full Harness runs use three repetitions each. Ordinary answers improved, but
completion, grounding and rejection coverage did not justify promotion. Both
`retain_dialogue_evidence` and `filtered_dialogue_evidence` remain off by default.
See the linked report for raw requests, missing outputs, assistant review and commands.

## Fixed dataset

`memory_cases.yaml` contains 24 event groups, with two questions per group. All
paraphrases of an event stay in the same split: 24 development queries and 24 test
queries. Each NPC has 100 episodic candidates, including deterministic background
events. The raw expanded corpus is saved with every retrieval run.

The labels were authored before either encoder was evaluated. They are not
independently human-validated. Development and test share one memory corpus, as
in retrieval over a fixed collection; the event/query groups are disjoint. This
is not evidence of generalization to another game or another distribution.

Categories include paraphrase, similar entities, changed information, multi-event
questions, temporal questions, private memories, unknown answers and another NPC's
private knowledge. Private memories can be correct retrieval targets while still
being forbidden to disclose. Retrieval does not measure disclosure compliance.

## Metrics and controls

- Macro Recall@3: mean fraction of relevant IDs among the first three results,
  over answerable queries. Micro recall also retains hits and total gold labels.
- MRR: reciprocal of the first relevant rank across all 100 candidates, not MRR@3.
- All relevant@3: fraction of answerable questions retrieving every required ID.
- No-answer nonempty rate: fraction of unanswerable queries receiving at least one
  result. No-answer queries are excluded from recall/MRR, not awarded 100% recall.
  The current store always returns Top-K, so this measures returned noise, not
  an acceptance decision or a calibrated rejection threshold.
- Foreign-memory hits: owner violations in returned IDs. This is a storage
  isolation check, not model privacy capability.
- Query latency includes query encoding and production-store ranking; model load
  and corpus encoding are measured separately. Percentiles on 24 queries are
  descriptive, not production latency estimates.

Both backends use the existing `MemoryStore`, K=3 and importance weight 0.5.
Hashing has 64 dimensions; Qwen3 has 256, with its existing instruction-aware
query encoding. Qwen runs on CPU with four torch threads and its text cache
disabled. Retrieval is deterministic and runs once per condition. Real generation
runs three repetitions at temperature zero, which does not guarantee determinism.
Package versions, model weight/configuration hashes, dataset/runtime hashes and
the git HEAD are recorded. A missing Qwen model/backend is reported as unavailable,
never substituted with Hashing.

## Multi-turn probes

Two supplemental events test a newly supplied notebook name and meeting place.
For each repetition, a real `Harness.respond` writes the initial exchange using
Hashing. Both retrieval backends load that identical prefix and world state.
Lag 0, 10 and 30 probes branch independently; an earlier probe's answer cannot
refresh a later branch's memory.

Intervening exchanges use the authored `remember_exchange` path. They test memory
interference, not 30 autonomous or model-generated turns. At lag 30, the world and
memory are read back from disk into fresh objects. This checks the actual memory
save/load path, not HTTP session restoration or a browser playthrough.

Each probe records the written memory IDs, initial retrieval IDs, final generation
messages, answer-string presence in that context, response, world snapshots, full
Trace and all HTTP attempts. Literal answer hits are a screen only: a name can
appear in a denial or with the wrong speaker. Semantic review must reference the
saved response and be labelled with the reviewer identity; assisted review is not
independent human annotation. No persona or disclosure score is inferred from
string matching.

Defaults cap a sequence run at 180 HTTP attempts, 600 output tokens per attempt,
25-second request timeout and a 1200-second pre-request elapsed budget. In-flight
requests may finish after that elapsed budget. Missing usage remains missing;
HTTP attempts include JSON reparses. Prefix costs and query costs are separate.

## Commands

From the project root; output directories must be new:

```powershell
.venv\Scripts\python.exe scripts/eval_memory.py describe
.venv\Scripts\python.exe scripts/eval_memory.py retrieval --backend hashing --split dev --output data/runtime/memory-hashing-dev
.venv\Scripts\python.exe scripts/eval_memory.py retrieval --backend qwen --split dev --model-path data/runtime/models/Qwen3-Embedding-0.6B --output data/runtime/memory-qwen-dev
```

Inspect development results and freeze the selected configuration before running
the same two commands with `--split test` and new output paths. Do not tune on test
failures; if those guide a new candidate, obtain a new held-out set for its final
evaluation.

```powershell
.venv\Scripts\python.exe scripts/eval_memory.py compare --reports data/runtime/memory-hashing-dev/report.json data/runtime/memory-qwen-dev/report.json --output data/runtime/memory-comparison.md
.venv\Scripts\python.exe scripts/eval_memory.py sequences --output data/runtime/memory-sequences --repeats 3 --max-requests 180 --max-seconds 1200
```

After inspecting sequence failures, a separate diagnostic can compare the existing
evidence-inclusion candidate with a byte-for-byte reconstructed baseline. Selection
uses repeat 1, lag 0 and the regeneration path, never answer success. This is a
development diagnostic, not held-out validation. Two rejected-disclosure fixtures
are included; all outputs require qualitative review.

```powershell
.venv\Scripts\python.exe scripts/eval_evidence.py --source data/runtime/memory-sequences/report.json --output data/runtime/evidence-replay --repeats 3 --max-requests 96 --max-seconds 600
```

Generation uses the existing configured backend and synthetic in-project context.
It does not silently use a mock when credentials or the real backend are missing.
Local model files live under ignored `data/runtime/`; model weights are not part
of the repository. Install the optional embedding dependency and supply the local
Qwen weights to reproduce the semantic condition.

The Windows CPU environment used for this experiment is pinned in
`requirements-memory.txt`. In this environment scipy 1.18.1 failed to load its
BLAS DLL even outside the sandbox; scipy 1.15.3 with numpy 2.2.6 and scikit-learn
1.7.2 imported successfully. This is an observed local compatibility issue, not
a claim that newer versions fail on every Windows installation.
