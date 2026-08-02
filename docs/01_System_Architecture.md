# 01. System Architecture

## 1. 设计原则

核心原则：**不按"功能名称"分层，而按"是否需要 LLM"分层。**

三类模块：

| 类型 | 定义 | 特征 |
|---|---|---|
| **Deterministic（确定性系统）** | 事实、规则、状态、排序 | 纯代码，无 LLM，可单测，延迟 <10ms |
| **Agent（智能系统）** | 开放式推理、生成、决策 | 依赖 LLM，不确定性输出，需要 Eval |
| **Observability（可观测/优化）** | 记录、展示、迭代 | 消费前两者产生的数据，不参与主流程 |

理由：一次玩家交互如果串行触发 5-6 次 LLM 调用，延迟和成本都不现实。工业级系统应该让确定性问题用结构化代码解决，只把真正开放式的推理/生成问题交给 LLM。

## 2. 架构图

```
                         Developer Platform
                                |
                 --------------------------------
                 |              |               |
             Trace Viewer   Eval Dashboard   Data Flywheel
                                ↑
                          Observability
                                ↑

┌───────────────────────────────────────────────────────┐
│                 AI Native Game Runtime                 │
│                                                         │
│   Player Model                    [Deterministic + 周期LLM] │
│   (behavior stats + periodic profile summary)          │
│          |                                              │
│          ↓                                              │
│   Experience Controller           [Deterministic]       │
│   (rule-based ranking of candidate events)              │
│          |                                              │
│          ↓                                              │
│   Narrative Engine                [Rule Trigger + LLM Generation] │
│   (event trigger rules -> LLM writes structured content)│
│          |                                              │
│          ↓                                              │
│   World State Manager             [Deterministic]       │
│   (Single Source of Truth + Action Validator)           │
│          |                                              │
│          ↓                                              │
│   NPC Agent Runtime               [Agent]                │
│    ├── Memory Retrieval (RAG)                            │
│    ├── Planning                                          │
│    ├── Tool Use (Function Calling)                       │
│    ├── Dialogue Generation  <---- 消费 Narrative Event    │
│    │   (把结构化状态/事件译成符合人设与剧情进度的台词)      │
│    └── Action Proposal  ---------> back to World State   │
│                                     Manager for validation│
└───────────────────────────────────────────────────────┘
                                |
                                ↓
                         Game Environment
```

## 3. 模块职责一览

| 模块 | 类型 | 职责一句话 | 详见 |
|---|---|---|---|
| Player Model | Deterministic + 周期 LLM | 玩家是谁，喜欢什么 | [03_Player_Model.md](./03_Player_Model.md) |
| Experience Controller | Deterministic | 张力准入 + 按玩家偏好排序候选叙事算子 | [05_Narrative_Engine.md](./05_Narrative_Engine.md) |
| Narrative Engine | Rule + LLM | 决定世界接下来发生什么，并生成具体内容 | [05_Narrative_Engine.md](./05_Narrative_Engine.md), [10_Narrative_Operators.md](./10_Narrative_Operators.md) |
| World State Manager | Deterministic | 世界唯一事实源 + 校验所有 Action | [04_World_State_Manager.md](./04_World_State_Manager.md) |
| NPC Agent Runtime | Agent | NPC 的记忆检索(RAG)/规划/工具调用(Function Calling)/对话生成/行动 | [06_NPC_Agent_Spec.md](./06_NPC_Agent_Spec.md) |
| Developer Platform | Observability | Trace/叙事状态面板/Eval/数据回流 | [07_Observability.md](./07_Observability.md), [08_Evaluation.md](./08_Evaluation.md) |
| Prompt Lab | Observability | 离线比较候选 prompt，量化选定 | [11_Prompt_Lab.md](./11_Prompt_Lab.md) |


## 6. 文档目录

```
docs/
├── 01_System_Architecture.md          本文档
├── 02_Sequence_Diagram.md             一次交互的完整时序，标注 LLM 调用点
├── 03_Player_Model.md                 玩家画像：实时统计 + 周期性 LLM 摘要
├── 04_World_State_Manager.md          世界状态、Action Validator、Player View
├── 05_Narrative_Engine.md             事件触发规则 + LLM 生成 + Experience Controller
├── 06_NPC_Agent_Spec.md               NPC Agent 规格：Memory/Planning/Tool/Dialogue/Eval
├── 07_Observability.md                Trace Viewer / 叙事状态面板 / Data Flywheel
├── 08_Evaluation.md                   一致性/记忆召回/工具成功率/叙事连贯性/叙事结构指标
├── 09_Reference_Scenario.md           参考场景：模块范围、实现顺序、测试策略
├── 10_Narrative_Operators.md          叙事算子：结构调度、伏笔账本、张力与偏好的分离
├── 11_Prompt_Lab.md                   Prompt 离线实验：候选对比、评分、选定流程
└── schemas/                           设计期草稿（Pydantic）
    ├── player_model.py                实际实现以 src/ai_native_rpg/schemas/ 为准
    ├── world_state.py
    ├── narrative_event.py
    ├── npc_agent.py
    ├── memory.py
    └── agent_trace.py
```

## 7. 代码结构

```
src/ai_native_rpg/
├── schemas/            Schema as Code（唯一事实源，docs/schemas/ 为设计期草稿）
├── world/              World State Manager [确定性]
│   ├── conditions.py   reveal_condition 求值器
│   ├── validator.py    规则列表
│   ├── player_view.py  信息不对称投影
│   └── manager.py      唯一写入口
├── scenario.py         剧本包加载 + 校验
├── llm/                LLM client Protocol + DeepSeek 实现 + mock
├── narrative/          Narrative Engine + Experience Controller + 叙事算子
├── agent/              NPC Agent Runtime [Agent]
├── player/             Player Model
└── observability/      Trace / Eval

scenarios/<name>/       剧情内容（YAML），换剧本不改代码
prompts/                当前生效的 prompt，运行时只读
prompt_lab/             prompt 候选、测试场景、对比报告（见 11）
```

**剧情背景分三层**，不是硬编码也不是全在初始数据里：静态设定（地点、persona、初始 facts 与 visibility、初始 trust）在 `scenarios/*.yaml`；触发规则在 `narrative/rules.py`（Python，[05](./05_Narrative_Engine.md#6-当前实现范围) 说明初期不配置化）；运行时剧情由 LLM 生成后经 Validator 写回。

**Prompt 不内联在代码里**，放在 `prompts/`，由 [11_Prompt_Lab.md](./11_Prompt_Lab.md) 的离线实验流程选定后写入。
