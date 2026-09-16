# 05. Narrative Engine & Experience Controller

## 1. 职责划分

两个模块经常被混为一谈，明确拆开：

| 模块 | 类型 | 职责 |
|---|---|---|
| Experience Controller | Deterministic | 给候选事件按玩家偏好**打分排序**，决定"接下来推给这个玩家哪个" |
| Narrative Engine | Rule Trigger + LLM Generation | 决定"世界规则上允许发生什么"（规则），并**生成具体内容**（LLM） |

类比：Experience Controller 像推荐系统的重排层，Narrative Engine 像 RimWorld 的 AI Director，但多了一步"用 LLM 把触发的事件写成具体剧情"。

## 2. Narrative Engine：规则触发 + LLM 生成

### 2.1 规则触发（确定性）

```python
def check_triggers(world_state: WorldState) -> list[EventCandidate]:
    candidates = []
    if world_state.factions["royal_family"].stability < 0.3:
        candidates.append(EventCandidate(event_type="betrayal", intensity=0.7, ...))
    # 关系值存在 WorldState.relationships，不在 NPCWorldState 上，见 04 文档 §2.1
    if world_state.get_trust("npc_a", "player_1") < 30:
        candidates.append(EventCandidate(event_type="npc_turns_hostile", intensity=0.5, ...))
    return candidates
```

