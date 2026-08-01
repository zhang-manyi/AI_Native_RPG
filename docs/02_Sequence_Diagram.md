# 02. Sequence Diagram — 一次玩家/NPC 交互的完整时序

## 1. 目的

验证架构是否可实现：走一遍"玩家点击 NPC 说话 → 系统如何流转 → 哪些模块被调用 → 哪里用 LLM → 延迟多少"。这一步决定架构在真实交互频率下是否成立。

## 2. 场景

玩家对 NPC_A 说："你知道凶手是谁吗？"

## 3. 时序图

```
Player                Client/UI          NPC Agent         World State      Narrative      Player Model
  |                        |             Runtime            Manager        Engine
  |--"你知道凶手是谁?"------>|                |                  |               |               |
  |                        |---dialogue----->|                  |               |               |
  |                        |    request      |                  |               |               |
  |                        |                 |                  |               |               |
  |                        |                 |--get NPCState---->|               |               |
  |                        |                 |<--state-----------|               |               |
  |                        |                 |  [DETERMINISTIC, <5ms]            |               |
  |                        |                 |                  |               |               |
  |                        |                 |--get PlayerView-->|               |               |
  |                        |                 |<--visible facts--|               |               |
  |                        |                 |  [DETERMINISTIC, <5ms, 见04文档]  |               |
  |                        |                 |                  |               |               |
  |                        |                 |--retrieve Memory (RAG: embedding 检索) |         |
  |                        |                 |  [DETERMINISTIC/embedding, ~20-50ms]              |
  |                        |                 |                  |               |               |
  |                        |                 |======= LLM CALL #1: Planning =======|            |
  |                        |                 |  Input: observation + memory + persona + goal    |
  |                        |                 |  Output: plan (e.g. "回避直接回答, 转移话题")      |
  |                        |                 |  可能触发 Function Calling 查询关系值/世界事实      |
  |                        |                 |  [LLM, ~1-3s]    |               |               |
  |                        |                 |                  |               |               |
  |                        |                 |--Action Proposal->|               |               |
  |                        |                 |   "reveal clue_1" (来自 plan)     |               |
  |                        |                 |                  |               |               |
  |                        |                 |                  |--Validator---|               |
  |                        |                 |                  |  check rules |               |
  |                        |                 |                  |  [DETERMINISTIC, <5ms]         |
  |                        |                 |<--approved/rejected+reason--------|               |
  |                        |                 |  ★ 必须在生成台词之前，见 §4.1     |               |
  |                        |                 |                  |               |               |
  |                        |                 |======= LLM CALL #2: Dialogue Generation =======  |
  |                        |                 |  Input: plan + 校验结果 + persona + narrative_event(若有) |
  |                        |                 |         -> 符合人设、剧情进度且不越过校验边界的台词  |
  |                        |                 |  [LLM, ~1-2s；无 Action 的回合可与 #1 合并]        |
  |                        |                 |                  |               |               |
  |                        |<--dialogue text-|                  |               |               |
  |<--显示台词--------------|                 |                  |               |               |
  |                        |                 |                  |               |               |
  |                        |                 |--log AgentTrace-->| (async, 不阻塞响应)            |
  |                        |                 |                  |               |               |
```

### 异步/周期性流程（不在这次交互的关键路径上）

```
每次玩家行为 -----> behavior_tracker.update()  [DETERMINISTIC, 实时, <1ms]
                     累积到 PlayerProfile.raw_stats

每个会话结束 / 每章结束 -----> profile_summarizer.run()
                     ======= LLM CALL: Profile Summary =======
                     Input: 累积的行为统计 + 关键对话摘要
                     Output: narrative_preference 自然语言描述
                     [LLM, ~2-5s, 周期触发，不阻塞玩家交互]

世界状态达到阈值(如 trust<30) -----> Narrative Engine 规则检查 [DETERMINISTIC]
                     若触发 -----> Experience Controller 排序候选事件 [DETERMINISTIC]
                     ======= LLM CALL: Event Content Generation =======
                     Input: event trigger + player profile + world state
                     Output: 具体事件文本/NPC 行为改变
                     [LLM, ~2-4s, 事件触发时才调用，不是每次交互]
```

## 4. 关键结论：一次玩家交互只需 1-2 次 LLM 调用

| 调用点 | 是否在关键路径 | 频率 |
|---|---|---|
| NPC Planning + Dialogue Generation | 是 | 每次对话交互 1-2 次（无 Action 合并为 1 次，有 Action 拆 2 次，见 §4.1/§4.2） |
| Player Profile Summary | 否，异步 | 每会话/章节 1 次 |
| Narrative Event Generation | 否，异步/低频 | 仅当规则判断触发事件时 |

### 4.1 为什么 Action 校验必须在 Dialogue Generation 之前

如果先生成台词再校验 Action，考虑这个序列：NPC_A 的 plan 是"透露线索 1"，台词已经把线索说出口，Validator 这时才判定 `trust=35 < 40` 拒绝该 Action。结果是玩家听到了线索、但世界状态里 `clue_1` 仍是 `hidden`——台词和事实源不一致，且**已经说出的话无法回滚**。后续 NPC 再基于 `hidden` 状态行动，玩家会看到明显的失忆/矛盾。

正确顺序是 `Plan → Action Proposal → Validate → Dialogue Generation`，把校验结果（批准/拒绝 + 原因）作为约束喂进生成阶段。被拒绝时 NPC 生成的是"回避/含糊其辞"的台词，这恰好是符合角色的行为，而不是一个需要特殊处理的错误分支。

### 4.2 合并 LLM 调用的适用范围

§4.1 意味着**有 Action 的回合无法合并成一次调用**（校验结果是第二次调用的输入）。采用分路径策略：

| 回合类型 | 调用次数 | 延迟 |
|---|---|---|
| 无 Action（纯对话/闲聊/信息已可见） | 1 次，输出 `{plan, dialogue}` | ~1.5-3s |
| 有 Action（透露线索、改变关系值等） | 2 次，中间插入确定性校验 | ~2.5-4s |

判断走哪条路径由第一次调用的输出决定：模型输出的 `plan.action_proposal` 为空即走快路径直接采用同次生成的 dialogue，非空则丢弃该 dialogue、校验后重新生成。代价是有 Action 的回合浪费了一部分首次生成的 token，换来的是台词与世界状态的强一致——这个交换在 RPG 场景里是划算的，因为不一致的代价是玩家直接察觉到的叙事崩坏。

## 5. 延迟预算

```
确定性部分总和:        ~30-60ms   (state读取 + memory检索 + validator)
LLM 调用:              ~1.5-3s    无 Action 回合，1 次合并调用
                       ~2.5-4s    有 Action 回合，2 次调用
------------------------------------------
总延迟 p50:            ~1.5-3s    (大部分回合不改变世界状态)
总延迟 p95:            ~4s
```

流式输出可以把**感知**延迟压到首 token 时间，对第二次调用尤其有效（校验已完成，台词可边生成边显示）。用小模型做 Planning、仅在需要复杂推理时路由到大模型可以进一步压缩——这是 Model Router 的落地场景，列为后续扩展而非必做项。

## 6. 对架构文档的反向验证

这次时序梳理验证了 [01_System_Architecture.md](./01_System_Architecture.md) 的分类：State 读取和校验必须是确定性代码（否则每轮多等几秒）；Player Model 的摘要必须异步；NPC Agent 的 LLM 调用是唯一必须同步等待的部分，因此也是交互体验优化（loading 状态、流式输出）的唯一着力点。
