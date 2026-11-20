const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const root = path.join(__dirname, '../runtime/web/frontend');
const source = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const start = source.indexOf('    function applyState(');
const end = source.indexOf('\n    }', start);
assert.ok(start >= 0 && end > start, 'actual state normalizer exists');
const ctx = {
  state: {}, hideSplash() {}, loadError: {value: ''}, rebootNeeded: {value: false},
  KLIPPY_DISCONNECTED_MESSAGE: 'disconnected', t: key => key,
  syncCalibrationDefaults() {}, _maybeAutoOpenInbox() {}, indexBase: {value: 0},
  dryerCfg: {}, window: {dispatchEvent() {}}, CustomEvent: function () {},
  picker: {ace: 2, slot: 3}, pickerTagReadDisabled: () => false,
};
vm.createContext(ctx);
vm.runInContext(source.slice(start, end + 6), ctx);

const block = html.match(/<div v-else-if="tagWriteOutcome"[\s\S]*?<\/div>/)[0];
const classExpression = block.match(/:class="([^"]+)"/)[1];
const retryButton = block.match(/<button v-if="[^"]+"[\s\S]*?class="tag-op-retry"[\s\S]*?<\/button>/)[0];
const retryExpression = retryButton.match(/v-if="([^"]+)"/)[1];
const disabledExpression = retryButton.match(/:disabled="([^"]+)"/)[1];
assert.ok(retryButton.includes('@click="tagRead()"'), 'retry uses the existing command path');
const render = expression => vm.runInContext(expression, ctx);
function normalize(result) {
  ctx.applyState({printer_state: 'standby', aces: [], toolheads: [],
                 tag_op: {busy: false, kind: 'read', seq: 5, ace: 2, slot: 3,
                          result: {kind: 'read', seq: 5, ace: 2, slot: 3, ...result}}});
  ctx.tagWriteOutcome = ctx.state.tag_op.result;
  return render(classExpression);
}

let classes = normalize({ok: false, pending: true, msg: '标签暂未读到，可重试'});
assert.equal(ctx.tagWriteOutcome.pending, true, 'pending survives the real state normalizer');
assert.equal(classes.includes('pending'), true, 'unread tag renders as information');
assert.equal(classes.includes('fail'), false, 'ordinary unread tag is not a red error');
assert.equal(render(retryExpression), true, 'matching read offers retry');
assert.equal(render(disabledExpression), false);
ctx.pickerTagReadDisabled = () => true;
assert.equal(render(disabledExpression), true, 'existing busy/STOP guards still disable retry');
ctx.picker.ace = 1;
assert.equal(render(retryExpression), false, 'old result cannot retry a different ACE');
ctx.picker.ace = 2;
ctx.picker.slot = 1;
assert.equal(render(retryExpression), false, 'old result cannot retry a different slot');
ctx.picker.slot = 3;

classes = normalize({ok: false, pending: false, msg: '识别回停 STOP 未确认'});
assert.equal(classes.includes('fail'), true, 'real STOP failure remains red');
assert.equal(classes.includes('pending'), false);
assert.equal(render(retryExpression), false);
classes = normalize({ok: true, pending: false, msg: '识别完成；余量未知'});
assert.equal(classes.includes('ok'), true, 'unknown remaining does not fail a successful read');
assert.equal(render(retryExpression), false);
classes = normalize({ok: false, msg: 'legacy error'});
assert.equal(classes.includes('fail'), true, 'missing pending does not relabel old errors');
assert.equal(ctx.tagWriteOutcome.pending, false);
normalize({ok: false, pending: true, kind: 'write'});
assert.equal(render(retryExpression), false, 'write result cannot trigger read retry');
console.log('RFID pending UI normalization and template checks passed');
