# 工具决策与短任务评测

本入口是现有村庄能力的小规模固定评测，不增加工具或剧情。
结果和不采用决定见 [2026-09-18 报告](reports/2026-09-18-tools/review.md)，
预算/标准见 [预先协议](reports/2026-09-18-tools/protocol.md)。

## 运行与复算

```powershell
.venv\Scripts\python.exe scripts/eval_tools.py --split dev --variant baseline --output data/runtime/tools-dev-new
.venv\Scripts\python.exe scripts/eval_tools.py --split dev --variant candidate --output data/runtime/tools-candidate-new
.venv\Scripts\python.exe scripts/eval_tools.py --split holdout --variant baseline --output data/runtime/tools-holdout-new
.venv\Scripts\python.exe scripts/eval_tools.py --split holdout --variant candidate --output data/runtime/tools-holdout-candidate-new
.venv\Scripts\python.exe scripts/summarize_tools.py --root evals/reports/2026-09-18-tools --output data/runtime/tools-summary-new.json
```

运行目录必须不存在；真实模式使用当前 Settings，不回退 mock。`--offline` 仅检查
夹具和输出契约，记录缺失，不产生模型能力得分。入口固定三次重复、每块 120 请求/
300k token/600 秒请求前截止，每请求 600 输出 token/25 秒超时；重解析计预算。
连接失败熔断，不逐题重试。留出已用于最终验证，若用其失败调优必须换新留出。

题库 input 仅包含 NPC、玩家记忆前缀和问题；expect 是评分标签，不发送给模型。
固定行动仅在 mode=local 生效；mode=full 的行动完全由实际模型决定。
两个表达候选仍关闭；本次 candidate 只加载报告目录内的 planning 模板 overlay。

## 指标与缺失

每划分每条件 6 个完整任务 × 3 = 18 个任务，其中一项为连续两步移动；
完整回合分母 21。另有 2 个局部拒绝控制 × 3 = 6 输出。
两步任务第一步没有到达目标，第二步保留缺失，整个任务失败，不伪造前缀。

| 层 | 检查 |
|---|---|
| 工具选择 | 是否覆盖必要信息来源、是否调用无关工具；允许多种合理路径 |
| 参数 | 指定 fact_id/target_id；记忆 query 非空且实际找回目标 ID；私密查询语义单独复核 |
| 执行 | 正常返回、工具前后世界不变；known=false 仍可算执行成功，但不代表参数正确 |
| 行动选择 | 实际 proposal 是否为要求的 move/reveal；改成关系不算命中 |
| 裁决/状态 | 拒绝规则、applied id 和独立前后增量；合法状态不代表任务完成 |
| 实际拒绝覆盖 | 指定 action_type 与 target 真正提交并被拒 / 6；主动回避不算覆盖 |
| 任务成功 | 查询回答正确或状态目标达成、表达不矛盾、无虚假成功/披露；多步全通过 |
| 表达 | 答案、拒绝、无依据附加、错误成功、披露为逐条助手复核，有理由和台词哈希 |
| 成本/延迟 | 全部 HTTP（含失败与重解析）、token usage、回合耗时；无核实费率金额为 null |

每项保留分子/分母/missing。已失败的请求若仍有完整后状态和执行日记，可以验证状态；
未执行的依赖回合不能报状态通过。台词缺失不算隐私通过。
任务成功不要求工具次数多，也不把无依据附加合并隐藏：目标答案正确仍可能编造钟楼。

评测专用观察包装保留部分步骤、工具调用前后只读检查、每个 submit 的 proposal /
verdict / 快照。游戏运行时 Harness/Manager 没有新增写路径。
模型请求/响应仅保存 JSON body，不保存请求头、后端 URL 或凭据。

`review-labels-screen-draft.json` 与 `development-summary.json` 是早期未完成语义复核的
工作草稿，保留用于追溯，不能作为结论。最终 `review_labels.json` 来自逐条阅读后的
显式标注（`assisted_review.py`），`summary.json` 可离线复算；字符串匹配不充当语义真值。
