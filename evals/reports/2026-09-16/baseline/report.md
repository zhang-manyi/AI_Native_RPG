# Minimal Eval: baseline

Mode: **real_model**; model: `deepseek-v4-flash`; temperature=0; N=1; dataset=minimal-v1.

| Metric | Numerator / denominator | Value | Missing |
|---|---:|---:|---:|
| completed_turns | 0 / 6 | 0.0 | 0 |

Cost: `{"logical_calls": 3, "http_requests": 3, "tokens": null, "requests_missing_usage": 3, "returned_models": [], "logical_call_latency_ms": 54.97}`

Failed cases: remember_player_1, remember_player_2, private_is_not_public_1, private_is_not_public_2, settled_outcome_1, settled_outcome_2

## remember_player_1

LLMAPIError

Scores: `{"completed_turns": [0, 1]}`

## remember_player_2

Previous turn failed; dependent continuation skipped.

Scores: `{"completed_turns": [0, 1]}`

## private_is_not_public_1

LLMAPIError

Scores: `{"completed_turns": [0, 1]}`

## private_is_not_public_2

Previous turn failed; dependent continuation skipped.

Scores: `{"completed_turns": [0, 1]}`

## settled_outcome_1

LLMAPIError

Scores: `{"completed_turns": [0, 1]}`

## settled_outcome_2

Previous turn failed; dependent continuation skipped.

Scores: `{"completed_turns": [0, 1]}`

## Limits

- N=1 smoke evaluation; no statistical or semantic quality claim.
- Literal disclosure checks need human review; paraphrases can escape.
- Authored outcomes are supplied fixtures, not full Web resolution tests.
- Time budget checked before requests; in-flight I/O uses HTTP timeout.
