# 09. Reference Scenario

## 1. 目的

给出一个具体、可实现的最小场景，用来验证整套架构（[01_System_Architecture.md](./01_System_Architecture.md)）能否真实跑通，而不是停留在设计层面。每个模块只实现"能证明这一层存在且工作正常"的最小版本，复杂度按需增长。

## 2. 场景设定

小型村庄，玩家调查一起失踪案。

- **World**：1 个村庄场景，3-4 个地点（村庄广场、酒馆、NPC_A 家、森林入口）。
- **NPC**：2 个 NPC 足够展示架构完整性：
  - NPC_A：知道部分真相，persona = 恐惧、保护家人，目标是"隐藏秘密"。
  - NPC_B：真正的失踪案关键人物（或凶手），persona = 冷静、有城府。
- **Narrative Event**：1 条主线——"随着玩家和 NPC_A 的信任值提升，NPC_A 逐渐透露线索，最终引向 NPC_B"。触发规则用信任度阈值（如 trust > 40 触发线索 1，trust > 70 触发线索 2）。
- **叙事算子**：实现 `foreshadow` / `reveal` / `escalate` / `reverse` 四个，配合伏笔账本和节奏规则，见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)。本场景的 `reverse` 已经埋好触发条件：`npc_a_threatened`（洛伦威胁过玛尔塔的儿子）一旦解锁，玛尔塔此前所有的回避都从"包庇凶手"重读成"保护儿子"——玩家手上信息没变，意义全变了。
- **结局**：至少 3 个，否则是技术演示而不是故事——查明真相 / 被洛伦先动手 / 玛尔塔彻底闭口（fear 过高，社交线断掉）。
- **Player Model**：跟踪 1-2 个维度即可（如 `exploration_score`、`social_score`），影响 NPC_A 透露线索的方式（探索型玩家给环境线索，社交型玩家给对话线索）。

这个规模足以走完 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md) 里的完整链路，同时避免陷入世界观内容本身的工作量。

## 3. 各模块的实现范围（目标深度，非进度）

