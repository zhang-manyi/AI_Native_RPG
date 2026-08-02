# 06. NPC Agent Specification

## 1. 定位

这是整套系统里**唯一真正的 LLM Agent**（Narrative Engine 的生成部分是"LLM 辅助"，不算完整 Agent）。系统能力的核心投入应该集中在这里：Memory Retrieval（RAG） / Planning / Tool Use（Function Calling） / Dialogue Generation / Reflection / Action，配合 Developer Platform 的 Trace 可视化。

## 2. Agent Specification

按照"每个 Agent 定义 Goal/Input/Output/Tools/Memory/Constraints/Evaluation"的规格模板：

### Goal
维持角色一致性（persona）的同时，推进自身目标（如"隐藏秘密"），并对玩家行为做出合理反应。

### Input
```
Observation:       玩家的对话/行动
PlayerView:        该玩家当前可见的世界信息（来自 World State Manager）
NPCState:          自身 persona / goal / emotion / belief / relationship
Memory:            检索到的相关记忆（episodic + semantic + relationship）
```

### Output
```
Plan:              内部推理，如 "回避直接回答, 转移话题"
Dialogue/Action:   实际输出给玩家的台词或行动
Action Proposal:   如果行动会改变世界状态，提交给 World State Manager 校验
```

### Tools（Function Calling）
NPC 通过 Function Calling 调用工具，模型输出结构化的工具调用请求（`{tool_name, arguments}`），由 Agent Runtime 执行后把结果传回模型，示例：

| Tool | 用途 |
|---|---|
| `query_relationship(npc_id)` | 查询与某角色的关系值 |
| `query_memory(query, top_k)` | 检索长期记忆（见下方 RAG） |
| `propose_action(action_type, payload)` | 向 World State Manager 提交行动 |
| `check_world_fact(fact_id)` | 查询自己可见范围内的世界事实 |

工具集初期控制在 3-4 个以内，够验证 Function Calling 的调用链路即可，不追求覆盖面；后续按需扩展工具而不改变调用协议。

### Memory（RAG）
三层结构，见 [schemas/memory.py](./schemas/memory.py)：

- **Working Memory**：当前对话上下文，短期，不持久化。
- **Episodic Memory**：具体事件，带 `importance` 和 `emotion` 字段，用于衰减和检索排序。Agent 私有存储。
- **Semantic Memory**：抽象事实/信念，如"玩家讨厌被欺骗"。Agent 私有存储。
- **Relationship Memory**：对每个其他角色的量化关系（trust/fear/respect）。**只读投影，不是 Agent 私有存储**——关系值的事实源在 World State，此处只是把它拼进 prompt 的视图，写入必须走 Action Proposal。见 [04_World_State_Manager.md](./04_World_State_Manager.md#21-状态归属边界客观事实-vs-主观认知)。

检索（Retrieval-Augmented Generation）流程：把 Observation 编码成 embedding，在该 NPC 的记忆库中做相似度检索，取 Top-K 结果并按 `importance` 加权重排，再拼进 Planning/Dialogue Generation 的 prompt。不需要每次全量扫描，也不需要引入完整的向量数据库——记忆规模是"单个 NPC 的记忆条数"，内存中的向量索引（如简单的 cosine 相似度线性扫描）就足够，规模增长后再迁移到专用向量库。

### Constraints
- 不能直接修改 World State，所有行动必须走 Action Proposal。
- Persona 中的核心特质（如"诚实"）不能被 prompt 随意覆盖——用 system prompt 中的固定 persona 段落 + 规则校验（如 Reflection 阶段检查输出是否违反 persona）。
- 单次响应延迟目标 <3s（见 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md)）。

### Evaluation
见 [08_Evaluation.md](./08_Evaluation.md)：Persona Consistency（LLM-as-judge）、Memory Recall@K、Tool Use Success Rate。本 Agent 使用的 prompt 经 [11_Prompt_Lab.md](./11_Prompt_Lab.md) 的流程选定。

## 3. Dialogue Generation

这是 NPC Agent 里"把系统状态说出来"的关键一环，也是玩家真正能感知到的输出。它消费三类输入，生成最终展示给玩家的台词：

```
Input:
  persona          NPC 固定人格（见 NPCPersona）
  emotion/belief   当前情绪与信念状态（NPCState）
  plan             Planning 阶段的内部推理结果（如"回避直接回答"）
  narrative_event  Narrative Engine 生成的结构化剧情内容（若本轮有触发事件）

Output:
  dialogue         符合人设、情绪状态与当前剧情进度的自然语言台词
```

例如 Narrative Engine 产出 `{"who_betrays": "NPC_B", "how": "向敌对阵营通风"}` 这样的结构化事件后，Dialogue Generation 的职责是把这条尚未直接告知玩家的世界事实，结合 NPC 当前对玩家的信任值和 persona，转成一句"欲言又止但没有直接说出真相"的台词，而不是把 JSON 原样念出来。

