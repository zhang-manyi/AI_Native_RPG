// Exercise the actual no-build client against server snapshots, without a browser dependency.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function client() {
  const elements = new Map();
  const element = () => ({
    hidden: true, disabled: false, innerHTML: '', textContent: '', value: '',
    classList: { toggle() {} }, setAttribute() {},
    listeners: {}, addEventListener(type, listener) { this.listeners[type] = listener; },
    querySelectorAll() { return []; }, appendChild() {}, focus() {},
  });
  const requests = [];
  const confirmations = [];
  const confirmation = { accepted: false };
  const context = vm.createContext({
    window: { confirm(message) { confirmations.push(message); return confirmation.accepted; } },
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      addEventListener() {}, createElement: element,
    },
    fetch: async (...args) => { requests.push(args); return { ok: true, json: async () => null }; },
    console,
  });
  const source = fs.readFileSync(path.join(__dirname, '../src/ai_native_rpg/web/static/app.js'), 'utf8');
  // A vm script cannot `import`; the art is decoration, so stand-ins that still carry the
  // key are enough to check which figure or landmark the client asked for. The landmark
  // stub knows one kind, like the real table, so "no landmark" is testable too.
  const stubs = 'const sprite = (key) => `<svg data-sprite="${key}"></svg>`;\n' +
    'const avatar = (key) => `<svg data-avatar="${key}"></svg>`;\n' +
    'const landmark = (kind) => (kind === "square" ? `<svg data-landmark="${kind}"></svg>` : "");\n';
  vm.runInContext(stubs + source.replace(/^import .*\.js";\r?\n/gm, '').replace(/boot\(\);\s*$/, '') +
    '\nglobalThis.testClient = { onScene, advance, onMove, onPanel, renderWrapUp, state };', context);
  return { ...context.testClient, elements, requests, confirmations, confirmation };
}

function scene(overrides = {}) {
  return {
    turn: 1, time_day: 1, time_slot: 'morning', ready: true,
    location: { name: '玛尔塔的家' }, current_npc_id: 'npc_a',
    npcs: [{ npc_id: 'npc_a', name: '玛尔塔' }],
    options: [{ option_id: 'goodwill', tag: 'goodwill', text: '我想帮忙' }],
    passages: [
      { speaker: 'player', name: '你', text: '我想问问那晚的事。' },
      { speaker: 'npc_a', name: '玛尔塔', text: '你小声些。' },
    ], passage_id: 'opening', ...overrides,
  };
}

test('reading fixed dialogue reveals choices without submitting player text', async () => {
  const c = client();
  c.onScene(scene());
  assert.equal(c.elements.get('dialogue-name').textContent, '你');
  assert.equal(c.elements.get('options').hidden, true);
  await c.advance();
  assert.equal(c.elements.get('dialogue-name').textContent, '玛尔塔');
  await c.advance();
  assert.equal(c.elements.get('options').hidden, false);
  assert.equal(c.elements.get('composer').hidden, false);
  assert.equal(c.requests.length, 0);
  c.onScene(scene());
  assert.equal(c.state.awaitingAdvance, false, 'duplicate snapshot must not replay opening');
});

test('the cast draws the figure the server assigned, and only NPC lines carry a face', async () => {
  const c = client();
  c.onScene(scene({ npcs: [{ npc_id: 'npc_a', name: '玛尔塔', sprite_key: 'sprite_b' }] }));
  // The key is the server's (scene.py SPRITE_KEYS), never worked out from the name.
  assert.match(c.elements.get('cast').innerHTML, /data-sprite="sprite_b"/);
  assert.match(c.elements.get('cast').innerHTML, /玛尔塔/, 'the nameplate stays beside the portrait');

  // The opening line is the player's: no figure, so no avatar.
  assert.equal(c.elements.get('dialogue-avatar').hidden, true);
  await c.advance();
  assert.equal(c.elements.get('dialogue-name').textContent, '玛尔塔');
  assert.equal(c.elements.get('dialogue-avatar').hidden, false);
  assert.match(c.elements.get('dialogue-avatar').innerHTML, /data-avatar="sprite_b"/);
});

