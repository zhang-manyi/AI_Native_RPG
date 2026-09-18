# Minimal Eval: baseline

Mode: **real_model**; model: `deepseek-v4-flash`; temperature=0; N=1; dataset=minimal-v1.

| Metric | Numerator / denominator | Value | Missing |
|---|---:|---:|---:|
| classification | 2 / 2 | 1.0 | 0 |
| completed_turns | 5 / 6 | 0.8333333333333334 | 0 |
| literal_answer | 0 / 3 | 0.0 | 0 |
| literal_disclosure_guard | 1 / 1 | 1.0 | 0 |
| memory_recall_at_3 | 1 / 1 | 1.0 | 0 |
| required_tools | 1 / 2 | 0.5 | 0 |
| state_consistency | 5 / 5 | 1.0 | 0 |
| tool_execution | 4 / 4 | 1.0 | 0 |

Cost: `{"logical_calls": 14, "http_requests": 15, "tokens": {"prompt_tokens": 26347, "completion_tokens": 3742, "total_tokens": 30089}, "requests_missing_usage": 0, "returned_models": ["deepseek-flash"], "logical_call_latency_ms": 30778.179}`

Failed cases: remember_player_2, private_is_not_public_1, private_is_not_public_2, settled_outcome_1

## remember_player_1

青石手记……成，我记着这名字了。可你要真还来，我就得把门再闩一遍了——你别怪我。

Scores: `{"completed_turns": [1, 1], "tool_execution": [2, 2], "state_consistency": [1, 1]}`

## remember_player_2

你写在封皮上的那个名字，我记得，一个字都没记错……今晚你没提那些事，这点我心里是有数的。

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [1, 1], "tool_execution": [0, 0], "required_tools": [0, 1], "literal_answer": [0, 1], "state_consistency": [1, 1]}`

## private_is_not_public_1

……是叶莲娜家的小托沙。你问这个做什么？

Scores: `{"completed_turns": [1, 1], "tool_execution": [2, 2], "required_tools": [1, 1], "literal_answer": [0, 1], "state_consistency": [1, 1]}`

## private_is_not_public_2

LLMAPIError

Scores: `{"completed_turns": [0, 1]}`

## settled_outcome_1

……修好了？我试试。……嗯，这回扣得紧了。谢谢你，真的——这种天还劳烦你跑一趟。要不、要不要进来喝口热的？就一会儿。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_answer": [0, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## settled_outcome_2

……你说什么？我听不懂这些。那晚我睡得早，什么都没看见——你们问来问去，我只会说这一句。要是没别的事，就请回吧，我儿子该吃药了。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## Limits

- N=1 smoke evaluation; no statistical or semantic quality claim.
- Literal disclosure checks need human review; paraphrases can escape.
- Authored outcomes are supplied fixtures, not full Web resolution tests.
- Time budget checked before requests; in-flight I/O uses HTTP timeout.
