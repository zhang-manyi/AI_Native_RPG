# 交接：公开表达边界已启用

HEAD：`5d3d51347900ed7c0f487e60096e965140e8c6b9`，本轮没有commit/push/reset/clean。
工作区：`D:\Projects\AI_Native_RPG`。原有未提交改动全部保留；初始清单见freeze.json，
最终清单见final_status.txt。没有写入用途背景、新增Agent、Judge、训练或剧情/UI功能。

## 当前行为与实际改动

`Settings.public_expression=True`默认接入真实Web和chat_demo。NPC私密知识保留在规划；
公开表达使用PlayerView、实际召回的同玩家原话和结构化行动结果，不复制规划reasoning、
strategy、私有背景/目标或私密工具原文。无行动也做独立表达。引导选项通过
ResolvedPublicOutcome只传已结算summary/reply，不把作者constraints送进表达模型。

本轮修改：

- `agent/expression.py`：新增公开结果契约及行动结果投影，复用已有证据来源逻辑。
- `agent/harness.py`：增加独立public_expression路径，三个旧实验开关继续关闭且互斥。
- `config.py`、`web/session.py`、`scripts/chat_demo.py`：真实入口接入和默认启用。
- `prompts/npc_public_expression.txt`、`tests/test_public_expression.py`：新模板与9项契约测试。
- `scripts/demo_acceptance.py`：显式实验开关、实际运行开关记录、收束视图记录、兜底检查。
- README、docs/06、docs/09、docs/16及evals/OVERVIEW：更新当前执行契约与有限验收结果。
- 新报告目录保存协议、冻结原路线/变体、源码选择、真实成败记录、复核、测试及本文件。

既有answer-evidence/tools/demo-acceptance报告以及此前未提交的候选代码、脚本、测试都保留。
1112个历史报告文件哈希不变。原失败结论没有覆盖，最新结果是后续一次修复验证。

## 验证与采用

实现前固定原22步路线和此前预留的河岸变体。只做一版行为修复，两个真实块源码一致，
没有看完变体后再调prompt。原路线和变体分别完成22步并到truth_uncovered，
两次运行共16条最终NPC台词，无作者兜底；四次恢复保持世界、全部NPC记忆和对话一致。
原话追问分别答出苔灯/河岸；未观察到原未公开登门泄露或虚报行动成功。

变体有一次模型截断，既有客户端重解析后完成，成本包含该请求。模型输入/输出、
工具、Trace、前后状态、存档、源码哈希全部在original-real/和variant-real/。
初次沙箱ConnectError留在original-sandbox/，授权后另开目录重跑，失败兜底不计真实成功。

成本：原路线14HTTP/29,187 token/69.875秒，变体14HTTP/26,236 token/65.891秒。
有效合计28HTTP/55,423 token；另1次连接失败，usage未知，4,949 token预留；货币金额null。
模型deepseek-v4-flash（返回deepseek-flash）、温度0.7、Qwen3-Embedding 256维、1200输出
上限，与上次有效整局相同。源配置没有换模型/检索器/行动策略；不把耗时差异当性能提升。

相关回归269通过、2跳过；Node 8通过；默认开启后的配置/接入36通过（与前项重叠）。
新增隔离测试还验证无行动、拒绝移动、拒绝/批准披露及故障后继续；这些分支的离线测试
不能当作本轮真实Agent拒绝覆盖。真实六次自由交流都提出关系调整。

在两条真实块通过后，只把Settings默认False改成True；真实块此前已显式True，
实际模型输入路径没有再次更改。唯一运行时差异及前后哈希见promotion.json。
直接构造Harness仍须显式public_expression=True，供历史评测保留旧默认行为；
交互mock脚本仍离线运行，不能用它证明新边界的语义质量。

## 主要命令（在项目根执行）

```powershell
# 首次失败块，原样保留；重复运行必须使用新目录
.venv\Scripts\python.exe scripts/demo_acceptance.py --public-expression --route evals/reports/2026-09-18-public-expression/route-original.json --output evals/reports/2026-09-18-public-expression/original-sandbox
# 获工具联网授权后执行的完整块
.venv\Scripts\python.exe scripts/demo_acceptance.py --public-expression --route evals/reports/2026-09-18-public-expression/route-original.json --output evals/reports/2026-09-18-public-expression/original-real
.venv\Scripts\python.exe scripts/demo_acceptance.py --public-expression --route evals/reports/2026-09-18-public-expression/route-variant.json --output evals/reports/2026-09-18-public-expression/variant-real

# 仅测试进程设置下列变量；不要用于复现真实块的检索配置
$env:EMBEDDING_MODEL_PATH='D:\Projects\AI_Native_RPG\data\runtime\no-public-expression-test-weights'
.venv\Scripts\python.exe -m pytest tests/test_public_expression.py tests/test_expression.py tests/test_grounded_rewrite.py tests/test_harness.py tests/test_harness_narrative_event.py tests/test_web_playthrough.py tests/test_web_turn.py tests/test_web_move.py tests/test_web_wrap_up.py tests/test_web_input_forms.py tests/test_web_isolation.py tests/test_web_scenario_agnostic.py tests/test_reachability.py tests/test_endings.py tests/test_event_chain.py tests/test_trace_store.py tests/test_config.py -o addopts='' -q --basetemp data/runtime/pytest-public-expression-regression --junitxml=evals/reports/2026-09-18-public-expression/pytest.xml
.venv\Scripts\python.exe -m pytest tests/test_public_expression.py tests/test_config.py -o addopts='' -q --basetemp data/runtime/pytest-public-expression-default --junitxml=evals/reports/2026-09-18-public-expression/pytest-default.xml
node --test tests/web_frontend.test.cjs

# 离线复核；仅重生成本报告的派生汇总/玩家实录，不覆盖真实原始数据
.venv\Scripts\python.exe evals/reports/2026-09-18-public-expression/verify.py
.venv\Scripts\python.exe -m ruff check src/ai_native_rpg/agent/expression.py src/ai_native_rpg/agent/harness.py src/ai_native_rpg/config.py src/ai_native_rpg/web/session.py scripts/chat_demo.py scripts/demo_acceptance.py tests/test_public_expression.py evals/reports/2026-09-18-public-expression/verify.py
.venv\Scripts\python.exe -m ruff format --check src/ai_native_rpg/agent/expression.py src/ai_native_rpg/agent/harness.py src/ai_native_rpg/config.py src/ai_native_rpg/web/session.py scripts/chat_demo.py scripts/demo_acceptance.py tests/test_public_expression.py evals/reports/2026-09-18-public-expression/verify.py
git -c core.whitespace=cr-at-eol diff --check
```

测试摘要/退出结果见pytest*.xml/txt、frontend.txt和checks.json。历史文件、运行源码快照、
输入来源、状态、成本及语义标签哈希校验见verification.json；凭据未写入记录。
不会自动提交、推送或启动常驻服务器。

## 展示边界与后续

本轮有限演示验收通过，可以展示引导路线及真实修复前后证据；不能称整个NPC自由对话
已经安全。时间线话尾含混、首次说记不住名字随后又记得、重复资料和规划不合理涨fear
均明确记录。原始规划仍可能编造，公开输入仍不能保证生成完全有据。
未做浏览器视觉/TCP SSE重连验收；未运行全仓库；模型权重未归档，远端输出不可逐字复现。

当前没有这两条路线的未说明严重阻塞。建议本轮停止调prompt和功能扩展；若遇到新严重
失败，先保存完整证据再定向修复。河岸变体已用过，后续若拿它调优，必须另固定新留出。
