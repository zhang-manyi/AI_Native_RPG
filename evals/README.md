# Minimal Eval

先读 [实验、指标与文件总览](OVERVIEW.md)：汇总全部已完成实验，解释指标、题库/脚本/结果文件，以及不同候选的实现区别。

新增 [受限表达证据实验](reports/2026-09-17-expression/review.md)：来源字段、公开投影与裁决约束的候选已实现，先固定输入对照，再做三次重复完整 Harness 留出。普通答案改善，但完成率、事实依据和拒绝覆盖仍不足，默认不启用。此实验与下述旧 6 轮冒烟套件分开计分；复核为助手复核，不是独立人工或校准 Judge。

独立的记忆实验入口为 `scripts/eval_memory.py`，数据、指标和运行约定见
[Memory evaluation](MEMORY.md)。下文仍描述原有 6 轮冒烟套件，不能把两套评测的分母混用。
2026-09-17 的检索留出结果、多轮证据使用和固定上下文重放见
[实验复核与采用决策](reports/2026-09-17-memory/review.md)。检索改善不等于完整 NPC 质量改善，证据透传候选仍未推广。

这是 3 组、共 6 轮的固定冒烟评测，入口为 `scripts/eval.py`。不包含 judge、自动搜索、画像或剧情扩展。
`cases.json` 同时保存输入、标注和 mock 脚本；mock 脚本不提供给真实模型。每组重置世界及玛尔塔 seed memory，组内沿用同一个世界和记忆。

## 运行

在项目根目录 PowerShell 中执行，输出目录必须不存在，防止覆盖旧证据：

```powershell
.venv\Scripts\python.exe scripts/eval.py --mode mock --variant baseline --output data/runtime/eval-mock-baseline
.venv\Scripts\python.exe scripts/eval.py --mode mock --variant candidate --output data/runtime/eval-mock-candidate
.venv\Scripts\python.exe scripts/eval.py --mode real --variant baseline --label baseline --output data/runtime/eval-real-baseline --max-requests 32 --max-seconds 240
.venv\Scripts\python.exe scripts/eval.py --mode real --variant candidate --label evidence-context --output data/runtime/eval-real-candidate --max-requests 32 --max-seconds 240
.venv\Scripts\python.exe scripts/eval.py --compare data/runtime/eval-real-baseline/report.json data/runtime/eval-real-candidate/report.json --output data/runtime/eval-comparison.md
```

真实模式读取现有 `.env` / 环境配置，缺少可用后端或启用 `USE_MOCK_LLM` 时明确记录不可运行，绝不回退为 mock 成绩。网络受限时需要运行环境许可。每次最多 32 个 HTTP 请求（含 JSON 重解析），每请求最多 600 输出 token、20 秒 HTTP 超时，无传输重试。240 秒预算在请求前检查，不是整轮硬实时截止；正在进行的 I/O 仍受 HTTP 超时约束。结构化输出本身仍允许一次重解析。

退出码 2 表示至少一个回合执行未完成；行为指标失败保留在 `failed_cases` 中，不等同于入口崩溃。完整回归仍由 pytest 承担。

## 指标、分母和证据

| 指标 | 分母与 ground truth | 缺失处理 / 边界 |
|---|---|---|
| completed_turns | 固定 6 轮；完成 respond 并得到结构化输出 | 错误和因前轮失败而跳过均为 0/1 |
| memory_recall_at_3 | 两次标注检索，共 3 个唯一 episode 标签：前轮反思新 ID 1 个、seed 中 sighting/threat 2 个；micro Recall@3 | 只测 Harness 首次 episodic 检索，不把 semantic 的另 3 条混入 K；缺检索记录或前轮未完成为 missing；已记录空列表为 0 |
| tool_execution | 实际 `tool_call` 数，`ok is True` 为成功 | 零调用为 0/0、值 null；缺 ok 为 missing；只验证执行/参数契约，不证明工具选择或回答正确 |
| required_tools | 样例明确要求的工具名称共 4 项，成功执行才命中 | 已完成却未调用为失败；回合失败为 missing；不以调用次数掩盖遗漏，也不声称语义参数完全正确 |
| literal_answer | 3 个需直接回答的回合：笔记名、公开姓名、修门闩 | 标注字符串全部命中才通过；同义表达可能误报，需人工核对，不是语义质量分 |
| literal_disclosure_guard | 2 个受约束回合的预先列出正则 | 检查低信任透露身份/威胁及失败结果台词；不使用 hidden fact 的任意字面值充当可靠判据；同义泄漏、虚构、说谎均可能漏检 |
| state_consistency | 每个完成回合的前后完整快照、proposal、裁决、是否有 applied id | 本组只允许本 NPC 对玩家的受限关系增量、对已公开事实的幂等重复披露；拒绝行动/无行动不得改变世界；忽略时间戳；不是所有游戏状态路径的通用证明 |
| classification | 2 个固定输入，gold 为 `help` / null | 只把可见选项给分类器；分类失败为 missing；这 2 条不足以验收自然语言分类质量 |

