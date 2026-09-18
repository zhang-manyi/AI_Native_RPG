# 可复现命令

在项目根目录 PowerShell 运行。重跑时改用全新输出路径；下列路径为本次已经保存的证据，不可覆盖。真实生成读取现有配置，不提供 mock 回退。三组必须依次执行；开发后先冻结候选。

```powershell
.venv\Scripts\python.exe scripts/eval_expression.py --stage dev --output evals/reports/2026-09-17-expression/dev-authorized --repeats 3 --max-requests 48 --max-tokens 150000 --max-seconds 600
.venv\Scripts\python.exe scripts/eval_expression.py --stage local --output evals/reports/2026-09-17-expression/holdout-local --repeats 3 --max-requests 56 --max-tokens 150000 --max-seconds 600
.venv\Scripts\python.exe scripts/eval_expression.py --stage full --output evals/reports/2026-09-17-expression/holdout-full --repeats 3 --max-requests 180 --max-tokens 450000 --max-seconds 1200
.venv\Scripts\python.exe scripts/summarize_expression.py --root evals/reports/2026-09-17-expression --output evals/reports/2026-09-17-expression/summary-reproduced.json
```

首次 dev 使用 `.../dev`，沙箱内网络被拒；获授权后完整重跑到 `.../dev-authorized`，保留失败组，不混算模型成绩。全部调用硬上限600输出token、25秒HTTP超时、无transport重试；JSON一次重解析计入请求数。累计token/时间在下一请求前检查，在途调用可越过该累计界限；未知usage不能用于金额估计。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_expression.py tests/test_expression_evaluation.py -q --basetemp .pytest-expression-unit3
$env:EMBEDDING_MODEL_PATH='D:\Projects\AI_Native_RPG\data\runtime\models\Qwen3-Embedding-0.6B'
$env:HF_HUB_OFFLINE='1'
$env:OMP_NUM_THREADS='4'
$env:MKL_NUM_THREADS='4'
.venv\Scripts\python.exe -m pytest tests/ -q --basetemp .pytest-expression-regression-local --junitxml=evals/reports/2026-09-17-expression/pytest.xml
.venv\Scripts\python.exe evals/reports/2026-09-17-expression/verify_offline.py
.venv\Scripts\python.exe -m ruff check src/ai_native_rpg/agent/harness.py src/ai_native_rpg/agent/expression.py src/ai_native_rpg/agent/memory_store.py src/ai_native_rpg/agent/tools.py src/ai_native_rpg/schemas/memory.py src/ai_native_rpg/expression_evaluation.py tests/test_expression.py tests/test_expression_evaluation.py scripts/eval_expression.py scripts/summarize_expression.py
node --test tests/web_frontend.test.cjs
git -c core.whitespace=cr-at-eol diff --check
```

全仓库检查保留失败；`verify_offline.py` 仅在测试进程显式替换 Web registry 的 embedding factory 为 Hashing，保持真实 HTTP/SSE/存档/Harness/Manager 与断言，用于隔离本地权重加载时间，未改变实际游戏自动检测配置。重跑该辅助脚本也应先改用新的 basetemp 和 XML 输出名，以保留原结果。
