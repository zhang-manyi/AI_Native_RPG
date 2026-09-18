# Fixed-suite comparison

Mode: real_model; N=1 per variant.

Positive deltas are observations, not statistical evidence of better model quality.

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| classification | 1.0 | 1.0 | 0.0 |
| completed_turns | 1.0 | 1.0 | 0.0 |
| literal_answer | 0.6666666666666666 | 0.6666666666666666 | 0.0 |
| literal_disclosure_guard | 1.0 | 1.0 | 0.0 |
| memory_recall_at_3 | 1.0 | 1.0 | 0.0 |
| required_tools | 0.75 | 0.25 | -0.5 |
| state_consistency | 1.0 | 1.0 | 0.0 |
| tool_execution | 1.0 | 1.0 | 0.0 |

Baseline cost: `{"logical_calls": 13, "http_requests": 14, "tokens": {"prompt_tokens": 22258, "completion_tokens": 2593, "total_tokens": 24851}, "requests_missing_usage": 0, "returned_models": ["deepseek-flash"], "logical_call_latency_ms": 23353.078}`
Baseline failures: remember_player_2

Candidate cost: `{"logical_calls": 14, "http_requests": 16, "tokens": {"prompt_tokens": 28117, "completion_tokens": 2731, "total_tokens": 30848}, "requests_missing_usage": 0, "returned_models": ["deepseek-flash"], "logical_call_latency_ms": 25336.185}`
Candidate failures: remember_player_2, private_is_not_public_2, settled_outcome_1
