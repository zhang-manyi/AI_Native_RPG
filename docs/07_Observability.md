# 07. Observability & Developer Platform

## 1. 职责

让开发者能看见、调试、迭代 Agent 系统，不参与游戏主流程（不阻塞玩家交互）。

## 2. 子模块

### 2.1 Trace Viewer

记录每次 NPC Agent 交互的完整决策链：Observation → Memory Retrieval → Plan → Tool Call → Action → Result，附带每步的延迟和 token 用量。类似 LLM Agent 调试器（对标 LangSmith/OpenTelemetry 的思路，但不需要引入完整的 APM 系统）。

数据结构见 [schemas/agent_trace.py](./schemas/agent_trace.py)。写入是异步的（fire-and-forget），不阻塞对玩家的响应，见 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md) 中的 `log AgentTrace` 步骤。

### 2.2 World Viewer / Memory Explorer

调试用的只读视图，不是独立服务，是对 World State Manager 和 NPC Memory 存储的查询接口 + 简单前端：

- World Viewer：展示当前 `WorldState`（NPC 位置、阵营状态、任务进度）。
- Memory Explorer：点开某个 NPC，展示其 `EpisodicMemory` / `SemanticMemory` / `RelationshipMemory`。

初期可以合并成一个页面，没必要做成两个独立工具。

### 2.3 Narrative State Panel（叙事状态面板）

Trace Viewer 回答"这一轮 Agent 是怎么决策的"，回答不了"这个故事现在是什么形状"。叙事结构（见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)）本来是隐形的，这个面板把它变成看得见、可调的东西。四块内容按实用性排序：

**1. 伏笔账本**

| 伏笔 | 埋下轮次 | 回收条件 | 已欠 | 状态 |
|---|---|---|---|---|
| 玛尔塔那晚没睡 | 第 3 轮 | `trust >= 40` | 9 轮 | ⚠️ 超期 |
| 森林里的脚印被雨冲过 | 第 7 轮 | `stage >= 2` | 5 轮 | 正常 |

把"LLM 埋了伏笔但忘了回收"从只能通读对话记录才能发现，变成一眼可见。

**2. 解锁进度板**

```
clue_1_witness      trust 32/40          ▓▓▓▓▓▓▓▓░░  80%
clue_2_identity     trust 32/70          ▓▓▓▓░░░░░░  46%
killer_identity     trust 32/85  或  stage 1/3       (any)
npc_a_threatened    trust 32/70 且 fear 30/≤20       (all, fear 不达标)
```

条件表就是整个悬疑的解锁图，这里让它变成实时进度。**调阈值本来全靠猜**（40 是不是太高？玩家会不会永远卡住），有这个就有依据。

配套一个"**为什么玩家还看不到 X**"：点开一条 hidden fact，显示每个 clause 是否满足、当前值多少。就是把求值器的中间结果暴露出来，成本极低。

**3. 被拒绝的 Action Proposal**

```
第 4 轮  npc_a  reveal_fact(killer_identity)
         ✗ reveal_requires_condition_met — 条件未满足 (trust 32 < 85)
```

最容易被低估的一块，但它是**架构主张的直接证据**：看到"LLM 想直接说出凶手、被规则挡下"，就不需要解释为什么要有确定性校验层。同时是 §3 数据回流里最有价值的样本——高频被拒说明 prompt 里的约束没写清楚。

**4. 算子时间线与关系值走势**

按轮次展示触发了哪个算子，叠加 trust/fear 曲线。用来看节奏：是不是连着三轮 `reveal`，是不是十轮没有任何算子触发。

### 2.4 ⚠️ 面板绕开 PlayerView，玩家接口只走 PlayerView

面板的数据源是 Trace + `WorldState`（全量事实），**不能走 `PlayerView()`**——它要显示的恰恰是玩家看不到的东西。反过来，面向玩家的接口**只能**走 `PlayerView()`。

**两个端点必须在代码层面分开，不共用 handler 或序列化器**，否则调试面板本身就成了整套 visibility 机制要防的泄漏通道。三条强制手段：

- 调试路由独立前缀（`/debug/*`），非本地环境默认关闭
- 玩家侧响应模型只接受 `VisibleState`，不接受 `WorldState`，用类型签名钉死
- 一条测试断言玩家侧响应里不出现任何 hidden fact 的 value（`test_player_view.py` 里 `test_hidden_fact_value_never_appears_anywhere_in_view` 已是这个思路，扩展到 HTTP 层）

### 2.5 Eval Dashboard

见 [08_Evaluation.md](./08_Evaluation.md)，本文档只负责"如何展示"，指标定义在 Evaluation 文档里。

## 3. Data Flywheel（数据回流）

```
Player Interaction
      |
      v
AgentTrace (记录)
      |
      v
Failure Detection (规则: 如 tool_call 失败率高 / persona 不一致)
      |
      v
Human Review (人工标注问题样本)
      |
      v
Dataset (用于后续 prompt 调优或 few-shot 示例库)
      |
      v
Prompt / Rule 更新
```

初期落地方式：Trace 存下来后，写一个简单脚本筛出"低分交互"（比如 Eval 规则判定 persona 不一致的样本），人工看一眼，改 prompt。不需要做自动化 fine-tuning 管线，那是更大规模的后续工作。

## 4. 数据结构

见 [schemas/agent_trace.py](./schemas/agent_trace.py)。

## 5. 当前实现范围

- Trace 存储用 SQLite/JSON 文件即可，不引入专门的 tracing 后端；数据量增长后再评估是否需要专用存储。
- 前端可以是一个简单的本地网页（甚至 Streamlit），核心是把 Trace 数据结构化展示出来，不追求生产级 UI。
- Data Flywheel 先跑通一轮完整流程（记录 -> 筛选 -> 人工看 -> 改 prompt -> 对比前后效果），自动化留作后续工作。
- 叙事状态面板按 §2.3 的排序实现：伏笔账本和解锁进度板优先（调阈值直接依赖它们），算子时间线可以最后做。
- §2.4 的隔离要求是硬性的，不是"注意事项"：调试路由独立前缀 + 玩家侧响应模型只接受 `VisibleState` + 一条断言 hidden fact 不出现在玩家响应里的测试，三者都要有。
