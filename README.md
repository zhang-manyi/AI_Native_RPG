# AI Native RPG

[![CI](https://github.com/zhang-manyi/AI_Native_RPG/actions/workflows/ci.yml/badge.svg)](https://github.com/zhang-manyi/AI_Native_RPG/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230)](https://github.com/astral-sh/ruff)

一个 LLM 驱动的 RPG Agent 系统：可交互的 NPC Agent 运行时 + 确定性世界状态层 + 完整的 Trace/Eval 闭环。

场景是一起村庄失踪案：玩家自由对话调查，NPC 在人设约束下决定说什么、隐瞒什么，线索随关系值和调查进度逐步解锁。

## 核心设计主张

**不按功能分层，而按是否需要 LLM 分层。**

| 类型 | 内容 | 特征 |
|---|---|---|
| Deterministic | 世界状态、校验、信息投影、排序 | 纯代码，可单测，延迟 <10ms |
| Agent | 记忆检索、规划、工具调用、对话生成 | 依赖 LLM，输出不确定，需要 Eval |
| Observability | Trace、叙事状态面板、Eval | 消费前两者的产出，不在主流程 |

一次玩家交互只触发 1-2 次 LLM 调用（p50 ~1.5-3s）。若把状态管理、校验、排序也交给 LLM，延迟和成本都不现实。

三条由此展开的具体结论：

- **世界状态有唯一事实源。** 没有任何 Agent 能直接写世界状态，全部变更走 `Action Proposal → Validator → State Update`。这不是约定而是代码强制：`WorldState` 实例私有，读取返回深拷贝。
- **信息不对称是数据 + 纯函数，不是一个 Agent。** `Fact.visibility` + `reveal_condition` + `PlayerView()` 投影，省掉一整层 LLM 调用。条件是结构化对象而非字符串表达式——后者要么需要表达式解析器，要么用 `eval()`。
- **Action 校验在生成台词之前。** 否则台词已经说出口、Validator 才拒绝该行动，玩家听到的和世界状态不一致，且无法回滚。

## 状态

World State Manager 已完成（含剧本包加载与交叉引用校验）。当前进度见 [docs/09_Reference_Scenario.md](docs/09_Reference_Scenario.md) 的切片表。

## 快速开始

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest        # 全部测试，不需要 API key
```

需要真实 LLM 时（对话生成、剧情生成、LLM-as-judge 评分）：

```bash
cp .env.example .env              # 填入 DEEPSEEK_API_KEY
```

模型是 `deepseek-v4-flash`（OpenAI 兼容端点）。`.env` 已在 `.gitignore` 中，不会入库。测试一律走 mock client，不发网络请求。

## 结构

```
src/ai_native_rpg/
├── schemas/            Schema as Code（Pydantic，唯一事实源）
├── world/              World State Manager [确定性]
│   ├── conditions.py   reveal_condition 求值器
│   ├── validator.py    规则列表（非规则引擎）
│   ├── player_view.py  信息不对称投影
│   └── manager.py      唯一写入口
├── scenario.py         剧本包加载 + 校验
├── narrative/          Narrative Engine + 叙事算子
├── agent/              NPC Agent Runtime [Agent]
├── player/             Player Model
└── observability/      Trace / Eval

scenarios/<name>/       剧情内容（YAML），换剧本不改代码
docs/                   架构设计（01-10）
```

## 文档

| 文档 | 内容 |
|---|---|
| [01 System Architecture](docs/01_System_Architecture.md) | 分层原则与模块职责 |
| [02 Sequence Diagram](docs/02_Sequence_Diagram.md) | 一次交互的完整时序、LLM 调用点、延迟预算 |
| [03 Player Model](docs/03_Player_Model.md) | 实时行为统计 + 周期性语义摘要 |
| [04 World State Manager](docs/04_World_State_Manager.md) | 事实源、Validator、信息不对称 |
| [05 Narrative Engine](docs/05_Narrative_Engine.md) | 触发规则 + LLM 生成 + Experience Controller |
| [06 NPC Agent Spec](docs/06_NPC_Agent_Spec.md) | Memory / Planning / Tool Use / Dialogue / Harness |
| [07 Observability](docs/07_Observability.md) | Trace Viewer、叙事状态面板、数据回流 |
| [08 Evaluation](docs/08_Evaluation.md) | 一致性、记忆召回、工具成功率、叙事结构指标 |
| [09 Reference Scenario](docs/09_Reference_Scenario.md) | 场景设定、切片计划、测试策略 |
| [10 Narrative Operators](docs/10_Narrative_Operators.md) | 叙事结构调度、伏笔账本、张力与偏好分离 |
| [11 Prompt Lab](docs/11_Prompt_Lab.md) | Prompt 离线实验：候选对比、评分、选定 |
