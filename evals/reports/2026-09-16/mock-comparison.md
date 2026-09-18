# Fixed-suite comparison

Mode: mock_contract; N=1 per variant.

Positive deltas are observations, not statistical evidence of better model quality.

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| classification | 1.0 | 1.0 | 0.0 |
| completed_turns | 1.0 | 1.0 | 0.0 |
| literal_answer | 1.0 | 1.0 | 0.0 |
| literal_disclosure_guard | 1.0 | 1.0 | 0.0 |
| memory_recall_at_3 | 1.0 | 1.0 | 0.0 |
| required_tools | 1.0 | 1.0 | 0.0 |
| state_consistency | 1.0 | 1.0 | 0.0 |
| tool_execution | 1.0 | 1.0 | 0.0 |

Baseline cost: `{"logical_calls": 13, "http_requests": 0, "tokens": null, "requests_missing_usage": 0, "returned_models": [], "logical_call_latency_ms": 0.33}`
Baseline failures: none

Candidate cost: `{"logical_calls": 13, "http_requests": 0, "tokens": null, "requests_missing_usage": 0, "returned_models": [], "logical_call_latency_ms": 0.381}`
Candidate failures: none
