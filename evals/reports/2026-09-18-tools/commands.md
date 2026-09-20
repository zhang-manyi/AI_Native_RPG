# 本轮命令（项目根目录 PowerShell）

读取用户指定六份文档；检查 Harness、工具注册、schemas、Validator/Manager、Trace
及现有 evaluation/expression_evaluation/memory_evaluation。起始 git status --short 为空，
HEAD 为 5d3d513。未提交、未推送。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_evaluation.py -q --basetemp data/runtime/tools-eval-tests-1
.venv\Scripts\python.exe -m ruff check src/ai_native_rpg/tool_evaluation.py scripts/eval_tools.py tests/test_tool_evaluation.py --fix
.venv\Scripts\python.exe -m ruff format src/ai_native_rpg/tool_evaluation.py scripts/eval_tools.py tests/test_tool_evaluation.py
.venv\Scripts\python.exe -m pytest tests/test_tool_evaluation.py -q --basetemp data/runtime/tools-eval-tests-2 --junitxml=evals/reports/2026-09-18-tools/pytest-preflight.xml
.venv\Scripts\python.exe scripts/eval_tools.py --split dev --variant baseline --output evals/reports/2026-09-18-tools/dev-baseline
.venv\Scripts\python.exe scripts/eval_tools.py --split dev --variant baseline --output evals/reports/2026-09-18-tools/dev-baseline-authorized
.venv\Scripts\python.exe scripts/eval_tools.py --split dev --variant candidate --output evals/reports/2026-09-18-tools/dev-candidate
.venv\Scripts\python.exe scripts/eval_tools.py --split holdout --variant baseline --output evals/reports/2026-09-18-tools/holdout-baseline
.venv\Scripts\python.exe scripts/eval_tools.py --split holdout --variant candidate --output evals/reports/2026-09-18-tools/holdout-candidate
```

首次测试 9 通过/1 失败：标签隔离测试误将工具 JSON Schema 的 `required` 也视为
评分标签，收紧为实际评分字段后 10 通过。首次 lint 报长行，format 后 lint 通过。
这些都发生在数据/预算冻结及真实生成前，不涉及模型结果筛选。

沙箱基线一次 ConnectError 后停止请求，其余计划行保留缺失。随后按环境流程授权
完整三次基线重跑；另外三块分别按工具流程授权。没有补跑截断或任务失败样本。
真实入口 exit 2（PTY 报 exit 1 的包装表现也出现）表示 partial：依赖失败或真实调用失败，
不是可以忽略的成功。每块原始 report/wire 内有所有计划行与实际请求。

预算及生成配置硬编码在冻结源码；重现请使用新的输出目录。原目录拒绝覆盖。
每块 sources 保存运行时的完整 Python 源码、模板、场景、题库、协议和哈希；
候选仅有一个模板 overlay。

复核脚本开发时出现两个离线错误：JSON 的列表与内存 tuple 比较误报评分漂移，
以及缺少私密 query_memory 的逐条语义标签。均为离线汇总契约问题，修正后
仍核验冻结结构分数相等；无新增模型请求。开发 provisional 汇总保留，最终逐条
语义复核见 review_labels.json（附审阅理由），最终结论用 summary.json。

后续复核/验证命令：

```powershell
.venv\Scripts\python.exe scripts/summarize_tools.py --root evals/reports/2026-09-18-tools --output evals/reports/2026-09-18-tools/summary.json
$env:EMBEDDING_MODEL_PATH='D:\Projects\AI_Native_RPG\data\runtime\tools-offline-20260918\absent.gguf'
.venv\Scripts\python.exe -m pytest tests -q --basetemp data/runtime/tools-final-tests --junitxml=evals/reports/2026-09-18-tools/pytest-offline.xml
.venv\Scripts\python.exe -m pytest tests/test_tool_evaluation.py tests/test_tool_summary.py -q --basetemp data/runtime/tools-summary-tests --junitxml=evals/reports/2026-09-18-tools/pytest-summary.xml
node --test tests/web_frontend.test.cjs
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
git -c core.whitespace=cr-at-eol diff --check
.venv\Scripts\python.exe evals/reports/2026-09-18-tools/verify.py
```

全量942通过/6跳过；之后新增4个汇总契约，相关14项均通过。前端8项通过。
初次全仓库lint发现新助手标注脚本长行，已格式化/拆分字符串；audit独立入口的
源码路径引导被明确标注E402例外，原有文件与历史sources未改。最终检查见 validation.json。
需要重跑时换输出JSON/XML/临时目录名，避免覆盖现有证据。
