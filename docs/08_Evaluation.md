# 08. Evaluation

## 1. 目的

回答"这个系统好不好用"，而不是只回答"能不能跑"。没有 Eval 闭环的 Agent 系统无法验证 prompt/规则调整是否真的带来了改善。

## 2. 四个核心指标

| 指标 | 定义 | 计算方式 | 是否需要 LLM |
|---|---|---|---|
| Persona Consistency | NPC 回复是否符合其 persona 设定 | 规则前置过滤 + LLM-as-judge 打分（1-5），见 §3.1 | 是 |
| Memory Recall@K | 检索到的记忆是否包含真正相关的记忆 | 标注一小批"应该被召回的记忆"作为 ground truth，计算 Top-K 命中率 | 否，纯统计 |
| Tool Use Success Rate | Tool Call 是否成功返回预期结果、参数是否正确 | 规则检查：返回是否报错、参数是否符合 schema | 否，纯代码 |
| Narrative Coherence | 生成的剧情是否和世界状态一致、逻辑通顺 | LLM-as-judge 打分（1-5），抽样人工校准 | 是 |

### 2.1 叙事结构指标

§2 的四个指标衡量单次交互，衡量不了"整个故事有没有形状"。补充三个结构指标，数据来自 Trace + `StoryBeats`（见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)）：

| 指标 | 定义 | 计算方式 | 是否需要 LLM |
|---|---|---|---|
| **Foreshadow Payoff Rate** | 已回收伏笔 / 已埋下伏笔 | 读 `StoryBeats.open_foreshadowings` | 否 |
| **Payoff Span** | 回收轮次 - 埋下轮次（均值/分布） | 同上 | 否 |
| **Consistency Violations** | NPC 台词提到了其可见集合之外的事实的次数 | 两层，见下 | 第二层需要 |

`Consistency Violations` 分两层，因为字面比对会漏掉语义等价的泄漏（不说"洛伦"而说"那个猎人"），而这个指标的假阴性代价高——漏掉一次泄漏就是一次未被发现的叙事崩坏：

| 层 | 方式 | 覆盖范围 |
|---|---|---|
| 1 | 规则：检查台词是否包含不可见 fact 的字面值 | 准确、零成本、无假阳性；抓不到换一种说法的泄漏 |
| 2 | LLM-as-judge：给出可见集合 + 台词，问是否透露了集合外的信息 | 只对第一层未命中的样本抽样跑 |

另有两个辅助指标可从解锁进度板（见 [07_Observability.md](./07_Observability.md#23-narrative-state-panel叙事状态面板)）直接读出，用于调阈值而非衡量质量：**解锁图覆盖率**（实际解锁 / 全部可解锁）和**卡死率**（玩家在某条件前停留超过 N 轮的比例）。

## 3. 具体计算方法

### 3.1 Persona Consistency

两层，规则在前是为了省钱和消除明显噪声，不是为了替代 judge：

```python
def eval_persona_consistency(npc_state: NPCState, dialogue: str, judge: Judge) -> PersonaScore:
    # 第一层：规则。命中即 0 分，不再调用 judge。
    #   例：persona.traits['honest'] > 0.7 却出现确定性谎言标记
    if violation := check_hard_rules(npc_state, dialogue):
        return PersonaScore(score=0.0, source="rule", reason=violation)

    # 第二层：LLM-as-judge，1-5 分 + 理由
    return judge.score_persona(npc_state.persona, dialogue)
```

judge 的输出必须是结构化的 `{score: 1-5, reason: str}`：`reason` 不参与统计，但没有它就无法判断低分是模型真的不一致、还是 judge 自己理解错了。

**必须做的三件事**（否则这个指标是自欺欺人）：

1. **人工校准集**：标注 20-30 条样本的期望分数，用来量 judge 与人工的一致率（Spearman 相关或简单的 ±1 分命中率）。一致率过低说明 judge 的 prompt 有问题，而不是被测系统有问题。
2. **judge 与被测模型分离记录**：judge 用的模型和 prompt 版本要写进报告。换了 judge 就等于换了尺子，跨版本的分数不可直接比较。
3. **位置/长度偏差检查**：LLM judge 倾向于给更长的回复更高分。抽样核对分数与回复长度的相关性，若显著则在 prompt 里显式要求"不因长度加分"。

Narrative Coherence 用同一套 judge 基础设施，只换 prompt 和评分维度。

### 3.2 Memory Recall@K

```python
def eval_recall_at_k(retrieved: list[str], ground_truth: list[str], k: int) -> float:
    top_k = retrieved[:k]
    hits = len(set(top_k) & set(ground_truth))
    return hits / len(ground_truth) if ground_truth else 0.0
```

需要提前标注 10-20 个测试 case（"给定这个问题，应该召回哪些记忆"），这是纯工程活，不需要复杂标注平台。

### 3.3 Tool Use Success Rate

```python
def eval_tool_success(trace: AgentTrace) -> float:
    tool_steps = [s for s in trace.steps if s.step_name == "tool_call"]
    if not tool_steps:
        return 1.0
    successes = sum(1 for s in tool_steps if "error" not in s.output_summary)
    return successes / len(tool_steps)
```

### 3.4 Narrative Coherence

LLM-as-judge，1-5 分，评估"这个事件是否符合当前世界状态、逻辑是否通顺"。输入是事件内容 + 相关的世界状态片段，输出 `{score, reason}`。与 §3.1 共用 judge 基础设施和三项要求（校准集、版本记录、长度偏差检查）。

## 4. Eval Dashboard 展示什么

```
Persona Consistency:    4.3/5 (judge: deepseek-v4-flash, prompt v3; 人工一致率 87%)
Memory Recall@5:        0.87 (基于 20 个标注测试 case)
Tool Use Success:       96%  (基于所有 tool_call 记录)
Narrative Coherence:    4.2/5 (judge 同上, 基于 M 次事件生成)

--- 叙事结构 ---
Foreshadow Payoff Rate: 6/8   (2 条超期未回收)
Payoff Span (均值):     7.3 轮
Consistency Violations: 1     (第 14 轮, npc_a 提到未解锁的 clue_2；规则层命中)
```

judge 的模型与 prompt 版本必须一同展示：换了 judge 就是换了尺子，跨版本分数不可直接比较。

## 5. 实现约定

- 不做完整的自动化评测 pipeline（如 CI 里跑 eval 套件）：judge 调用要花钱且有网络依赖，不适合放进每次 push 的检查。跑一次离线评估脚本、出一份报告即可。纯代码的指标（Memory Recall、Tool Use、伏笔）可以进 CI。
- 标注数据量从 10-20 个 case 起步，judge 校准集 20-30 条。
- 实现顺序：纯代码指标（Memory Recall@K、Tool Use Success Rate、伏笔两项）→ judge 基础设施 + 校准集 → Persona Consistency 与 Narrative Coherence → `Consistency Violations` 的第二层。
- judge 的模型、prompt 版本、温度必须记录在报告里，且**judge 的 prompt 本身也要经过 [11_Prompt_Lab.md](./11_Prompt_Lab.md) 的流程选定**——否则用一把没校准过的尺子量东西。