注意关系值的读取路径是 `world_state.relationships[npc_id][target_id].trust`（Manager 上有 `get_trust()` 便捷方法），不是 `NPCWorldState.trust_player`——后者是早期草稿里的字段，已废弃。原因见 [04_World_State_Manager.md](./04_World_State_Manager.md#21-状态归属边界客观事实-vs-主观认知)。

纯规则判断"是否达到触发条件"，不需要 LLM 推理。这一步的输出是候选事件列表，不是最终内容。

**上面的示例比当前实现更接近正确的设计。** 这里写的 `event_type="betrayal"` 是*事件*——有前件、有后件、有分支；而实现走到了算子粒度（`foreshadow` / `reveal` / `escalate` / `reverse`），`EventCandidate.operator` 是那时加的字段（见 §5 变更表第一行），`event_type` 随之退化成 `"planted_detail"` 这样的标签。算子是修辞动作，拼不出情节，后果记录在 [09 §4](./09_Reference_Scenario.md)。

修正后的分层：**事件回答"发生什么"，算子回答"怎么讲"**，候选来自剧本定义的事件，算子由事件声明。§2.2 的两段式选择不变——它本来就是为候选事件写的。详见 [13_Narrative_Events.md](./13_Narrative_Events.md)。

### 2.2 Experience Controller：候选事件准入 + 排序（确定性）

**两段式，不是一个乘式。** 偏好和张力是两个正交维度（详见 [10_Narrative_Operators.md](./10_Narrative_Operators.md#3-选择张力准入-偏好排序)）：偏好是玩家的口味轴，一个会话内基本不变；张力是故事的时间轴，每个 beat 都在变。相乘无法表达**对味但不是时候**——玩家偏好阴谋，但刚连着三次揭露，第四次阴谋事件依然是错的，而乘法里偏好那一项还在给它加分。

```python
def select_candidate(
    candidates: list[EventCandidate],
    player_profile: PlayerProfile,
    beats: StoryBeats,
) -> EventCandidate | None:
    # 第一段：张力准入（硬过滤）——现在允许哪些算子
    admissible = [c for c in candidates if passes_pacing_rules(c, beats)]
    if not admissible:
        return None

    # 第二段：偏好排序（软打分）——允许的里面这个玩家要哪个
    def score(c: EventCandidate) -> float:
        return player_profile.play_style.get(c.preference_tag, 0.5) * c.intensity

    return max(admissible, key=score)
```

偏好排序这一段类似推荐系统的重排层，不需要 LLM。玩家偏好阴谋剧情，"背叛"类事件排名靠前；玩家偏好轻松探索，同样的世界状态下"背叛"事件会被降权。但节奏规则先于偏好生效：无论玩家多偏好阴谋，连续两轮 `reveal` 都不允许。`passes_pacing_rules` 的具体规则、以及为什么本场景不做完整张力曲线，见 [10_Narrative_Operators.md](./10_Narrative_Operators.md#31-节奏规则张力准入的全部内容)。

### 2.3 LLM 只演绎已有事件

当前村庄剧本包含 `choice` / `narration` 事件，走 `_tick_authored()`：按地点、条件和剧本顺序推进，自动旁白直接使用剧本内容，不调用叙事生成模型。NPC 对话仍可使用模型演绎。

仅含 `dialogue` 事件的旧路径保留节奏准入和偏好排序，但生成式 `foreshadow` 候选已禁用。模型输出只含 `summary`、`dialogue_hook`、`participants`，不得创造新的物证、目击者、事实 ID 或回收条件。`reveal`、`escalate`、`reverse` 仅对已有内容进行表达。

预定义伏笔继续由剧本提供，`payoff_target` 指向已有事实；回收条件取目标事实自己的 `reveal_condition`。NPC 不承担编剧职责。本轮不增加独立 AI 编剧调用，今后若引入导演，应讨论对预定义事件池的调度，而不是开放模型自由创造案件事实。

## 3. 旧版生成式事件流程（当前村庄使用 §2.3 的剧本路径）

```
World State 变化
      |
      v
Narrative Engine: check_triggers()          [规则, 确定性]
      |
      v
候选事件列表 (可能有多个)
      |
      v
Experience Controller: select_candidate()    [确定性]
   ├── 张力准入 (硬过滤: 节奏规则)
   └── 偏好排序 (软打分: play_style)
      |
      v
Top-1 候选算子 (可能为 None: 节奏规则判定"现在什么都不该发生")
      |
      v
Narrative Engine: generate_content()        [LLM 调用]
      |
      v
Action Proposal (更新 Fact.visibility / NPC 状态 / Quest 状态 / story_beats)
      |
      v
World State Manager: Validator              [规则, 确定性]
      |
      v
World State Update
```

注意 Top-1 可以是 `None`——"这一轮什么都不该发生"是一个合法且重要的输出（对应 `relieve` 算子），不是失败分支。LLM 自由生成最常见的失败之一就是每一轮都要发生点什么。

## 4. 叙事结构：算子与伏笔

上面的流程解决了"什么时候发生什么"，但不解决"这个故事有没有形状"。LLM 自由生成会产出局部合理、整体无形状的内容——每一句都通顺，但没有铺垫和回收，没有逐步逼近核心的感觉。

解决方式是把叙事结构做成**离散的、可调度的算子**（`foreshadow` / `reveal` / `escalate` / `reverse` / `relieve`），由确定性系统决定用哪个，LLM 只在算子给定的槽位里填内容。`check_triggers()` 产出的候选因此带一个 `operator` 字段，而不只是笼统的 `event_type`。

其中 `foreshadow` 和 `payoff` 是唯一必须成对出现的算子，也是 LLM 最做不到的事（埋下时不记得回收）。本架构用确定性的**伏笔账本**解决，它可度量、可可视化。

完整讨论见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)。

## 5. 数据结构

事实源是 `src/ai_native_rpg/schemas/narrative.py`（`docs/schemas/narrative_event.py` 是早期草稿，已被它取代）。相对草稿的四处变化：

| 变化 | 原因 |
|---|---|
| `EventCandidate` 加 `operator` | §4 要求候选带算子而非只有 `event_type` |
| 加 `constraints` / `pays_off` | 显式禁止项（[10 §2.3](./10_Narrative_Operators.md)）；`pays_off` 标记这次 reveal 在回收哪条伏笔 |
| `reveal_timing: str` → 账本的 `payoff_condition: Condition` | 自由字符串无法被求值器判定，账本就无法自动回答"到时机了吗"，见 §2.3 |
| `PlayerProfile` 从 `player_model.py` 草稿移植过来 | Experience Controller 现在就要读它；`PlayerRawStats` 和 Behavior Tracker 等切片 4 有写入方时再移植 |

## 6. 当前实现范围

- 触发规则写死若干条即可（如"信任度<30 触发背叛候选"），初期不做通用规则配置系统；规则数量增多后再考虑配置化。
- Experience Controller 的偏好排序用简单加权求和，不引入机器学习排序模型；张力准入只做节奏规则（不能连续两轮 `reveal` 等），不做张力曲线拟合。
- LLM 生成内容限定输出 schema（用结构化输出/JSON mode），避免自由文本导致下游解析失败。生成的 `generated_content` 是给 NPC Agent 的 Dialogue Generation 阶段消费的结构化输入，而不是直接展示给玩家的文本，见 [06_NPC_Agent_Spec.md](./06_NPC_Agent_Spec.md#3-dialogue-generation)。
- `foreshadow` 仅用于剧本预定义的伏笔；生成路径只演绎 `reveal` / `escalate` / `reverse`，无事件时为 `relieve`。
- **Narrative Engine 没有绕过 `reveal_condition` 的权限。** 它推进 `story_beats`（章节/进度），由条件表决定这解锁了什么。理由见 [04_World_State_Manager.md](./04_World_State_Manager.md#33-叙事推进不等于披露授权)。
