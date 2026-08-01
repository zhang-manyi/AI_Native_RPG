# 03. Player Model

## 1. 职责

回答"玩家是谁，喜欢什么"，为 Experience Controller 和 Narrative Engine 提供输入。**不是 Agent**，是数据模型 + 两段处理逻辑。

## 2. 两层设计：实时统计 vs 周期性语义摘要

这是本模块最重要的设计决策：**不要每次玩家交互都调用 LLM 更新画像**。

| 层 | 触发时机 | 是否用 LLM | 输出 |
|---|---|---|---|
| Behavior Tracker | 每个玩家行为事件，实时 | 否，纯计数/比例统计 | `PlayerRawStats`（连续数值特征） |
| Profile Summarizer | 会话结束 / 章节结束 / 累积 N 次交互 | 是，批量调用一次 | `narrative_preference` 自然语言摘要 |

### 2.1 Behavior Tracker（确定性，实时）

```python
exploration_score = time_spent_exploring / total_play_time
combat_score = combat_encounters_initiated / total_encounters
risk_preference = risky_choices_taken / risky_choices_offered
```

这些是滑动窗口计数/比例，跟推荐系统的用户特征工程完全一样。不需要 LLM，延迟 <1ms，可单测。

### 2.2 Profile Summarizer（LLM，周期性批处理）

两个场景需要一点"智能"：

1. **自由文本对话选择的语义分类**（如果对话是开放文本而非按钮选项）：判断这句话体现"善良/欺骗/中立"。初期实现可以用轻量 embedding + 分类器，不一定要上 LLM。
2. **把结构化特征翻译成自然语言摘要**，供 Narrative Engine 和 NPC Agent 的 prompt 使用。例如把 `{exploration:0.9, combat:0.2}` 变成"这是一个偏好探索、回避冲突的玩家"。这一步适合用 LLM，因为下游消费者本身就是靠自然语言 prompt 工作的。

**触发时机**：会话结束、进入新章节、或累积 N 次交互后批量调用一次，不挂在每次交互的关键路径上（见 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md#4-关键结论一次玩家交互只需-1-2-次-llm-调用)）。

## 3. 数据流

```
玩家行为事件 (choice, dialogue, combat, exploration, time, failure)
        |
        v
Behavior Tracker (实时, 纯代码)
        |
        v
PlayerRawStats (连续数值特征, 存数据库)
        |
        | (会话结束/周期触发)
        v
Profile Summarizer (LLM, 批量)
        |
        v
PlayerProfile.narrative_preference (自然语言摘要, 供下游 prompt 使用)
```

## 4. Schema

见 [schemas/player_model.py](./schemas/player_model.py)。

## 5. 当前实现范围

- **对话输入是自由文本 + 2-3 个"建议话题"按钮**（与 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md) 的玩家自由提问一致）。自由文本是 NPC Agent 的理解与记忆检索能力唯一能被真正展示的入口，固定选项会把 Agent 降级成状态机查表。
- 行为打标签的代价：自由文本无法像按钮那样直接读到标签。分两条来源解决——**建议话题按钮被点击时**带确定的 `preference_tag`（零成本、无噪声）；**自由输入**则由 NPC Agent 那次 LLM 调用**顺带**输出一个 `player_intent_tag`（复用已有调用，不额外增加 LLM 请求），Behavior Tracker 消费这个标签。这样既保留自由文本，又不为打标签单独引入分类器或额外调用。
- `PlayerRawStats` 里同时记录 `tagged_from_button` 和 `tagged_from_llm` 的计数，便于评估 LLM 打标签的噪声水平（可与人工标注的一小批样本对比一致率）。
- Profile Summarizer 初期用规则模板（if-else 拼句子）代替 LLM 调用；累积到足够的行为数据后再切换成 LLM 摘要，能明显体现优势的时候才值得引入。这是渐进式的实现路径，不是能力上限。
