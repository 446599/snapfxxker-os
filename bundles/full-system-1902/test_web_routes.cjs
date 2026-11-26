// Exercise the packaged Vue setup and template bindings with its queue paused.
// Nothing in this harness can send a printer request.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.argv[2];
const noop = () => {};
let ui;
const globals = {
  Vue: {
    reactive: value => value,
    ref: value => ({value}),
    computed: getter => ({get value() { return getter(); }}),
    watch: noop, onMounted: noop, onUnmounted: noop, nextTick: noop,
    createApp: ({setup}) => ({mount() { ui = setup(); }}),
  },
  localStorage: {getItem: () => null, setItem: noop},
  location: {pathname: '/multiace/', search: '?panel=1', protocol: 'http:', host: 'localhost'},
  window: {addEventListener: noop},
  document: {addEventListener: noop, body: {classList: {add: noop}}, documentElement: {dataset: {}}},
  URLSearchParams, performance, queueMicrotask: noop, setTimeout: noop, clearTimeout: noop,
};
vm.runInNewContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), globals);
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const button = html.match(/<button class="panel-btn warn"[\s\S]*?>/)[0];
const clicked = button.match(/@click="([^"]+)"/)[1];
const disabled = button.match(/:disabled="([^"]+)"/)[1];
function evaluateCard(expression, ace, slot) {
  return vm.runInNewContext(expression, {...ui, isPrinting: ui.isPrinting.value,
    panelAce: {idx: ace}, slot: {idx: slot}});
}
function commands() {
  const out = [...ui.cmdQueue.value].reverse().map(item => [item.cmd, item.args]);
  ui.cmdQueue.value.length = 0;
  return JSON.parse(JSON.stringify(out));
}
ui.state.confirm_commands = false;
ui.state.printer_state = 'ready';
// Each physical head initially has a different source. Exercise all 16 cards.
for (const mode of ['multi', 'head']) {
  ui.state.mode = mode;
  ui.state.head_ace = {0: 3, 1: 2, 2: 1, 3: 0};
  ui.state.ace_heads = [0, 1, 2, 3];
  ui.state.toolheads = [0, 1, 2, 3].map(head => ({
    idx: head, ace: 3 - head, slot: mode === 'head' ? (head + 1) % 4 : head,
    head_source_known: true, filament_at_extruder: true,
  }));
  for (let ace = 0; ace < 4; ++ace) {
    for (let slot = 0; slot < 4; ++slot) {
      const source = ui.state.toolheads.find(th => th.ace === ace && th.slot === slot);
      assert.equal(evaluateCard(disabled, ace, slot), !source);
      evaluateCard(clicked, ace, slot);
      assert.deepEqual(commands(), source ? [['ACE_UNLOAD_HEAD', {
        HEAD: source.idx, ACE: ace, SLOT: slot,
      }]] : []);
    }
  }
}
// Multi-mode replacement unloads the observed old route before the requested one.
ui.state.mode = 'multi';
ui.state.toolheads = [{idx: 3, ace: 0, slot: 3, head_source_known: true}];
ui.loadSlot(2, 3);
assert.deepEqual(commands(), [
  ['ACE_UNLOAD_HEAD', {HEAD: 3, ACE: 0, SLOT: 3}],
  ['ACE_LOAD_HEAD', {HEAD: 3, ACE: 2, SLOT: 3}],
]);
// Head mode: neither the old slot nor the selected slot equals the head.
ui.state.mode = 'head';
ui.state.head_ace = {0: 2};
ui.state.ace_heads = [0];
ui.state.toolheads = [{idx: 0, ace: 2, slot: 1, head_source_known: true}];
ui.loadSlot(2, 3);
assert.deepEqual(commands(), [
  ['ACE_UNLOAD_HEAD', {HEAD: 0, ACE: 2, SLOT: 1}],
  ['ACE_LOAD_HEAD', {HEAD: 0, ACE: 2, SLOT: 3}],
]);
ui.unloadHead(0);
assert.deepEqual(commands(), [['ACE_UNLOAD_HEAD', {HEAD: 0}]]);
ui.state.printer_state = 'printing';
assert.equal(evaluateCard(disabled, 2, 1), true);
evaluateCard(clicked, 2, 1);
assert.deepEqual(commands(), []);
