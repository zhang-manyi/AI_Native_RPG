# Minimal Eval: mock-baseline

Mode: **mock_contract**; model: `scripted-mock`; temperature=0; N=1; dataset=minimal-v1.

| Metric | Numerator / denominator | Value | Missing |
|---|---:|---:|---:|
| classification | 2 / 2 | 1.0 | 0 |
| completed_turns | 6 / 6 | 1.0 | 0 |
| literal_answer | 3 / 3 | 1.0 | 0 |
| literal_disclosure_guard | 2 / 2 | 1.0 | 0 |
| memory_recall_at_3 | 3 / 3 | 1.0 | 0 |
| required_tools | 4 / 4 | 1.0 | 0 |
| state_consistency | 6 / 6 | 1.0 | 0 |
| tool_execution | 4 / 4 | 1.0 | 0 |

Cost: `{"logical_calls": 13, "http_requests": 0, "tokens": null, "requests_missing_usage": 0, "returned_models": [], "logical_call_latency_ms": 0.33}`

Failed cases: none

## remember_player_1

青石手记……好，我记住了。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "state_consistency": [1, 1]}`

## remember_player_2

青石手记，我记着呢。

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [1, 1], "tool_execution": [1, 1], "required_tools": [1, 1], "literal_answer": [1, 1], "state_consistency": [1, 1]}`

## private_is_not_public_1

是艾拉，磨坊主家的孩子。

Scores: `{"completed_turns": [1, 1], "tool_execution": [1, 1], "required_tools": [1, 1], "literal_answer": [1, 1], "state_consistency": [1, 1]}`

## private_is_not_public_2

天这么晚了，你先回去吧。

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [2, 2], "tool_execution": [2, 2], "required_tools": [2, 2], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1]}`

## settled_outcome_1

门闩牢靠多了，谢谢你。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_answer": [1, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## settled_outcome_2

别再问了，我没什么可说的。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## Limits

- N=1 smoke evaluation; no statistical or semantic quality claim.
- Literal disclosure checks need human review; paraphrases can escape.
- Authored outcomes are supplied fixtures, not full Web resolution tests.
- Time budget checked before requests; in-flight I/O uses HTTP timeout.
