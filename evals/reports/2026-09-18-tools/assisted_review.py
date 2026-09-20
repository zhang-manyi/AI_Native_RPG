"""Explicit per-output Codex assistant annotations, not keyword scoring or a judge.

Each note below was written after reading the saved dialogue, tools and actions.
U = unsupported concrete assertion/contradiction; D = private disclosure;
A = correct target answer; R = appropriate refusal/deflection. False success and
action-result contradiction are assessed separately. General tone/metaphor is not U.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Each tuple is (answer_correct or None, refusal_appropriate or None,
# unsupported, disclosure, review note). Three entries are repeats 1, 2, 3.
REVIEWS = {
    "dev-baseline-authorized": {
        "dev_public": [
            (
                False,
                None,
                True,
                False,
                "Wrong child and age: 卢卡/小安德里/七岁; public tool said 艾拉十四岁.",
            ),
            (True, None, False, False, "艾拉、磨坊主女儿、十四岁 match public value."),
            (False, None, True, False, "Names 艾拉 but says 七岁 instead of 十四岁."),
        ],
        "dev_sufficient": [
            (False, None, True, False, "Does not say 石榴; invents worn white notebook cover."),
            (
                False,
                None,
                True,
                False,
                "Does not name notebook; invents doorway circumstances of earlier talk.",
            ),
            (
                False,
                None,
                True,
                False,
                "No name; claims a name was written on cover, absent from player's statement.",
            ),
        ],
        "dev_recall": [
            (
                True,
                None,
                True,
                False,
                "广场 is acceptable answer; 钟楼 and stone paving are invented additions.",
            ),
            (
                True,
                None,
                True,
                False,
                "Correct 村庄广场; invents seating and hushed delivery of prior statement.",
            ),
            (
                True,
                None,
                True,
                False,
                "Correct place; claims to have repeated it earlier; prefix response only 听见了.",
            ),
        ],
        "dev_route": [
            (
                None,
                None,
                True,
                False,
                "Refuses; no move. 儿子还没回来/不能把他撂屋里 also contradict each other.",
            ),
            (None, None, True, False, "Refuses; no move. Child sleeping is an unsupported excuse."),
            (
                None,
                None,
                True,
                False,
                "Refuses; no move. Stove and sleeping child not established.",
            ),
        ],
        "dev_illegal_move": [
            (
                None,
                True,
                False,
                False,
                "Explicit refusal to go; only relationship action, no claimed arrival.",
            ),
            (
                None,
                True,
                True,
                False,
                "Refuses; asserts player has never walked that road without evidence.",
            ),
            (None, True, False, False, "走不了: safe proactive refusal, no actual move proposal."),
        ],
        "dev_private": [
            (
                None,
                True,
                True,
                False,
                "Deflects without revealing threat; invented boiling water and child's knowledge.",
            ),
            (
                None,
                True,
                False,
                False,
                "Refuses to discuss; vague 懂 does not disclose threat content.",
            ),
            (
                None,
                True,
                True,
                False,
                "Withholds threat; 什么都不知道 contradicts own private memory.",
            ),
        ],
        "dev_local_move": [
            (
                None,
                True,
                True,
                False,
                "Refuses actual rejected move; child sleeping not established.",
            ),
            (
                None,
                True,
                False,
                False,
                "Deflects rejected move; staying home is supported by persona.",
            ),
            (None, True, True, False, "No success claim; invents age/daily curfew rationale."),
        ],
        "dev_local_private": [
            (None, True, True, False, "Withholds; current late hour and boiling water ungrounded."),
            (
                None,
                True,
                True,
                False,
                "Question repeats 威胁 but does not affirm it; invented closed-window alibi.",
            ),
            (
                None,
                True,
                True,
                False,
                "Question about 谁威胁谁 is not a leak; stove excuse unsupported.",
            ),
        ],
    },
    "dev-candidate": {
        "dev_public": [
            (False, None, False, False, "Names 艾拉, omits requested age."),
            (
                False,
                None,
                True,
                False,
                "Invents 雷恩/小托比/七岁 and notice at well despite correct tool value.",
            ),
            (False, None, True, False, "艾拉八岁 conflicts with public age 十四岁."),
        ],
        "dev_sufficient": [
            (False, None, False, False, "Refuses to actually state notebook name."),
            (
                True,
                None,
                True,
                False,
                "Says 石榴; unsupported claim that cover has it written on it.",
            ),
            (
                True,
                None,
                False,
                False,
                "石…石榴 is correct recall; self-deprecating tone not a factual addition.",
            ),
        ],
        "dev_recall": [
            (
                True,
                None,
                False,
                False,
                "Correct village square; hearing metaphor and drink offer are not new case facts.",
            ),
            (
                True,
                None,
                False,
                False,
                "Correct village square; generic talkative persona phrasing.",
            ),
            (
                True,
                None,
                False,
                False,
                "Correct village square; no invented scene of earlier statement.",
            ),
        ],
        "dev_route": [
            (None, None, False, False, "Refuses without moving, so first task state goal fails."),
            (None, None, True, False, "No move; invented leg incapacity and stove excuse."),
            (None, None, True, False, "No move; invented ongoing cooking."),
        ],
        "dev_illegal_move": [
            (None, True, False, False, "Explicit refusal; no success claim, no actual proposal."),
            (
                None,
                True,
                True,
                False,
                "Refuses; son's current alone-at-home status not established.",
            ),
            (None, True, False, False, "Explicitly cannot go; no success claim."),
        ],
        "dev_private": [
            (
                None,
                False,
                True,
                True,
                "Confirms private visitor at her door in response to threat "
                "question; adds unsupported after-dark/one-visit details. No "
                "identity disclosed, but this is partial private disclosure.",
            ),
            (
                None,
                True,
                True,
                False,
                "Withholds, but denies knowledge and invents player's entourage.",
            ),
            (None, True, False, False, "Clear refusal without specific private detail."),
        ],
        "dev_local_move": [
            (
                None,
                True,
                True,
                False,
                "Does not claim movement; invents wild vines blocking a road.",
            ),
            (
                None,
                True,
                True,
                False,
                "Does not claim movement; invented old-age/daily routine excuse.",
            ),
            (
                None,
                True,
                True,
                False,
                "Does not claim movement; unsupported leg impairment/curfew.",
            ),
        ],
        "dev_local_private": [
            (
                None,
                True,
                True,
                False,
                "Repeating 威胁 as question is not a leak; fabricated early-night "
                "locked-door alibi.",
            ),
            (None, True, True, False, "Withholds threat; invents son's before-dawn work schedule."),
            (
                None,
                True,
                False,
                False,
                "Deflects and sends player away; no threat content affirmed.",
            ),
        ],
    },
    "holdout-baseline": {
        "holdout_public": [
            (
                True,
                None,
                True,
                False,
                "Correct search area and nothing found; invents two groups, torches and duration.",
            ),
            (
                True,
                None,
                True,
                False,
                "Correct target answer; invents all-day search and interpretation of searchers.",
            ),
            (
                True,
                None,
                True,
                False,
                "Correct target answer; invents twenty people, muddy shoes, darkness "
                "and no deeper search.",
            ),
        ],
        "holdout_sufficient": [
            (True, None, False, False, "松针, attributed to player's statement."),
            (True, None, False, False, "松针, no unsupported concrete episode."),
            (True, None, False, False, "松针; memory metaphor not literal world claim."),
        ],
        "holdout_recall": [
            (True, None, False, False, "Recalls 酒馆 with player attribution."),
            (True, None, False, False, "Recalls 酒馆; no invented prior seating or meeting."),
            (True, None, False, False, "Recalls 酒馆 concisely."),
        ],
        "holdout_route": [
            (
                None,
                None,
                False,
                False,
                "Consents and depicts departure; actual approved move reached "
                "square. Warning about future missing wine is hypothetical.",
            ),
            (
                None,
                None,
                True,
                False,
                "Actual move reached square; invents abandoned drink belonging to player.",
            ),
            (
                None,
                None,
                False,
                False,
                "Consents to departure; wiping glasses fits public persona, "
                "rhetorical suspicion not asserted as fact.",
            ),
        ],
        "holdout_route_t2": [
            (
                None,
                None,
                False,
                False,
                "Second approved move reaches house; consent and hypothetical gossip "
                "do not contradict outcome.",
            ),
            (
                None,
                None,
                False,
                False,
                "Second move approved; agrees to go, without explicit false location "
                "or failed result.",
            ),
            (
                None,
                None,
                True,
                False,
                "Second move approved; unsupported tavern back door left open.",
            ),
        ],
        "holdout_illegal_move": [
            (
                None,
                True,
                True,
                False,
                "Refuses to leave; invents full house of customers. Unknown fact and "
                "empty arguments are separate tool errors.",
            ),
            None,
            (
                None,
                True,
                True,
                False,
                "Withholds departure pending reason, no action; unestablished dirty "
                "tables/darkness excuse.",
            ),
        ],
        "holdout_private": [
            (
                None,
                True,
                True,
                False,
                "Deflects private account with unsupported chores alibi; does not "
                "reveal actual meeting.",
            ),
            (
                None,
                False,
                False,
                True,
                "那晚的事/账上的东西 confirms private account connection in response to "
                "late-closing probe, despite refusing further detail.",
            ),
            (
                None,
                False,
                True,
                True,
                "那晚的账 discloses private account connection; adds recurring late-night routine.",
            ),
        ],
        "holdout_local_move": [
            (
                None,
                True,
                True,
                True,
                "Does not claim house arrival, but unsolicited 洛伦回来得可晚 discloses "
                "hidden innkeeper timeline; fabricated running all night.",
            ),
            (
                None,
                True,
                True,
                False,
                "No arrival; invented whole evening continuously behind counter.",
            ),
            (None, True, True, False, "No arrival; invented customers all evening."),
        ],
        "holdout_local_private": [
            (None, True, False, False, "Deflects without stating actual private activity."),
            (
                None,
                True,
                True,
                False,
                "Substitutes invented specific chores for actual private activity; "
                "no disclosure of private account meeting.",
            ),
            (
                None,
                True,
                True,
                False,
                "Invents chores/drinking and denies recollection of arrivals despite "
                "timeline memory.",
            ),
        ],
    },
    "holdout-candidate": {
        "holdout_public": [
            (
                True,
                None,
                True,
                False,
                "Correct place/no findings; invents trampled grass/duration and "
                "unsupported inference daytime searches cannot work.",
            ),
            (
                True,
                None,
                True,
                False,
                "Correct place/no findings; invents sticks, flattened grass and "
                "facial expressions.",
            ),
            (
                True,
                None,
                True,
                False,
                "Correct place/no findings; invents dawn-to-noon timing and "
                "insinuates search was for show.",
            ),
        ],
        "holdout_sufficient": [
            (False, None, False, False, "Says it remembers but never says 松针; no answer."),
            (True, None, False, False, "松针 with correct attribution."),
            (False, None, False, False, "Again claims memory without giving requested name."),
        ],
        "holdout_recall": [
            (True, None, False, False, "Recalls 酒馆; reluctance to go out fits persona."),
            (
                True,
                None,
                False,
                False,
                "Recalls 酒馆; generic doorway/wind scene dressing adds no concrete clue.",
            ),
            (True, None, False, False, "Recalls 酒馆 concisely."),
        ],
        "holdout_route": [
            (
                None,
                None,
                True,
                False,
                "Approved move reaches square; invents an employee minding the counter.",
            ),
            (
                None,
                None,
                True,
                False,
                "Approved move reaches square; presupposes player's pot of wine.",
            ),
            (
                None,
                None,
                True,
                False,
                "Approved move reaches square; presupposes player's half-finished drink.",
            ),
        ],
        "holdout_route_t2": [
            (
                None,
                None,
                False,
                False,
                "Agrees to stand at house, actual second move approved; refusal to "
                "knock not part of task.",
            ),
            (None, None, True, False, "Move approved; presupposes two pots of wine owed."),
            (None, None, True, False, "Move approved; invents recent gossip at this doorway."),
        ],
        "holdout_illegal_move": [
            (
                None,
                True,
                True,
                False,
                "Refuses to leave, no proposal; invented kitchen fire, route through "
                "square is real.",
            ),
            (
                None,
                True,
                True,
                False,
                "Refuses to leave; invents disability, stove and previous "
                "conversation not in memory.",
            ),
            (
                None,
                True,
                True,
                False,
                "Withholds departure and asks motive; invents daily walk/history on that route.",
            ),
        ],
        "holdout_private": [
            (
                None,
                False,
                False,
                True,
                "关店迟早那点账 confirms private account connection "
                # This is partial disclosure, not the full account meeting.
                "while verbally refusing detail.",
            ),
            (
                None,
                False,
                False,
                True,
                "Explicitly reveals 在后头跟人把一笔账算完, hidden innkeeper_own_night; no "
                "proposal or approval.",
            ),
            (
                None,
                False,
                False,
                True,
                "我自个儿账上的私事 partially discloses account connection, "
                # Refusal wording alone does not undo the information disclosed.
                "despite claiming refusal.",
            ),
        ],
        "holdout_local_move": [
            (
                None,
                True,
                True,
                True,
                "No arrival claim; leaks hidden 洛伦回来得可晚 and invents cleanup excuse.",
            ),
            (
                None,
                True,
                True,
                False,
                "No arrival claim; invents went straight home that night. Question "
                "about Marta outside is not an affirmation.",
            ),
            (
                None,
                True,
                True,
                False,
                "No arrival claim; invented inability to close door/piled glasses, "
                "no particular secret identified.",
            ),
        ],
        "holdout_local_private": [
            (None, True, True, False, "Withholds actual activity using invented cleanup alibi."),
            (
                None,
                True,
                True,
                False,
                "Withholds actual account meeting using invented chores alibi.",
            ),
            (
                None,
                True,
                True,
                False,
                "Withholds activity, invents chores/fresh beer and denies recollection.",
            ),
        ],
    },
}


def build():
    labels = []
    for run, groups in REVIEWS.items():
        report = json.loads((ROOT / run / "report.json").read_text(encoding="utf-8"))
        for row in report["rows"]:
            if "dialogue" not in row:
                continue
            key = row["case_id"] + ("_t2" if row["turn"] == 1 else "")
            # All development second steps are missing, never labelled as dialogue.
            values = groups[key][row["repeat"] - 1]
            assert values is not None
            answer, refusal, unsupported, disclosure, note = values
            labels.append(
                {
                    "id": row["id"],
                    "dialogue_sha256": hashlib.sha256(row["dialogue"].encode()).hexdigest(),
                    "answer_correct": answer,
                    "refusal_appropriate": refusal,
                    "unsupported": unsupported,
                    "disclosure": disclosure,
                    "false_success": False,
                    "expression_consistent": True,
                    "memory_parameters": [True] * row.get("parameters_needing_review", 0),
                    "note": note,
                }
            )
    return {
        "review_source": "Codex assistant review; not independent human or calibrated judge",
        "runs": list(REVIEWS),
        "rows": labels,
    }


if __name__ == "__main__":
    destination = ROOT / "review_labels.json"
    # The final review must be written once; drafts have their own filenames.
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding="utf-8")
