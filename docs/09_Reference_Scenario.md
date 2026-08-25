# 09. Reference Scenario

## 1. 目的

给出一个具体、可实现的最小场景，用来验证整套架构（[01_System_Architecture.md](./01_System_Architecture.md)）能否真实跑通，而不是停留在设计层面。每个模块只实现"能证明这一层存在且工作正常"的最小版本，复杂度按需增长。

## 2. 场景设定

小型村庄，玩家调查一起失踪案。

- **World**：1 个村庄场景，3-4 个地点（村庄广场、酒馆、NPC_A 家、森林入口）。
- **NPC**：3 个。前两个足以展示架构完整性，第三个是切片 5 加的，理由见 [14 §3](./14_Case_Design.md#3-第三个-npc酒馆老板)：
  - NPC_A（玛尔塔）：知道部分真相，persona = 恐惧、保护家人，目标是"隐藏秘密"。
  - NPC_B（洛伦）：失踪案的关键人物，persona = 冷静、有城府。
  - NPC_C（酒馆老板）：切片 5 加入。他让"NPC 记忆是主观的、可能记错"**可被观察**——只有一个可对话 NPC 时，玩家分不出"主观记忆"和"客观世界"；他同时是玛尔塔闭口后的退路，和第二个可信嫌疑人。
- **Narrative Event**：切片 1-4 是 1 条主线（"随着 trust 提升，NPC_A 逐渐透露线索，最终引向 NPC_B"，触发用信任阈值）。切片 5 换成**剧本定义的事件网络**：主线事件 + 卡点处的支线，支线改变主线的形状而不只是进度，见 [13](./13_Narrative_Events.md)、[14 §6](./14_Case_Design.md#6-主线事件链)。
- **悬念**：单一嫌疑人是个内容缺陷——NPC_B 的 persona 写着"关键人物"，玩家第一眼就知道答案，三线索规则于是空转。修法不是把凶手移出可对话角色（那会减少悬念：不能盘问他就观察不到他撒谎），而是**给出第二个可信的解释**，见 [14 §2](./14_Case_Design.md#2-洛伦太明显根因是没有第二个解释)。
- **叙事算子**：实现 `foreshadow` / `reveal` / `escalate` / `reverse` 四个，配合伏笔账本和节奏规则，见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)。本场景的 `reverse` 已经埋好触发条件：`npc_a_threatened`（洛伦威胁过玛尔塔的儿子）一旦解锁，玛尔塔此前所有的回避都从"包庇凶手"重读成"保护儿子"——玩家手上信息没变，意义全变了。
- **结局**：至少 3 个，否则是技术演示而不是故事。切片 5 把它们倒推成结构化成立条件，各附一条可达路径（[14 §4](./14_Case_Design.md#4-三个结局)），现在写在剧本的 `narrative.endings` 里并由加载器校验可达。**四个终局**：查明真相 / 指控错人 / 被洛伦先动手 / 没查明白（天数用尽）。「玛尔塔彻底闭口」也声明并校验，但它是**里程碑而不是终局**（`terminal: false`）——它只关闭玛尔塔这条社交线，玩家仍可走独立调查通道，见 [14 §4.3](./14_Case_Design.md#43-被洛伦先动手-玛尔塔彻底闭口)。"被洛伦先动手"标着 `pending`，因为抬 tension 的 M6 还没写。`fear` 已经会涨了（见下方第二批）。
- **时间与地点**：切片 5 引入一天三个时段、每时段一个地点、天数上限。这给系统此前完全没有的**成本**——每个选择都在排除别的选择，也是 Player Model 能区分玩家风格的前提。已实现（第二批）。四个地点不加，分工见 [14 §5](./14_Case_Design.md#5-四个地点的分工)。
- **Player Model**：跟踪 1-2 个维度即可（如 `exploration_score`、`social_score`），影响 NPC_A 透露线索的方式（探索型玩家给环境线索，社交型玩家给对话线索）。

这个规模足以走完 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md) 里的完整链路，同时避免陷入世界观内容本身的工作量。

## 3. 各模块的实现范围（目标深度，非进度）

| 模块 | 范围 | 说明 |
|---|---|---|
| Player Model | 实现（简化） | Behavior Tracker 用真实代码；Profile Summarizer 初期用规则模板代替 LLM 批量调用 |
| Experience Controller | 实现（简化） | 加权求和打分，2 个维度即可 |
| Narrative Engine | 完整实现 | 规则触发 + 真实 LLM 生成结构化内容 |
| 叙事算子 | 实现（简化） | 4 个算子 + 伏笔账本 + 节奏规则；不做张力曲线拟合，见 [10](./10_Narrative_Operators.md#7-实现约定)。切片 5 起算子不再是调度单位，降级为"事件怎么讲" |
| 事件层 | 实现（切片 5） | 剧本定义的事件网络 + 时间制 + 声音层次 + 玩家动作（移动、结束对话），见 [13](./13_Narrative_Events.md) |
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
| 5 | 进行中（前两批完成） | 事件层 + 时间制 + 玩家动作（[13](./13_Narrative_Events.md)、[14](./14_Case_Design.md)） | 一次完整调查：3-4 天、多地点、3 个不同结局 |
| 6 | 未开始 | Eval 脚本 + 数据回流一轮 | 改 prompt 前后的指标对比 |

切片 5 是**修正一处设计偏差**，不是加功能：[05 §2.1](./05_Narrative_Engine.md#21-规则触发确定性) 原本写的候选是*事件*，实现走成了*算子*，于是"这回合该发生什么"没有依据可答（症状见下方"一轮真实对局暴露的四件事"，以及新增的第五件）。它同时让 [10 §5](./10_Narrative_Operators.md#5-结构质量优先级) 优先级表第 4 行「收束」从待实现变为可实现——现在做不了，是因为没有任何状态表达"故事走到哪一步了"。

切片 4 的**调试面板那一半已完成**：Web 界面（`src/ai_native_rpg/web/`，入口 `scripts/web.py`）替代了 `scripts/chat_demo.py` 作为主交互入口，实现见 [12_Web_Interface.md](./12_Web_Interface.md)。落地时确认的三件事：

- **叙事 tick 的异步化已解决。** 台词就绪即推送，tick 的 ~8.6s 落在玩家读台词和打字的时间里。传输用 SSE，`POST /turn` 返回 `202`，台词/tick/场景/面板四类事件走同一条流。
- **并发按会话串行。** 每个会话一个单线程执行器，世界因此只有一个写入者，`WorldStateManager` 不改。一个回合入队两个作业（对话、tick），所以 `dialogue` 事件不必等 tick。
- **面板判定下沉到 `observability/panels.py`**（纯函数），`chat_demo.py` 的三个 print 改为消费同一份模型——终端与 Web 共享判定，各自只拥有渲染。终端版因此仍然可用，是无 JS 环境下最快的排查入口。

切片 5 的**第一个垂直切片已完成**（[15 §8](./15_Event_Script.md#8-落地顺序建议) 的第 1-5 项）：事件 schema、`active_event` 状态、事件候选取代算子候选、剧本里的 M1 + M2 + F1、以及玩家回应映射到结果。链路"事件被选中 → 算子表达 → 玩家回应 → 映射到结果 → 数值变化"由 `tests/test_event_chain.py` 端到端钉住。落地时确认的四件事：

- **算子候选整体删除，`rules.py` 只剩事件触发。** 相应的 `tests/test_narrative_rules.py` 也删了（由 `test_event_triggers.py` 取代）——那些测试钉的是"算子作为调度单位"的行为，不是回归。
- **触发是硬 `Condition`，选项判定才走三段式**（[15 §6.2](./15_Event_Script.md)）。两套规则分在两处：`rules._trigger_holds` 用现有求值器，`schemas/events.check_band` 只被选项判定调用。
- **`Condition` 新增 `contains`。** 事件依赖图要问"M1 完成了吗"，读的是世界里的一个列表；已有的 `in` 问的是相反方向（世界里的标量是否属于作者写的集合），两者不能互相替代。
- **`foreshadow` 事件的账本条目用目标 fact 自己的 `reveal_condition`**，不再由模型写。这是 [13 §9](./13_Narrative_Events.md#9-伏笔重新挂到线索链上) 那条修正的落点：埋的东西现在通向剧本线索链，"该不该到期"和"玩家能不能看见"是同一个对象。

切片 5 的**第二批也已完成**：三件地基，各带测试。共同点是三件都不是"加功能"，而是**给已有的读者补上写入者**——这一批结束时全套 750 个测试通过。

- **玩家移动**（[13 §12](./13_Narrative_Events.md#12-玩家动作)）。`ActionType.MOVE` 存在、能通过校验、报告成功、而**谁也没动**：`_apply_move` 写的是 `npcs[actor_id].location`，玩家不在 `npcs` 里；`_move_must_be_adjacent` 也拿 NPC 的位置判邻接。两处现在都按 actor 的种类取位置。连带修掉一个内容 bug：`killer_identity` 的第二条通道（`stage >= 3`，"独立调查"）需要玩家能走到酒馆和森林，所以作者写了两条路、其中一条是碎石。
- **放宽 actor 检查必须同时收紧动作。** 让玩家 id 通过 `_actor_must_exist`，同时就暴露了其余所有动作类型给它——`reveal_fact` 会让玩家解锁自己的线索，`adjust_relationship` 会让他设定 NPC 对自己的观感，两者都是 [04 §3.3](./04_World_State_Manager.md#33-叙事推进不等于披露授权) 禁止的披露旁路。它们此前**只是被意外挡住的**（玩家 id 恰好通不过 actor 检查），所以新增 `players_may_only_move`。
- **时间制**（[13 §4](./13_Narrative_Events.md#4-时间制)）。`TimeSlot` 挂在 `story_beats` 下（该前缀已在 `Condition` 白名单里，所以剧本能把「只有晚上」写成触发条件），`time_day` 留在 `WorldState` 原处，由 Manager 一处同时写两半。晚上保持为可消耗的时段，理由见 §4.1。收束段（`wrap_up.py`）签名只收 `VisibleState`，因此在类型上就读不到 hidden fact；塔罗读的是聚合量而非内容。
- **fear 会涨了**（[14 §4.3](./14_Case_Design.md#43-被洛伦先动手-玛尔塔彻底闭口)）。两半：prompt 现在写全三个维度并明说 trust 与 fear 不是一根轴的两头；另有一条失败 [追问]/[试探] 的确定性下限。**只改 prompt 不够**——模型没有理由知道"闭口"这个结局存在，靠模型自愿选对维度的通道和死通道差不多。作者写了 fear 的结果优先于下限，否则 [15 §4](./15_Event_Script.md) 的每个数字都会悄悄膨胀。
- **结局可达性由加载器校验**（[13 §11](./13_Narrative_Events.md#11-每个结局必须可达)）。剧本用 `narrative.endings` 声明结局，`reachability.py` 在启动时校验。关键在于**原有的路径解析检查抓不到那三个 bug——那三条路径全都能正常解析**，缺的是"有没有人写这个路径"。关系值按 `(npc, dimension)` 成对追踪，因为 fear 那个 bug 是维度形状的：`relationships.npc_a.*` 几乎每回合都在动，唯独 `fear` 不动。检查器第一次跑就找到了第四条死通道：`tension >= 0.8` 在 M6（`escalate`）写出来之前无人可抬，因此该结局标 `pending: true`——把条件留在剧本里和事件放一起，而不是删掉；标记留着不清本身是加载错误。

**下一步从这里继续**：Web 跟上时段制与移动（[12 §13](./12_Web_Interface.md#13-待加由后续功能带出来的界面) 的 13.1/13.2/13.3，13.2 是最大的一块——现有页面假设"一个场景、一个 NPC、一直对着他说话"），切片 4 剩下的 Player Model 那一半，然后是 M3-M7 与支线，以及 `world.yaml` 的 `facts` 段重构。

两块**已知未做**，都不是疏漏：

- **`facts` 段仍是旧版**（洛伦是凶手、`killer_identity: npc_b`），与 [14 §1](./14_Case_Design.md#1-真相不是谋杀-已定) 定的"真相不是谋杀"冲突。前两批只动了 `narrative:`/`events:` 与新增的几条 fact，重构留到写 M4/M5/M7 时一起做。
- **可达性是窄的那个断言**："有东西写这条路径"，不是"12 个时段内可达"。后者需要搜索整个事件图与时段预算，错起来没人能调；[15 §6.2](./15_Event_Script.md) 的验算改由测试走一遍（4 天 × 3 时段 = 12）。

切片 4 剩下的 Player Model 那一半仍然待做：Behavior Tracker 写入 `PlayerProfile`，`weight_for()` 是唯一接口，`select_candidate(profile=...)` 已经在消费它。它不依赖 Web；面板届时加一块「玩家画像」即可。事件层给了它一个新的信号源——四个标签本身就是玩家风格的分类（[15 §2](./15_Event_Script.md#2-四个标签) 把每个标签映到一个 `PreferenceTag`）。

一轮真实对局（11 回合）暴露的四件事，都已修掉，但根因值得记住：

**`quests.<progress>.stage` 有作者、有读者，没有写入者。** [10 §3.2](./10_Narrative_Operators.md) 把它列为张力三个来源之一，[04 §3.3](./04_World_State_Manager.md) 明确允许 Engine 推进它，剧本按 `stage >= 2` 写伏笔回收条件，`_escalate_candidates` 按 `stage >= 1` 开闸——四方都假设有人在推，而没有任何代码路径推它。结果：整局 stage 恒为 0，三条伏笔永远不到期，`escalate` 一次没触发，张力始终 0.00。三个看起来独立的症状（伏笔为埋而埋、进展慢、张力不动）是同一个断口。现在由 `rules.earned_stage()`（已**说出口**的 `paced_clues` 条数，纯函数）+ Engine 每 tick 提一次 `advance_quest` 补上，一 tick 只走一步（理由同 `MAX_CHAPTER_STEP`）。用「已说出口」而不是「可解锁」：后者一个大方的回合就能满足，而前者是真的发生过的 beat，且单调，不会倒退。

**这个断口本来该被 loader 抓住。** 剧本加载器校验 `path` 能否解析，不校验通道有没有写入者——和 `_TENSION_CEILING_BY_STAGE` 当年那个「阈值不可达」是同一类静默内容 bug。现在 `narrative.progress_quest` 显式命名这个 quest，loader 交叉引用它是否存在；没有这一项就是「没有 stage 通道」，而不是猜一个。这同时修掉 `rules.py` 里硬编码的 `world.quests["investigation"]`——本文件说过 `rules.py` 不含具体 fact id，那行是漏网的反例，任何别的剧本拿到的都是一条永不触发的死通道。

**对话第二次调用看不见玩家原话，于是人称漂移。** 实测台词：`他既然照做了，还特意来宽我的心……那我就告诉你一点`——同一句话、同一个听话人，先「他」后「你」。`_dialogue_messages` 只拿到 `plan.reasoning`，而 reasoning 是内心活动，必然用第三人称称呼玩家（"他在打探那晚的事"）。prompt 给了一个第三人称指代、一个第二人称指代都没给，模型用了它拿到的那个。修法是把 observation 传进去恢复「在对谁说话」，而不是加一条「禁止用他」——外加 `npc_dialogue.txt` 一段说明 reasoning 的人称不是称呼的人称。

**伏笔生成器看不见账本，所以每次都另起一个东西。** 玩家在玛尔塔家里，模型却依次埋了磨坊水轮上的红布、水渠边的拖拽痕迹、窗台的白蜡——三个地点三件事，彼此不接。生成 prompt 只给了未回收条数（"1/3 loops open"），没给内容，「再给同一个结论补一条线索」根本不是它能瞄准的目标；而多条线索指向一个结论正是 `MAX_OPEN_FORESHADOWINGS = 3` 背后 Three Clue Rule 要的东西。现在把未回收条目的 `note` 一并给出，并要求新的一条是同一件事的另一个侧面。

**第五件，也是最贵的一件：算子这个粒度选错了。** 上面四条是断口，这条是分层。玩下来最明显的感受是"伏笔为埋而埋"——修完伏笔的生成上下文之后仍然如此，因为根因不在 prompt。`_foreshadow_candidates` 的触发条件只有"账本没满且上一轮没埋"，**没有任何一条问"这个故事现在需要一条伏笔吗"**；而它的 `intensity=0.3` 是五个候选里最低的，所以只在其他候选全空的回合被选中——它填的正是本该 `relieve` 的回合，而 [05](./05_Narrative_Engine.md) 开篇就写着"坚持每回合都得有事发生"是生成叙事最常见的出错方式。

再往上一层看：算子是**修辞动作**（怎么讲），没有前件和后件，所以拼不出**情节**（发生什么）。于是"这回合该发生什么"这个问题没有依据可答，只能靠账本空位来答。而 [05 §2.1](./05_Narrative_Engine.md#21-规则触发确定性) 的示例代码写的本来是 `event_type="betrayal"` 这样的*事件*——设计写的是事件导演，实现换成了算子，事件那一层没有落地。修法是加事件层、算子降级为"事件的表达方式"，见 [13](./13_Narrative_Events.md)。

值得记住的是这件事**改 prompt 改不掉**：伏笔内容漂移（窗上的绳子、一枚纽扣、半枚白蜡）是因为生成 prompt 只给可见 facts，模型被要求"埋一个以后会变成线索的细节"却看不见任何一条真线索——这个任务在信息上无解，不是模型没做好。

**存档/读档已就绪，回合级回溯没有。** 每回合 tick 结束（即回合边界，`advance_turn` 是最后一个写入者）写 `saves/<session_id>/` 三个文件：world、该 NPC 的私有记忆（它按设计不在 `WorldState` 里，只存世界会读到一个把玩家忘干净的 NPC）、以及玩家读到的对话。`POST /api/session {resume_from}` 在**装配前**读档，因为 Harness / Engine / tools 都在构造时捕获 manager，事后替换会让它们写进一个没人读的世界。`GET /api/saves` 列存档。剧本与 NPC 必须对得上：把村庄的世界读进另一个剧本，会得到一个校验通过、但什么都铺垫不了的世界。**做不到**精确回到第 7 回合或从那里分支——那要每回合一份快照加记忆回滚，是独立一块工作量。

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
