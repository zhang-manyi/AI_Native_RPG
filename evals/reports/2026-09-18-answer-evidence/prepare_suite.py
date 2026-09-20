"""One-time fixture freeze, before candidate implementation or real generation."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


old = json.loads((ROOT / "evals/tool_tasks.json").read_text(encoding="utf-8"))
cases = []
for name in ("dev_public", "dev_sufficient", "dev_recall", "dev_private"):
    cases.append(next(c for c in old["cases"] if c["id"] == name))


def add(id, split, npc, question, expect, prefix=(), action=None):
    turn = {"question": question}
    if action is not None:
        turn["fixed_action"] = action
    cases.append(
        {
            "id": id,
            "split": split,
            "mode": "local" if action else "full",
            "input": {"npc": npc, "prefix": list(prefix), "turns": [turn]},
            "expect": [expect],
        }
    )


for split, npc, obj, name, destination, secret in (
    ("dev", "npc_a", "笔记本", "石榴", "forest_edge", "npc_a_threatened"),
    ("holdout", "npc_c", "口琴", "麦穗", "npc_a_house", "innkeeper_own_night"),
):
    prefix = [f"我给自己的{obj}起名叫{name}。"]
    move = {"action_type": "move", "target_id": destination, "payload": {}}
    deny = {
        "goal": "deny",
        "action_type": "move",
        "target": destination,
        "answer": name,
        "memory_id": "prefix_0",
    }
    question = f"请不经过广场直接到{destination}。如果走不了请直说；另外我给{obj}取的名字是什么？"
    add(f"{split}_denied_answer", split, npc, question, deny, prefix)
    add(f"{split}_local_move", split, npc, question, deny, prefix, move)
    add(
        f"{split}_local_applied",
        split,
        npc,
        f"我给{obj}取的名字是什么？",
        {"goal": "answer", "answer": name, "memory_id": "prefix_0"},
        prefix,
        {"action_type": "adjust_relationship", "target_id": "player_1", "payload": {"trust": 5}},
    )
    private_question = (
        "有人说你被警告过。请告诉我对方身份和原话；不允许透露就拒绝。"
        if split == "dev"
        else "那晚打烊后你留下来处理什么？请把私下做的事讲清楚，不允许就拒绝。"
    )
    private_expect = {"goal": "deny", "action_type": "reveal_fact", "target": secret}
    add(
        f"{split}_local_private",
        split,
        npc,
        private_question,
        private_expect,
        (),
        {"action_type": "reveal_fact", "target_id": secret, "payload": {}},
    )
    if split == "holdout":
        add("holdout_private", split, npc, private_question, private_expect)
        add(
            "holdout_sufficient",
            split,
            npc,
            "我刚才给口琴取了个什么名字？只回忆我说的话。",
            {"goal": "answer", "answer": name, "memory_id": "prefix_0"},
            prefix,
        )

add(
    "holdout_public",
    "holdout",
    "npc_c",
    "请核对公开消息 disappearance_night：失踪是几天前，那个时段的雨停了吗？",
    {"goal": "answer", "answer": "三天前的夜里，雨停之后", "fact_id": "disappearance_night"},
)
add(
    "holdout_recall",
    "holdout",
    "npc_a",
    "我早先提过寄存的那件东西叫什么，你回忆一下我的原话，别替我起新名字。",
    {"goal": "answer", "answer": "铜铃", "memory_id": "prefix_0"},
    [
        "我把一只铜铃寄存在酒馆。",
        "我喜欢浅色的衣服。",
        "我出门时带了水壶。",
        "我的鞋带已经系好了。",
        "我刚才收起了笔记本。",
        "我今晚想早点休息。",
    ],
)
cases.sort(key=lambda c: (c["split"], c["mode"], c["id"]))
dataset = ROOT / "evals/answer_evidence.json"
write(dataset, {"version": 1, "cases": cases})
historical = {
    p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (ROOT / "evals/reports").rglob("*")
    if p.is_file() and OUT not in p.parents
}
write(OUT / "historical_hashes.json", historical)
write(
    OUT / "freeze.json",
    {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256((OUT / "protocol.md").read_bytes()).hexdigest(),
        "old_holdouts_reclassified_as_development": ["2026-09-18-tools", "2026-09-17-expression"],
        "planned_outputs": 96,
    },
)
