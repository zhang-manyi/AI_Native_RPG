# 完整玩家记录（生成内容与固定叙事分别标注）

模型语义结论见 review_labels.json；规则达到终局不代表质量通过。

## 普通开局

- 固定 narrator：门只开了一条缝。玛尔塔挡在门后，先看了看街上。
- 固定 player：我受镇上议事厅委托，来问问艾拉失踪那晚的事。
- 固定 npc_a：……小声点。我儿子在屋里。你想问什么？

## 步骤 00

输入：`{"kind": "move", "destination": "forest_edge"}`

- 规则移动结果：`{"turn_id": "48e96c0bd361446592148d0d92ac620d", "approved": false, "destination": "forest_edge", "name": "森林入口", "reason": "'forest_edge' is not reachable from 'npc_a_house'", "slot_spent": false, "slot": null, "day": null, "first_visit": false, "out_of_days": false, "latency_ms": 0.8584999886807054}`
- HTTP 202；模型请求范围 [0, 0]（从0起，右端不含）

## 步骤 01

输入：`{"kind": "choice", "option_id": "goodwill"}`

真实模型：……进来吧。门槛这儿别站太久。说话轻一点，我不想让邻居听见。
- 固定叙事 narrator：她想关门，门栓却卡在翘起的木刺上。里屋传来孩子翻身的声音。
- 固定叙事 npc_a：这门一到晚上就关不严……你问完就走吧。
- HTTP 202；模型请求范围 [0, 1]（从0起，右端不含）

## 步骤 02

输入：`{"kind": "choice", "option_id": "help_latch"}`

真实模型：……这样就能关紧了。谢谢。进来说吧——脚步轻点，别吵醒他。
- 固定叙事 npc_a：你问的是艾拉失踪的那晚吧。雨停以后，我确实还没睡。可我不想让人知道我和你说过这些。
- HTTP 202；模型请求范围 [1, 2]（从0起，右端不含）

## 步骤 03

输入：`{"kind": "choice", "option_id": "goodwill"}`

真实模型：……你不用这样说。我不是不肯帮忙，真的不是。我只怕——话说出去，往后就有人找上我们娘俩。你明白吗？
- 固定叙事 player：磨坊主家里这几天乱得很吗？
- 固定叙事 npc_a：哪还有心思收拾。艾拉的外衣、攒的钱，还有她母亲那把木梳，都不在了。别的东西倒没动。
- 固定叙事 npc_a：我确实看见了一个人。可你听完就走，我们还要在这里过日子。
- HTTP 202；模型请求范围 [2, 3]（从0起，右端不含）

## 步骤 04

输入：`{"kind": "choice", "option_id": "goodwill"}`

真实模型：雨刚停那会儿……我看见一个人从森林入口那边回村。脸没看清，真的没看清。就这些——你别再往下问了，行吗？
- HTTP 202；模型请求范围 [3, 4]（从0起，右端不含）

## 步骤 05

输入：`{"kind": "turn", "text": "我把这次调查记在一本叫‘苔灯’的笔记本里。先别着急，我会逐条核对。"}`

真实模型：你写吧，写你的……那晚我没睡，炉子还着着——洛伦在门口站着，也没敲门，就站着。你查他去。
- HTTP 202；模型请求范围 [4, 7]（从0起，右端不含）

## 步骤 06

输入：`{"kind": "resume"}`

- 恢复/拒绝后保留 npc_a：你写吧，写你的……那晚我没睡，炉子还着着——洛伦在门口站着，也没敲门，就站着。你查他去。
- HTTP 200；模型请求范围 [7, 7]（从0起，右端不含）

## 步骤 07

输入：`{"kind": "turn", "text": "刚才我说，我的笔记本叫什么？"}`

真实模型：……你在本子上写东西，我看见了。你别一条一条问我，我这人不经问。
- HTTP 202；模型请求范围 [7, 10]（从0起，右端不含）

## 步骤 08

输入：`{"kind": "move", "destination": "village_square"}`

- 固定叙事 narrator：有人说，艾拉前些天问过去镇上的路，还问过谁家的车最近要出村。
- 规则移动结果：`{"turn_id": "0bba360121ff48078a20e89e1e572de6", "approved": true, "destination": "village_square", "name": "村庄广场", "reason": null, "slot_spent": true, "slot": "afternoon", "day": 1, "first_visit": true, "out_of_days": false, "latency_ms": 3.1977999897208065}`
- HTTP 202；模型请求范围 [10, 10]（从0起，右端不含）

## 步骤 09

输入：`{"kind": "move", "destination": "tavern"}`

