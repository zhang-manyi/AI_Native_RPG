# 08. Evaluation

## 1. 目的

回答"这个系统好不好用"，而不是只回答"能不能跑"。没有 Eval 闭环的 Agent 系统无法验证 prompt/规则调整是否真的带来了改善。

## 2. 四个核心指标

| 指标 | 定义 | 计算方式 | 是否需要 LLM |
|---|---|---|---|
| Persona Consistency | NPC 回复是否符合其 persona 设定 | 规则检查关键词/语气 + 小模型打分（如"这句话是否符合'诚实'特质，1-5分"） | 轻量 LLM（可用小模型批量跑） |
| Memory Recall@K | 检索到的记忆是否包含真正相关的记忆 | 人工标注一小批"应该被召回的记忆"作为 ground truth，计算 Top-K 命中率 | 否，纯统计 |
| Tool Use Success Rate | Tool Call 是否成功返回预期结果、参数是否正确 | 规则检查：返回是否报错、参数是否符合 schema | 否，纯代码 |
| Narrative Coherence | 生成的剧情是否和世界状态一致、逻辑通顺 | 人工评分（量表 1-5），可选 LLM-as-judge | 人工，可选 LLM-as-judge |

### 2.1 叙事结构指标

上面四个指标衡量"每一次交互好不好"，但衡量不了"整个故事有没有形状"——一局里每句台词都合格，故事仍然可能是一盘散沙。补充三个结构指标，全部是**纯统计，不需要 LLM**，数据来自 Trace + `StoryBeats`（见 [10_Narrative_Operators.md](./10_Narrative_Operators.md)）：

| 指标 | 定义 | 计算方式 | 是否需要 LLM |
|---|---|---|---|
| **Foreshadow Payoff Rate** | 已回收伏笔 / 已埋下伏笔 | 纯统计，读伏笔账本 | 否 |
| **Payoff Span** | 回收轮次 - 埋下轮次（均值/分布） | 纯统计 | 否 |
| **Consistency Violations** | NPC 台词提到了其可见集合之外的事实的次数 | 两层，见下 | 第二层需要 |
前两个是纯统计，读 `StoryBeats.open_foreshadowings` 即可。`Consistency Violations` 需要两层，因为字面比对会漏掉语义等价的泄漏：

**第一层（规则，零成本）**：检查台词是否包含不可见 fact 的字面值（如凶手叫"洛伦"，而台词里出现了"洛伦"）。抓得准、无假阳性，但**抓不到换一种说法的泄漏**——不说"洛伦"而说"那个猎人"就绕过了。

**第二层（LLM-as-judge，抽样）**：把 NPC 的可见集合和台词一起交给模型，问"这句话是否透露了可见集合之外的信息"。只对第一层未命中的样本抽样跑，成本可控。

分两层的理由是这个指标的**假阴性代价高**：漏掉一次泄漏就等于一次没被发现的叙事崩坏，而这恰好是自然语言最容易发生的地方。只做第一层会给出一个虚高的好看数字。

另外两个可从解锁进度板（见 [07_Observability.md](./07_Observability.md#23-narrative-state-panel叙事状态面板)）直接读出的辅助指标：**解锁图覆盖率**（一局里实际被解锁的 fact / 全部可解锁 fact）和**卡死率**（玩家在某条件前停留超过 N 轮的比例）。这两个主要用于调阈值，不作为质量指标。

## 3. 具体计算方法

### 3.1 Persona Consistency

```python
def eval_persona_consistency(npc_state: NPCState, dialogue: str) -> float:
    # 规则检查 + 可选 LLM-as-judge
    # 规则示例：persona.traits['honest'] > 0.7 时，检查回复是否包含明显谎言标记词
    ...
```

初期先用规则覆盖明显案例（比如"诚实"人格却说了确定的谎言关键词），复杂案例留给人工看 Trace 判断，不追求全自动化评分。

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

初期用人工评分表（1-5 分，评估"这个事件是否符合当前世界状态"），不做自动化；数据量增长后可以引入 LLM-as-judge 降低人工成本。

## 4. Eval Dashboard 展示什么

```
Persona Consistency:    94%  (基于 N 次交互的规则+抽样人工核验)
Memory Recall@5:        0.87 (基于 20 个标注测试 case)
Tool Use Success:       96%  (基于所有 tool_call 记录)
Narrative Coherence:    4.2/5 (人工评分均值, 基于 M 次事件生成)

--- 叙事结构 ---
Foreshadow Payoff Rate: 6/8   (2 条超期未回收)
Payoff Span (均值):     7.3 轮
Consistency Violations: 1     (第 14 轮, npc_a 提到未解锁的 clue_2；规则层命中)
```

## 5. 当前实现范围

- 不做完整的自动化评测 pipeline（如 CI 里跑 eval 套件），跑一次离线评估脚本、出一份报告即可；标注数据和 Trace 积累到一定规模后再接入 CI。
- 标注数据量从 10-20 个 case 起步，足够验证评估方法本身是否合理，规模可随迭代扩大。
- 优先做 Memory Recall@K 和 Tool Use Success Rate（纯代码，成本最低），Persona Consistency 和 Narrative Coherence 可以先用人工评分代替自动化评分，视资源投入逐步自动化。
- §2.1 的叙事结构指标：前两个（伏笔）是纯统计，与 Memory Recall 同批实现；`Consistency Violations` 先只做第一层规则检查，LLM-as-judge 那一层等 Trace 积累到能抽样的规模再加。
