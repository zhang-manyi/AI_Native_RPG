/* Scene page + developer panel (docs/12 §8).
 *
 * The server sends whole snapshots (docs/12 §3.3), so this file has almost no state:
 * it keeps the last `scene` and `panel` payloads and redraws from them. There is
 * deliberately no local mirror to reconcile — a front end that maintained its own copy
 * could disagree with the world, and a panel that disagrees with the world is worse
 * than no panel.
 *
 * One EventSource carries everything. Its automatic reconnect resumes from
 * Last-Event-ID, which the server replays (docs/12 §4.1).
 */

const el = (id) => document.getElementById(id);

const ui = {
  app: el("app"),
  title: el("title"),
  meta: el("backend-meta"),
  dayProgress: el("day-progress"),
  dayProgressLabel: el("day-progress-label"),
  dayProgressTrack: el("day-progress-track"),
  toggle: el("panel-toggle"),
  when: el("when"),
  placeLabel: el("place-label"),
  placeDesc: el("place-desc"),
  cast: el("cast"),
  notes: el("notes"),
  ways: el("ways"),
  waysList: el("ways-list"),
  conclude: el("conclude"),
  concludeReason: el("conclude-reason"),
  history: el("history"),
  dialogue: el("dialogue"),
  dialogueName: el("dialogue-name"),
  dialogueText: el("dialogue-text"),
  advance: el("advance"),
  logToggle: el("log-toggle"),
  status: el("status"),
  feedbackToast: el("feedback-toast"),
  options: el("options"),
  composer: el("composer"),
  say: el("say"),
  send: el("send"),
  scene: el("scene"),
  wrapup: el("wrapup"),
  wrapDay: el("wrap-day"),
  wrapKnown: el("wrap-known"),
  wrapOpenBlock: el("wrap-open-block"),
  wrapOpen: el("wrap-open"),
  wrapIntroduction: el("wrap-introduction"),
  wrapDiscovered: el("wrap-discovered"),
  wrapRelationships: el("wrap-relationships"),
  wrapClose: el("wrap-close"),
  intro: el("intro"),
  introTitle: el("intro-title"),
  introPremise: el("intro-premise"),
  introGoal: el("intro-goal"),
  introStart: el("intro-start"),
  panel: el("panel"),
  panelBody: el("panel-body"),
  panelTurn: el("panel-turn"),
  panelResizer: el("panel-resizer"),
  panelViewToggle: el("panel-view-toggle"),
  ending: el("ending"),
  endingTitle: el("ending-title"),
  endingText: el("ending-text"),
};

const state = {
  sessionId: null,
  devMode: false,
  scene: null,
  currentNpcId: null,
  panel: null,
  // The dialogue box holds one line at a time: { kind, who, text }. `kind` is 'player' or
  // 'npc' and decides only how the name is styled — both voices get the same box, so
  // neither reads as an aside.
  line: null,
  // Scripted lines still to be said, in order. The player advances through them and the
  // last one submits the turn, so a whole opening speech costs one turn rather than one
  // each (docs/15 §1: it exists to move the scene without spending a decision).
  queue: [],
  // True while a queued line is waiting on the player to advance it.
  awaitingAdvance: false,
  // Set when a scripted line put itself on screen before submitting, so `turn_accepted`
  // knows not to show it a second time.
  pendingEcho: false,
  // The scripted speech already queued or spoken, keyed by its own text. Two `scene`
  // snapshots arrive per turn, so without this the same opening speech would be re-offered
  // the moment the tick landed. Keyed on content rather than on an event id because the
  // lines are all the front end needs to know it has seen them.
  spokenFor: null,
  waiting: null, // 'dialogue' | 'tick' | 'move' | null
  busy: false, // mirrors the composer lock, so a redraw re-applies it
  panelOpen: false,
  logOpen: false,
  resumed: false, // continuing a save: suppresses the turn-0 intro screen
  wrapUpActive: false, // the day's review screen has replaced the scene
  // The active event's options, held back from the tray until any scripted line ahead
  // of them has actually been read. The server sends both in the same `scene` snapshot
  // — an option is not conditioned on the line before it there — so without this the
  // tray would render before the player had read what he is being asked to respond to.
  pendingOptions: [],
  passageId: null,
  authoredQueue: false,
  panelGauge: false,
};

// --- helpers ---------------------------------------------------------------

const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const num = (v, digits = 0) => (typeof v === "number" ? v.toFixed(digits) : "—");

/* Slot wording. A front-end asset like `sprite_key`, not pack content: the server sends
 * the bare enum value and the page decides what to call it. Declared up here because
 * both the scene page and the panel read it, and a `const` further down would be in its
 * temporal dead zone when the first `scene` event arrives. */
const SLOT_LABELS = {
  morning: "上午",
  afternoon: "下午",
  evening: "夜里",
  wrap_up: "收束",
};

const slotLabel = (slot) => SLOT_LABELS[slot] || slot || "";

function setStatus(text, kind = "") {
  ui.status.className = `status ${kind}`.trim();
  ui.status.innerHTML = kind === "working" ? `<span class="spinner"></span>${esc(text)}` : esc(text);
}

/** Lock the composer while a turn is in flight (docs/12 §5.3: visible, not hidden). */
function setBusy(busy) {
  state.busy = busy;
  ui.say.disabled = busy;
  ui.send.disabled = busy;
  ui.say.setAttribute("aria-busy", busy ? "true" : "false");
  // Travel and options are turns too, so one lock covers all three: otherwise a second
  // option could be clicked while the first turn's tick is still writing (docs/12 §13.7).
  for (const button of ui.waysList.querySelectorAll("button")) button.disabled = busy;
  for (const button of ui.options.querySelectorAll("button")) button.disabled = busy;
  renderComposerVisibility();
  // Only when the composer is actually on screen: focusing a hidden input is a silent
  // no-op in every browser, but it would also steal focus back the moment a scripted
  // line's advance re-hides it, if this were ever called in that order.
  if (!busy && !ui.composer.hidden) ui.say.focus();
}

// --- boot ------------------------------------------------------------------

/** Packs that can actually be started right now. */
async function playableScenarios() {
  try {
    const res = await fetch("/api/scenarios");
    if (!res.ok) return [];
    const { scenarios } = await res.json();
    return scenarios || [];
  } catch {
    return [];
  }
}

/** The newest resumable save whose pack still exists, or null.
 *
 * The pack filter is load-bearing, not tidiness: `saves/` accumulates runs from packs that
 * have since been renamed or removed (a throwaway fixture pack, a scenario under a temp
 * directory). Offering one of those produced a 400 on session creation, which stopped the
 * game from starting at all — so a save whose pack is gone is not a candidate.
 */
async function latestSave(scenarios) {
  try {
    const res = await fetch("/api/saves");
    if (!res.ok) return null;
    const { saves } = await res.json();
    // Already newest-first from the server; a save with no turns is not worth resuming.
    return (
      (saves || []).find((s) => s.turn > 0 && scenarios.includes(s.scenario)) || null
    );
  } catch {
    // A missing or broken save list must never stop a new game from starting.
    return null;
  }
}

