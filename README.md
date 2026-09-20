# AI Native RPG

[![CI](https://github.com/zhang-manyi/AI_Native_RPG/actions/workflows/ci.yml/badge.svg)](https://github.com/zhang-manyi/AI_Native_RPG/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230)](https://github.com/astral-sh/ruff)

一个 LLM 驱动的 RPG Agent 系统：可交互的 NPC Agent 运行时 + 确定性世界状态层 + 完整的 Trace/Eval 闭环。

场景是一起村庄失踪案：玩家自由对话调查，NPC 在人设约束下决定说什么、隐瞒什么，线索随关系值和调查进度逐步解锁。

## 核心设计主张

**不按功能分层，而按是否需要 LLM 分层。**

| 类型 | 内容 | 特征 |
|---|---|---|
| Deterministic | 世界状态、校验、信息投影、排序 | 纯代码，可单测，延迟 <10ms |
| Agent | 记忆检索、规划、工具调用、对话生成 | 依赖 LLM，输出不确定，需要 Eval |
| Observability | Trace、叙事状态面板、Eval | 消费前两者的产出，不在主流程 |

引导选项通常只需一次表达调用；自由对话还可能包含分类、工具续轮、行动后重写和重解析。
调用数与耗时以 Trace 和 HTTP 记录为准，世界状态、检定和排序由确定性代码处理。

三条由此展开的具体结论：

- **世界状态有唯一事实源。** 没有任何 Agent 能直接写世界状态，全部变更走 `Action Proposal → Validator → State Update`。这不是约定而是代码强制：`WorldState` 实例私有，读取返回深拷贝。
- **信息不对称是数据 + 纯函数，不是一个 Agent。** `Fact.visibility` + `reveal_condition` + `PlayerView()` 投影，省掉一整层 LLM 调用。条件是结构化对象而非字符串表达式——后者要么需要表达式解析器，要么用 `eval()`。
- **Action 校验在生成台词之前。** 否则台词已经说出口、Validator 才拒绝该行动，玩家听到的和世界状态不一致，且无法回滚。

## 状态

切片 1-3 已完成：World State Manager（含剧本包加载与交叉引用校验）+ NPC Agent Harness 端到端链路（Memory 检索 → Tool Use → Planning → Validator → Dialogue → Reflection）+ 真实 DeepSeek 客户端与 Function Calling + Narrative Engine（4 个叙事算子、伏笔账本、节奏准入），Trace 落盘可查。当前进度见 [docs/09_Reference_Scenario.md](docs/09_Reference_Scenario.md) 的切片表。

## 快速开始

```bash
uv venv && uv pip install -e ".[dev]"
python -m pytest        # 全部测试，不需要 API key，零网络请求
```

跑一轮真实终端对话（Web 和真实验收脚本也会联网）：

```bash
cp .env.example .env                   # 填入 DEEPSEEK_API_KEY
python scripts/chat_demo.py            # 和玛尔塔（npc_a）说话
python scripts/chat_demo.py --mock     # 离线脚本，没有 key 也能看完整链路
python scripts/chat_demo.py --npc npc_b   # 换成猎人洛伦
```

### Web 界面（推荐）

```bash
pip install -e ".[dev,web]"
python scripts/web.py                  # http://127.0.0.1:8000
python scripts/web.py --mock           # 离线
python scripts/web.py --no-dev         # 只有玩家视图，/debug 不挂载
```

Windows 已有 `.venv` 时的最短真实启动：

```powershell
# .env: LLM_PROVIDER=deepseek, DEEPSEEK_API_KEY=<实际密钥>
# DEEPSEEK_MODEL=deepseek-v4-flash, USE_MOCK_LLM=0
.venv\Scripts\python.exe scripts/web.py --no-dev
```

打开 `http://127.0.0.1:8000`，新建村庄失踪案会话；有存档可选择继续。
启动横幅必须显示真实供应商与模型；缺少可用密钥时普通 Web 会使用离线模式。
验收脚本则拒绝缺少真实配置的运行。可选 embedding 的实际后端以页头/验收记录为准；
安装了语义检索依赖但未指定本地权重时，首次启动可能下载权重，不能保证离线。

2026-09-18 修复私有规划到公开台词的输入边界后，原路线和事先冻结的变体均经真实
玩家接口到达 `truth_uncovered`，通过本轮有限演示验收。真实Web和终端入口默认使用
新边界；无行动也独立生成公开台词，不再直接展示私有规划草稿。
共28次真实请求、55,423 token；保留措辞生硬、时间线解释含混及一次重解析的限制。
这不是全部自由对话安全性的保证。见[修复与完整证据](evals/reports/2026-09-18-public-expression/review.md)，
此前[失败实录](evals/reports/2026-09-18-demo-acceptance/review.md)原样保留。

场景页面支持固定对白、自动旁白、关键选项和可选的自由对话，右侧是开发者面板。
无需自由打字即可走完整个调查；操作路线、结局条件和当前范围见
[引导式调查](docs/16_Guided_Playthrough.md)。Mock 模式的固定流程不调用模型；真实后端
在关键选择结算后生成 NPC 回复，自由输入仍可调用完整 Agent。
和终端版最重要的区别是**叙事 tick 不再压在玩家等待上**：台词一就绪就推送，
叙事结果（实测 ~8.6s）随后单独到达，面板同步刷新。传输用 SSE，一条流承载
台词 / 叙事 tick / 场景 / 面板四类事件，`curl -N` 可直接看。设计见
[docs/12](docs/12_Web_Interface.md)。

⚠️ 这个服务**没有鉴权**，绑 loopback，`/debug/*` 暴露完整 `WorldState`（含全部隐藏线索
和凶手是谁）。不要暴露到 `0.0.0.0` 或反代出去。

每回合会打印完整决策链：检索到几条记忆、调了哪些工具、计划与策略、行动校验结果、
信任值变化、几次 LLM 调用、token 与延迟、Trace 落盘路径；以及这一轮触发了哪个叙事算子、
哪些候选被节奏规则挡下。

三个面板命令把叙事结构变成看得见的东西：

```
/beats     章节、张力、算子时间线
/ledger    伏笔账本：埋于第几轮、回收条件、已欠几轮、是否超期
/unlock    解锁进度板：每条 hidden fact 逐 clause 显示当前值与阈值
```

`/unlock` 回答的是"为什么玩家还看不到 X"——调阈值本来全靠猜（40 是不是太高？玩家会不会
永远卡住），有它就有依据。这三个是开发者视角，故意绕开 `PlayerView` 直读世界状态；
玩家接口只走 `PlayerView`（[docs/07 §2.4](docs/07_Observability.md)）。

模型是 `deepseek-v4-flash`（OpenAI 兼容端点）。`.env` 已在 `.gitignore` 中，不会入库。
自动化测试一律走 mock client，`tests/conftest.py` 里的 autouse fixture 强制
`USE_MOCK_LLM=1`，因此即使本机有真 key 也不会发出请求。

### 语义检索（可选）

默认用 `HashingEmbedder`——它只匹配字面，"那晚你看到什么" 和 "失踪当夜我目击了有人
返回村庄" 几乎不共享字符，该召回的记忆召不回来。换成真模型才有语义检索：

```bash
# 方式一：GGUF（量化，不需要 torch）
pip install llama-cpp-python
export EMBEDDING_MODEL_PATH=/path/to/Qwen3-Embedding-0.6B-Q8_0.gguf

# 方式二：HuggingFace 权重（会拖 torch，约几百 MB）
uv pip install -e ".[embedding]"
```

后端由文件扩展名决定（`.gguf` 走 llama.cpp，否则走 sentence-transformers）。两种都没装
时自动回退到 `HashingEmbedder`，demo 的页头会写明当前用的是哪个。

### Windows / WSL 注意

`.venv` 是 Windows 布局（`Scripts/`，不是 `bin/`），WSL 下可以直接调
`./.venv/Scripts/python.exe`。但**环境变量不会跨过 WSL 到 Windows exe 的边界**：
`USE_MOCK_LLM=1 ./.venv/Scripts/python.exe ...` 里的变量到不了 Python，要么用
`WSLENV=USE_MOCK_LLM` 传递，要么直接用 `--mock` 参数。demo 脚本会把 stdio 强制成
UTF-8，否则 Windows 控制台默认的 GBK 编不出剧本里的中文。

## 结构

```
src/ai_native_rpg/
├── schemas/            Schema as Code（Pydantic，唯一事实源）
├── world/              World State Manager [确定性]
│   ├── conditions.py   reveal_condition 求值器
│   ├── validator.py    规则列表（非规则引擎）
│   ├── player_view.py  信息不对称投影
│   └── manager.py      唯一写入口
├── scenario.py         剧本包加载 + 校验（世界 / persona / 初始记忆）
├── config.py           环境变量 → Settings → LLMClient（密钥只在这里）
├── llm/                LLMClient Protocol + DeepSeek / Mock 两个实现
├── narrative/          Narrative Engine + 叙事算子
│   ├── rules.py        触发规则：现在允许发生什么 [确定性]
│   ├── controller.py   张力准入 + 偏好排序（两段，不相乘）[确定性]
│   └── engine.py       算子槽位填内容 → Action Proposal [1 次 LLM]
├── agent/              NPC Agent Runtime [Agent]
│   ├── harness.py      运行时循环（含 Tool Use 工具循环）
│   ├── tools.py        3 个只读工具 + 注册表
│   ├── memory_store.py 检索 / 更新 / 遗忘
│   └── embedding*.py   Embedder Protocol：哈希 与 Qwen3
├── player/             Player Model
├── observability/      Trace / Eval
│   ├── trace_store.py  Trace / NarrativeTick 落盘
│   └── panels.py       面板判定（纯函数）：终端与 Web 共用一份
└── web/                Web 界面 [Observability]
    ├── session.py      装配 + 每会话单线程执行器（世界只有一个写入者）
    ├── events.py       事件模型 + SSE 封帧
    ├── routes_player.py  /api/*    只接受 VisibleState
    ├── routes_debug.py   /debug/*  直读 WorldState，非 dev 不挂载
    └── static/         无构建步骤：一个 ES module + 一份 CSS + 内联 SVG 立绘

prompts/                运行时只读的 prompt 文件，不在代码里内联
scenarios/<name>/       剧情内容（YAML），换剧本不改代码
scripts/chat_demo.py    手动跑一轮对话，打印完整决策链
scripts/web.py          Web 界面入口（场景页面 + 开发者面板）
docs/                   架构设计（01-11）
```

## 文档

| 文档 | 内容 |
|---|---|
| [01 System Architecture](docs/01_System_Architecture.md) | 分层原则与模块职责 |
| [02 Sequence Diagram](docs/02_Sequence_Diagram.md) | 一次交互的完整时序、LLM 调用点、延迟预算 |
| [03 Player Model](docs/03_Player_Model.md) | 实时行为统计 + 周期性语义摘要 |
| [04 World State Manager](docs/04_World_State_Manager.md) | 事实源、Validator、信息不对称 |
| [05 Narrative Engine](docs/05_Narrative_Engine.md) | 触发规则 + LLM 生成 + Experience Controller |
| [06 NPC Agent Spec](docs/06_NPC_Agent_Spec.md) | Memory / Planning / Tool Use / Dialogue / Harness |
| [07 Observability](docs/07_Observability.md) | Trace Viewer、叙事状态面板、数据回流 |
| [08 Evaluation](docs/08_Evaluation.md) | 一致性、记忆召回、工具成功率、叙事结构指标 |
| [09 Reference Scenario](docs/09_Reference_Scenario.md) | 场景设定、切片计划、测试策略 |
| [10 Narrative Operators](docs/10_Narrative_Operators.md) | 叙事结构调度、伏笔账本、张力与偏好分离 |
| [11 Prompt Lab](docs/11_Prompt_Lab.md) | Prompt 离线实验：候选对比、评分、选定 |
| [12 Web Interface](docs/12_Web_Interface.md) | Web 界面：场景页面 + 开发者面板、SSE、叙事 tick 异步化 |