- 固定叙事 narrator：老板擦着杯子，目光在你和门外之间来回。
- 固定叙事 npc_c：都问那天晚上的事。那晚人多，我也不敢说记得准。
- 规则移动结果：`{"turn_id": "1d632522d8af44cc92e94d04132d1ddd", "approved": true, "destination": "tavern", "name": "酒馆", "reason": null, "slot_spent": true, "slot": "evening", "day": 1, "first_visit": true, "out_of_days": false, "latency_ms": 3.341300005558878}`
- HTTP 202；模型请求范围 [10, 10]（从0起，右端不含）

## 步骤 10

输入：`{"kind": "choice", "option_id": "goodwill"}`

真实模型：来了——先喝着。……洛伦经过那会儿，我正打算关门，大概三更吧？也可能早些，我记着是那样，可不敢跟你打包票。后来送货的倒是提过一句，说在去镇上的路边，好像见过一个背小包的姑娘。
- 固定叙事 narrator：老板记不清钟点，但提到了先后：洛伦回来以后，送货的人还见过一个背小包的姑娘往镇上走。这和“艾拉死在林中”的猜测对不上。
- HTTP 202；模型请求范围 [10, 11]（从0起，右端不含）

## 步骤 11

输入：`{"kind": "turn", "text": "你刚才说钟点记不准，那能确定的先后顺序是什么？"}`

真实模型：顺序啊……我能拍胸脯的只有一件，洛伦进来那会儿我正上最后一块门板。玛尔塔的事是听街坊说的，我可不敢给你排到里头去。
- HTTP 202；模型请求范围 [11, 14]（从0起，右端不含）

## 步骤 12

输入：`{"kind": "move", "destination": "village_square"}`

- 规则移动结果：`{"turn_id": "5694534589344eceaada32719772f9a6", "approved": true, "destination": "village_square", "name": "村庄广场", "reason": null, "slot_spent": true, "slot": "wrap_up", "day": 1, "first_visit": false, "out_of_days": false, "latency_ms": 3.5604000149760395}`
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 13

输入：`{"kind": "close_day"}`

- 规则移动结果：`{"turn_id": "5e98ccc56a74490689404b3ce006db9e", "approved": true, "destination": "village_square", "name": "", "reason": null, "slot_spent": false, "slot": "morning", "day": 2, "first_visit": false, "out_of_days": false, "latency_ms": 0.0}`
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 14

输入：`{"kind": "move", "destination": "forest_edge"}`

- 固定叙事 narrator：林边的泥地还没干透。洛伦站在小径旁整理猎具，视线避开了荆棘丛。你可以先查看现场。
- 规则移动结果：`{"turn_id": "cc641a1e88594392aad3ec3afd51b830", "approved": true, "destination": "forest_edge", "name": "森林入口", "reason": null, "slot_spent": true, "slot": "afternoon", "day": 2, "first_visit": true, "out_of_days": false, "latency_ms": 4.674099996918812}`
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 15

输入：`{"kind": "choice", "option_id": "observe_traces"}`

- 固定叙事 narrator：地上的痕迹——不止一个人走过
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 16

输入：`{"kind": "choice", "option_id": "observe_cloth"}`

- 固定叙事 narrator：荆棘上挂着一小块布
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 17

输入：`{"kind": "choice", "option_id": "observe_blood"}`

- 固定叙事 narrator：石头上有一片血
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 18

输入：`{"kind": "conclude"}`

- 固定叙事 narrator：你翻开写给镇上议事厅的调查报告，将已知见闻与个人推测分开记录。以下都是可提交的判断，不代表已经证实。提交最终报告将结束本次受托调查；也可以合上报告，继续搜集线索。
- 规则移动结果：`{"turn_id": "c8a4ae006f5d4cd0a561f8f942b273e6", "approved": true, "destination": "forest_edge", "name": "", "reason": null, "slot_spent": false, "slot": "afternoon", "day": 2, "first_visit": false, "out_of_days": false, "latency_ms": 0.0}`
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 19

输入：`{"kind": "choice", "option_id": "tell_loren_alive"}`

- 固定叙事 narrator：你在报告中写下艾拉可能自行离村、仍然生还的判断，附上目前的调查记录，封好交给镇上议事厅的信件。这份判断不等于已经找到她。
- HTTP 202；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 20

输入：`{"kind": "resume"}`

- 恢复/拒绝后保留 narrator：你在报告中写下艾拉可能自行离村、仍然生还的判断，附上目前的调查记录，封好交给镇上议事厅的信件。这份判断不等于已经找到她。
- HTTP 200；模型请求范围 [14, 14]（从0起，右端不含）

## 步骤 21

输入：`{"kind": "turn", "text": "继续调查", "status": 400}`

- 恢复/拒绝后保留 narrator：你在报告中写下艾拉可能自行离村、仍然生还的判断，附上目前的调查记录，封好交给镇上议事厅的信件。这份判断不等于已经找到她。
- HTTP 400；模型请求范围 [14, 14]（从0起，右端不含）