| 模块 | 范围 | 说明 |
|---|---|---|
| Player Model | 实现（简化） | Behavior Tracker 用真实代码；Profile Summarizer 初期用规则模板代替 LLM 批量调用 |
| Experience Controller | 实现（简化） | 加权求和打分，2 个维度即可 |
| Narrative Engine | 完整实现 | 规则触发 + 真实 LLM 生成结构化内容 |
| 叙事算子 | 实现（简化） | 4 个算子 + 伏笔账本 + 节奏规则；不做张力曲线拟合，见 [10](./10_Narrative_Operators.md#6-实现约定) |
| World State Manager | 完整实现 | Action Proposal/Validator 全流程跑通，PlayerView 支持 hidden/revealed/partial 三态，剧本包加载 + 交叉引用校验 |
| NPC Agent Runtime | 完整实现 | Memory（三层，RAG）+ Planning + Tool Use（Function Calling）+ Dialogue Generation + Action，核心投入区域 |
| Developer Platform | 实现（简化） | Trace Viewer + World/Memory Viewer + Narrative State Panel（伏笔账本、解锁进度、被拒 proposal） |
| Evaluation | 实现（简化） | Memory Recall@K + Tool Use Success Rate + 三个叙事结构指标优先（均为纯代码），Persona Consistency 人工评分 |
| 戏剧性反讽 | 不在本场景范围 | 需要 visibility 从"每 fact 一态"改成"每观察者一态"，见 [10](./10_Narrative_Operators.md#45-戏剧性反讽扩展点不在本场景) |
| Faction Agent / Event Agent | 不在本场景范围 | 架构上可复用 NPC Agent Runtime，作为后续横向扩展 |
| Model Router | 不在本场景范围 | 固定用一个模型；后续可按任务复杂度路由到不同大小的模型 |

## 4. 建议的实现顺序（对应依赖关系）

**这是依赖顺序，不是交付顺序。** 严格按 1→6 逐层做完会把 Trace 排到最后，等发现 prompt 有问题时已经没有数据可查（§5 的第 4 条正好要求相反）。实际交付按**垂直切片**：每个切片都是一条端到端可跑通的窄链路，任何时候中断都有可演示的东西。

依赖关系：

1. **World State Manager**：Schema 和 Validator 是所有其他模块的基础。实现约定与已知边界见 [04_World_State_Manager.md](./04_World_State_Manager.md#5-实现约定)。
2. **NPC Agent Runtime**：Memory + Planning + Dialogue Generation + Action，先让一个 NPC 能对话。
3. **Player Model**：Behavior Tracker 先接入，Profile Summarizer 可以先用规则模板占位。
4. **Narrative Engine + Experience Controller**：接入触发规则和排序逻辑，串联主线事件。叙事算子和伏笔账本在这一步之后叠加。
5. **Developer Platform**：Trace 记录从第 2 步就该同步做（越早接入越容易积累调试数据），可视化页面可以放最后。
6. **Evaluation**：最后补充，基于前面积累的 Trace 数据跑一次评估脚本。

切片划分建议：

切片划分（**本表是全项目唯一维护完成状态的地方**，其他文档只描述设计与契约；每个切片完成后打一个 git tag，历史由 git 回答）：

| 切片 | 状态 | 内容 | 结束时能演示什么 |
|---|---|---|---|
| 1 | 进行中 | World State（完成）+ 1 个 NPC + mock LLM + Trace 落盘 | 玩家问一句话拿到一句回复，Trace 可查 |
| 2 | 未开始 | 真实 LLM + embedding 记忆检索 + Tool Use | NPC 会查关系值/世界事实再回答 |
| 3 | 未开始 | Narrative Engine + 算子 + 伏笔账本 + `StoryBeats` | 线索按节奏逐步解锁，伏笔有回收 |
| 4 | 未开始 | Player Model 影响披露方式 + 调试面板 | 两种玩法风格拿到不同的线索呈现 |
| 5 | 未开始 | Eval 脚本 + 数据回流一轮 | 改 prompt 前后的指标对比 |

**下一步从这里继续**：切片 1 的后半 —— NPC Agent Harness（Memory 检索 + Planning/Dialogue + Action Proposal）、mock LLM client（Protocol 抽象，便于换真实模型和单测）、Trace 落盘。World State 侧的接口已就绪：`WorldStateManager.player_view()` 给 Agent 提供可见信息，`dry_run()` / `submit()` 走 Action 校验，`get_relationship()` 提供 prompt 所需的关系值。

## 5. 测试策略

按模块类型分，不是所有单元都 TDD：

| 模块类型 | 测试方式 | 理由 |
|---|---|---|
| 确定性（World State、Validator、PlayerView、Experience Controller、Behavior Tracker、Eval 指标） | **严格先写测试**，纯函数，输入输出明确 | 测试便宜且能真正挡住回归 |
| LLM（Planning、Dialogue Generation、事件内容生成） | mock LLM 做**契约测试**（JSON schema 解析、tool call 循环、重试降级、超时），不做行为断言 | `assert dialogue == "..."` 没有意义；行为质量交给 [08_Evaluation.md](./08_Evaluation.md) 的 Eval 套件 |

这个区分本身就是"评测体系"的落地：**单元测试保证系统不崩，Eval 保证效果不退**，两者不能互相替代。

## 6. 设计原则的落地体现

1. 不是所有模块都调用 LLM：确定性问题（状态管理、排序、校验）由结构化系统解决，LLM 只负责真正开放式的推理和生成（NPC 对话决策、剧情内容生成、把结构化状态译成台词）。
2. 世界状态和玩家可见信息分离：通过 `Fact.visibility` + `PlayerView()` 投影函数解决，而不是引入额外的 Agent。
3. 一次玩家交互只触发 1-2 次 LLM 调用，延迟预算在 4 秒以内（无 Action 的回合 1.5-3s），见 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md)。
4. 系统有完整的 Trace/Eval 闭环，支持持续迭代，不是一次性脚本。
5. 叙事结构由确定性系统调度，LLM 只在给定槽位里填内容：这是原则 1 在叙事维度上的重演，见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)。
6. 未实现的部分（Faction Agent、Model Router、戏剧性反讽、更复杂的规则引擎）在架构上是自然的横向扩展，当前场景范围内没有必要，超出范围的原因见上表。
