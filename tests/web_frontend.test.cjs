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
    classList: { toggle() {} }, setAttribute() {}, addEventListener() {},
    querySelectorAll() { return []; }, appendChild() {}, focus() {},
  });
  const requests = [];
  const context = vm.createContext({
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
    '\nglobalThis.testClient = { onScene, advance, onMove, state };', context);
  return { ...context.testClient, elements, requests };
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
  c.onScene(scene({ passages: [], options: [], ending: { title: '调查结束', text: '你作出了判断。' } }));
  assert.equal(c.elements.get('ending').hidden, false);
  assert.equal(c.elements.get('ending-title').textContent, '调查结束');
  assert.equal(c.elements.get('composer').hidden, true);
  assert.equal(c.elements.get('options').hidden, true);
  assert.equal(c.elements.get('ways').hidden, true);
});
