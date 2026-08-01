# 04. World State Manager

## 1. 职责

**Single Source of Truth**。世界的事实、时间、任务状态、NPC 状态、阵营关系的唯一存储位置。没有任何 Agent 可以直接修改世界状态——所有变更必须经过 Action Proposal → Validator → State Update 流程。

这是整个系统最重要的确定性模块，不涉及 LLM。

## 2. Action Proposal / Validator 模式

```
Agent (NPC / Narrative Engine)
        |
        v
  Action Proposal   {actor, action_type, target, payload}
        |
        v
     Validator       规则检查：权限、一致性、游戏规则
        |
   ---- 通过 ----          ---- 拒绝 ----
        |                        |
        v                        v
  State Update              Rejection + Reason
  (写入 World State)         (返回给 Agent，可能触发 Reflection)
```

**示例**：NPC 提出 `kill(player)`。Validator 检查：关系值是否 < -80、是否持有武器、任务状态是否允许。任一条件不满足则拒绝。

这个模式同时承担了原来讨论中"Governance Layer"（权限/安全/规则校验）的职责——**不再单独设一层**，因为校验 Action Proposal 本来就是 World State Manager 该做的事。

## 2.1 状态归属边界：客观事实 vs 主观认知

哪些状态属于 World State（必须走 Validator），哪些属于 Agent 私有，需要一条明确的线，否则 Reflection 直接改 NPC 状态就绕过了上面的约束。

| 归属 | 内容 | 谁能改 | 消费者 |
|---|---|---|---|
| **World State**（客观、可被多方观测） | NPC 位置/存活、任务状态、阵营关系、`Fact`、**角色间关系值 trust/fear/respect** | 仅通过 Action Proposal → Validator | Validator、Narrative Engine 触发规则、NPC Agent prompt、Eval |
| **Agent 私有 store**（主观、单个 NPC 内部） | `emotion`、`beliefs`、`EpisodicMemory`、`SemanticMemory` | Reflection 阶段可直接写，无需 Validator，但必须落 Trace | 仅该 NPC 自己的 prompt |

**关系值（trust/fear/respect）归 World State**，这是有意的设计决策：它同时被 Validator（校验 `kill(player)` 要看关系值）、Narrative Engine 触发规则（`trust > 40` 触发线索）、NPC Agent prompt 三方读取。若存在 Agent 记忆里，确定性层就要反向依赖 Agent 层，且会出现 Agent 认为 `trust=70`、Validator 看到 `trust=0` 的状态分裂。`memory.py` 里的 `RelationshipMemory` 因此退化为**从 World State 读取的只读投影**，不独立存储。

代价是放弃了"NPC 可能误判玩家"这种主观偏差的表达力。需要时可以在 Agent 私有 store 里加一层 belief 偏移（如 `beliefs['player_is_lying'] = True`），而不是让关系值本身分裂成两份。

## 3. Reality Layer vs Player View（信息不对称）

RPG 的核心问题：世界的完整事实 ≠ 玩家应该看到的信息。之前讨论中提出的"第一人称叙事导演"就是为了解决这个问题，但作为独立 Agent 会和 Narrative Engine 职责重叠、且增加不必要的 LLM 调用。

**替代方案**：把"信息披露"下沉为 World State 上的数据字段 + 一个纯函数。

### 3.1 数据设计

每个 Fact/Event 带 `visibility` 和 `reveal_condition`：

```json
{
  "fact_id": "killer_identity",
  "value": "NPC_B",
  "visibility": "hidden",
  "reveal_condition": {
    "mode": "any",
    "clauses": [
      {"path": "relationships.npc_a.player_1.trust", "op": "gt", "value": 60},
      {"path": "quests.investigation.stage", "op": "gte", "value": 3}
    ]
  }
}
```

`visibility` 取值：`hidden`（玩家完全不可见） / `partial`（可见但信息不全，如"有个嫌疑人"但不知道是谁） / `revealed`（完全可见）。

**条件为什么是结构化对象而不是字符串表达式**：`"player.trust[NPC_A] > 60 OR quest.stage >= 3"` 这样的字符串要么需要写一个表达式解析器（和本文档 §5"不做通用规则引擎"矛盾），要么用 `eval()`（把剧本数据文件变成任意代码执行入口，不可接受）。结构化的 `{path, op, value}` + `any/all` 组合覆盖了当前场景需要的全部条件形式，求值器是一个几十行的纯函数，可单测，且 `path` 非法时能明确报错而不是静默返回 False。表达力不够时（需要算术、跨字段比较）再考虑升级，而不是一开始就上 DSL。

### 3.2 Player View Projection

```python
def PlayerView(world_state: WorldState, player_id: str) -> VisibleState:
    """纯函数：根据 visibility 规则过滤，返回该玩家当前能看到的世界。"""
    ...
```

披露不需要一个专门的 Agent 来"决定"：信息何时可见由 `Fact` 上的数据 + 这个纯函数共同决定。功能保留，但少了一整层 Agent，也不会和 Narrative Engine 的"决定世界发生什么"职责打架。

Narrative Engine 想制造反转，是通过推进 `story_beats` 让条件表自然满足，**而不是直接翻某个 Fact 的 `visibility`**——这个限定很重要，见下一节。

### 3.3 叙事推进不等于披露授权

**没有任何 actor 可以绕过 `reveal_condition`，Narrative Engine 也不行。**

