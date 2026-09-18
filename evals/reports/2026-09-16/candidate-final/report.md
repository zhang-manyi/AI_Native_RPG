# Minimal Eval: evidence-context

Mode: **real_model**; model: `deepseek-v4-flash`; temperature=0; N=1; dataset=minimal-v1.

| Metric | Numerator / denominator | Value | Missing |
|---|---:|---:|---:|
| classification | 2 / 2 | 1.0 | 0 |
| completed_turns | 6 / 6 | 1.0 | 0 |
| literal_answer | 2 / 3 | 0.6666666666666666 | 0 |
| literal_disclosure_guard | 2 / 2 | 1.0 | 0 |
| memory_recall_at_3 | 3 / 3 | 1.0 | 0 |
| required_tools | 1 / 4 | 0.25 | 0 |
| state_consistency | 5 / 6 | 0.8333333333333334 | 0 |
| tool_execution | 4 / 4 | 1.0 | 0 |

Cost: `{"logical_calls": 14, "http_requests": 16, "tokens": {"prompt_tokens": 28117, "completion_tokens": 2731, "total_tokens": 30848}, "requests_missing_usage": 0, "returned_models": ["deepseek-flash"], "logical_call_latency_ms": 25336.185}`

Failed cases: remember_player_2, private_is_not_public_1, private_is_not_public_2, settled_outcome_1

## remember_player_1

青石手记……好，我记住了。可你下回别在我门口站太久——这村里，眼睛多。

Scores: `{"completed_turns": [1, 1], "tool_execution": [2, 2], "state_consistency": [1, 1]}`

## remember_player_2

青石手记……你倒是真记住了。唉，可你把这名字搁在心里就好，别念出声来——这村里，连名字都能长脚。

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [1, 1], "tool_execution": [0, 0], "required_tools": [0, 1], "literal_answer": [1, 1], "state_consistency": [1, 1]}`

## private_is_not_public_1

艾拉……磨坊主家的那个，才十四岁。你要问的，我告诉你了。

Scores: `{"completed_turns": [1, 1], "tool_execution": [2, 2], "required_tools": [1, 1], "literal_answer": [1, 1], "state_consistency": [0, 1]}`

## private_is_not_public_2

……那晚我起夜，雨刚停，是看见有个人从林子那边走回来——可我没点灯，就凭个影子，哪敢认脸啊。你别逼我了，行不行？

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [2, 2], "tool_execution": [0, 0], "required_tools": [0, 2], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1]}`

## settled_outcome_1

……你修好了？我看看。嗯，严实了，关起来不那么晃了。谢了——我不是不知好歹的人，你走吧，天快黑了，别在这门口站太久。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_answer": [0, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## settled_outcome_2

……你说什么？我听不懂那些词。修门闩的事我谢过你了，别的我没有什么可说的。你走吧，别在我家门口站着了，天要黑了。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## Limits

- N=1 smoke evaluation; no statistical or semantic quality claim.
- Literal disclosure checks need human review; paraphrases can escape.
- Authored outcomes are supplied fixtures, not full Web resolution tests.
- Time budget checked before requests; in-flight I/O uses HTTP timeout.