test('the figure is drawn once per scene, not again on every line', async () => {
  const c = client();
  const cast = c.elements.get('cast');
  let writes = 0;
  let html = '';
  Object.defineProperty(cast, 'innerHTML', {
    get: () => html,
    set: (v) => { writes += 1; html = v; },
  });

  c.onScene(scene({ npcs: [{ npc_id: 'npc_a', name: '玛尔塔', sprite_key: 'sprite_a' }] }));
  const drawn = writes;
  await c.advance();
  await c.advance();
  // The second snapshot of a turn carries the same people.
  c.onScene(scene({ npcs: [{ npc_id: 'npc_a', name: '玛尔塔', sprite_key: 'sprite_a' }] }));
  assert.equal(writes, drawn, 'advancing lines must not rebuild the portrait (it blinked)');

  // Someone else arriving is a real change and is drawn.
  c.onScene(scene({ npcs: [
    { npc_id: 'npc_a', name: '玛尔塔', sprite_key: 'sprite_a' },
    { npc_id: 'npc_b', name: '洛伦', sprite_key: 'sprite_b' },
  ] }));
  assert.equal(writes, drawn + 1);
  assert.match(html, /data-sprite="sprite_b"/);
});

test('an empty scene can narrate and offer investigation actions without a chat box', async () => {
  const c = client();
  c.onScene(scene({ current_npc_id: null, npcs: [], passage_id: 'square', passages: [
    { speaker: 'narrator', name: '旁白', text: '艾拉问过去镇上的路。' },
  ] }));
  assert.equal(c.elements.get('dialogue-name').textContent, '旁白');
  await c.advance();
  assert.equal(c.elements.get('composer').hidden, true);
  assert.equal(c.elements.get('options').hidden, false);
  assert.equal(c.elements.get('cast').innerHTML, '', 'an empty place shows no "nobody here" line');
  assert.equal(c.requests.length, 0);
});

test('an empty place shows its landmark when it has one, and nothing otherwise', () => {
  const c = client();
  c.onScene(scene({ current_npc_id: null, npcs: [], location: { name: '村庄广场', backdrop: 'square' } }));
  assert.match(c.elements.get('cast').innerHTML, /data-landmark="square"/);
  assert.doesNotMatch(c.elements.get('cast').innerHTML, /这里没有人/);

  // Moving on to an empty place of another kind redraws: the well must not follow you.
  c.onScene(scene({ current_npc_id: null, npcs: [], location: { name: '小路', backdrop: 'road' } }));
  assert.equal(c.elements.get('cast').innerHTML, '');
});

test('report hides nearby NPC chat and requires confirmation before final submission', async () => {
  const c = client();
  c.onScene(scene({report_active: true, passages: [], options: [
    {option_id: 'report', tag: 'observe', text: '提交报告：个人判断', final_report: true},
    {option_id: 'return', tag: 'observe', text: '继续调查', final_report: false},
  ]}));
  assert.equal(c.elements.get('composer').hidden, true);
  assert.equal(c.elements.get('options').hidden, false);
  assert.match(c.elements.get('cast').innerHTML, /调查报告/);
  assert.doesNotMatch(c.elements.get('cast').innerHTML, /玛尔塔/);
  assert.match(c.elements.get('options').innerHTML, /\[报告\]/);
  const click = c.elements.get('options').listeners.click;
  const event = id => ({ target: { closest() { return {
    dataset: {option: id}, querySelector() { return {textContent: '提交报告：个人判断'}; },
  }; } } });
  await click(event('report'));
  assert.equal(c.confirmations.length, 1);
  assert.equal(c.requests.length, 0, 'cancel keeps the report open');
  c.confirmation.accepted = true;
  await click(event('report'));
  assert.equal(c.requests.length, 1);
  assert.equal(JSON.parse(c.requests[0][1].body).option_id, 'report');
});

test('public ready snapshots unlock input without developer tick events', async () => {
  const c = client();
  c.onScene(scene({ ready: false }));
  assert.equal(c.state.busy, true);
  assert.equal(c.state.awaitingAdvance, false);
  c.onScene(scene({ ready: true }));
  assert.equal(c.state.busy, false);
  assert.equal(c.state.awaitingAdvance, true);
});

