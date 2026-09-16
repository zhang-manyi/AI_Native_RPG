# 12. Web Interface（场景页面 + 开发者面板）

## 1. 职责

替代 `scripts/chat_demo.py` 成为主交互入口，同时交付切片 4 的调试面板那一半（[09 §4](./09_Reference_Scenario.md#4-建议的实现顺序对应依赖关系)）。三件事，缺一件这层就不成立：

1. **场景页面**：玩家视角。简单的小人立绘 + NPC 台词出现在场景里 + 手打自由对话。数据只走 `PlayerView()`。
2. **开发者面板**：[07 §2.3](./07_Observability.md#23-narrative-state-panel叙事状态面板) 的 Narrative State Panel，可隐藏，只有开发者模式可见。数据直读 `WorldState` + Trace。
3. **叙事 tick 的异步化**：对话先返回，叙事结果稍后推送。这是本层存在的技术理由，见 §2。

明确不做（避免这层长成一个平台）：多人会话、玩家移动、选项菜单、生产部署、鉴权、Eval Dashboard（[08](./08_Evaluation.md) 的事）。`player_id` 仍然只是关系值二元组的一半，不是多人伏笔（[04 §3.2](./04_World_State_Manager.md)）。

## 2. 为什么异步是这一步的核心问题

[09 §4](./09_Reference_Scenario.md#4-建议的实现顺序对应依赖关系) 末尾的实测：

| 链路 | 时间 | 在玩家等待路径上？ |
|---|---|---|
| 对话，无 Action | ~3-4s | 是（[02 §5](./02_Sequence_Diagram.md) 预算内） |
| 对话，有 Action | ~7.4s | 是（2 次调用） |
| 叙事 tick（压短后） | **~8.6s** | **架构上不是，实现上是** |

架构早已把 tick 挪出关键路径：生成内容存 `pending_event`，下一回合才由 `take_pending_event()` 取走（[02 §4](./02_Sequence_Diagram.md)）。但 `chat_demo.py` 在打印完台词后同步调用 `engine.tick()`，于是那 8.6 秒仍然全压在玩家身上——**这是实现问题，不是架构问题**，终端版故意留着不修，就是为了在 Web 里一次做对。

由此推出本文档三个不可让步的结论：台词与 tick 必须走两次推送（§3）；两者对世界状态的读写必须串行（§5）；串行的代价必须显示给用户而不是藏成卡顿（§5.3）。

## 3. 状态怎么传：SSE

### 3.1 选型

| 方案 | 结论 | 理由 |
|---|---|---|
| 轮询 | 否 | tick 延迟 8.6s 且方差大。轮询间隔要么加上一整个间隔的滞后，要么空转。更糟的是它把「发生了什么」退化成「状态变了，自己 diff」——而面板最有信息量的恰恰是事件本身（哪个算子触发、哪条候选被挡下），diff 看不出来 |
| **SSE** | **选用** | 流量是单向不对称的：玩家几秒一句，服务端要推台词/tick/面板三类更新。SSE 是普通 HTTP，同一个 ASGI 应用里就是一个 `StreamingResponse`，不需要协议升级、心跳、鉴权另设一套；浏览器 `EventSource` 自带重连和 `Last-Event-ID` 补发。而且能 `curl -N` 直接看——面板本身是调试工具，它的传输层也该可调试 |
| WebSocket | 暂不 | 双向全双工在这里买不到东西：玩家输入本来就是一次 POST。等真要做「生成中打断」这类高频上行时再换，届时事件契约不用改 |

不引入 `sse-starlette`：SSE 的封帧（`id:` / `event:` / `data:` / 空行 + 定期注释行保活）三十行以内，少一个依赖比省这三十行值。

### 3.2 一条流承载所有要渲染的东西

`POST /api/session/{id}/turn` **不返回台词**，返回 `202 + turn_id`；台词、tick、面板刷新全部作为事件从流里来。

理由：这样前端只有一个渲染入口、一份有序事件日志，「台词现在到」「tick 8 秒后到」「面板各刷一次」变成同一种东西的三个事件，而不是一个 HTTP 响应加两个推送。副作用是回合内失败也必须是事件而不是 HTTP 错误——这不是妥协，tick 根本没有对应的请求可以失败。POST 只对**准入**类错误同步返回 4xx（会话不存在、输入为空、该会话已有回合在飞）。

### 3.3 事件契约

每个事件 `data:` 是一个 JSON 对象，字段 `type` 决定形状。`turn_id` 贯穿一个回合的全部事件，前端据此把 tick 归到对应的台词之后。

| event | 何时发 | 载荷（要点） |
|---|---|---|
| `hello` | 连上时 | `session_id`、`dev_mode`、`scene`（首帧场景，见 §4.2）、`turn`（当前回合数） |
| `turn_accepted` | POST 落地后立刻 | `turn_id`、玩家原话（回显，前端不必自己乐观插入） |
| `dialogue` | 台词就绪（~3-7s） | `turn_id`、`npc_id`、`name`、`dialogue`、`trust_before/after` |
| `narrative_tick` | tick 完成（再 ~8.6s） | `turn_id`、算子/被挡候选/被拒 proposal/生成 hook/延迟/model；**仅 dev 模式发送** |
| `panel` | 每次世界状态变化后 | 面板全量快照（§4.3）；**仅 dev 模式发送** |
| `scene` | 玩家可见状态变化后 | 场景全量快照（§4.2） |
| `turn_failed` | 该阶段抛异常 | `turn_id`、`stage`（`dialogue` \| `tick`）、异常类型与消息 |
| `ping` | 15s 无事件 | 保活注释行，不进事件日志 |

三个决定：

**面板与场景都发全量快照，不发增量。** 面板要显示的是「故事现在是什么形状」，本来就是快照语义；发 diff 会让前端持有一份需要自己维护一致性的镜像状态，而这份状态出错的症状（面板显示的不是真实世界）恰好是面板存在的意义被抵消。快照的量级是几十个 fact + 一个账本，不值得优化。

**`turn_failed` 分 stage。** 台词失败是这一回合废了，tick 失败是这一回合的**下一回合**少一句铺垫——前者要提示玩家重说，后者只是面板上的一条记录。`chat_demo.py` 用两个 `except` 分别 `continue` 表达了同一件事，事件流必须保留这个区分。

**dev 专属事件在服务端按连接过滤，不靠前端不渲染。** 见 §7。

## 4. API 面

两组路由，前缀分开，**不共用 handler、不共用序列化器**——[07 §2.4](./07_Observability.md#24-面板绕开-playerview玩家接口只走-playerview) 的硬性要求。

### 4.1 玩家侧

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/session` | 建会话。body: `{scenario, npc_id?, resume_from?}`（都可省，默认第一个包 / 包里第一个 NPC / 从头开始）。返回 `session_id`、`resumed_from`、`turn` |
| `GET` | `/api/session/{sid}/events` | SSE 流。`?last_event_id=` 或标准 `Last-Event-ID` 头补发 |
| `POST` | `/api/session/{sid}/turn` | body: `{text}`。返回 `202 {turn_id}`。台词走事件流 |
| `POST` | `/api/session/{sid}/move` | body: `{destination}`。返回 `202 {turn_id}`。落地与否走 `move` 事件 |
| `POST` | `/api/session/{sid}/conclude` | 打开剧本的 `conclusion_event`（[13 §12](./13_Narrative_Events.md#12-玩家动作)）。无 body。返回 `202 {turn_id}`；拒绝（已有 `active_event`、剧本未声明）走 `move` 事件，不是 4xx |
| `GET` | `/api/session/{sid}/scene` | 场景快照，等价于 `scene` 事件的载荷。给刷新页面和无 JS 排查用 |
| `GET` | `/api/scenarios` | 可选剧本列表（`list_scenarios()`），启动页用 |
| `GET` | `/api/saves` | 可续玩的存档，新的在前。只列剧本、NPC、进度，没有受控内容 |

### 4.1.1 存档与续玩

每回合的 tick 结束时写一次 `saves/<session_id>/`，共三个文件：`world.json`、`memory_<npc>.json`、`session.json`（对话记录 + 元信息）。选在这里是因为**tick 是回合边界**——`advance_turn` 是一个回合的最后一个写入者，此时世界一定是一致的；而且它跑在会话线程上，和其他所有碰世界的调用一样。存档失败只发一条 `turn_failed` 事件，不让玩家丢掉刚打完的这个回合。

记忆必须单独存：它按设计不属于 `WorldState`（[06 §Memory](./06_NPC_Agent_Spec.md)），只存世界会读出一个把玩家彻底忘掉的 NPC。向量一起存，因为那是贵的部分；宽度不匹配时 `MemoryStore.load` 走 `rebind_embedder` 重编码，不会等到检索时才在 `cosine_similarity` 里报长度错。

`resume_from` 在**装配之前**读档（`load_save()` → `Session(resume=...)`）：Harness、Engine 和每个 tool 都在构造时捕获 manager 并一直持有，事后替换 `session.manager` 只会让它们继续写一个没人读的世界。所以续玩是**用存档的世界代替**剧本的初始世界，而不是叠加在它上面。剧本包里的东西（persona、prompt、tools、`narrative:` 块）一律每次重新加载，不存——否则改了剧本再续玩会静默沿用旧的，对调试工具来说恰好是最坏的行为。

不做的部分：**精确回到第 N 回合、以及从那里分支**。那需要每回合一份世界快照加记忆回滚，还要处理面板时间线的截断，是独立一块工作量。续玩恢复世界、记忆和对话记录；面板的三条按回合累积的序列（算子时间线、被拒 proposal、信任曲线）故意从空开始——它们是逐回合的观测记录，而一个刚续上的会话还没有任何回合。

响应模型的**类型签名只接受 `VisibleState`**，不接受 `WorldState`。`SceneView` 由 `VisibleState` + 剧本包的公开展示数据（`Location.name/description`、`NPCWorldState.display_name`、`intro` 块）组装，构造函数里拿不到 `WorldState`。

### 4.2 `SceneView`（玩家侧响应模型）

```
SceneView
├── turn: int                      回合数（story_beats.turn，非隐藏信息）
├── time_day: int
├── location: {id, name, description}
├── npcs: [{id, name, sprite_key}]         仅同地点的 NPC（PlayerView 已过滤）
├── visible_facts: [{id, value}]           来自 VisibleState.visible_facts
├── quest_stages: {quest_id: stage}
├── intro: {title, premise, facts_header, goal}   仅首帧
└── transcript_tail: [{speaker, text}]     最近若干条对话，刷新后不空屏
```

`sprite_key` 是**前端资产名，不是剧本内容**：由 `npc_id` 直接映射到内置的一组小人（`sprite_a`/`sprite_b`/…），剧本包不需要提供立绘，也不许硬编码任何剧本名字（§6.3）。剧本包想自带立绘是后续扩展，届时加一个可选的 `sprite:` 字段即可，不影响现在。

`intro` 直接复用 `load_intro()`，缺字段落回中性默认——`chat_demo.render_intro()` 的逻辑照搬，包括「不写 detective 措辞」这条（那是 `render_intro` 的注释里已经定下的约束）。

### 4.3 开发者侧

前缀 `/debug/*`，**非本地环境默认关闭**（[07 §2.4](./07_Observability.md#24-面板绕开-playerview玩家接口只走-playerview)）。

| 方法 | 路径 | 对应 07 §2.3 |
|---|---|---|
| `GET` | `/debug/session/{sid}/panel` | 全部面板，一次拿完（`panel` 事件同一载荷） |
| `GET` | `/debug/session/{sid}/memory?npc_id=` | Memory Explorer（§2.2），对应 `/mem` |
| `GET` | `/debug/session/{sid}/world` | 全量 `WorldState`（World Viewer，§2.2） |
| `GET` | `/debug/trace/{trace_id}` | 单条 `AgentTrace`（Trace Viewer，§2.1） |
| `GET` | `/debug/tick/{tick_id}` | 单条 `NarrativeTick` |
| `GET` | `/debug/session/{sid}/unlock/{fact_id}` | 「为什么玩家还看不到 X」：逐 clause 的求值中间结果 |

`PanelView` 的分块与来源——**每一块都复用现有数据来源，不写第二套逻辑**：

| 面板块 | 07 §2.3 | 数据来源 | 现有实现 |
|---|---|---|---|
| 伏笔账本 | 块 1 | `story_beats.open_foreshadowings` + `turns_owed()` / `is_overdue()` | `print_ledger` |
| 解锁进度板 | 块 2 | 全量 `facts` 里 `visibility != revealed && reveal_condition != None`，逐 clause 走 `resolve_path` + `clause_holds_for` | `print_unlock_board` |
| 被拒 proposal | 块 3 | `NarrativeTick.proposals`（`approved=False`）+ `AgentTrace` 的 `action_validation` step | `print_turn` / `print_narrative_tick` |
| 算子时间线 | 块 4 | `story_beats.recent_operators` + 每回合 tick 记录 | `print_beats` |
| 关系值走势 | 块 4 | 每回合 `get_relationship(npc, player)` 采样 | `/trust` |
| 叙事进度 | — | `story_beats.chapter` / `.tension` / `.turn` / `.spent_one_shots` | `print_beats` |
| 记忆检索 | §2.2 | `AgentTrace` 的 `memory_retrieval` step（含 id/importance/text）| `print_turn` |
| 本回合开销 | §2.1 | Trace 的 `llm_calls` / tokens / `total_latency_ms` | `print_turn` |

用户列举之外、实际内容里存在且值得上面板的两项：**`spent_one_shots`**（一次性算子用掉了没有，`reverse` 只能触发一次，看不见就没法判断反转是否已经发生）和**`starved`/被挡候选**（[07 §2.3](./07_Observability.md#23-narrative-state-panel叙事状态面板) 说这是 Engine 最不可见也最有信息量的输出——「有候选但节奏压住了」与「什么都没触发」在界面上必须能区分）。

### 4.4 三个 print 函数怎么复用

**不能直接调用**：它们 `print` 到 stdout 且返回 `None`。但重写一份判定逻辑正是要避免的（伏笔是否超期、clause 是否满足、哪些 fact 算「待解锁」——这三处判定错了面板就在骗人）。

做法：把每个函数的**判定部分**下沉成纯函数，放 `src/ai_native_rpg/observability/panels.py`，返回 Pydantic 模型；`chat_demo.py` 的三个 print 改成消费同一个模型再格式化成文本。终端版和 Web 版从此共享一份判定，各自只拥有渲染。这样也保住了 `chat_demo.py` ——它是无 JS 环境下最快的排查入口，不该因为有了 Web 版就腐烂。

```
observability/panels.py     纯函数：WorldState (+Trace/Tick) -> PanelView
        ├── chat_demo.py    渲染成终端文本
        └── web/            直接 model_dump_json 进 SSE
```

`panels.py` 放 `observability/` 而不是 `web/`：它消费前两层的产出、不参与主流程，正是 [01 §1](./01_System_Architecture.md) 给 Observability 的定义；放 `web/` 会让终端版反向依赖 Web 层。

## 5. 并发：每个会话一个串行执行器

### 5.1 约束

`WorldStateManager` 不是线程安全的：`_state` 是普通的可变 Pydantic 模型，`submit()` 读-校验-写三步之间没有任何互斥，`_applied_proposals` 是普通 `set`。而一个回合里有两个写入方：

- 对话回合：`harness.respond()` 可能 `submit(adjust_relationship)` / `reveal_fact`
- 叙事 tick：**每轮至少一次** `advance_turn`，可能加 `advance_story_beat` / `plant_foreshadowing`

`snapshot()` 返回深拷贝，所以并发的**读**不会返回半个对象；但深拷贝本身在另一个线程写的中途执行，可以拷到一个逻辑上不一致的世界（账本已加条目、`planted_total` 还没加）。面板读到这种状态就是显示错误的数据。

### 5.2 方案

**每个会话一个单线程执行器，所有触碰 manager / harness / engine / memory 的工作都在它上面排队。** 世界状态因此只有一个写入线程，与 `WorldStateManager` 现有的假设一致——**不改 manager**。

```
HTTP 线程（POST /turn）
    │ 校验准入，生成 turn_id，把两个作业推入队列
    │ 立刻 202 返回
    ↓
会话执行器（单线程，串行）        事件队列 → SSE
    ├── job: dialogue(turn_id)   ──→ dialogue 事件 + scene + panel
    └── job: tick(turn_id)       ──→ narrative_tick 事件 + panel
```

一个回合入队两个作业而不是一个：这样 `dialogue` 事件在台词就绪的那一刻就能发出，不必等 tick。顺序由队列保证，正好等于 `chat_demo.py` 的顺序（`take_pending_event` → `respond` → `tick`），也正好是架构要求的顺序。

为什么是「每会话一个线程」而不是全局锁或 `asyncio.Lock`：

- 现有的 `Harness.respond()` / `NarrativeEngine.tick()` / `OpenAICompatibleClient`（`httpx.Client`）全是**同步阻塞**代码。在 async handler 里直接调用会堵死整个事件循环。异步化它们意味着复制一遍 client、harness、engine 三处的调用路径，为一个单人调试界面付出双 API 表面的代价——不值得。
- 一把全局锁能保证正确性，但会让两个会话互相等 8 秒。会话之间没有共享状态（各自的 manager / memory / engine），按会话分区是自然边界。
- 执行器还顺带给出了「这个会话现在忙」的唯一权威判断，`turn` 的准入校验（§5.3）直接读它。

落地形态：`ThreadPoolExecutor(max_workers=1)` per session，或一个 `queue.Queue` + 一个 worker 线程。事件从 worker 线程投进 `asyncio.Queue`（用 `loop.call_soon_threadsafe`），SSE handler 只负责从队列取出封帧——事件循环里不出现任何阻塞调用。

`MemoryStore` 同理不是线程安全的（`_episodic` 是普通 list，Reflection 每回合追加一条），执行器一并覆盖它。

### 5.3 玩家在 tick 期间说了下一句怎么办

这是串行化唯一会被玩家感知到的地方，必须明确定下来，而不是留给实现。

三个候选：**排队**（tick 跑完再处理下一句，玩家等 8 秒）/ **抢占**（tick 让位，台词优先）/ **拒绝**（该会话已有回合在飞，POST 返回 409）。

**选排队 + 前端显式禁用输入框**，理由：

- 抢占要么把 tick 结果丢掉（那这一回合的铺垫没了，且 `advance_turn` 没提交，节奏规则会误判相邻性——[10 §7.1](./10_Narrative_Operators.md#71-实现时的四条解读) 里 `relieve` 必须被记录正是这个坑），要么要中断一个已经飞出去的 HTTP 请求，两条都在为一个调试界面引入真正的复杂度。
- 拒绝（409）把状态管理甩给前端，而前端还是得禁用输入框，等于两处都做。
- 排队的代价是玩家可能等 8 秒——**但这个等待是可见的**：`narrative_tick` 未到期间前端显示「叙事引擎正在推进…」并禁用输入。可见的等待和卡顿是两件事，而这个界面本来就是给开发者看系统在干什么的，把 8.6 秒明确画出来比隐藏它更符合这层的目的。

顺带：这也让延迟数字变成界面上的一等公民（每个事件带 `latency_ms`），是调 prompt 时最需要的反馈——8.6s 这个数就是这么测出来的。

### 5.4 会话生命周期

会话在内存里（`dict[session_id, Session]`），跟 `WorldStateManager` 一样不做持久化（[04 §5](./04_World_State_Manager.md)：内存模型 + JSON 快照，迁移点在 `save`/`load` 后面）。进程重启会话就没了，符合「本地调试工具」的定位。Trace 和 tick 仍然照现在落盘到 `traces/`，那部分本来就是持久的。

一个上限（`MAX_SESSIONS`，比如 8）+ 空闲超时回收，防止刷新页面刷出几十个 Qwen3 embedder 实例把内存吃掉。**embedder 全局共享一个实例**（它是只读的、加载一次几百 MB），memory / manager / engine 每会话独立。

## 6. 与现有模块对接

### 6.1 挂点（全部已就绪，不改现有签名）

```python
# 建会话：与 chat_demo.main() 的装配完全一致
world = load_scenario(name)
personas = load_personas(name)
seeds = load_seed_memories(name)
intro = load_intro(name)
directives = load_narrative_directives(name)
prompts = PromptLibrary(overlay=pack_prompts_dir(name) or None)
manager = WorldStateManager(world)
memory = MemoryStore(npc_id, embedder=shared_embedder)
seeds[npc_id].load_into(memory)
tools = build_npc_tools(npc_id=..., manager=manager, memory=memory, player_id=...)
llm = build_llm_client(Settings.from_env(), responses=...)
harness = Harness(
    npc_state=..., manager=manager, llm=llm, memory=memory, prompts=prompts, tools=tools
)
engine = NarrativeEngine(manager=manager, llm=llm, prompts=prompts, directives=directives)

# 一个回合，在会话执行器上串行执行
pending = engine.take_pending_event()  # 上一轮生成的铺垫
response, trace = harness.respond(text, player_id=pid, narrative_event=pending)
emit("dialogue", ...)
traces.save(trace)  # 台词先返回 ←—— 这是本层的目的
tick = engine.tick(player_id=pid)  # 再跑 tick
emit("narrative_tick", ...)
narrative_traces.save(tick)
```

这段与 `chat_demo.py` 的主循环逐行同构。**Web 层不新增任何领域逻辑**：它是一个装配器 + 一个执行器 + 一个事件流。这也是验收标准——如果 `web/` 里出现了判断伏笔是否超期、或者决定该不该触发算子的代码，那是放错了地方。

`session_id` 传给 `harness.respond(session_id=...)`，让 Trace 的 `session_id` 与 Web 会话对齐（现在 `chat_demo` 不传，每回合随机一个，跨回合的 trace 串不起来）。这是本层顺带修掉的一个可观测性缺口。

### 6.2 `pending_event` 的时序在 Web 下不变

`take_pending_event()` 在 `respond()` **之前**调用，取的是上一回合 tick 生成的内容。Web 版没有改变这个契约，只是让 tick 的 8.6 秒发生在玩家读台词和打字的时间里，而不是发生在他等待的时间里。这正是 `pending_event` 机制当初为什么存在。

两个必须保住的性质（[09 §4](./09_Reference_Scenario.md#4-建议的实现顺序对应依赖关系) 记录的坑）：hook 只被消费一次；只有真正 landed 的 beat 才留下 `pending_event`。两者都在 `engine.py` 内部，Web 层不碰——**只要不绕过 `take_pending_event()` 自己去读 `pending_event` 属性**（那是个只读 property，用它来预览面板是安全的，但绝不能用来喂 `respond`）。

### 6.3 剧本无关

界面不含任何剧本内容。检查清单：

- NPC 显示名走 `NPCWorldState.display_name`（`name` 字段，[09 §4](./09_Reference_Scenario.md#4-建议的实现顺序对应依赖关系) 已定为公开事实），不用 `_display_name()` 从 persona background 猜——那是 `name` 字段存在之前的兜底。
- 地点名/描述走 `Location.name/description`；开场文案走 `load_intro()` + 中性默认。
- 剧本列表走 `list_scenarios()`，启动页从它渲染。
- `sprite_key` 由 npc_id 映射到内置资产（§4.2），不读剧本。
- 面板标签用结构名（「伏笔账本」「张力」），不用剧本里的专有名词。
- 一条测试：换一个只有 1 个 NPC、无 `narrative:` 块、无 `intro:` 块的最小剧本包，页面与面板都能渲染（面板显示空账本、空解锁板，不是报错）。

### 6.4 provider 与 mock

`Settings.from_env()` 已支持 `LLM_PROVIDER=deepseek|openai`，Web 层只把结果显示在页头（provider + model + embedder + prompt 来源 + 工具列表，即 `print_header` 的内容）：换 provider 是配置项。`?mock=1` 或 `USE_MOCK_LLM=1` 走 `MockLLMClient`，`_MOCK_SCRIPT` 从 `chat_demo.py` 移到共享位置——那份脚本里「每条 entry 同时满足 NPC 与叙事两个 schema」的注释解释了为什么不能按调用顺序交错，Web 版同样需要（同一个 client 服务两个消费者这件事没变）。

## 7. 玩家侧与调试侧的隔离（硬性要求）

[07 §2.4](./07_Observability.md#24-面板绕开-playerview玩家接口只走-playerview) 的三条强制手段在本层的落地，逐条对应：

1. **调试路由独立前缀 `/debug/*`，非本地默认关闭。** 由 `DEV_MODE` 决定（默认按 bind 地址是否 loopback 推断，可用环境变量显式开启）。关闭时整个 `/debug` 路由不注册——不是返回 403，而是不存在：一个 403 说明端点在那里，只是这次没让进。
2. **玩家侧响应模型只接受 `VisibleState`，用类型签名钉死。** `SceneView.from_view(view: VisibleState, ...)` 的构造路径拿不到 `WorldState`。玩家侧模块**不导入** `WorldState`——一条 import 检查的测试比人工 review 可靠。
3. **一条测试断言玩家侧响应里不出现任何 hidden fact 的 value。** 扩展 `test_player_view.py` 里 `test_hidden_fact_value_never_appears_anywhere_in_view` 的思路到 HTTP 层：把 `SceneView` 与 `dialogue` 事件序列化成 JSON 字符串，断言每个 hidden fact 的 `value` 的字符串形式不出现在里面（`loren_that_night` 的值是 `npc_b`，所以这条测试顺带覆盖了「id 当作值泄漏」）。

再加两条本层特有的：

4. **dev 事件在服务端按连接过滤。** `narrative_tick` / `panel` 事件在非 dev 连接上根本不生成，而不是发出去让前端不显示。前端隐藏面板只是 UI 状态；如果关掉面板的按钮是唯一的屏障，那么 F12 就是完整的剧透工具。
5. **一条测试断言非 dev 模式的事件流里只出现 `hello`/`turn_accepted`/`dialogue`/`scene`/`turn_failed`。** 新加一种 dev 事件时忘记过滤会被这条挡住。

## 8. 前端选型

**原生 HTML + CSS + 一个 ES module，无构建步骤。** 不用 React/Vue/Svelte，不用打包器。

理由：

- 交互面小到没有框架的用武之地：一个输入框、一段台词、两三个小人、一个面板。状态就是「最后一次收到的 scene / panel 快照 + 事件日志」——SSE 已经把状态同步做完了，前端拿到的是完整快照，`innerHTML` 一次重绘就够。这正好是 §3.3「发快照不发增量」的红利。
- **零构建**是关键取舍。加了 npm/vite 之后，这个 Python 项目的启动命令会从 `python scripts/web.py` 变成两个进程、两套依赖、两套 lint、一个 CI job。对一个本地开发者界面来说这是最贵的一处复杂度，而买到的东西（组件复用、响应式）在这个规模上没有兑现空间。
- [07 §5](./07_Observability.md#5-当前实现范围) 也是这个方向：「简单的本地网页（甚至 Streamlit），不追求生产级 UI」。这里不选 Streamlit——它对 SSE 与自定义布局的支持要靠 hack，而异步推送和「场景 + 侧栏」的布局恰好是本层的两个核心需求。

技术点：

- SSE 用 `EventSource`。它自动重连并带 `Last-Event-ID`，与 §4.1 的补发对应。
- 立绘用内联 SVG 小人（几十行，一个 `<g>` 一个角色），按 `sprite_key` 取。不引资产管线，换姿态/表情就是换一个 SVG class——够表达「在场 / 说话中 / 离场」。
- 台词出现在场景里：气泡定位在对应小人上方（`position: absolute` + 小人的坐标），而不是聊天记录列表。历史记录折叠在场景下方。
- 布局：CSS Grid，主区自适应 + 右侧固定宽面板；面板 `display: none` 即隐藏，非 dev 模式下服务端不渲染这个容器。
- 无障碍：输入框有 `<label>`；台词区 `aria-live="polite"`（异步到达的内容要被读屏播报，这正是本层的主要交互）；等待态用 `aria-busy`；面板折叠按钮用 `aria-expanded`；SVG 小人带 `<title>` 与 `role="img"`；不靠颜色单独表达「超期」（配 ⚠️ 文本）。

## 9. 后端选型与依赖

**FastAPI + uvicorn**，加进 `pyproject.toml` 的一个新 optional extra：

```toml
[project.optional-dependencies]
web = ["fastapi>=0.115,<1", "uvicorn>=0.30,<1"]
```

optional 而不是主依赖：核心库和测试套件不该因为多了一个界面就要装 web 框架，跟 `embedding` extra 同样的判断。FastAPI 而非裸 Starlette，因为 Pydantic 模型直接做响应模型和自动 schema 是这个项目已有的表达方式（全项目 schema as code），省掉手写序列化。

入口 `scripts/web.py`（与 `chat_demo.py` 并列，同样支持 `--scenario` / `--npc` / `--mock` / `--list`，加 `--host` / `--port` / `--no-dev`）。默认绑 `127.0.0.1`。

**这个服务没有鉴权，也不该有。** 它绑定 loopback、是单人本地调试工具、`/debug` 端点按设计暴露完整世界状态。文档里明说：**不要把它暴露到 0.0.0.0 或反代出去**——那等于把剧本答案和 `WorldState` 全量公开。如果哪天真要给别人看，`DEV_MODE` 必须关闭，并且鉴权是那时候要新增的一层，不是现在偷懒省掉的一层。

## 10. 目录结构

```
src/ai_native_rpg/
├── observability/
│   └── panels.py          纯函数：WorldState/Trace/Tick -> PanelView（§4.4）
└── web/
    ├── app.py             ASGI 应用装配；DEV_MODE 决定是否挂 /debug
    ├── session.py         Session（装配 harness/engine/memory）+ 单线程执行器（§5）
    ├── events.py          事件模型 + SSE 封帧（§3.3）
    ├── routes_player.py    /api/*   —— 只 import VisibleState，不 import WorldState
    ├── routes_debug.py     /debug/* —— 直读 WorldState + Trace
    └── static/
        ├── index.html
        ├── app.js
        ├── styles.css
        └── sprites.svg

scripts/web.py             入口：uvicorn.run(app)
tests/
├── test_panels.py         判定逻辑（纯函数，严格先写测试）
├── test_web_isolation.py  §7 的 5 条断言
└── test_web_turn.py       事件顺序与 turn_failed 分 stage（mock LLM）
```

## 11. 测试策略

按 [09 §5](./09_Reference_Scenario.md#5-测试策略) 的分类，本层大部分落在「确定性」一侧，**严格先写测试**：

| 对象 | 方式 |
|---|---|
| `panels.py` 的判定（超期、clause 求值、待解锁筛选） | 纯函数，先写测试。这是三个 print 函数逻辑的唯一副本，也是面板会不会骗人的全部 |
| 隔离（§7 五条） | 先写测试。这是硬性要求，不是注意事项 |
| 事件流顺序与形状 | `TestClient` + mock LLM：一个回合产出 `turn_accepted` → `dialogue` → `narrative_tick`，且 `dialogue` 在 `narrative_tick` 之前 |
| 执行器串行性 | 两个回合并发 POST，断言世界状态一致且 `story_beats.turn` 恰好 +2（不是 +1 或 +3）。这条直接测 §5 要防的竞态 |
| `turn_failed` 分 stage | 让 mock 在 tick 阶段抛异常，断言 `dialogue` 已发出且 `turn_failed.stage == "tick"` |
| 剧本无关 | §6.3 最后一条：最小剧本包能渲染 |
| 前端 | 不做自动化测试。手动跑一轮，符合 [07 §5](./07_Observability.md#5-当前实现范围)「不追求生产级 UI」 |

Web 层的测试一律走 mock LLM（`conftest.py` 的 autouse fixture 已经强制 `USE_MOCK_LLM=1`），零网络请求。

## 12. 实现顺序

每一步结束时都有能跑的东西：

1. **`panels.py` + 测试**，`chat_demo.py` 的三个 print 改为消费它。此时终端版行为不变，但判定逻辑已经可复用、可单测。
2. **Session + 执行器 + 事件流**，先只发 `dialogue`。此时用 `curl -N` 就能看到「台词先到、tick 后到」——本层的核心目标在这一步就已验证，且不需要任何前端。
3. **玩家侧页面**：场景、小人、气泡、输入框。可玩。
4. **开发者面板**：按 [07 §2.3](./07_Observability.md#23-narrative-state-panel叙事状态面板) 的实用性排序——伏笔账本、解锁进度板、被拒 proposal 先做，算子时间线与关系值走势最后（[07 §5](./07_Observability.md#5-当前实现范围) 已经这么排了，因为调阈值直接依赖前两块）。
5. **隔离测试补齐**，`/debug` 的开关按 §7 收紧。

第 4 步之后切片 4 的调试面板那一半完成；Player Model 那一半（Behavior Tracker 写 `PlayerProfile`，`weight_for()` 是唯一接口）不依赖本层，随后再做——届时面板加一块「玩家画像」即可，`select_candidate(profile=...)` 已经在消费它。

§1-12 已实现并提交。以下是**后续要加的**，全部由别的层带出来，不是本层现在的缺口。

## 13. 待加：由后续功能带出来的界面

每条都注明「被什么带出来」，因为**先做那件事再做界面**才有意义——反过来做等于给不存在的状态画界面。

> **前置状态（切片 5 第二批已落地）**：时段制、玩家移动、收束段、结局可达性校验都已实现，见 [09 的切片表](./09_Reference_Scenario.md)。所以 13.1 / 13.2 / 13.3 / 13.5 的「等那件事先做」条件已经满足，各节里描述现状的句子相应改成了它们现在的样子。可直接读的东西：`story_beats.time_slot`、`story_beats.slots_spent_today`、`story_beats.day_limit`、`story_beats.visited_locations`、`narrative/wrap_up.py`、`narrative/player_actions.py`。

### 13.1 时段与天数（由 [13 §4.1](./13_Narrative_Events.md) 时间制带出）

`SceneView.time_day` 字段已有，`time_slot` 现在也有了（`story_beats.time_slot`，四个取值：morning / afternoon / evening / wrap_up）。界面要跟上的：

- 场景页显示「第 2 天 · 下午」。这是玩家侧信息，走 `VisibleState`。
- 面板显示时段预算（[15 §6](./15_Event_Script.md) 的 4 天 × 3 时段 = 12 个时段）：已用几个、剩几个。**这是新的一类面板内容**——它衡量的是玩家还剩多少机会，而现有各块衡量的都是故事的形状。
- `panels.py` 加一个纯函数，不要在 Web 层直接算。
- 别把总数写成字面量：`SLOTS_PER_DAY` 与 `StoryBeats.day_limit` 是来源（`day_limit` 是字段而非常量，剧本可以缩短案件）。收束段**不占时段**，所以「已用 3 / 12」在收束段那一刻是对的，不要把它算成第 4 个。

### 13.2 地点选择（由玩家移动带出）

**本层后续最大的一块。** 后端已经就绪：`_apply_move` 现在按 actor 的种类写 `player_locations` 或 `npcs`，`narrative/player_actions.move_player()` 是入口，它提一个 `move` 提案、通过后再扣一个时段。被拒的移动不扣时段。

现在的页面假设「一个场景、一个 NPC、一直对着他说话」。多地点会改变这个假设：每个时段要先选去哪，到了之后才是对话。这不是加一个按钮，是**在对话之上多一层**——`SceneView` 要带上「可去的地点」（从 `Location.connected_to` 与玩家当前位置推出，且必须只走 `PlayerView`），事件流要能表达「移动」这个不产生台词的回合。

三个具体约束：

- 移动消耗时段，所以它和对话一样要走 §5 的串行执行器，不能是一个直接改状态的旁路。`move_player()` 本身不加锁，它只是保证走 Validator。
- **不要在 Web 层复算邻接**。被拒的原因（`"'forest_edge' is not reachable from 'tavern'"`）是写给玩家看的（[02 §4.1](./02_Sequence_Diagram.md)），直接显示即可；前端自己判一遍会得到第二份规则。
- 「首次到达某地」是剧本触发条件（M4/M5 用），由 `story_beats.visited_locations` 承载，移动时自动记录。界面若想显示「没去过」，读它，不要另存一份。

### 13.3 收束段页面（由 [13 §4.2](./13_Narrative_Events.md) 带出）

今日新收获、已知线索、关系变化与未解问题，不占时段，每天自动到来。**是一个新的界面形态，不是场景页的变体**：没有 NPC、没有立绘、没有对话框。

后端在 `narrative/wrap_up.py`，引擎上的入口是 `is_wrapping_up()` / `wrap_up(player_id=...)` / `close_out_day()`。`wrap_up()` 在收束段之外返回 `None`，因为一天一次的仪式感是设计的一部分（[13 §4.2](./13_Narrative_Events.md)）——随时可看的整理只是一个屏幕。

- 整理线索**只重述已知**：`review_clues()` 的签名只收 `VisibleState`，所以泄漏需要改参数类型而不是漏一个判断。它还给出 `unanswered`（仍然敞着的问题），措辞是玩家本可以自己问的问题，不是对答案的描述——**渲染时不要替它补充"该去哪查"**，那是系统代替玩家推理。
- 每日开始记录已知事实及 NPC 对玩家的关系值，收束时比较差异；基准随存档保存。旧存档缺基准时明确显示不可追溯，不把所有线索冒充今日所得。

### 13.4 `active_event` 的显示（由事件层带出）

事件是多回合的（`StoryBeats.active_event` 记录哪个事件在进行、进行到第几句），而现有面板只显示**单回合**的算子决策。面板需要一块「当前事件」：哪个事件、第几句、它的分支条件是什么。

算子时间线届时的定位会变——它从「这一轮发生了什么」降级为「这个事件是怎么被讲出来的」，与 [13 §1](./13_Narrative_Events.md) 把算子降级为「事件的表达方式」一致。

### 13.5 结局与可达性（由 [13 §11](./13_Narrative_Events.md) 带出）

- 各个终局的呈现（[14 §4](./14_Case_Design.md)）。仍待做，M7 未写。
- **面板显示每个结局当前够不到哪一步**。加载器侧已经实现（`narrative.endings` + `reachability.py`，剧本现在声明四个终局加一个里程碑），所以这一块**现在就有数据可显示**：`load_endings()` 给出每个的条件、`path_note`、`pending` 与 `terminal` 标记，每个 clause 都能拿现有求值器逐条判「这一条满足了吗」。这正是解锁进度板的思路推广到结局一级：**把「为什么玩家还到不了这个结局」变成一眼可见**。
- **`terminal: false` 的那个要单独画。** 「玛尔塔彻底闭口」达成后游戏继续，把它和终局并列显示会告诉玩家游戏结束了——而他还在局中（[14 §4.3](./14_Case_Design.md#43-被洛伦先动手-玛尔塔彻底闭口)）。它该读作「这条路断了」，和解锁进度板里一条永远不会亮的线索同类。
- 加载器答的是窄问题——「有东西写这条路径吗」，不是「12 个时段内够得到吗」。面板显示时别把它说成后者。`pending: true` 的结局（当前是"被洛伦先动手"，等 M6）要显式标出来，否则它看起来像一个玩家差得很远的结局，而实际上是还没写。

### 13.6 玩家画像（由 Player Model 那一半带出）

`PlayerProfile` 有写入方之后，面板加一块显示 `play_style` 权重与 `narrative_preference`。最小的一块。

注意依赖方向：**它的价值依赖时段制**，而时段制现在有了——所以这个前提已经满足，玩家的行为终于有可区分的成本（去了哪个地点、花在谁身上、逼问还是耐心）。这也是 [13 §4.1](./13_Narrative_Events.md) 说时段是 Player Model 前提的原因。

两个现成的信号源：`story_beats.visited_locations`（去过哪、去过几处）和选项标签——[15 §2](./15_Event_Script.md) 已经把四个标签各映到一个 `PreferenceTag`（`OptionTag.preference_tag`），所以玩家点了什么本身就是分类结果，不需要再推断一次。

### 13.7 三种输入形态（由 [15 §1](./15_Event_Script.md) 带出）

现在只有一种输入：手打自由对话（§1 明确把选项菜单推后）。[15 §1](./15_Event_Script.md) 定下了三种：

| 形态 | 界面 | 数据来源 |
|---|---|---|
| 剧本台词（玩家自发说出） | 一句话直接出现在玩家侧，无需输入 | **剧本**：事件定义里的文本 |
| 带标签的选项 | 若干可点的按钮，标签可见 | **剧本**：事件定义里的选项表 |
| 自由输入 | 现有的输入框，始终可用 | 玩家 |

**用哪种不是模型决定的，是剧本决定的。** [15 §1](./15_Event_Script.md) 的判据可在加载时判定：一个位置的所有选项若导致同样的结果集，它就不该是选项——结果集只有一个的地方自动用剧本台词。所以前端拿到的事件里已经写明了形态，界面只负责渲染，不做判断。

模型只在一处参与：[15 §1.1](./15_Event_Script.md) 的分类——玩家自由输入的内容若等同于某个选项，就按那个选项判定，而且是**复用 NPC 那次调用顺带做**，不额外发请求。界面上的后果是自由输入和点选项**必须走同一个提交路径**（同一个 `POST /turn`，同一个执行器），否则两者的判定会分叉，而 [15 §1.1](./15_Event_Script.md) 存在的理由正是不让打字比点按钮吃亏。

三条界面约束：

- **剧本台词不是 NPC 台词**：它是玩家说的，渲染在玩家侧（历史记录里标为「你」），不进 NPC 的气泡。
- **选项出现即是信号**。[15 §1](./15_Event_Script.md) 说「大部分推进自然发生时，选择出现的那一刻本身就是信号：这里重要」——所以选项**不该常驻**一个空的选项区，没有选项时那块区域应当不存在，否则信号被稀释。
- **等待期间选项要禁用**，和输入框同一套逻辑（§5.3 的可见等待），否则玩家能在 tick 未落地时点第二个选项。

### 13.8 关系值走势的采样密度（本层自己的遗留）

现在每回合采一个点（`Session._sample_relationship`）。时段制之后可能该按时段采，否则一个时段内多轮对话会把曲线拉得很密而看不出节奏。

时段现在存在了，所以这条可以定了——但**先看一局真实数据再改**：一个时段里到底有几轮对话，决定的是该按时段采、还是保持按回合而在时段边界画一条分隔线。后者信息不丢，可能就够了。判据是曲线能不能看出节奏，不是采样点的疏密本身。




## 14. 角色总览与常驻操作

开发者面板按所有 NPC 分组，展开角色即可看到其对每个已记录目标的信任、恐惧和尊重。角色自己的地点、生存状态、人格、目标、情绪、信念、完整情节/语义记忆、可用工具、参与事件，以及最近对白、提案、执行步骤和调用成本归在该角色下面。嵌入向量不发给前端；原始信念和隐藏事件只走开发者接口，不能进入玩家场景或每日回顾。

关系趋势按 NPC 独立记录，最多 40 点；提案保留本次会话最近 40 条。恢复存档时恢复角色记忆和关系，调试轨迹重新开始。字段旁的 `!` 提供悬停说明；`importance` 是情节记忆重要度，`confidence` 是 NPC 对信念的确信程度，均不是本次检索的相似度。面板刷新保留各角色的折叠状态。

“做出结论”在调查和每日回顾页面均常驻。不可用时禁用并展示服务端给出的原因（对话未结束、地点不符、正在收束、已有结局或剧本未定义结论）；等待服务器或阅读对白时前端也暂时禁用。后台继续校验，不靠隐藏按钮维护规则。

每日开始时保存玩家已知事实和各 NPC 对玩家的关系基准，随存档保存。收束自动显示调查结束提示、新增/更新的已知线索、当天关系净变化、全部已知线索和未解问题。旧存档没有基准时明确说明，不能把所有旧线索标成今日收获。此流程不调用额外模型。
