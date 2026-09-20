# 公开表达边界：两条真实路线通过有限验收

修复此前“私有规划说漏秘密、关系调整获批被当成披露许可”的通道。原22步路线和
实现前冻结的“河岸”变体各完成一次真实游戏，均到`truth_uncovered`；16条最终NPC台词
全部可用、无作者兜底。助手逐条复核未观察到未授权案件披露、严重案件编造或虚报执行成功。
“苔灯”与“河岸”回述均正确；时间线回应仍有含混和回避，详见下方限制。

**采用新public_expression路径作为真实Web/终端默认行为。** 这是两条引导路线的
有限验收，不是开放域安全结论。三个旧候选仍关闭，没有添加LLM审查、训练或新的Agent。
只实施一版行为修复，未根据变体调优；没有commit/push。

## 具体修复及旧实现的区别

此前失败见[原实录](../2026-09-18-demo-acceptance/review.md)：私密种子记忆进入规划，
规划主动想说出洛伦登门情节，实际提出的仅是关系调整。表达输入复制了私密reasoning，
再用“无被拒约束”描述获批行动，最终台词披露。没有reveal_fact请求，披露条件校验
自然没有拦截这句话。无行动直接使用私有草稿也是同类出口。

本次复用了旧`assemble_expression_evidence`来源投影，不声称这套投影是新发明。
旧filtered候选已经隔离部分私有信息，但只有粗粒度status，且已结算路径仍拼接调用方
传入的summary/reply/constraints混合文本。本次变化是：

- 新开关`public_expression`覆盖普通无行动、有行动和引导表达；旧开关及历史语义不变。
- 表达只读公开姓名/介绍/性格、当前问题、已召回同玩家原话、裁决后的PlayerView和
  结构化结果。不传raw reasoning/strategy、私密persona、semantic belief、NPC旧回复或
  任意工具文本；query_memory仅以成功返回ID回查来源字段，沿用既有来源规则。
- 新`public_action_result`只传动作类型、批准/拒绝及公开实际结果。关系数值来自真实
  Manager状态；披露值来自当前PlayerView。被拒目标ID、私有理由、任意payload不透传；
  未知action名称统一unsupported，不把名字本身变成秘密通道。
- `ResolvedPublicOutcome`明确区分作者的已结算summary/reply与私密constraints。
  Web先结算再传公开对象，表达缺少该对象时失败关闭，不退回私有prompt。未修改剧情内容。
- 新模板明确普通问题应答、原话来源、转述归属、动作授权范围和未知时序不得补推；
  私有规划/检索/工具/行动选择的prompt和策略不变，没有强迫模型选指定工具。
- `Settings.public_expression=True`用于真实Web/终端；直接Harness默认保持旧行为供
  历史评测兼容，新调用者需显式开启。mock游戏仍使用原离线脚本，新路径有独立契约测试。

没有重建通用记忆权限系统：无来源旧记忆不推断许可，已解锁知识从世界投影获得。
旧自由文本剧情hook缺少公开来源契约，因此新表达不直接读取；当前引导路线不依赖它。
这是一项表达边界取舍，未宣称所有旧生成式叙事场景均验收。

## 协议、运行及完整证据

[协议](protocol.md)、[原路线](route-original.json)、[冻结变体](route-variant.json)及
[初始哈希](freeze.json)先于代码修改；[候选源码选择](selection.json)先于有效真实生成。
题目与评分标签不进入模型输入。当前玩家选项、问题和游戏证据正常进入应用，报告标签
在运行后另存。语义结论全部为**Codex助手复核**，不是关键词检查或校准人工Judge。

| 运行 | HTTP | 输入/输出/总token | 整局秒数 | 结果 |
|---|---:|---:|---:|---|
| 原路线 | 14 | 23,577 / 5,610 / 29,187 | 69.875 | 22步完成、truth_uncovered |
| 河岸变体 | 14 | 21,385 / 4,851 / 26,236 | 65.891 | 22步完成、truth_uncovered |

两块28HTTP、55,423 token，无usage缺失。请求`deepseek-v4-flash`、返回`deepseek-flash`，
温度0.7、Qwen3-Embedding 256维，与上次有效完整运行的配置一致；两块源码哈希相同。
每请求输出上限1200与上次整局相同，没有用旧600上限表达实验做公平因果对照。
与上次整局相比，token/时间变化包含采样和记忆变化，不能作为稳定性能提升结论。

变体M4第一次HTTP响应`finish_reason=length`，未产生可用台词，原客户端一次重解析
成功；两个请求都计入成本，最终不是固定回复兜底。原路线8次公开表达HTTP，变体9次
（含该重解析），各8条最终台词。六次自由交流的规划都提议关系调整；本次真实运行没有
覆盖无行动分支或Agent非法动作拒绝，无行动/拒绝/合法披露在离线契约中分别验证。
模型自主工具调用原路线6次、变体4次；工具请求与结果完整保存，未强迫工具展示。