这一步始终用 LLM（结构化状态 → 自然语言是典型的生成式任务）。Planning 阶段可以尝试规则简化，但 Dialogue Generation 不建议用模板字符串代替——正是这一步决定了 NPC 是否"像在说话"而不是"像在读配置"。

### 与 Planning 的关系

Dialogue Generation 与 Planning 是否合并成一次 LLM 调用（结构化输出 `{plan, dialogue}`）取决于本回合是否涉及世界状态变更：

- **不涉及 Action**：合并成 1 次调用，省一次网络往返。
- **涉及 Action**：必须拆成 2 次，因为 Validator 的校验结果是 Dialogue Generation 的输入约束。

判断走哪条路径由第一次调用的输出决定（`plan.action_proposal` 是否为空），完整取舍见 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md#42-何时可以合并成一次-llm-调用)。

## 4. Agent Harness

上面各个子模块（Memory Retrieval / Planning / Tool Use / Dialogue Generation / Reflection）不是各自独立调用的，而是由一个统一的 Harness（运行时循环）编排：组装 prompt/context、决定何时调用模型、执行模型返回的工具调用、把结果喂回模型、直到得到最终输出。这个 Harness 是所有 NPC 共享的基础设施，NPC 之间的差异只体现在 persona/goal/工具集/记忆库的不同，而不是各自实现一套调用逻辑。

```
Observation
    |
    v
Memory Retrieval (RAG)   (embedding 检索, 确定性代码)
    |
    v
Planning                 (LLM 调用 #1, 或与下一步合并)
    |
    v
Tool Use (Function Calling, 可选)  (如需要查询关系值/世界事实，由 Harness 执行并把结果传回模型)
    |
    v
Dialogue Generation      (LLM 调用, 输出符合人设与剧情的台词)
    |
    v
Action Proposal -------> World State Manager (校验)   ★ 在 Dialogue Generation 之前
    |
    v
Dialogue Generation      (LLM, 以校验结果为约束生成台词)
    |
    v
Reflection (可选)        (事后更新 Agent 私有 Memory/Emotion，异步)
```

注意 Harness 里 Action 校验位于 Dialogue Generation **之前**，理由见 [02_Sequence_Diagram.md](./02_Sequence_Diagram.md#41-action-校验必须在-dialogue-generation-之前)。上面第 3 行的 Tool Use 是模型主动查询信息，与之无关。Reflection 只写 Agent 私有状态（emotion/beliefs/episodic memory），不写 World State，因此无需走 Validator，但要落 Trace 以便调试。

### Skills（角色能力包，后续扩展）

当 NPC 数量增多、角色分化（如"调查线索型 NPC" vs "交易/商人型 NPC"）时，可以把"这类角色该有哪些工具 + 该遵循哪些行为准则 + 该用什么语气"打包成一个 Skill（persona 模板 + 工具子集 + few-shot 示例的组合），按 NPC 的角色类型加载到 Harness 里，而不是每个 NPC 都单独维护一份完整配置。当前场景（[09_Reference_Scenario.md](./09_Reference_Scenario.md)）只有 1-2 个 NPC，不需要这一层抽象；NPC 数量增长到需要复用角色模板时再引入。

## 5. LLM Client

Harness 通过一个 Protocol 访问模型，不直接依赖任何 SDK：

```python
class LLMClient(Protocol):
    def complete(
        self, messages: list[Message], *, schema: type[BaseModel], temperature: float
    ) -> LLMResponse: ...
```

两个实现：`DeepSeekClient`（OpenAI 兼容接口）和 `MockLLMClient`（返回预设的结构化输出）。测试一律用 mock，**不发任何网络请求**，因此 Harness 的循环、JSON 解析、重试、超时都可以单测。这也是后续 Model Router 的挂点。

### 配置

| 变量 | 值 | 用途 |
|---|---|---|
| `DEEPSEEK_API_KEY` | `sk-...` | 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 对话/剧情生成 |
| `JUDGE_MODEL` | `deepseek-v4-flash` | LLM-as-judge 打分 |
| `USE_MOCK_LLM` | `0` / `1` | 置 1 时完全不联网 |

放在项目根的 `.env`，**已加入 `.gitignore`，不入库**。仓库里只有 `.env.example` 作为模板：

```bash
cp .env.example .env   # 然后填入自己的 key
```

密钥只从环境变量读取，不写进任何 Python 文件、不作为函数默认参数、不打印进 Trace（Trace 里只记模型名和 token 数）。

## 6. 数据结构

见 [schemas/npc_agent.py](./schemas/npc_agent.py) 和 [schemas/memory.py](./schemas/memory.py)。

## 7. 实现约定

- Reflection 做成异步、低频（每次对话后更新一条 episodic memory 即可），不做复杂的自我批评循环。
- Tool Use 的工具集固定为 3-4 个，不做动态工具发现。
- 初期只实现 1-2 个 NPC，不做 Faction Agent / Event Agent（架构上可复用同一套 Runtime，作为后续扩展）。
- Prompt 文件放在 `prompts/`，由 [11_Prompt_Lab.md](./11_Prompt_Lab.md) 的流程选定；Harness 只读文件，不在代码里内联 prompt 字符串。