所有比率保留 numerator、denominator、missing；分母 0 时为 null。missing 不加入已观察分母，必须连同完成率一起看。没有标注的指标不适用，不报成通过。

额外 `environment_contract` 直接使用真实 Manager：拒绝从玛尔塔家非相邻直达森林，再经广场合法移动到森林；它是确定性契约，不计为模型能力。四个结局和完整 HTTP/Session 路径由现有回归保证。

## 可观测性与复现

- 现有 Trace 已有排序后的记忆 ID/文本、工具参数及 ok、行动裁决、最终台词。旧 Trace 没有完整世界快照，无法单独证明状态一致性；工具只有返回键名，无法验证返回内容；工具请求那次模型调用此前也漏记了成本。
- 本轮 Trace 补 `tool_request` 的模型/token/延迟、`tool_call.result`；既有 JSON schema 可容纳这些字段。仍不把旧 Trace 缺失字段当成成功。游戏 Trace 是开发者数据，工具结果包含 NPC 私有记忆。
- Eval 保存每轮前后世界、memory snapshot、gold ID、完整 response、Trace 文件、请求 prompt 哈希、模板/剧本/Harness 文件哈希、git HEAD、配置、实际返回模型标识。当前只有 HashingEmbedder(64)，不是语义 embedding 验收。
- Eval 的 HTTP transport 记录每次请求的状态、响应 message、finish_reason、usage 和延迟，包含失败 JSON 与重解析；不保存 URL、请求头或凭据。Trace 的成本不是重试总账，真实对比采用 HTTP 记录。缺 usage 为 null/计数，token 求和仅为已返回部分；mock token 为 null。
- 所有运行固定 temperature=0、N=1；没有 seed，服务端仍可能波动。报告不证明统计显著改善。已结算结果由 fixture 注入 Harness，分类与表达单独调用，不冒充完整 Web 真实模型验收。

## 2026-09-16 闭环

报告索引与人工复核见 [review.md](reports/2026-09-16/review.md)，正式数值见 [comparison.md](reports/2026-09-16/comparison.md)。

唯一行为候选：`Harness(retain_dialogue_evidence=True)` 在行动后的台词重写中保留当轮召回内容和工具返回，同时说明记忆不等于披露许可、裁决优先。`prompts/` 实际是三个 `.txt` 文件；这次改的是 Harness 的上下文组装规则，没有改模板文件。

默认 `retain_dialogue_evidence=False`，游戏保持基线行为。固定集未证明候选整体改善，因此不推广。`--variant candidate` 仅在 Eval 显式启用；契约测试证明它保留了证据，不证明生成质量更好。

评分规则修正可以只重算保存的证据，不产生模型调用，且不得覆盖原报告：

```powershell
.venv\Scripts\python.exe scripts/eval.py --rescore evals/reports/2026-09-16/baseline-final/report.json --output evals/reports/2026-09-16/baseline-final/rescored-again.json
```

下一轮优先人工标注这里的失败台词及同义表达，补少量对照，检查私有记忆在普通台词中的泄漏和虚构。不要因本轮所有规则检查通过就验收 persona、叙事连贯性、记忆质量或披露安全。

## 下一阶段：记忆与证据使用实验

下一轮只做两个相互独立的对照，暂不扩展 GUI、Browser、Multi-Agent 或训练链路：

1. **检索对照**：在相同记忆集、查询集、`top_k`、重要性权重和生成模型下，比较 `HashingEmbedder` 与可用的 Qwen3 Embedding。报告 Recall@3、MRR、无答案误召回率、端到端回答正确率，以及每回合 token、请求数和延迟。
2. **证据使用对照**：固定记忆命中、工具返回、计划和裁决，只比较最终台词上下文是否包含这些证据；随后再跑少量完整 Agent 序列。报告回答正确率、私有信息越权披露率、状态/角色约束违反率和成本。

每个案例应标注相关记忆 ID、期望答案、允许披露范围和是否必须调用工具。检索命中、证据进入 prompt、最终回答使用必须分别记录；“召回成功”不能替代“回答正确”。开发集用于选择候选，留出集只用于最终验证；真实模型条件至少重复 3 次，N=1 只能作为探索证据。

这里的自进化仅指**受控的离线策略优化闭环**：从失败 Trace 归因，生成少量候选上下文或 Prompt，使用固定 Eval 选择，再由人工决定是否采用。候选不得修改评分规则、ground truth 或权威世界状态；在评分体系稳定前，不让运行中的 NPC 自动修改代码、Prompt 或规则。
