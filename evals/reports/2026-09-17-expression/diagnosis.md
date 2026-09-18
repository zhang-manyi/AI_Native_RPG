# 旧 Trace 的开发归因

来源保持只读：`../2026-09-17-memory/sequences/report.json` 和 `evidence-replay/report.json`。以下是 Codex 助手对已保存模型请求、计划、工具返回及台词的检查，不是独立人工标注。原因可同时存在，不强制互斥。

| Trace ID | 观察 | 归因 |
|---|---|---|
| notebook_retention_r1_hashing_lag0 | 初始召回包含白桦随记；最终请求不含名字；计划明确说“一旦承认记住了…说一半” | 上下文丢失 + 角色主动回避，不是检索失败 |
| notebook_retention_r1_hashing_lag10 | 召回正确；计划愿意说出名字但未携带名字；台词变成“我天天翻” | 上下文丢失 + 无依据经历/人称错误 |
| appointment_retention_r1_hashing_lag0 | 初始检索漏掉事件；须同时检查后续工具（不能以首次漏召回推断全回合未获得） | 初始检索遗漏与后续上下文流转分开计数 |
| appointment_retention_r1_hashing_lag10 / lag30 | 初始漏召回，但工具与最终上下文补回南岸茶棚，最终回答正确 | 初始召回遗漏被补救，不记为最终记忆失败 |
| appointment_retention_r1_qwen_lag10 | 初始已召回茶棚；最终上下文不含地点；输出磨坊后橡树 | 上下文丢失后编造，不能归咎检索 |
| appointment_retention_r1_qwen_lag30 | 给出“南岸的茶棚”，另称“我去过一次” | 同义答案正确；额外经历无依据，事实正确不等于全句有据 |
| privacy_npc_b_candidate_r3 | 全量证据进入最终请求；裁决禁止披露；回答“我收套子去了” | 私密认知误作可披露证据，违反裁决 |

实际边界：MemoryStore 只隔离 NPC 所有者，不管理对玩家披露。query_memory 返回无 ID 的原文列表；check_public_fact 使用 PlayerView；Manager.submit 执行 Validator。拒绝理由、计划、persona.background 本身也可能携带秘密。仅过滤附加记忆仍可能从这些通道泄漏。无行动快路径直接输出 PlanningOutput.dialogue，因此候选必须对这一分支也执行表达投影。

最小候选：保留私有规划；玩家来源记忆仅回述原玩家原话（不带 NPC 回答）；公开事实重新读取当前 PlayerView（partial 只取投影值）；表达只接收结构化裁决状态及可公开身份/性格，不透传计划、私密背景、目标和拒绝原文。工具记忆通过执行结果的 ID 回查当前本 NPC 存储，不接受模型声称的许可。旧存档来源缺失时保守不回述，不从拼接文本猜来源。

留出文件初始 SHA256：`ee3eab4e58304b2bca341e4d4e869a50e4376d55d4681140a82c497b8838862c`。