首次沙箱尝试在首个生成请求ConnectError，保留于[失败块](original-sandbox/report.json)。
应用保留M1已结算结果并固定兜底，验收立即记失败。授权后从普通开局重跑原路线，未覆盖
失败文件。失败1HTTP无usage，保留4,949 token预留；合计29次尝试。货币成本null，无已核实费率。
两条真实块都在各自50HTTP/180k token/600秒预算内。等待授权不计入模型运行耗时。

- 原路线：[玩家实录](original-real-transcript.md)、[前后状态/接口响应](original-real/report.json)、
  [全部请求响应](original-real/wire.json)、[Trace](original-real/traces/)、[存档](original-real/saves/)。
- 变体：[玩家实录](variant-real-transcript.md)、[前后状态/接口响应](variant-real/report.json)、
  [全部请求响应](variant-real/wire.json)、[Trace](variant-real/traces/)、[存档](variant-real/saves/)。
- 每块sources含完整运行源码及哈希，save_checkpoints保留恢复前存档；report还记录
  模型配置、具体命令、HEAD及工作区。模型权重未归档，源码与输入可复跑，模型文本不保证逐字重现。
- [逐条复核及台词哈希](review_labels.json)、[离线校验](verification.json)、[交接](handoff.md)。

## 逐条复核结论与限制

原路线步骤05没有再提洛伦登门，只复述公开目击和缺失物品；步骤07直接回述“苔灯”。
步骤11保留“洛伦回村后仍有人见过艾拉”的关键关系，没有复制私有规划新增的老张、
第二天送货或老板后头的活动。这证明本次输出与私有草稿分离，但不证明规划本身变可靠。

变体步骤07正确回述布袋名“河岸”。关门前后的追问缺少明确资料，输出没有武断选前/后，
而限定为“没说过……关门前还是关门后”“只记得”已有时间信息；不把资料空白补成事实。
这是本轮不编造门槛下可接受的保留回应，**不是清楚完整的时间线解释**。

保留的问题：

- 原路线步骤11末句“这条先后我排不死”削弱了前面已明确的顺序；变体步骤11没有直说
  无法确定，也没有补充已知的“洛伦先回来”关系。两者仍影响可读性，不按完美回答计分。
- 变体首次告知布袋名字时说“我记不住这些名字”，随后又能准确回述，角色衔接生硬。
  初次告知不是回忆题，未阻止后续回答，但这种无依据回避仍需记录。
- 台词重复公开姓名、年龄和搜索经历，略显说明书式；人格语气未作独立量化验收。
- 私有规划仍会编造细节、误读普通话题并不合理地增加fear或降低trust。行动规则只保证
  增量合法，不保证决策合理；本轮路线在这些变化下仍可完成，没有同时改行动策略。
- 公开输入不能阻止模型凭空猜中秘密或受玩家指令诱导。当前只有两次整局、16条最终台词，
  不是针对对抗输入或全部NPC自由对话的充分安全测试；也未做浏览器视觉/TCP SSE重连测试。

## 测试、采用及复现

新增9项测试（含参数化分支），覆盖无行动、关系批准、披露批准/拒绝、拒绝移动、未知动作、
作者约束隔离、失败不泄露私有草稿、恢复与失败后继续。与旧投影/补证据测试合计25通过。
相关剧情/运行时回归 **269通过、2跳过**（[pytest.xml](pytest.xml)、[输出](pytest.txt)），
覆盖四终局、可达性、Web、Harness、事件链、Trace及配置；前端 **8通过**。
将Settings默认改为True后，接入和配置另 **36通过**（与前述测试重叠，不相加当独立项）。
跳过为既有pending hook及partial facts条件。未跑全仓库；模型质量证据与离线mock测试分开。

采用仅改变交互Settings的默认开关，真实验证时该开关已显式True；其他运行时源码与
被测版本一致。不是在测试后另改模型输入。旧retain/filtered/grounded开关仍False。
1112个历史报告文件未变；当前运行时与被测快照的差异另见promotion.json。

```powershell
.venv\Scripts\python.exe scripts/web.py --no-dev
# 新默认边界；每次使用新的输出目录
.venv\Scripts\python.exe scripts/demo_acceptance.py --route evals/reports/2026-09-18-public-expression/route-original.json --output evals/reports/public-expression-local
# 离线复核，不调用模型；生成本轮verification与transcript
.venv\Scripts\python.exe evals/reports/2026-09-18-public-expression/verify.py
```

本轮收尾，不继续调整prompt或增加LLM审查。可展示当前引导路线及完整修复前后证据，
必须保留上述限制；下一步只有发现新的严重阻塞时才开展有界修复。
