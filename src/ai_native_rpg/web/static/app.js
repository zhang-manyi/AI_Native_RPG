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

import { backdrop } from "/static/backdrops.js";
import { sprite } from "/static/sprites.js";

const el = (id) => document.getElementById(id);

const ui = {
  app: el("app"),
  title: el("title"),
  meta: el("backend-meta"),
  toggle: el("panel-toggle"),
  backdrop: el("backdrop"),
  placeName: el("place-name"),
  placeDesc: el("place-desc"),
  cast: el("cast"),
  notes: el("notes"),
  history: el("history"),
  status: el("status"),
  composer: el("composer"),
  say: el("say"),
  send: el("send"),
  intro: el("intro"),
  introTitle: el("intro-title"),
  introPremise: el("intro-premise"),
  introGoal: el("intro-goal"),
  introStart: el("intro-start"),
  panel: el("panel"),
  panelBody: el("panel-body"),
  panelTurn: el("panel-turn"),
};

const state = {
  sessionId: null,
  devMode: false,
  scene: null,
  panel: null,
  speaking: null, // { npcId, text } — the bubble currently on screen
  waiting: null, // 'dialogue' | 'tick' | null
  panelOpen: false,
  resumed: false, // continuing a save: suppresses the turn-0 intro screen
};

// --- helpers ---------------------------------------------------------------

const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const num = (v, digits = 0) => (typeof v === "number" ? v.toFixed(digits) : "—");

function setStatus(text, kind = "") {
  ui.status.className = `status ${kind}`.trim();
  ui.status.innerHTML = kind === "working" ? `<span class="spinner"></span>${esc(text)}` : esc(text);
}

/** Lock the composer while a turn is in flight (docs/12 §5.3: visible, not hidden). */
function setBusy(busy) {
  ui.say.disabled = busy;
  ui.send.disabled = busy;
  ui.say.setAttribute("aria-busy", busy ? "true" : "false");
  if (!busy) ui.say.focus();
}

// --- boot ------------------------------------------------------------------

/** The newest save for this pack, or null. Offering a choice needs something to offer. */
async function latestSave() {
  try {
    const res = await fetch("/api/saves");
    if (!res.ok) return null;
    const { saves } = await res.json();
    // Already newest-first from the server; a save with no turns is not worth resuming.
    return (saves || []).find((s) => s.turn > 0) || null;
  } catch {
    // A missing or broken save list must never stop a new game from starting.
    return null;
  }
}

async function boot() {
  const save = await latestSave();
  const resumeFrom =
    save &&
    window.confirm(
      `继续上次的进度？\n\n${save.scenario} · 第 ${save.turn} 轮 · ${save.lines} 句对话\n` +
        `保存于 ${save.saved_at}\n\n取消则重新开始。`,
    )
      ? save.save_id
      : null;

  const created = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(resumeFrom ? { resume_from: resumeFrom } : {}),
  });
  if (!created.ok) {
    setStatus(`建立会话失败：${created.status}`, "error");
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
  // The echo comes from the server, so the transcript is never optimistic.
  appendLine("player", "你", data.text);
  state.waiting = "dialogue";
  setStatus("正在思考……", "working");
  renderCast();
}

function onDialogue(data) {
  state.speaking = { npcId: data.npc_id, text: data.dialogue };
  state.waiting = "tick";
  appendLine("npc", data.name, data.dialogue);
  renderCast();
  // The tick's seconds are shown, not hidden: this is the wait the whole design
  // moved off the player's critical path, and naming it is more honest than a freeze.
  setStatus(`台词 ${num(data.latency_ms)}ms · 叙事引擎正在推进……`, "working");
}

function onTick(data) {
  state.waiting = null;
  const entry = data.entry || {};
  const op = entry.operator || "relieve";
  const ms = num(entry.latency_ms);
  setStatus(op === "relieve" ? `本轮无算子触发 · ${ms}ms` : `算子 ${op} · ${ms}ms`);
  setBusy(false);
}

function onScene(scene) {
  if (!scene) return;
  state.scene = scene;
  renderScene();
}

function onPanel(panel) {
  state.panel = panel;
  renderPanel();
}

