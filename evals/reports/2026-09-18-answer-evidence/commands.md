# 命令与执行记录

根目录D:\Projects\AI_Native_RPG，PowerShell；HEAD 5d3d513。
首先git status --short确认并保留原工具评测/文档脏工作区。
已读用户指定八份文档及实际Harness、planning/dialogue/expression模板、
工具注册/返回、Manager、Validator、记忆投影和现有评测实现。

```powershell
.venv\Scripts\python.exe evals/reports/2026-09-18-answer-evidence/prepare_suite.py
.venv\Scripts\python.exe scripts/eval_answer_evidence.py --split dev --variant baseline --output evals/reports/2026-09-18-answer-evidence/dev-baseline
.venv\Scripts\python.exe scripts/eval_answer_evidence.py --split dev --variant baseline --output evals/reports/2026-09-18-answer-evidence/dev-baseline-authorized
.venv\Scripts\python.exe scripts/eval_answer_evidence.py --split dev --variant candidate --output evals/reports/2026-09-18-answer-evidence/dev-candidate
.venv\Scripts\python.exe scripts/eval_answer_evidence.py --split holdout --variant baseline --output evals/reports/2026-09-18-answer-evidence/holdout-baseline
.venv\Scripts\python.exe scripts/eval_answer_evidence.py --split holdout --variant candidate --output evals/reports/2026-09-18-answer-evidence/holdout-candidate
.venv\Scripts\python.exe evals/reports/2026-09-18-answer-evidence/assisted_review.py
.venv\Scripts\python.exe scripts/summarize_answer_evidence.py --root evals/reports/2026-09-18-answer-evidence --output evals/reports/2026-09-18-answer-evidence/summary.json
.venv\Scripts\python.exe -m pytest tests/test_grounded_rewrite.py tests/test_expression.py tests/test_tool_evaluation.py tests/test_tool_summary.py -q --basetemp data/runtime/answer-evidence-focused --junitxml evals/reports/2026-09-18-answer-evidence/pytest-focused.xml
$env:EMBEDDING_MODEL_PATH='D:\Projects\AI_Native_RPG\data\runtime\answer-evidence-offline\absent.gguf'
.venv\Scripts\python.exe -m pytest tests -q --basetemp data/runtime/answer-evidence-all --junitxml evals/reports/2026-09-18-answer-evidence/pytest-offline.xml
.venv\Scripts\python.exe -m pytest tests/test_answer_evidence_evaluation.py -q --basetemp data/runtime/answer-evidence-summary --junitxml evals/reports/2026-09-18-answer-evidence/pytest-summary.xml
.venv\Scripts\python.exe evals/reports/2026-09-18-answer-evidence/verify.py
git -c core.whitespace=cr-at-eol diff --check
```

prepare_suite.py和标注输出用x模式，运行目录拒绝覆盖；复现应换新路径或从sources
隔离恢复，不覆盖原始报告。候选选择文件在开发基线结束后、开发候选前生成，
哈希覆盖两个runtime文件、新模板、运行脚本及共享执行器。

过程记录：首次沙箱ConnectError保留1 HTTP/24缺失，申请网络授权后整块重跑；
后续沿批准的命令前缀执行。新增确定性测试先红：assemble_rewrite_evidence尚不存在
导致ImportError；实现后30项通过。原工具评测与汇总14项先通过。
首次lint发现评测CLI一行101字符和测试未使用变量；测试修正。
为保持实验源码哈希，CLI保留已冻结的101字符行；单独以line-length=101检查，
其余本轮运行时代码、汇总、测试、审阅/验证脚本用项目100字符规则。
格式检查不改写冻结源码。没有新增依赖、没有运行Browser/GUI或改UI。

离线verify开发时先误将Settings.api_key当字符串，改用SecretStr.get_secret_value，
未输出实际值；随后因内存tuple与JSON列表比较导致复算检查误报，统一JSON规范化后
精确相等。两次失败未写verification.json；没有增加模型请求或改变标签、原始结果。
verification.json仅包含最终通过的检查，原始失败说明在此保留。