async function createSession(body) {
  return fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function boot() {
  const scenarios = await playableScenarios();
  const save = await latestSave(scenarios);
  const wants =
    save &&
    window.confirm(
      `发现上次的存档：${save.scenario} · 第 ${save.turn} 轮 · ${save.lines} 句对话\n` +
        `保存于 ${save.saved_at}\n\n` +
        `【确定】＝继续这局，接着上次的进度往下玩\n` +
        `【取消】＝开始新的调查，忽略这个存档`,
    );

  // The save's own pack, not the server's default: a save only resumes into the scenario
  // it was played in, and omitting it left that agreement to luck.
  let created = wants
    ? await createSession({ scenario: save.scenario, resume_from: save.save_id })
    : await createSession({});

  if (!created.ok && wants) {
    // A save that will not load must not cost the player a new game. It can be refused for
    // reasons the front end cannot see (an edited pack, a world written by an older
    // schema), so the fallback is to start fresh and say so.
    setStatus("上次的存档读不出来，已经重新开始。", "error");
    created = await createSession({});
  }
  if (!created.ok) {
    const detail = await created.json().catch(() => null);
    setStatus(`建立会话失败：${created.status} ${detail?.detail || ""}`.trim(), "error");
    return;
  }
  const info = await created.json();
  state.sessionId = info.session_id;
  state.devMode = info.dev_mode;
  state.resumed = Boolean(info.resumed_from);

  if (state.devMode) {
    ui.toggle.hidden = false;
    setPanelOpen(true);
  }

  connect();
}

function connect() {
  const source = new EventSource(`/api/session/${state.sessionId}/events`);

  source.addEventListener("hello", (e) => onHello(JSON.parse(e.data)));
  source.addEventListener("turn_accepted", (e) => onTurnAccepted(JSON.parse(e.data)));
  source.addEventListener("dialogue", (e) => onDialogue(JSON.parse(e.data)));
  source.addEventListener("move", (e) => onMove(JSON.parse(e.data)));
  source.addEventListener("narrative_tick", (e) => onTick(JSON.parse(e.data)));
  source.addEventListener("scene", (e) => onScene(JSON.parse(e.data)));
  source.addEventListener("panel", (e) => onPanel(JSON.parse(e.data)));
  source.addEventListener("turn_failed", (e) => onFailed(JSON.parse(e.data)));

  source.onerror = () => {
    // EventSource retries on its own; say so rather than looking frozen.
    setStatus("连接中断，正在重连……", "error");
  };
}

// --- event handlers --------------------------------------------------------

function onHello(data) {
  state.devMode = data.dev_mode;
  const b = data.backend || {};
  ui.meta.innerHTML = [
    b.model ? `<b>${esc(b.model)}</b>` : "",
    b.embedder ? esc(b.embedder) : "",
    b.scenario ? esc(b.scenario) : "",
  ]
    .filter(Boolean)
    .join(" · ");

  onScene(data.scene);
  if (data.panel) onPanel(data.panel);

  // A resumed run skips the opening screen: its premise is written for turn 0
  // ("于是敲响了她家的门"), which reads wrong in front of a conversation already
  // under way. The restored transcript is the context instead.
  const intro = state.resumed ? {} : data.scene?.intro || {};
  if (intro.premise || intro.title) {
    ui.introTitle.textContent = intro.title || "当前情况";
    ui.introPremise.textContent = intro.premise || "";
    ui.introGoal.textContent = intro.goal || "";
    ui.intro.hidden = false;
    ui.introStart.focus();
  } else {
    setBusy(false);
  }
  setStatus("");
}

function onTurnAccepted(data) {
  // The echo comes from the server, so neither the screen nor the log is optimistic. The
  // exception is a scripted line, which the player advanced onto the screen himself a
  // moment ago — re-showing it would duplicate the line in the log.
  if (state.pendingEcho) {
    state.pendingEcho = false;
  } else {
    say("player", "你", data.text);
    appendLine("player", "你", data.text);
  }
  state.waiting = "dialogue";
  setStatus("正在思考……", "working");
  renderCast();
  renderDialogue();
}

function onDialogue(data) {
  // The reply takes the box over: one line on screen, the rest in the log.
  state.currentNpcId = data.npc_id;
  say("npc", data.name, data.dialogue);
  state.waiting = "tick";
  appendLine("npc", data.name, data.dialogue);
  showRelationshipFeedback(data.relationship_changes || {});
  renderCast();
  // The tick's seconds are shown, not hidden: this is the wait the whole design
  // moved off the player's critical path, and naming it is more honest than a freeze.
  setStatus(`台词 ${num(data.latency_ms)}ms · 叙事引擎正在推进……`, "working");
}

let feedbackTimer = null;
function showRelationshipFeedback(changes) {
  const labels = { trust: "信任", fear: "警惕", respect: "尊重" };
  const parts = Object.entries(changes)
    .filter(([, value]) => Number(value))
    .map(([key, value]) => `${labels[key] || key}${Number(value) > 0 ? "+" : ""}${Number(value)}`);
  if (!parts.length || !ui.feedbackToast) return;
  ui.feedbackToast.textContent = parts.join("　");
  ui.feedbackToast.hidden = false;
  clearTimeout(feedbackTimer);
  feedbackTimer = setTimeout(() => { ui.feedbackToast.hidden = true; }, 3500);
}

function onMove(data) {
  if (!data.approved) {
    // The Validator's own sentence, shown as written. Explaining it here would mean
    // knowing the rule (docs/12 §13.2).
    state.waiting = null;
    setStatus(`过不去：${data.reason || "这条路走不通"}`, "error");
    setBusy(false);
    return;
  }

  // Travel goes in the log like a line does: the transcript is what happened, and "I
  // walked to the square" is part of that. Marked player-side, never as an NPC bubble.
  appendLine("player", "你", `（前往${data.name || data.destination}）`);
  // Arriving somewhere ends the previous exchange: whoever was talking is behind us, and
  // nothing is being said in the new place yet. Clearing `spokenFor` too, so the event
  // waiting at the new location can offer its own scripted lines.
  clearDialogue();
  state.currentNpcId = null;
  state.spokenFor = null;
  state.waiting = "tick";
  const first = data.first_visit ? " · 第一次来" : "";
  setStatus(`到了${data.name || data.destination}${first} · 叙事引擎正在推进……`, "working");
  if (data.out_of_days) setStatus("天数已经用尽了。", "error");
  renderCast();
}

function onTick(data) {
  state.waiting = null;
  const entry = data.entry || {};
  const op = entry.operator || "relieve";
  const ms = num(entry.latency_ms);
  setStatus(op === "relieve" ? `本轮无算子触发 · ${ms}ms` : `算子 ${op} · ${ms}ms`);
}

function onScene(scene) {
  if (!scene) return;
  state.scene = scene;
  state.currentNpcId = scene.current_npc_id ?? null;
  if (scene.ready !== undefined) {
    state.waiting = scene.ready ? null : state.waiting;
    setBusy(!scene.ready);
  }
  renderScene();
  if (scene.ready) {
    setStatus(scene.ending ? "调查结束。你仍可以查看对话记录。" :
      scene.time_slot === "wrap_up" ? "今天的调查结束了。盘点收获后，回家休息。" :
      scene.report_active ? "选择报告判断；提交最终报告将结束调查。暂不提交可继续调查。" :
      scene.event_in_progress ? "阅读对白后，选择一个行动，也可以自由输入。" :
      "可以前往其他地点继续调查。有人在场时，也可以留下自由交谈。");
  }
}

function onPanel(panel) {
  state.panel = panel;
  renderPanel();
  if (state.scene) renderDayProgress(state.scene);
}

function onFailed(data) {
  if (data.stage === "dialogue") {
    // The turn is lost; invite another attempt. The player's line comes down with it —
    // leaving it on screen would look like it is still waiting for an answer.
    clearDialogue();
    state.waiting = null;
    setStatus(`这一回合失败了：${data.error_type} — ${data.message}`, "error");
    setBusy(false);
  } else {
    // Only the *next* turn loses its setup; this turn's line still stands.
    state.waiting = null;
    setStatus(`叙事引擎这一轮失败了：${data.error_type}（台词不受影响）`, "error");
    setBusy(false);
  }
  renderCast();
}

// --- scene rendering ------------------------------------------------------

function renderScene() {
  const s = state.scene;
  // "第 2 天 · 下午" (docs/12 §13.1). Day and slot only: the 12-slot budget belongs to
  // the panel, since it is the kind of number a player would optimise against.
  ui.when.textContent = `第 ${s.time_day} 天 · ${slotLabel(s.time_slot)}`;
  renderDayProgress(s);
  ui.placeLabel.textContent = s.location?.name || "";
  ui.placeDesc.textContent = s.location?.description || "";
  ui.panelTurn.textContent = `第 ${s.turn} 轮 · 第 ${s.time_day} 天`;

  const facts = s.visible_facts || [];
  ui.notes.hidden = facts.length === 0;
  // Chips live in their own wrapping box so that a wrapped row lines up with the first
  // one instead of starting back at the label's edge.
  ui.notes.innerHTML =
    `<span class="label">已知</span><div class="chips">` +
    facts.map((f) => `<span class="note">${esc(f.value)}</span>`).join("") +
    `</div>`;

  renderWays(s.destinations || []);
  renderConclude();
  ui.ending.hidden = !s.ending;
  ui.endingTitle.textContent = s.ending?.title || "";
  ui.endingText.textContent = s.ending?.text || "";
  if (s.passages?.length) queuePassages(s);
  else queueScriptedLines(s.scripted_lines || []);
  // Held rather than rendered immediately: the pack sends both in one snapshot, so an
  // option is not itself conditioned on the scripted line ahead of it having been read.
  // `renderDialogue` below always ends by calling `renderComposerVisibility`, which is
  // what actually decides whether to show them — including once the queue drains.
  state.pendingOptions = s.options || [];
  renderCast();
  renderHistory(s.transcript_tail || []);
  renderDialogue();

  // The slot in the scene snapshot is the only signal needed; keeping a separate "are we
  // wrapping up" flag would be a second source of truth for one boolean.
  setWrapUp(s.time_slot === "wrap_up" && !s.ending);
}

function renderDayProgress(s) {
  const budget = state.panel?.slot_budget;
  if (!budget || s.time_slot === "wrap_up") { ui.dayProgress.hidden = true; return; }
  ui.dayProgress.hidden = false;
  const perDay = Math.max(1, Math.round((budget.total || 3) / (budget.day_limit || 1)));
  const spentToday = Math.max(0, budget.spent_total - (budget.day - 1) * perDay);
  ui.dayProgressLabel.textContent = `今日还可调查 ${Math.max(0, perDay - spentToday)} 个地点`;
  ui.dayProgressTrack.innerHTML = Array.from({length: perDay}, (_, i) => `<span class="day-segment ${i < spentToday ? "spent" : ""} ${i === spentToday ? "current" : ""}></span>`).join("");
}

/** Show or withhold the options tray, per the same "is it the player's move" test the
 * composer uses (docs/15 §1.1: they are equivalent inputs, so one rule governs both).
 * Called after anything that could change that answer — a fresh scene, or a scripted
 * line finishing — so the tray reliably appears the moment the last scripted line is
 * actually said, not the moment the server happened to mention it.
 */
function updateOptionsTray() {
  const idle =
    !state.busy &&
    !state.awaitingAdvance &&
    !state.wrapUpActive &&
    !state.scene?.ending;
  renderOptions(idle ? state.pendingOptions : []);
}

/** Offer the event's scripted lines for the player to say, one at a time.
 *
 * They are the player's own words (docs/15 §1), so they have to be *said* rather than
 * displayed: an event whose only content is a scripted line — F1 is one — otherwise put the
 * player's line on screen with nothing to click and no reply ever coming.
 *
 * Guarded by content because two `scene` snapshots arrive per turn; without that the speech
 * would be re-offered the moment the tick landed.
 */
function queuePassages(scene) {
  if (state.busy || state.passageId === scene.passage_id) return;
  state.passageId = scene.passage_id;
  state.authoredQueue = true;
  state.queue = [...scene.passages];
  showNextQueued();
}

function queueScriptedLines(lines) {
  const key = lines.join(" ");
  if (!lines.length || state.spokenFor === key) return;
  // Not while the player is mid-turn: the line would jump the queue in front of a reply
  // that is already on its way.
  if (state.busy) return;

  state.spokenFor = key;
  // The queue holds every line, the first one included, and `advance` is what actually says
  // each. Keeping the first queued is what makes a *single* scripted line work: it is read
  // in the box first, and the advance that follows is the act of saying it.
  state.queue = [...lines];
  state.authoredQueue = false;
  showNextQueued();
}

/** Put the next queued line in the box without sending it. */
function showNextQueued() {
  const next = state.queue[0];
  if (next === undefined) return;
  if (state.authoredQueue) {
    const kind = next.speaker === "player" ? "player" :
      next.speaker === "narrator" ? "narrator" : "npc";
    if (kind === "npc") state.currentNpcId = next.speaker;
    say(kind, next.name, next.text, { awaitAdvance: true });
    renderCast();
    return;
  }
  say("player", "你", next, { awaitAdvance: true });
}

function clearDialogue() {
  state.line = null;
  state.queue = [];
  state.awaitingAdvance = false;
  renderDialogue();
}

/* Visible labels for the option tags (docs/15 §2). Wording, so it lives here. */
const TAG_LABELS = {
  goodwill: "示好",
  press: "逼问",
  probe: "试探",
  observe: "观察",
};

/** Scripted lines and tagged options (docs/12 §13.7).
 *
 * Three rules, all of them the document's:
 * - a scripted line is the *player* speaking, so it renders player-side;
 * - with no options the area does not exist — its appearance is the signal;
 * - no consequence is shown. The payload carries none, so there is none to show.
 */
function renderOptions(options) {
  ui.options.hidden = options.length === 0;
  if (options.length === 0) {
    ui.options.innerHTML = "";
    return;
  }

  const buttons = options
    .map(
      (o) =>
        `<button type="button" class="option" data-option="${esc(o.option_id)}"
                 ${state.busy ? "disabled" : ""}>
           <span class="tag-label">[${esc(state.scene?.report_active ? (o.final_report ? "报告" : "返回") : TAG_LABELS[o.tag] || o.tag)}]</span>
           <span class="option-text">${esc(o.text)}</span>
         </button>`,
    )
    .join("");

  ui.options.innerHTML = buttons ? `<div class="option-list">${buttons}</div>` : "";
}

/** Swap between the scene and the wrap-up screen (docs/12 §13.3).
 *
 * A swap, not an overlay: the day is over, so there is nobody to talk to and no composer
 * to leave sitting there.
 */
async function setWrapUp(active) {
  ui.wrapup.hidden = !active;
  ui.scene.hidden = active;
  state.wrapUpActive = active;
  renderComposerVisibility();
  // There is nobody to say it to, so the options go with the composer. `renderOptions`
  // owns this element otherwise; hiding here only ever adds to what it decided.
  if (active) {
    ui.options.hidden = true;
    // The day's talking is over: nothing from the last exchange should survive into the
    // wrap-up, or it would reappear on screen when the next morning redraws.
    clearDialogue();
  }

  if (!active) return;
  // Fetched rather than pushed: it is a read of current state, and the scene event that
  // put us here already told us it exists.
  try {
    const res = await fetch(`/api/session/${state.sessionId}/wrap_up`);
    const view = res.ok ? await res.json() : null;
    if (view) renderWrapUp(view);
  } catch {
    setStatus("拿不到今天的整理。", "error");
  }
}

function renderWrapUp(view) {
  ui.wrapDay.textContent = `第 ${view.day} 天 · 夜里`;

  ui.wrapKnown.innerHTML = (view.known || [])
    .map((f) => `<span class="note">${esc(f.value)}</span>`)
    .join("");

  // No open questions is a state worth rendering as absence, not as an empty heading.
  const open = view.unanswered || [];
  ui.wrapOpenBlock.hidden = open.length === 0;
  // Verbatim (docs/12 §13.3): no "该去哪查" appended, no rephrasing into a lead.
  ui.wrapOpen.innerHTML = open.map((q) => `<li>${esc(q)}</li>`).join("");

  ui.wrapIntroduction.textContent = view.introduction;
  ui.wrapDiscovered.innerHTML = (view.discovered || []).length
    ? view.discovered.map((f) => `<li>${esc(f.value)}</li>`).join("")
    : `<li>${view.baseline_available ? "今天没有新增线索。已有记录仍可在下方查阅。" : "旧存档没有当天开始的记录，以下列出目前已知的线索。"}</li>`;
  const labels = {trust: "信任", fear: "恐惧", respect: "尊重"};
  ui.wrapRelationships.innerHTML = (view.relationship_changes || []).map((r) =>
    `<li>${esc(r.name)}：${Object.entries(labels).filter(([k]) => r[k]).map(([k, label]) =>
      `${label} ${r[k] > 0 ? "+" : ""}${num(r[k], 1)}`).join("，")}</li>`).join("") ||
    `<li>${view.baseline_available ? "今天的关系值没有变化。" : "旧存档缺少当天的关系基准，暂不推测变化。"}</li>`;

}

/** Where the player may go. The list is the server's; this only draws it.
 *
 * No filtering and no "you can't get there from here" logic: adjacency is a Validator
 * rule, and a copy of it here would be a second rule free to drift (docs/12 §13.2).
 */
function renderWays(destinations) {
  if (state.scene?.ending) destinations = [];
  ui.ways.hidden = destinations.length === 0;
  ui.waysList.innerHTML = destinations
    .map(
      (d) =>
        `<button type="button" class="way${d.visited ? " visited" : ""}"
                 data-destination="${esc(d.location_id)}" ${state.busy ? "disabled" : ""}>
           ${esc(d.name)}${d.visited ? "" : `<span class="fresh" aria-hidden="true">·</span>`}
           ${d.visited ? "" : `<span class="sr-only">（没去过）</span>`}
         </button>`,
    )
    .join("");
}

/** Put a line in the dialogue box. One box for every speaker, player included.
 *
 * The name carries who is talking, so the player's lines are exactly as prominent as the
 * NPC's — the previous per-speaker bubble left his line looking like a footnote, because
 * the player has no sprite to anchor a bubble to.
 */
function say(kind, who, text, { awaitAdvance = false } = {}) {
  state.line = { kind, who, text };
  state.awaitingAdvance = awaitAdvance;
  renderDialogue();
}

function renderDialogue() {
  const line = state.line;
  const thinking = state.waiting === "dialogue" && !line;

  ui.dialogue.hidden = !line && !thinking;
  if (!line && !thinking) {
    renderComposerVisibility();
    return;
  }

  if (thinking) {
    // Someone is composing a reply. Shown in the box rather than over the sprite so the
    // wait happens where the words will appear (docs/12 §5.3: visible, not hidden).
    const npc = state.scene?.npcs?.find((entry) => entry.npc_id === state.currentNpcId);
    ui.dialogueName.textContent = npc?.name || "";
    ui.dialogueName.className = "dialogue-name npc";
    ui.dialogueText.innerHTML = `<span class="thinking">在想<span class="dots"><i></i><i></i><i></i></span></span>`;
    ui.advance.hidden = true;
    ui.dialogue.classList.toggle("advanceable", false);
    renderComposerVisibility();
    return;
  }

  ui.dialogueName.textContent = line.who;
  ui.dialogueName.className = `dialogue-name ${line.kind}`;
  ui.dialogueText.textContent = line.text;
  ui.advance.hidden = !state.awaitingAdvance;
  ui.dialogue.classList.toggle("advanceable", state.awaitingAdvance);
  renderComposerVisibility();
}

/** Show the composer, and the options tray beside it, only when it is actually the
 * player's move: NPC and scripted lines are read on their own, and the typing box (or a
 * tagged option that answers a line not yet read) would otherwise sit on screen ahead of
 * content that is not there yet, competing with the scene for attention. One function
 * for both — docs/15 §1.1 makes them equivalent inputs, so they must appear and vanish
 * together rather than on two rules that can drift apart.
 *
 * "The player's move" is: not mid-turn (`busy`), not reading a queued scripted line
 * that still needs advancing (`awaitingAdvance`), and not at the wrap-up (no one to
 * talk to there — `setWrapUp` already owns hiding it for that case, but the check is
 * repeated here so a stray call from `renderDialogue` cannot re-show it underneath).
 */
function renderConclude() {
  ui.conclude.hidden = false;
  const reason = state.scene?.conclude_reason || (state.busy ? "请等待当前回合完成。" :
    state.awaitingAdvance ? "请先读完当前对白。" : "");
  ui.conclude.disabled = !state.scene?.can_conclude || state.busy || state.awaitingAdvance;
  ui.conclude.title = reason;
  ui.concludeReason.textContent = reason;
}

function renderComposerVisibility() {
  renderConclude();
  const idle =
    !state.busy &&
    !state.awaitingAdvance &&
    !state.wrapUpActive &&
    !state.scene?.ending &&
    !state.scene?.report_active &&
    state.currentNpcId !== null &&
    (state.scene?.npcs || []).length > 0;
  ui.composer.hidden = !idle;
  updateOptionsTray();
}

/** Say the next queued scripted line, or submit the turn if that was the last one.
 *
 * The final line is what sends the turn (docs/15 §1.1's shared path — the same
 * ``POST /turn`` a typed sentence or a clicked option takes), so the NPC answers a scripted
 * line like any other. Without this the player was shown his own words with nothing to do
 * and no reply coming.
 */
async function advance() {
  if (!state.awaitingAdvance || state.busy) return;

  // The line on screen is the head of the queue; advancing is what says it.
  const spoken = state.queue.shift();
  if (spoken === undefined) return;

  if (state.authoredQueue) {
    if (state.queue.length) showNextQueued();
    else {
      state.awaitingAdvance = false;
      state.currentNpcId = state.scene?.current_npc_id ?? null;
      renderDialogue();
      renderCast();
    }
    return;
  }

  appendLine("player", "你", spoken);

  if (state.queue.length > 0) {
    showNextQueued();
    return;
  }

  // That was the last line. The whole speech counts as one turn — two opening lines should
  // not cost two — and it goes out by the same path a typed sentence or a clicked option
  // takes (docs/15 §1.1), so the NPC answers it like anything else.
  state.awaitingAdvance = false;
  renderDialogue();
  await submitTurn(spoken, null, { echo: false });
}

function renderCast() {
  if (state.scene?.ending) {
    ui.cast.innerHTML = "";
    return;
  }
  if (state.scene?.report_active) {
    ui.cast.innerHTML = `<p class="cast-empty">调查报告 · 个人记录</p>`;
    return;
  }
  const npcs = state.scene?.npcs || [];
  if (npcs.length === 0) {
    ui.cast.innerHTML = `<p class="cast-empty">这里没有人。</p>`;
    return;
  }
  ui.cast.innerHTML = npcs
    .map((npc) => {
      const isSpeaking =
        state.line?.kind === "npc" && npc.npc_id === state.currentNpcId;
      // The note is public, ungated pack content (NPCWorldState.public_note) — never
      // an excerpt of persona.background, which runs on into what the player must earn.
      // Text-only for now (docs/12 §8's manual pass is what would restore sprite/art):
      // the mechanics under review here do not need a drawn figure to make their point.
      return `
        <div class="actor ${isSpeaking ? "speaking" : ""}">
          <span class="nameplate">${esc(npc.name)}</span>
          ${npc.note ? `<span class="who-note">${esc(npc.note)}</span>` : ""}
        </div>`;
    })
    .join("");
}

/** The full record, behind the toggle. Every line goes in, the player's own included.
 *
 * Kept up to date whether or not it is open: it is a record, so it must be complete the
 * moment someone asks to see it rather than filled in from that point on.
 */
function renderHistory(lines) {
  ui.history.innerHTML = lines.map((l) => logLine(l.speaker, l.name, l.text)).join("");
  scrollLogToEnd();
}

/** One row of the log. The player's lines are marked, not merely coloured.
 *
 * The marker is a glyph plus the name, because colour alone would not survive a
 * colour-blind reader or a greyscale screen — the same reason the ledger writes "⚠️ 超期"
 * instead of turning a row red.
 */
function logLine(speaker, name, text) {
  const isPlayer = speaker === "player";
  const who = isPlayer ? "你" : name || speaker;
  const mark = isPlayer ? "▸" : "—";
  return `<div class="line ${isPlayer ? "player" : "npc"}">
    <span class="who"><span class="mark" aria-hidden="true">${mark}</span>${esc(who)}</span>
    <span class="said">${esc(text)}</span>
  </div>`;
}

function appendLine(kind, who, text) {
  const div = document.createElement("div");
  div.innerHTML = logLine(kind === "player" ? "player" : "npc", who, text);
  ui.history.appendChild(div.firstElementChild);
  scrollLogToEnd();
}

function scrollLogToEnd() {
  // Only meaningful while open; a hidden element has no scroll height to speak of, so the
  // toggle re-runs this when it opens.
  ui.history.scrollTop = ui.history.scrollHeight;
}

function setLogOpen(open) {
  state.logOpen = open;
  ui.history.hidden = !open;
  ui.logToggle.setAttribute("aria-expanded", open ? "true" : "false");
  ui.logToggle.classList.toggle("open", open);
  if (open) scrollLogToEnd();
}

// --- panel rendering ------------------------------------------------------

function setPanelOpen(open) {
  state.panelOpen = open;
  ui.panel.hidden = !open;
  ui.app.classList.toggle("panel-open", open);
  ui.toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

const OP_LABELS = {foreshadow: "剧本伏笔", reveal: "揭示", escalate: "施压", reverse: "重新理解", relieve: "平静回合"};
const FIELD_HELP = {
  trust: "信任：此 NPC 对目标的信赖程度，范围 -100 至 100。关系有方向，并非双方共享。",
  fear: "恐惧：此 NPC 对目标的畏惧程度，范围 -100 至 100。升高可能使其闭口，不等于好感增加。",
  respect: "尊重：此 NPC 对目标能力或判断的认可，范围 -100 至 100。",
  importance: "重要度（0—1）：用于情节记忆的检索排序和遗忘衰减，不是相关度。",
  confidence: "确信程度（0—1）：NPC 对这条信念有多确信；高分也可能是误解。",
  score: "情节记忆这里显示重要度，语义记忆显示确信程度；不是向量相似度。",
  npc_id: "角色的内部唯一标识。", name: "角色的显示名字。",
  alive: "世界状态中该角色是否存活。", location: "角色当前所在地点的 ID。",
  faction_id: "角色所属阵营；空值表示未设置。", sprite_key: "角色立绘的样式标识。",
  persona: "剧本配置的相对稳定的人格设定。", traits: "剧本定义的人格倾向及强度，供模型扮演时参考。",
  background: "角色背景设定。", goal: "角色自己的目标，和玩家目标可能不同。",
  primary: "角色的首要目标。", secondary: "角色的次要目标。", emotion: "角色当前情绪标签。",
  beliefs: "角色的主观认知，可能与世界事实不符。", world_state: "世界记录的客观状态。",
  agent_state: "NPC 的人格、目标、情绪和信念；这里不代表所有字段都会逐回合自动改变。",
  relationships: "从此 NPC 出发的全部已记录关系；没有记录的 NPC 间关系不会虚构显示。",
  memory: "该 NPC 的完整长期记忆库，不只显示最近检索命中的条目。",
  episodic: "情节记忆：角色经历或记住的具体事件。", semantic: "语义记忆：角色持有的概括和信念。",
  retrieved_memory: "该角色最近一次对话实际取出的记忆；切换地点不会换成其他角色的记录。",
  memory_id: "记忆条目的唯一标识。", event_description: "角色记住的事件描述。", fact: "NPC 持有的信念文本，不保证为真。",
  occurred_at_day: "记忆中事件发生的游戏天数。", created_at: "记忆创建时间。", last_updated: "该关系或状态最后写入时间。",
  tools: "这个 NPC 能调用的工具。", proposals: "此 NPC 提交的行动及校验结果，保留本次会话最近 40 条。",
  action_type: "申请执行的动作类型。", target_id: "动作要作用的对象。", approved: "校验器是否允许执行此动作。",
  rule_name: "产生拒绝的具体规则。", reason: "规则给出的可读原因。",
  defaulted_to_dialogue_player: "是否因关系提案漏填目标而使用本次对话的玩家 ID。显式非法目标不会被替换。",
  steps: "此 NPC 最近一次调用的执行记录：规划、工具调用、校验和对白等。",
  step_name: "这一步的名称。", input_summary: "这一步实际使用的输入摘要。", output_summary: "这一步产生的结果摘要。",
  token_usage: "模型供应商返回的 Token 使用量。", model_used: "实际使用的模型；空值通常是纯规则步骤。",
  events: "剧本中由这个 NPC 参与的事件及当前状态。", event_id: "剧本事件的唯一标识。",
  operator: "事件的叙事用途标签；不是一位隐藏的全能 AI 编剧。", delivery: "呈现方式：对话、选择或旁白。",
  locations: "允许触发事件的地点。", active: "这个事件是否正在进行。", completed: "是否已完成过此事件。",
  trigger: "剧本写明的触发条件。", mode: "all 要全部满足；any 只需满足一项。", clauses: "条件子句列表。",
  path: "条件读取的世界状态路径。", op: "比较方式，例如 gte 表示大于等于。", value: "剧本定义的目标值或事实内容。",
  turn: "游戏回合号。", text: "实际记录的内容。", kind: "记忆种类。",
  last_turn: "这个 NPC 最近参与对话的回合。", last_dialogue: "这个 NPC 最近输出的对白。",
  last_turn_cost: "这个 NPC 最近一次模型交互的开销。", llm_calls: "实际模型调用次数。",
  tokens: "记录到的 Token 总量。", total_latency_ms: "总耗时（毫秒）。", latency_ms: "该步骤耗时（毫秒）。", trace_id: "用于查阅完整调用过程的追踪标识。",
  relationship_trend: "该 NPC 对玩家的关系变化采样，最近 40 点；恢复存档后从当前值开始重新记录。",
  trigger_reason: "触发本事件的规则原因；事件选择由剧本条件及引擎完成。",
  foreshadow: "只呈现剧本预定义的伏笔，并指向已定义的回收目标。已禁止模型自由创作伏笔。",
  reveal: "呈现已由规则允许的事实或事件，不允许模型凭空创造证据。",
  escalate: "加强已有处境的压力，不新增案件事实。", reverse: "重新解释已知信息，不改变事实本身。",
  relieve: "本轮没有新叙事事件，或节奏规则安排缓冲。",
  章节: "剧本当前章节编号。", 回合: "游戏已推进的回合数。", 张力: "引擎记录的压力值，0—1；不是 NPC 的情绪。",
  埋设总数: "历史上登记过的剧本伏笔数量，不是玩家还差多少线索。",
  天: "当前游戏天数 / 剧本允许的调查天数。", 时段: "上午、下午、夜里或免费的每日收束。",
  已用: "累计消耗的调查时段 / 总预算；每日收束不计费。", 剩余: "还能消耗的调查时段数。",
  "LLM 调用": "本回合的模型调用次数。", 延迟: "记录的耗时，单位毫秒。",
  叙事进度: "剧本的章节、回合及压力状态。", 时段预算: "调查时间预算。",
  伏笔账本: "预定义伏笔的登记及回收条件；不再由模型发明新线索。",
  解锁进度: "隐藏事实的条件和当前值，仅开发者可见。",
  "被拒的 Proposal": "校验未通过的动作，没有按该提案改变世界。",
  算子时间线: "每轮事件的用途、选择原因、阻塞原因及生成的文本。当前剧本路径由规则推进。",
  关系值走势: "按单个 NPC 展示，避免不同 NPC 的数值连成一条误导曲线。",
  记忆检索: "该 NPC 最近对话检索的记忆及重要度/确信程度。",
  本回合开销: "最近一次交互的调用开销和待使用的剧情内容。",
  所有角色: "按角色展开查看世界状态、关系、完整记忆、事件和本次会话活动。",
};

function help(key) {
  const description = FIELD_HELP[key] || `剧本或记录中的「${key}」字段；下方是当前保存的值，未设置时显示为空。`;
  return `<span class="field-help" tabindex="0" role="img" aria-label="${esc(description)}" title="${esc(description)}">!</span>`;
}

function debugFields(value, path) {
  if (value === null || value === undefined) return `<span class="debug-value">未设置</span>`;
  if (typeof value !== "object") return `<span class="debug-value">${esc(typeof value === "boolean" ? (value ? "是" : "否") : value)}</span>`;
  if (!Object.keys(value).length) return empty("暂无记录");
  return `<div class="debug-fields">${Object.entries(value).map(([key, item]) => {
    const label = Array.isArray(value) ? `记录 ${Number(key) + 1}` : key;
    const caption = `${esc(label)}${help(Array.isArray(value) ? path.split(".").at(-1) : key)}`;
    return item && typeof item === "object"
      ? `<details class="debug-field" data-key="${esc(path + "." + key)}"><summary>${caption}</summary>${debugFields(item, path + "." + key)}</details>`
      : `<div class="debug-field">${caption}：${debugFields(item, path + "." + key)}</div>`;
  }).join("")}</div>`;
}

function renderNpcs(p) {
  const rows = p.npcs || [];
  return block("所有角色", rows.length, rows.map(npc => {
    const relations = Object.entries(npc.relationships || {}).map(([target, rel]) => {
      const name = rows.find(r => r.npc_id === target)?.name || target;
      return `<div class="row"><div>对 ${esc(name)}${help("relationships")}</div><div class="stats">${
        Object.entries({trust: "信任", fear: "恐惧", respect: "尊重"}).map(([k, label]) =>
          `<div class="stat">${label}${help(k)}<div class="v">${num(rel[k], 1)}</div></div>`).join("")
      }</div></div>`;
    }).join("");
    const {relationship_trend, relationships, npc_id, name, ...detail} = npc;
    return `<details class="block npc-card" data-key="npc:${esc(npc.npc_id)}">
      <summary>${esc(npc.name)} · ${esc(npc.npc_id)}${help("npc_id")}</summary>
      <div class="block-body">${relations}${debugFields(detail, npc.npc_id)}${renderTrend(npc)}</div>
    </details>`;
  }).join(""));
}

function block(title, count, body, { open = true, alert = false } = {}) {
  const badge =
    count === null || count === undefined
      ? ""
      : `<span class="count ${alert ? "alert" : ""}">${esc(count)}</span>`;
  return `<details class="block" data-key="${esc(title)}" ${open ? "open" : ""}>
    <summary>${esc(title)}${help(title)}${badge}</summary>
    <div class="block-body">${body}</div>
  </details>`;
}

const empty = (text) => `<p class="empty">${esc(text)}</p>`;

function renderPanel() {
  const p = state.panel;
  if (!p) return;
  const expanded = new Map([...ui.panelBody.querySelectorAll("details[data-key]")].map(d => [d.dataset.key, d.open]));
  ui.panelBody.innerHTML = [
    renderBeats(p),
    renderBudget(p),
    renderLedger(p),
    renderUnlock(p),
    renderRejected(p),
    renderTimeline(p),
    renderNpcs(p),
    renderCost(p),
  ].join("");
  ui.panelBody.classList.toggle("gauge-mode", state.panelGauge);
  if (state.panelGauge) renderGaugeSummary(p);
  ui.panelBody.querySelectorAll("details[data-key]").forEach(d => {
    if (expanded.has(d.dataset.key)) d.open = expanded.get(d.dataset.key);
  });
  ui.panelBody.querySelectorAll(".k").forEach(k => { k.innerHTML += help(k.textContent); });
}

function renderGaugeSummary(p) {
  const b = p.beats || {}, budget = p.slot_budget || {};
  const tension = Math.max(0, Math.min(100, Math.round(Number(b.tension || 0) * 100)));
  const remaining = Number(budget.remaining ?? 0), total = Number(budget.total || 1);
  const gauge = document.createElement("div");
  gauge.className = "gauge-dashboard";
  gauge.innerHTML = `<div class="gauge-ring" style="--value:${tension * 3.6}deg"><div><strong>${tension}%</strong><small>叙事张力</small></div></div><div class="gauge-stats"><div><b>${remaining}</b><span>剩余时段</span></div><div><b>${budget.day ?? "—"}</b><span>当前天数</span></div><div><b>${total - remaining}/${total}</b><span>已用预算</span></div></div><p class="gauge-hint">聚合关键参数；展开下方分组可查看完整追踪信息。</p>`;
  ui.panelBody.prepend(gauge);
}

function renderBeats(p) {
  const b = p.beats || {};
  const pct = Math.round((b.tension || 0) * 100);
  const body = `
    <div class="stats">
      <div class="stat"><div class="k">章节</div><div class="v">${b.chapter ?? "—"}</div></div>
      <div class="stat"><div class="k">回合</div><div class="v">${b.turn ?? "—"}</div></div>
      <div class="stat">
        <div class="k">张力</div><div class="v">${num(b.tension, 2)}</div>
        <div class="meter tension"><i style="width:${pct}%"></i></div>
      </div>
      <div class="stat"><div class="k">埋设总数</div><div class="v">${b.planted_total ?? 0}</div></div>
    </div>
    ${
      b.spent_one_shots?.length
        ? `<div class="why">已用一次性：${b.spent_one_shots.map(esc).join("、")}</div>`
        : ""
    }`;
  return block("叙事进度", null, body);
}

/** The slot budget (docs/12 §13.1): how many chances are left, not the story's shape. */
function renderBudget(p) {
  const b = p.slot_budget;
  if (!b) return "";

  // Server-computed. Recomputing spent/total here would be a second copy of the one
  // piece of arithmetic docs/12 §13.1 warns is easy to get wrong.
  const pct = b.total ? Math.round((b.spent_total / b.total) * 100) : 0;
  const body = `
    <div class="stats">
      <div class="stat"><div class="k">天</div><div class="v">${b.day} / ${b.day_limit}</div></div>
      <div class="stat"><div class="k">时段</div><div class="v">${esc(slotLabel(b.slot))}</div></div>
      <div class="stat">
        <div class="k">已用</div><div class="v">${b.spent_total} / ${b.total}</div>
        <div class="meter"><i style="width:${pct}%"></i></div>
      </div>
      <div class="stat"><div class="k">剩余</div><div class="v">${b.remaining}</div></div>
    </div>
    ${b.wrapping_up ? `<div class="why">收束段不占时段，所以「已用 ${b.spent_total}」在这里是对的。</div>` : ""}
    ${b.out_of_days ? `<div class="why">⚠️ 天数已用尽</div>` : ""}`;
  return block("时段预算", `${b.remaining} 剩`, body, { alert: b.out_of_days });
}

function renderLedger(p) {
  const rows = p.ledger || [];
  const overdue = rows.filter((r) => r.overdue).length;
  const body = rows.length
    ? rows
        .map(
          (r) => `
      <div class="row">
        <div class="row-head">
          <span class="id">${esc(r.label)}</span>
          <span class="tag ${r.overdue ? "warn" : ""}">${r.overdue ? "⚠️ 超期" : "正常"}</span>
        </div>
        <div class="why">埋于第 ${r.planted_at_turn} 回合 · 已欠 ${r.turns_owed} 轮（阈值 ${r.overdue_after_turns}）</div>
        <div class="clauses">${clauses(r.payoff_clauses)}</div>
      </div>`,
        )
        .join("")
    : empty("没有未回收的伏笔");
  return block("伏笔账本", rows.length, body, { alert: overdue > 0 });
}

function clauses(list) {
  return (list || [])
    .map((c) => {
      if (!c.resolvable) {
        return `<span class="clause unresolved">${esc(c.path)} = ?(路径无法解析)</span>`;
      }
      const actual = typeof c.actual === "number" ? num(c.actual) : esc(c.actual);
      return `<span class="clause ${c.met ? "met" : ""}">${c.met ? "✓" : "·"} ${esc(c.label)} ${actual}/${esc(c.expected)}</span>`;
    })
    .join("");
}

function renderUnlock(p) {
  const rows = p.unlock_board || [];
  const body = rows.length
    ? rows
        .map((r) => {
          const pct = Math.round((r.clauses.filter((c) => c.met).length / r.clauses.length) * 100 || 0);
          return `
      <div class="row">
        <div class="row-head">
          <span class="id">${esc(r.fact_id)}</span>
          <span class="tag ${r.unlockable ? "ok" : ""}">${r.mode === "any" ? "任一" : "全部"}</span>
        </div>
        <div class="clauses">${clauses(r.clauses)}</div>
        <div class="meter"><i style="width:${pct}%"></i></div>
      </div>`;
        })
        .join("")
    : empty("没有待解锁的事实");
  return block("解锁进度", rows.length, body);
}

function renderRejected(p) {
  const rows = p.rejected_proposals || [];
  const body = rows.length
    ? rows
        .map(
          (r) => `
      <div class="row">
        <div class="row-head">
          <span class="tag danger">✗</span>
          <span class="id">第 ${r.turn} 轮 ${esc(r.actor)} ${esc(r.action_type)}${r.target_id ? `(${esc(r.target_id)})` : ""}</span>
        </div>
        <div class="why">${esc(r.rule_name || "")} — ${esc(r.reason || "")}</div>
      </div>`,
        )
        .join("")
    : empty("还没有被拒绝的提议");
  // The most under-rated block: direct evidence a rule stopped the model.
  return block("被拒的 Proposal", rows.length, body, { alert: rows.length > 0 });
}

function renderTimeline(p) {
  const rows = p.operator_timeline || [];
  const body = rows.length
    ? rows
        .map(
          (r) => `
      <div class="beat">
        <span class="turn">${r.turn}</span>
        <div>
          <span class="op op-${esc(r.operator)}">${esc(OP_LABELS[r.operator] || r.operator)}${help(r.operator)}</span>
          <div class="why">触发原因${help("trigger_reason")}：${esc(r.trigger_reason || "没有满足条件的事件，或节奏规则暂缓触发。")}</div>
          ${r.starved ? `<span class="tag warn">已停摆</span>` : ""}
          ${r.event_type ? `<span class="id"> ${esc(r.event_type)}</span>` : ""}
          ${r.hook ? `<div class="hook">${esc(r.hook)}</div>` : ""}
          ${
            (r.blocked || []).length
              ? `<div class="blocked">被挡：${r.blocked.map((b) => `${esc(b.operator)} — ${esc(b.reason)}`).join("；")}</div>`
              : ""
          }
        </div>
      </div>`,
        )
        .join("")
    : empty("还没有任何回合");
  return block("算子时间线", rows.length, body, { open: false });
}

//: Smallest span the trend will show. Autoscaling alone would blow a 1-point drift up
//: into a dramatic swing; a floor keeps small changes looking small while still letting
//: real movement fill the band. The axis labels state the actual bounds either way,
//: because an unlabelled autoscaled chart misleads about magnitude by construction.
const TREND_MIN_SPAN = 20;

function renderTrend(p) {
  const pts = p.relationship_trend || [];
  if (pts.length < 2) {
    return block("关系值走势", null, empty("数据点不足（需要至少两个回合）"), { open: false });
  }

  const keys = ["trust", "fear", "respect"];
  const values = pts.flatMap((pt) => keys.map((k) => pt[k]));
  let lo = Math.min(...values);
  let hi = Math.max(...values);

  // Widen a too-narrow window symmetrically around the data, clamped to the schema's
  // own -100..100 so the axis never claims a value the model could not hold.
  if (hi - lo < TREND_MIN_SPAN) {
    const mid = (hi + lo) / 2;
    lo = Math.max(-100, mid - TREND_MIN_SPAN / 2);
    hi = Math.min(100, mid + TREND_MIN_SPAN / 2);
  }
  const pad = (hi - lo) * 0.12;
  lo = Math.max(-100, lo - pad);
  hi = Math.min(100, hi + pad);

  const w = 100;
  const h = 100; // taller than before; CSS gives it real height
  const x = (i) => (i / (pts.length - 1)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * h;
  const path = (key) =>
    pts.map((pt, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(pt[key]).toFixed(1)}`).join(" ");

  const last = pts[pts.length - 1];
  const first = pts[0];
  const delta = (key) => {
    const d = last[key] - first[key];
    return d === 0 ? "±0" : `${d > 0 ? "+" : ""}${num(d)}`;
  };

  // A zero line only when it is inside the visible window.
  const zero = lo < 0 && hi > 0 ? `<line class="zero-line" x1="0" y1="${y(0).toFixed(1)}" x2="${w}" y2="${y(0).toFixed(1)}" />` : "";

  const body = `
    <div class="trend-wrap">
      <div class="trend-axis"><span>${num(hi)}</span><span>${num(lo)}</span></div>
      <svg class="trend" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
           aria-label="关系值走势，纵轴 ${num(lo)} 到 ${num(hi)}：trust ${num(last.trust)}（${delta("trust")}），fear ${num(last.fear)}（${delta("fear")}），respect ${num(last.respect)}（${delta("respect")}）">
        ${zero}
        <path class="trust" d="${path("trust")}" />
        <path class="fear" d="${path("fear")}" />
        <path class="respect" d="${path("respect")}" />
        ${keys
          .map(
            (k) =>
              `<circle class="dot ${k}" cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last[k]).toFixed(1)}" r="1.8" />`,
          )
          .join("")}
      </svg>
    </div>
    <div class="legend">
      <span class="k-trust">trust <b>${num(last.trust)}</b> <i>${delta("trust")}</i></span>
      <span class="k-fear">fear <b>${num(last.fear)}</b> <i>${delta("fear")}</i></span>
      <span class="k-respect">respect <b>${num(last.respect)}</b> <i>${delta("respect")}</i></span>
    </div>
    <div class="why">${pts.length} 个采样点 · 纵轴按实际范围缩放</div>`;
  return block("关系值走势", null, body, { open: false }).replace('data-key="关系值走势"', `data-key="trend:${esc(p.npc_id || "current")}"`);
}

function renderMemory(p) {
  const hits = p.memory || [];
  const counts = p.memory_counts || {};
  const body = hits.length
    ? hits
        .map(
          (m) => `
      <div class="mem ${esc(m.kind)}">
        <span class="score">${m.kind === "episodic" ? "●" : "~"} ${num(m.score, 2)}</span>
        <span>${esc(m.text)}</span>
      </div>`,
        )
        .join("")
    : empty("这一回合没有检索记录");
  const label = `${counts.episodic ?? 0}/${counts.semantic ?? 0}`;
  return block("记忆检索", label, body, { open: false });
}

function renderCost(p) {
  const c = p.last_turn_cost;
  const pending = p.pending_hook;
  const body = `
    ${
      c
        ? `<div class="stats">
             <div class="stat"><div class="k">LLM 调用</div><div class="v">${c.llm_calls}</div></div>
             <div class="stat"><div class="k">tokens</div><div class="v">${c.tokens}</div></div>
             <div class="stat"><div class="k">延迟</div><div class="v">${num(c.total_latency_ms)}ms</div></div>
           </div>
           ${c.trace_id ? `<div class="why">trace <a href="/debug/trace/${esc(c.trace_id)}" target="_blank" rel="noopener">${esc(c.trace_id.slice(0, 12))}…</a></div>` : ""}`
        : empty("还没有回合")
    }
    ${pending ? `<div class="why">下一回合的铺垫：${esc(pending)}</div>` : ""}`;
  return block("本回合开销", null, body, { open: false });
}

// --- interactions ---------------------------------------------------------

/** Submit a turn. The one path for both typing and clicking (docs/12 §13.7).
 *
 * Deliberately not two functions: two submit paths would let the two forms be judged
 * differently, and docs/15 §1.1 exists so that typing is never the worse deal.
 */
async function submitTurn(text, optionId = null, { echo = true } = {}) {
  setBusy(true);
  // A scripted line is already on screen and in the log, having been advanced there by the
  // player; `turn_accepted` must not add it twice.
  state.pendingEcho = !echo;

  const res = await fetch(`/api/session/${state.sessionId}/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(optionId ? { text, option_id: optionId } : { text }),
  });

  if (res.status === 409) {
    setStatus("上一回合还没结束，稍等一下。", "error");
    return; // stays disabled; the tick event will release it
  }
  if (!res.ok) {
    setStatus(`发送失败：${res.status}`, "error");
    setBusy(false);
  }
}

ui.composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = ui.say.value.trim();
  if (!text) return;

  ui.say.value = "";
  await submitTurn(text);
});