function onFailed(data) {
  if (data.stage === "dialogue") {
    // The turn is lost; invite another attempt.
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
  ui.placeName.textContent = s.location?.name || "";
  ui.placeDesc.textContent = s.location?.description || "";
  ui.panelTurn.textContent = `第 ${s.turn} 轮 · 第 ${s.time_day} 天`;

  // Backdrop is keyed on the pack's declared place *kind*; absent means neutral.
  const art = backdrop(s.location?.backdrop);
  if (ui.backdrop.dataset.kind !== (s.location?.backdrop || "")) {
    ui.backdrop.dataset.kind = s.location?.backdrop || "";
    ui.backdrop.innerHTML = art;
  }

  const facts = s.visible_facts || [];
  ui.notes.hidden = facts.length === 0;
  // Chips live in their own wrapping box so that a wrapped row lines up with the first
  // one instead of starting back at the label's edge.
  ui.notes.innerHTML =
    `<span class="label">已知</span><div class="chips">` +
    facts.map((f) => `<span class="note">${esc(f.value)}</span>`).join("") +
    `</div>`;

  renderCast();
  renderHistory(s.transcript_tail || []);
}

function renderCast() {
  const npcs = state.scene?.npcs || [];
  ui.cast.innerHTML = npcs
    .map((npc) => {
      const isSpeaking = state.speaking?.npcId === npc.npc_id;
      const isThinking = state.waiting === "dialogue";
      let bubble = "";
      if (isThinking) {
        bubble = `<div class="bubble thinking">在想<span class="dots"><i></i><i></i><i></i></span></div>`;
      } else if (isSpeaking) {
        bubble = `<div class="bubble">${esc(state.speaking.text)}</div>`;
      }
      // The note is public, ungated pack content (NPCWorldState.public_note) — never
      // an excerpt of persona.background, which runs on into what the player must earn.
      return `
        <div class="actor ${isSpeaking ? "speaking" : ""}">
          ${bubble}
          ${sprite(npc.sprite_key, npc.name)}
          <span class="nameplate">${esc(npc.name)}</span>
          ${npc.note ? `<span class="who-note">${esc(npc.note)}</span>` : ""}
        </div>`;
    })
    .join("");
}

function renderHistory(lines) {
  // Everything before the current line: the bubble carries the newest one.
  ui.history.innerHTML = lines
    .map((l) => {
      const who = l.speaker === "player" ? "你" : l.name || l.speaker;
      const kind = l.speaker === "player" ? "player" : "npc";
      return `<div class="line ${kind}"><span class="who">${esc(who)}</span><span class="said">${esc(l.text)}</span></div>`;
    })
    .join("");
  ui.history.scrollTop = ui.history.scrollHeight;
}

function appendLine(kind, who, text) {
  const div = document.createElement("div");
  div.className = `line ${kind}`;
  div.innerHTML = `<span class="who">${esc(who)}</span><span class="said">${esc(text)}</span>`;
  ui.history.appendChild(div);
  ui.history.scrollTop = ui.history.scrollHeight;
}

// --- panel rendering ------------------------------------------------------

function setPanelOpen(open) {
  state.panelOpen = open;
  ui.panel.hidden = !open;
  ui.app.classList.toggle("panel-open", open);
  ui.toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

function block(title, count, body, { open = true, alert = false } = {}) {
  const badge =
    count === null || count === undefined
      ? ""
      : `<span class="count ${alert ? "alert" : ""}">${esc(count)}</span>`;
  return `<details class="block" ${open ? "open" : ""}>
    <summary>${esc(title)}${badge}</summary>
    <div class="block-body">${body}</div>
  </details>`;
}

const empty = (text) => `<p class="empty">${esc(text)}</p>`;

function renderPanel() {
  const p = state.panel;
  if (!p) return;
  ui.panelBody.innerHTML = [
    renderBeats(p),
    renderLedger(p),
    renderUnlock(p),
    renderRejected(p),
    renderTimeline(p),
    renderTrend(p),
    renderMemory(p),
    renderCost(p),
  ].join("");
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
          <span class="op op-${esc(r.operator)}">${esc(r.operator)}</span>
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
  return block("关系值走势", null, body, { open: false });
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

ui.composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = ui.say.value.trim();
  if (!text) return;

  ui.say.value = "";
  setBusy(true);

  const res = await fetch(`/api/session/${state.sessionId}/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (res.status === 409) {
    setStatus("上一回合还没结束，稍等一下。", "error");
    return; // stays disabled; the tick event will release it
  }
  if (!res.ok) {
    setStatus(`发送失败：${res.status}`, "error");
    setBusy(false);
  }
});

ui.toggle.addEventListener("click", () => setPanelOpen(!state.panelOpen));

ui.introStart.addEventListener("click", () => {
  ui.intro.hidden = true;
  setBusy(false);
});

boot();
