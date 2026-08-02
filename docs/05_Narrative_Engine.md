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

### 2.3 LLM 生成具体内容

规则确定"要发生背叛事件"之后，LLM 负责把它写成具体的剧情：

```
Input: operator="foreshadow", event_type="betrayal", intensity=0.7,
       world_state 相关片段, player_profile.narrative_preference,
       constraints: ["不能揭露 NPC_B 的身份"]     ← 来自算子槽位
Output: {
  "who_betrays": "NPC_B",
  "how": "在玩家不知情时向敌对阵营通风",
  "dialogue_hook": "...",
  "payoff_condition": {"path": "story_beats.chapter", "op": "gte", "value": 2}
}
```

这一步是真正需要"创造性"的地方，交给 LLM 合理。输出结构化 JSON，写回 World State 时仍然走 Action Proposal → Validator 流程（Narrative Engine 也不能直接改世界状态）。

注意 `payoff_condition` 取代了早期草稿里的 `reveal_timing: "next_chapter"` 这种自由字符串：回收时机必须是一个**可被求值器判定**的结构化条件，否则伏笔账本无法自动检查"到回收时机了吗"，只能靠人读。这也是 `constraints` 字段存在的原因——算子已经规定了这一场不能说什么，把它显式传给模型比指望它自己不说破更可靠。

## 3. 完整流程

```
World State 变化
      |
      v
Narrative Engine: check_triggers()          [规则, 确定性]
      |
      v
候选算子列表 (可能有多个)
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

见 [schemas/narrative_event.py](./schemas/narrative_event.py)。

## 6. 当前实现范围

- 触发规则写死若干条即可（如"信任度<30 触发背叛候选"），初期不做通用规则配置系统；规则数量增多后再考虑配置化。
- Experience Controller 的偏好排序用简单加权求和，不引入机器学习排序模型；张力准入只做节奏规则（不能连续两轮 `reveal` 等），不做张力曲线拟合。
- LLM 生成内容限定输出 schema（用结构化输出/JSON mode），避免自由文本导致下游解析失败。生成的 `generated_content` 是给 NPC Agent 的 Dialogue Generation 阶段消费的结构化输入，而不是直接展示给玩家的文本，见 [06_NPC_Agent_Spec.md](./06_NPC_Agent_Spec.md#3-dialogue-generation)。
- 初期只实现 4 个算子（`foreshadow` / `reveal` / `escalate` / `reverse`），`relieve` 用"本轮不触发任何算子"隐式表达。
- **Narrative Engine 没有绕过 `reveal_condition` 的权限。** 它推进 `story_beats`（章节/进度），由条件表决定这解锁了什么。理由见 [04_World_State_Manager.md](./04_World_State_Manager.md#33-叙事推进不等于披露授权)。