/* Delegated: the option buttons are replaced on every `scene` snapshot. */
ui.options.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-option]");
  if (!button || state.busy) return;
  const option = state.pendingOptions.find(o => o.option_id === button.dataset.option);
  if (option?.final_report && !window.confirm(`${option.text}\n\n提交后将结束本次调查。确定提交这份最终报告吗？`)) return;

  // The option's own text is what the player said, so it goes in as the utterance —
  // exactly what typing that sentence would have sent.
  const text = button.querySelector(".option-text")?.textContent?.trim() || "";
  await submitTurn(text, button.dataset.option);
});

ui.wrapClose.addEventListener("click", async () => {
  ui.wrapClose.disabled = true;
  const res = await fetch(`/api/session/${state.sessionId}/wrap_up/close`, { method: "POST" });
  if (!res.ok) {
    setStatus(`天还没亮：${res.status}`, "error");
    ui.wrapClose.disabled = false;
    return;
  }
  // The scene event that follows turns the page back to the morning; re-enable for the
  // next evening.
  ui.wrapClose.disabled = false;
});

/* Delegated, because the buttons are replaced on every `scene` snapshot. */
ui.waysList.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-destination]");
  if (!button || state.busy) return;

  const destination = button.dataset.destination;
  setBusy(true);
  state.waiting = "move";
  setStatus("正在赶路……", "working");

  const res = await fetch(`/api/session/${state.sessionId}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ destination }),
  });

  if (res.status === 409) {
    setStatus("上一回合还没结束，稍等一下。", "error");
    return; // stays disabled; the tick releases it
  }
  if (!res.ok) {
    setStatus(`出发失败：${res.status}`, "error");
    setBusy(false);
  }
});

ui.conclude.addEventListener("click", async () => {
  if (ui.conclude.disabled || state.busy) return;
  setBusy(true);
  state.waiting = "move";
  setStatus("正在整理调查报告……", "working");

  const res = await fetch(`/api/session/${state.sessionId}/conclude`, { method: "POST" });

  if (res.status === 409) {
    setStatus("上一回合还没结束，稍等一下。", "error");
    return; // stays disabled; the scene refresh releases it
  }
  if (!res.ok) {
    setStatus(`未能开始：${res.status}`, "error");
    setBusy(false);
  }
});

/* Advancing the dialogue: click the box, or press space/enter. The box is the target
 * because it is where the words are — hunting for a separate button would be worse. */
ui.dialogue.addEventListener("click", () => advance());

document.addEventListener("keydown", (e) => {
  if (e.key !== " " && e.key !== "Enter") return;
  // Never while typing: space belongs to the sentence being written, and Enter submits it.
  // Same for the wrap-up's own button, which has its own Enter behaviour.
  const active = document.activeElement;
  if (active === ui.say || active === ui.wrapClose) return;
  if (!state.awaitingAdvance) return;

  e.preventDefault(); // space would otherwise scroll the scene
  advance();
});

ui.logToggle.addEventListener("click", () => setLogOpen(!state.logOpen));

ui.toggle.addEventListener("click", () => setPanelOpen(!state.panelOpen));

ui.panelViewToggle.addEventListener("click", () => {
  state.panelGauge = !state.panelGauge;
  ui.panelViewToggle.setAttribute("aria-pressed", state.panelGauge ? "true" : "false");
  ui.panelViewToggle.textContent = state.panelGauge ? "☷ 详细" : "◉ 表盘";
  renderPanel();
});

let resizingPanel = false;
ui.panelResizer.addEventListener("pointerdown", (event) => {
  resizingPanel = true;
  ui.panelResizer.setPointerCapture(event.pointerId);
  document.body.classList.add("resizing-panel");
});
ui.panelResizer.addEventListener("pointermove", (event) => {
  if (!resizingPanel) return;
  const width = Math.max(280, Math.min(window.innerWidth * 0.65, window.innerWidth - event.clientX));
  document.documentElement.style.setProperty("--panel-width", `${width}px`);
});
ui.panelResizer.addEventListener("pointerup", () => { resizingPanel = false; document.body.classList.remove("resizing-panel"); });
ui.panelResizer.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--panel-width"));
  document.documentElement.style.setProperty("--panel-width", `${Math.max(280, current + (event.key === "ArrowLeft" ? 24 : -24))}px`);
  event.preventDefault();
});

ui.introStart.addEventListener("click", () => {
  ui.intro.hidden = true;
  setBusy(false);
});

boot();