test('ending leaves the transcript accessible and closes action controls', () => {
  const c = client();
  c.onScene(scene({ passages: [], options: [], ending: { title: '结局 · 调查结束', text: '你作出了判断。' } }));
  assert.equal(c.elements.get('ending').hidden, false);
  assert.equal(c.elements.get('cast').innerHTML, '');
  assert.equal(c.elements.get('ending-title').textContent, '结局 · 调查结束');
  assert.equal(c.elements.get('composer').hidden, true);
  assert.equal(c.elements.get('options').hidden, true);
  assert.equal(c.elements.get('ways').hidden, true);
});

test('conclusion remains visible with a reason and unlocks when the scene allows it', () => {
  const c = client();
  c.onScene(scene({ passages: [], can_conclude: false, conclude_reason: '请先结束对话' }));
  assert.equal(c.elements.get('conclude').hidden, false);
  assert.equal(c.elements.get('conclude').disabled, true);
  assert.equal(c.elements.get('conclude-reason').textContent, '请先结束对话');
  c.onScene(scene({ passages: [], can_conclude: true, conclude_reason: '' }));
  assert.equal(c.elements.get('conclude').disabled, false);
  c.onScene(scene({ passages: [], ready: false, can_conclude: true, conclude_reason: '' }));
  assert.equal(c.elements.get('conclude').disabled, true);
});

test('conclusion button hides while the report is open and returns after it closes', () => {
  const c = client();
  c.onScene(scene({ passages: [], report_active: true, can_conclude: false, conclude_reason: '已有进行中的事件' }));
  assert.equal(c.elements.get('conclude').hidden, true);
  assert.equal(c.elements.get('conclude-reason').hidden, true);
  c.onScene(scene({ passages: [], report_active: false, can_conclude: true, conclude_reason: '' }));
  assert.equal(c.elements.get('conclude').hidden, false);
  assert.equal(c.elements.get('conclude-reason').hidden, false);
  assert.equal(c.elements.get('conclude').disabled, false);
});

test('conclusion button hides once an ending is reached', () => {
  const c = client();
  c.onScene(scene({ passages: [], options: [], can_conclude: false, conclude_reason: '调查已经结束',
    ending: { title: '结局 · 调查结束', text: '你作出了判断。' } }));
  assert.equal(c.elements.get('conclude').hidden, true);
  assert.equal(c.elements.get('conclude-reason').hidden, true);
});

test('review announces the day boundary and lists gains without mixing them with old clues', () => {
  const c = client();
  c.renderWrapUp({day: 1, introduction: '今天的调查结束了', baseline_available: true,
    known: [{id: 'old', value: '旧线索'}, {id: 'new', value: '新线索'}],
    discovered: [{id: 'new', value: '新线索'}], unanswered: [],
    relationship_changes: [{name: '玛尔塔', trust: 3, fear: -2, respect: 0}],
  });
  assert.equal(c.elements.get('wrap-introduction').textContent, '今天的调查结束了');
  assert.match(c.elements.get('wrap-discovered').innerHTML, /新线索/);
  assert.doesNotMatch(c.elements.get('wrap-discovered').innerHTML, /旧线索/);
  assert.match(c.elements.get('wrap-relationships').innerHTML, /玛尔塔.*信任 \+3.*恐惧 -2/);
});

test('NPC panel renders every character, directional values, memory and field explanations', () => {
  const c = client();
  c.onPanel({npcs: [
    {npc_id: 'npc_a', name: '玛尔塔', relationships: {player_1: {trust: 13, fear: 2, respect: 0}},
      memory: {semantic: [{fact: '<private belief>', confidence: 0.8}]}},
    {npc_id: 'npc_b', name: '洛伦', relationships: {player_1: {trust: 7, fear: 0, respect: 3}}},
  ]});
  const panel = c.elements.get('panel-body').innerHTML;
  assert.match(panel, /玛尔塔/);
  assert.match(panel, /洛伦/);
  assert.match(panel, /13\.0/);
  assert.match(panel, /7\.0/);
  assert.match(panel, /&lt;private belief&gt;/);
  assert.match(panel, /title="确信程度/);
});
