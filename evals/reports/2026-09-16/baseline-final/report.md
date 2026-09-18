# Minimal Eval: baseline

Mode: **real_model**; model: `deepseek-v4-flash`; temperature=0; N=1; dataset=minimal-v1.

| Metric | Numerator / denominator | Value | Missing |
|---|---:|---:|---:|
| classification | 2 / 2 | 1.0 | 0 |
| completed_turns | 6 / 6 | 1.0 | 0 |
| literal_answer | 2 / 3 | 0.6666666666666666 | 0 |
| literal_disclosure_guard | 2 / 2 | 1.0 | 0 |
| memory_recall_at_3 | 3 / 3 | 1.0 | 0 |
| required_tools | 3 / 4 | 0.75 | 0 |
| state_consistency | 6 / 6 | 1.0 | 0 |
| tool_execution | 4 / 4 | 1.0 | 0 |

Cost: `{"logical_calls": 13, "http_requests": 14, "tokens": {"prompt_tokens": 22258, "completion_tokens": 2593, "total_tokens": 24851}, "requests_missing_usage": 0, "returned_models": ["deepseek-flash"], "logical_call_latency_ms": 23353.078}`

Failed cases: remember_player_2

## remember_player_1

青石手记……好，我记住了。你下回提起它，我就知道是你。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "state_consistency": [1, 1]}`

## remember_player_2

名字我记得，我记性还没坏到那份上——接生婆要是记不住名字，早没人来找我了。可你别指望因为这点小事，我就把门给你敞开。

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [1, 1], "tool_execution": [0, 0], "required_tools": [0, 1], "literal_answer": [0, 1], "state_consistency": [1, 1]}`

## private_is_not_public_1

……艾拉。磨坊主家那个丫头，十四岁。
这谁不知道，你何必来问我。天冷了，我得进去了。

Scores: `{"completed_turns": [1, 1], "tool_execution": [2, 2], "required_tools": [1, 1], "literal_answer": [1, 1], "state_consistency": [1, 1]}`

## private_is_not_public_2

那晚黑得伸手不见五指……我哪看得清是谁，你别问我了——也别去找我儿子，他什么都不知道，真的。

Scores: `{"completed_turns": [1, 1], "memory_recall_at_3": [2, 2], "tool_execution": [2, 2], "required_tools": [2, 2], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1]}`

## settled_outcome_1

……门闩修好了？谢了。这门旧了，我夜里总怕它自己开。你……你别站太久，风大。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_answer": [1, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## settled_outcome_2

……我不知道你说的什么秘密，也别在我门口喊这些。那晚我睡得沉，什么都没看见。你走吧，求你，别再来了。

Scores: `{"completed_turns": [1, 1], "tool_execution": [0, 0], "literal_disclosure_guard": [1, 1], "state_consistency": [1, 1], "classification": [1, 1]}`

## Limits

- N=1 smoke evaluation; no statistical or semantic quality claim.
- Literal disclosure checks need human review; paraphrases can escape.
- Authored outcomes are supplied fixtures, not full Web resolution tests.
- Time budget checked before requests; in-flight I/O uses HTTP timeout.
