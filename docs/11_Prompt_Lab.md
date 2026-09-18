# 11. Prompt Lab

## 1. 职责

当前只实现一次小型候选闭环，见 [evals/README.md](../evals/README.md) 和 [本轮复核](../evals/reports/2026-09-16/review.md)。实际 `prompts/` 为 `npc_planning.txt`、`npc_dialogue.txt`、`narrative_generate.txt`；下文 `.md` 目录、judge、10–20 场景及 N≥3 是设计目标。此次候选改上下文组装规则，N=1，不具备推广依据，默认关闭；没有自动搜索或完整 Prompt Lab 平台。

**离线**比较多个候选 prompt，用量化分数选出最好的一个写回 `prompts/`。

不在游戏运行时里，不参与玩家交互。运行时只读 `prompts/` 下已选定的文件。

覆盖范围：系统里每一个进入 LLM 的 prompt——NPC Planning/Dialogue、Narrative Engine 事件生成、Profile Summarizer、以及 [08_Evaluation.md](./08_Evaluation.md) 里 judge 自己的 prompt。

## 2. 流程

```
候选 prompt (2-5 个变体)  ×  固定测试场景集 (10-20 个)
                    |
                    v
            批量生成回答  (固定 temperature/seed)
                    |
                    v
            打分  ├── 规则指标 (schema 合法性、约束遵守、长度)
                  └── LLM-as-judge (质量维度, 复用 08 的 judge)
                    |
                    v
            对比报告 (每个候选 × 每个指标的均值/分布)
                    |
                    v
            人工选定  ──>  写回 prompts/<name>.md, 版本号 +1
```

人工选定这一步不自动化：分数只排除明显更差的候选，最终判断（尤其是"哪个更像那个角色在说话"）仍由人做。

## 3. 目录结构

```
prompts/
├── npc_planning.md            当前生效版本（运行时只读这些）
├── npc_dialogue.md
├── narrative_event.md
├── judge_persona.md
└── judge_coherence.md

prompt_lab/
├── candidates/
│   └── npc_dialogue/
│       ├── v1_baseline.md
│       ├── v2_explicit_constraints.md
│       └── v3_few_shot.md
├── scenarios/
│   └── npc_dialogue.yaml      固定测试场景集
└── reports/
    └── npc_dialogue_2026-08-03.md
```

`prompts/` 入库并纳入 git 历史，因此每次 prompt 变更都能被 diff 和回溯。`prompt_lab/reports/` 也入库——报告是"为什么选了这个版本"的证据。

## 4. 测试场景集

每个场景固定给出一组输入和期望约束，不含期望输出（那会退化成字符串比对）：

```yaml
# prompt_lab/scenarios/npc_dialogue.yaml
- id: refuse_when_trust_low
  input:
    npc: npc_a
    player_utterance: 你那天晚上看到了什么？
    world_snapshot: fixtures/trust_10.json
    visible_facts: [victim_name, disappearance_night]
  constraints:
    must_not_mention: [clue_1_witness, loren_that_night, npc_b]
    must_stay_in_character: true

- id: reveal_when_trust_high
  input:
    npc: npc_a
    player_utterance: 你那天晚上看到了什么？
    world_snapshot: fixtures/trust_55.json
    visible_facts: [victim_name, disappearance_night, clue_1_witness]
  constraints:
    should_mention: [clue_1_witness]
    must_not_mention: [loren_that_night]
```

场景集必须包含**约束冲突**的情况（NPC 想说但不允许说），因为那是 prompt 最容易失效的地方。

## 5. 评分维度

| 维度 | 类型 | 说明 |
|---|---|---|
| Schema 合法性 | 规则 | 输出能否解析成要求的 JSON，字段是否齐全 |
| 约束遵守率 | 规则 | `must_not_mention` 是否泄漏；命中即该场景 0 分 |
| Persona Consistency | judge | 复用 [08 §3.1](./08_Evaluation.md#31-persona-consistency) |
| 任务完成度 | judge | `should_mention` 的内容是否自然地表达了出来 |
| Token 成本 | 规则 | 平均输入/输出 token，用于在分数接近时取更便宜的 |

约束遵守率优先于所有质量维度：泄漏了不该说的信息，写得再好也是废的。

## 6. 可重现性要求

- 固定 `temperature`（judge 用 0，被测 prompt 用运行时的实际值）。
- 每个候选 × 每个场景跑 N 次（N≥3）取均值——单次采样的方差会大于候选之间的差距。
- 记录模型名、prompt 版本、`temperature`、N、日期到报告头部。
- judge 的 prompt 版本变化时，旧报告的分数作废，不可跨版本比较。

## 7. 实现约定

- 报告是 Markdown 文件，不做 Web 界面；对比表 + 每个候选的 2-3 条样例输出，足够人工判断。
- 不做自动 prompt 搜索/进化（如 APE、OPRO 那类）：候选由人写，机器只负责打分和对比。规模到几十个候选时再考虑。
- 场景集从 10 个起步，其中至少 3 个是约束冲突场景。
- 与 [07_Observability.md](./07_Observability.md) 的 Data Flywheel 衔接：Trace 里筛出的低分交互，应该被加工成新的测试场景补进场景集。这是让场景集随真实失败增长的机制，而不是一次性写完。
