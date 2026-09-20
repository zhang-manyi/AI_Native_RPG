# 交接：真实路线完成，演示语义验收阻塞

HEAD：`5d3d51347900ed7c0f487e60096e965140e8c6b9`，本轮未commit/push、未reset/clean。
目录：`D:\Projects\AI_Native_RPG`。未发现适用AGENTS.md；没有调用子Agent。
继续工作前以本目录的review.md、report.json、wire.json和磁盘文件为准。

## 实际交付与工作区

- 新增 `scripts/demo_acceptance.py`：固定预算、实际玩家HTTP路由、真实模型网络记录、
  每步前后状态/事件、恢复检查点和运行源码快照。输出目录已存在时拒绝覆盖。
- 新增本报告目录：协议/路线、首次失败和授权后的完整运行、语义复核、完整玩家记录、
  离线复算脚本、测试输出及交接。
- 更新 `README.md`、`docs/16_Guided_Playthrough.md`：最短启动、真实复现命令、
  建议路线与展示能力、输入至状态链路、真实/规则/mock区别及限制。
- 在 `docs/09_Reference_Scenario.md`、`evals/OVERVIEW.md` 加入本次未通过验收的结论。
- 没有修改游戏运行时代码或prompt；修复轮数0。未运行预留变体，因为没有表达候选修复。

开始时已有改动全部保留：跟踪文件 `docs/08_Evaluation.md`、`docs/09_Reference_Scenario.md`、
`evals/OVERVIEW.md`、`evals/README.md`、`agent/expression.py`、`agent/harness.py`；
未跟踪的tools/answer-evidence题库、报告、脚本、表达模板、tool_evaluation和相关测试。
准确列表见 initial_manifest.json.status；最终列表见 final_status.txt。
898个既有报告文件哈希不变；运行时源码与真实运行快照一致。

## 命令与结果

预检使用 `git status --short`、`git rev-parse HEAD`、`rg --files` 查指引/入口，阅读
用户指定四份文档及README、.env.example、配置/Session/Harness/客户端/相关测试。
没有打印.env内容或密钥。

```powershell
# 首次沙箱运行：1次ConnectError；M1固定兜底未计真实成功
.venv\Scripts\python.exe scripts/demo_acceptance.py --route evals/reports/2026-09-18-demo-acceptance/route.json --output evals/reports/2026-09-18-demo-acceptance/initial-run
# 通过工具申请联网授权后执行：22步完整路线，truth_uncovered
.venv\Scripts\python.exe scripts/demo_acceptance.py --route evals/reports/2026-09-18-demo-acceptance/route.json --output evals/reports/2026-09-18-demo-acceptance/authorized-run

# 离线测试仅在该命令进程使用不存在的embedding路径，避免加载权重
$env:EMBEDDING_MODEL_PATH='D:\Projects\AI_Native_RPG\data\runtime\no-demo-test-weights'
.venv\Scripts\python.exe -m pytest tests/test_web_playthrough.py tests/test_reachability.py tests/test_endings.py tests/test_web_turn.py tests/test_web_move.py tests/test_web_wrap_up.py tests/test_web_input_forms.py tests/test_web_isolation.py tests/test_harness.py tests/test_trace_store.py -q --basetemp data/runtime/pytest-demo-acceptance
node --test tests/web_frontend.test.cjs

# 无网络复核（会再生成本轮verification.json和transcript.md，不碰历史报告）
.venv\Scripts\python.exe evals/reports/2026-09-18-demo-acceptance/verify.py
.venv\Scripts\python.exe -m ruff check scripts/demo_acceptance.py evals/reports/2026-09-18-demo-acceptance/verify.py
.venv\Scripts\python.exe -m ruff format --check scripts/demo_acceptance.py evals/reports/2026-09-18-demo-acceptance/verify.py
git -c core.whitespace=cr-at-eol diff --check
```

真实有效块：14请求、30,285 token、87.297秒；请求deepseek-v4-flash，返回deepseek-flash，
实际Qwen3-Embedding 256维；5条选项表达和3条普通自由对话，无表达兜底。
沙箱失败另计1请求，usage缺失，8,135 token预留。货币费用null。
真实运行的协议/输入/源码已冻结；复跑必须换新目录，不覆盖以上两个目录。
真实运行不要复制测试用的临时EMBEDDING_MODEL_PATH设置。

相关pytest：192项，190通过、2跳过；Node：8通过。测试输出由Tee-Object保存在tests.txt
及frontend-tests.txt；pytest多重-q未打印总计，另以--collect-only -o addopts=''确认192项。
未运行全仓库测试，没有运行时改动需扩展回归。初次开发lint发现长行/未用导入，已修正；
最终lint、格式、diff检查及离线复算结果见checks.txt。
启动脚本和验收脚本--help检查通过；Web应用实际启动/装配由真实TestClient运行覆盖，
未做浏览器视觉或真实TCP/SSE重连验收。弃用警告见原始测试输出。

## 验收结果和最小后续

规则到终局、非法移动拒绝后继续、两次恢复、终局操作拒绝通过；语义验收失败。
步骤05泄露尚未公开的洛伦登门关联并混淆时间，属于严重阻塞。步骤07答不出玩家已存
笔记本名，步骤11漏掉公开时间线并编造附加细节，作为非卡死质量限制保存。
完整复核见review.md、review_labels.json；不能把report.completed=true当语义通过。

只需先解决私有规划到公开表达的披露权限边界（含无行动分支），然后验证原路线与
协议中尚未运行的“河岸”变体。不要启用已否决候选、换模型/检索器或扩大新功能。
由于这已涉及核心表达边界，本轮按范围停止，不宣称demo已完成。
