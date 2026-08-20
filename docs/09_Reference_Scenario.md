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
| 叙事算子 | 实现（简化） | 4 个算子 + 伏笔账本 + 节奏规则；不做张力曲线拟合，见 [10](./10_Narrative_Operators.md#7-实现约定) |
| World State Manager | 完整实现 | Action Proposal/Validator 全流程跑通，PlayerView 支持 hidden/revealed/partial 三态，剧本包加载 + 交叉引用校验 |
| NPC Agent Runtime | 完整实现 | Memory（三层，RAG）+ Planning + Tool Use（Function Calling）+ Dialogue Generation + Action，核心投入区域 |
| Developer Platform | 实现（简化） | Trace Viewer + World/Memory Viewer + Narrative State Panel（伏笔账本、解锁进度、被拒 proposal） |
| Evaluation | 实现（简化） | 纯代码指标优先（Memory Recall@K、Tool Use Success Rate、伏笔两项），Persona Consistency 与 Narrative Coherence 用 LLM-as-judge + 20-30 条人工校准集 |
| Prompt Lab | 实现（简化） | 候选 prompt 批量对比 + 评分报告，见 [11](./11_Prompt_Lab.md)；不做自动搜索/进化 |
| 戏剧性反讽 | 不在本场景范围 | 需要 visibility 从"每 fact 一态"改成"每观察者一态"，见 [10](./10_Narrative_Operators.md#5-结构质量优先级) |
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
| 1 | 完成 | World State（完成）+ 1 个 NPC + mock LLM + Trace 落盘 | 玩家问一句话拿到一句回复，Trace 可查 |
| 2 | 完成 | 真实 LLM + embedding 记忆检索 + Tool Use | NPC 会查关系值/世界事实再回答 |
| 3 | 完成 | Narrative Engine + 算子 + 伏笔账本 + `StoryBeats` | 线索按节奏逐步解锁，伏笔有回收 |
| 4 | 进行中 | Player Model 影响披露方式 + 调试面板 | 两种玩法风格拿到不同的线索呈现 |
| 5 | 未开始 | Eval 脚本 + 数据回流一轮 | 改 prompt 前后的指标对比 |

切片 4 的**调试面板那一半已完成**：Web 界面（`src/ai_native_rpg/web/`，入口 `scripts/web.py`）替代了 `scripts/chat_demo.py` 作为主交互入口，实现见 [12_Web_Interface.md](./12_Web_Interface.md)。落地时确认的三件事：

- **叙事 tick 的异步化已解决。** 台词就绪即推送，tick 的 ~8.6s 落在玩家读台词和打字的时间里。传输用 SSE，`POST /turn` 返回 `202`，台词/tick/场景/面板四类事件走同一条流。
- **并发按会话串行。** 每个会话一个单线程执行器，世界因此只有一个写入者，`WorldStateManager` 不改。一个回合入队两个作业（对话、tick），所以 `dialogue` 事件不必等 tick。
- **面板判定下沉到 `observability/panels.py`**（纯函数），`chat_demo.py` 的三个 print 改为消费同一份模型——终端与 Web 共享判定，各自只拥有渲染。终端版因此仍然可用，是无 JS 环境下最快的排查入口。

**下一步从这里继续**：切片 4 剩下的 Player Model 那一半——Behavior Tracker 写入 `PlayerProfile`，`weight_for()` 是唯一接口，`select_candidate(profile=...)` 已经在消费它。它不依赖 Web；面板届时加一块「玩家画像」即可。

实现时发现、值得记住的一件事：**`busy` 不能从 `queue.unfinished_tasks` 推导。** 那个计数在最后一个作业被*取走*时就减到 0，于是出现一个窗口——台词已完成、tick 还在写世界状态、而下一个回合被放行了，正是执行器要防的竞态。改成显式的在飞计数（两个作业都算），并由 `test_busy_stays_true_until_the_tick_completes` 钉住。

切片 1-3 已就绪的挂点：

- `Harness.respond(observation, player_id=..., narrative_event=...)` 走完 Memory 检索 → Tool Use → Planning → Validator → Dialogue → Reflection 并产出 `AgentTrace`；`narrative_event` 注入 planning 与 dialogue 两个 prompt（无 action 的快路径只有第 1 次调用，只注入 dialogue 会在这类回合整个丢掉铺垫）。
- `NarrativeEngine.tick(player_id=..., profile=None)` 走完 `check_triggers()` → `select_candidate()` → `generate_content()` → Action Proposal，产出 `NarrativeTick`；候选为空时**零 LLM 调用**。生成内容存为 `pending_event`，由下一回合 `take_pending_event()` 取走——叙事生成因此永远不在玩家等待的路径上（[02 §4](./02_Sequence_Diagram.md)）。
- `PlayerProfile`（`schemas/narrative.py`）已定义且被 `select_candidate()` 消费，但**还没有写入方**：`profile=None` 时全部偏好按 0.5 中性处理。切片 4 补 Behavior Tracker 即可接上，`weight_for()` 是唯一接口。
- `WorldStateManager.player_view()` / `dry_run()` / `submit()` / `get_relationship()` 提供确定性世界层；`WorldState.story_beats` 承载叙事进度，`story_beats.chapter` / `.tension` 是合法 condition path。
- `scripts/chat_demo.py` 可手动跑真实对话，`/beats` `/ledger` `/unlock` 三个命令对应 [07 §2.3](./07_Observability.md#23-narrative-state-panel叙事状态面板) 的前三块面板内容——切片 4 的可视化页面可以直接照这三个渲染函数搬。
- `OpenAICompatibleClient` / `MockLLMClient` 同实现 `LLMClient` Protocol，`USE_MOCK_LLM` 或 `--mock` 切换。前者走通用 OpenAI 兼容 `chat/completions`，`LLM_PROVIDER=deepseek|openai` 选一组 `*_API_KEY` / `*_BASE_URL` / `*_MODEL`（表在 `config._PROVIDERS`）。换 provider 是配置项而非第二个实现，这是当初不用 vendor SDK 换来的。
- 剧本包已承载叙事内容：`world.yaml` 的 `narrative:` 块给出 `language` / `paced_clues`（fact_id + 该线索被压着时的禁止项）/ `reversal_fact` / `universal_constraints`，由 `load_narrative_directives()` 读出并在加载时做交叉引用校验。`rules.py` 因此不含任何具体 fact id 或中文字符串——**结构留在 Python，内容属于剧本**。省掉 `directives` 参数的调用方会退化成「什么都不铺垫」，不会继承别的剧本的 fact id。
- `NPCWorldState.name` 是公开显示名（`玛尔塔` / `洛伦`），与 `Location.name` 同性质的客观事实。叙事生成 prompt 只给名字不给 id：id 会漏进生成的散文，而且 fact 的值本身可能就是一个 id（本场景 `killer_identity` 的值是 `npc_b`），按 id 列出场名单等于把未披露的值写进 prompt。

切片 3 实现时发现、值得记住的四件事：

**张力上限必须随 `quests.investigation.stage` 抬升**（`rules.py` 的 `_TENSION_CEILING_BY_STAGE`）。最初用一个常量阈值，结果 `escalate` 一旦把张力顶过常量就不再触发，张力永久停在略高于常量处——任何以更高张力为条件的 fact 就成了永远解锁不了的死内容。这类错误剧本加载器抓不到：它校验 `path` 能否解析，不校验阈值能否达到。

**同时可以有几条伏笔未回收，不是一条。** 最初的上限是「同时 1 条 + 一局总配额 2」，理由是防止欠下还不清的债。但账本变长恰恰是账本该显示的东西（`is_overdue` 就是为此存在），用「不许欠第二笔」来保证「不会欠太多笔」，是把温度计当空调用。推理类型的标准做法是多条线索指向一个结论——Justin Alexander 的 Three Clue Rule 从故障率立论：一条线索承担一个结论就是个 chokepoint，而这里最后一环是 LLM 愿不愿意把 hook 用出来，比桌面上的检定更不可靠。现在是 `MAX_OPEN_FORESHADOWINGS = 3` 作失控保护 + `FORESHADOW_SPACING` 不许连着两回合埋（后者顺带防住「回收清空账本后立刻又提供埋点、模型复用刚用掉的 id 白烧一次调用」，那才是原配额真正在防的事）。

**生成内容必须校验它提到的人存在。** 一次真实对局里模型埋了一条关于「磨坊主」的伏笔——剧本只有两个 NPC，没有磨坊主。其他规则全过：fact id 是新的、条件可求值，因为没有任何规则看名字。凭空的角色于是进了账本，欠玩家一个关于不存在的人的回收。这是 [10 §5](./10_Narrative_Operators.md#5-结构质量优先级) 优先级 1「不自相矛盾」的一个缺口：架构保证的是 NPC 不会误报世界，管不了 Engine 给自己加演员。现在由 `participants_must_exist` 拦下，id 和显示名都接受。根因有两层，prompt 不给出场名单是另一层——模型无法遵守一个没人描述过的边界。

**`pending_event` 必须跟着「这个 beat 到底发生了吗」一起判定。** 生成先于 Validator 裁决，所以被拒的 beat 手上仍握着一句 hook。原先只用这个判断决定要不要记 cooldown，结果一个记成 `relieve` 的回合照样把 hook 交给下一回合，NPC 于是去铺垫一件没发生的事——伏笔的话还是一条永远不会被回收的坑，正是账本要防的失败从后门进来。

实测延迟（[02 §5](./02_Sequence_Diagram.md) 预算：p50 1.5-3s，p95 4s）：

| 链路 | 时间 | tokens | 备注 |
|---|---|---|---|
| 对话，无 Action | ~4s / 1 次调用 | 2100 | 2026-08-09，`deepseek-v4-flash` |
| 对话，有 Action | ~7.4s / 2 次调用 | 3200 | 同上 |
| 叙事生成，压短前 | **50s** | completion 1675 | `deepseek-v4-flash`。不是推理开销（`token_usage` 无 `reasoning_tokens`），是输出真有那么长 |
| 叙事生成，压短后 | **8.7s** | completion 356 | `gpt-5.6-sol`。prompt 明确「直接给 JSON、不写推导过程」+ 各字段字数上限 + 收紧 `GeneratedContent` 的 Field description（它作为 JSON Schema 进请求，是指令的另一半） |

叙事生成这一类调用不在原预算里，且仍超预算。**架构上它已经不在玩家等待路径上**（生成存 `pending_event`，下一回合才用），但 `chat_demo.py` 是同步调用，所以那几秒实际全压在玩家身上。终端版故意不做异步——留给 Web 界面一次做对，见 [12](./12_Web_Interface.md)。注意 `WorldStateManager` 不是线程安全的，而 tick 每轮至少写一次 `advance_turn`。

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