Engine 只推进条件所读取的状态（`story_beats.chapter`、`quests.*.stage`），由条件表决定这解锁了什么。想让某条线索在第二章无条件可见，就给它加第二条通道：

```yaml
reveal_condition:
  mode: any
  clauses:
    - path: relationships.npc_a.player_1.trust   # 玩家自己挣到
      op: gte
      value: 40
    - path: story_beats.chapter                   # 或剧情推到那儿
      op: gte
      value: 2
```

**关键性质：Engine 推进 chapter 时并不知道自己解锁了什么。** 谁在第几章解锁写在剧本里，`killer_identity` 的条件里没有 `chapter`，所以 Engine 无论推到第几章都不会泄漏凶手。

曾考虑给 Engine 一个 `force_reveal_fact` 无视条件，已否决：beat 会自己往前走，任何预留了通道的 fact 迟早都能解锁，真正打不开的只有作者**刻意**没留通道的那几条——而那正是结局信息。配额也救不了它（配额按作者意图分配，用掉它的是运行时的 LLM 判断）。失败代价不对称：晚一章解锁玩家察觉不到，第一章泄漏凶手一次就毁掉整局。

代价是每个披露都必须被作者预先表达成条件。这既是成本也是特性：作者不留通道就是硬约束，不是可绕过的默认值。

## 4. 数据结构

见 [schemas/world_state.py](./schemas/world_state.py)。核心分四块：

- `WorldState`：Reality Layer，全量事实，NPC/阵营/任务状态，时间。
- `Fact` / `Event`：带 `visibility` 字段，供 Player View 过滤。
- `ActionProposal` / `ActionValidationResult`：Agent 提交变更的标准接口。
- `StoryBeats`：叙事进度状态，Narrative Engine 唯一能推进的东西（见 §3.3）。

### 4.1 StoryBeats（切片 3 引入，当前未实现）

```python
class StoryBeats(BaseModel):
    chapter: int = 1
    tension: float = 0.0  # 当前张力，[0, 1]
    open_foreshadowings: dict[str, Foreshadowing]  # 未回收的伏笔账本
    recent_operators: list[str]  # 最近 N 轮触发的算子，供节奏规则判断
```

三个字段对应 [10_Narrative_Operators.md](./10_Narrative_Operators.md) 的三件事：`chapter` 是 Engine 的推进通道（§3.3），`open_foreshadowings` 是伏笔账本，`recent_operators` 供 Experience Controller 的节奏准入使用。

**为什么推迟到切片 3**：切片 1-2 没有消费者，现在加就是加一个没人读的字段。`open_foreshadowings` 每条要存什么，到实现伏笔回收时才会清楚。当前剧本里没有任何条件引用 `story_beats.*`，因此推迟不会导致剧本改写——一旦剧本里出现了这类 `path`，schema 就应该立即定稿。

## 5. 实现约定

本节描述这一层的实现方式与边界，不记录完成进度——进度只在 [09_Reference_Scenario.md](./09_Reference_Scenario.md#4-建议的实现顺序对应依赖关系) 的切片表维护一处，历史由 git 记录。

- 用内存字典 + 落盘（JSON）代替真实数据库，不引入 ORM；后续如需多进程共享状态再迁移。
- Validator 规则是一个规则列表（每条规则是纯函数 `(proposal, world_state) -> RuleOutcome`），不做通用规则引擎，避免过度工程。规则按顺序执行、首个失败即短路，因此拒绝原因指向最根本的那条违规，而不是附带的下游问题。
- `PlayerView` 支持 `hidden` / `revealed` / `partial` 三态。`partial` 在缺 `partial_value` 时**降级为不可见**（fail closed），并在剧本加载时报错——因为一条"部分可见但没有可展示内容"的 fact 几乎一定是作者笔误。
- `WorldState` 不对外暴露可变引用：Manager 持有私有实例，`snapshot()` 返回深拷贝，构造函数也拷贝入参，写入只经 `submit()`。这是把"没有任何 Agent 可以直接修改世界状态"从约定变成代码强制。
- `reveal_condition` 求值器支持 `eq/ne/gt/gte/lt/lte/in` 七个算子，`path` 用点号寻址。路径非法或类型不匹配时**抛异常而不是返回 False**——静默的 False 会变成"线索永远解锁不了且日志里什么都没有"。
- 剧本加载时校验所有交叉引用（地点连通性、NPC/玩家所在地、阵营归属、每个条件 `path` 能否解析）。
- **关系值步长上限** `MAX_RELATIONSHIP_STEP = 15.0`：单次 proposal 对任一维度的改动不得超过 15 点。这限制了 LLM 用一句话把 `trust` 拉满、一次解锁整条线索链的能力；模型仍可跨多轮累积推进。设剧本阈值时要考虑这一点（`trust` 从 10 到 40 至少需要 2 轮）。写入时对 ±100 做钳制，因此合法的连续推进不会撞上 schema 校验错误。
- **proposal_id 幂等去重**：同一个 `proposal_id` 第二次提交会被拒绝。重试的 LLM 调用不会把关系值加两次。
- `dry_run()` 在不写入的前提下返回校验结论，供 Agent 在 Planning 阶段作为工具调用查询"这个行动会被允许吗"。规则是纯函数，因此这样做没有副作用。
- **没有任何 actor 可以绕过 `reveal_condition`**（见 §3.3）。
- `StoryBeats`（§4.1）及 `advance_story_beat` action 属于切片 3，届时同步加一条 `chapter` 单调递增的 Validator 规则。
