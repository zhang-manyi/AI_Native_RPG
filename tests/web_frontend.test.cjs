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
  vm.runInContext(source.replace(/boot\(\);\s*$/, '') +
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

test('an empty scene can narrate and offer investigation actions without a chat box', async () => {
  const c = client();
  c.onScene(scene({ current_npc_id: null, npcs: [], passage_id: 'square', passages: [
    { speaker: 'narrator', name: '旁白', text: '艾拉问过去镇上的路。' },
  ] }));
  assert.equal(c.elements.get('dialogue-name').textContent, '旁白');
  await c.advance();
  assert.equal(c.elements.get('composer').hidden, true);
  assert.equal(c.elements.get('options').hidden, false);
  assert.match(c.elements.get('cast').innerHTML, /这里没有人/);
  assert.equal(c.requests.length, 0);
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
  c.onScene(scene({ passages: [], options: [], ending: { title: '结局：调查结束', text: '你作出了判断。' } }));
  assert.equal(c.elements.get('ending').hidden, false);
  assert.equal(c.elements.get('cast').innerHTML, '');
  assert.equal(c.elements.get('ending-title').textContent, '结局：调查结束');
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
