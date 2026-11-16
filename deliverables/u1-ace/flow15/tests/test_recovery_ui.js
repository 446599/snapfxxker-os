const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../runtime/web/frontend/app.js'), 'utf8');
function actualFunction(name) {
  const start = source.indexOf(`    function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n    }', start);
  assert.ok(end > start, name);
  return source.slice(start, end + 6);
}
const row = {can_auto: true, can_check: true, can_clear: true, can_probe: true, ticket: 'a'.repeat(32)};
const state = {flow_version: 15, printer_state: 'error', swap_in_progress: false, calibration: {state: 'idle'}};
const ctx = {
  state, slotRecoveryRow: () => row,
  slotActionPending: {value: false}, cmdQueue: {value: []}, cmdQueueRunning: false,
  slotOperationsBusy: () => false, rfidHoldForRoute: () => false,
  rfidRecoveryAllowed: () => true, slotActionHeadBlocked: () => false,
  slotActiveOperation: () => ({kind: 'recover'}), slotCancelPending: {},
  slotOperationKey: (a, s) => `${a}/${s}`,
  hideSplash: () => {}, loadError: {value: ''}, rebootNeeded: {value: true},
  KLIPPY_DISCONNECTED_MESSAGE: '控制器暂时离线，正在等待重新连接；连接恢复后会重新检查料路。',
  t: key => key,
  dispIdx: value => value + 1,
  REMAINING_PROFILE_FIELDS: ['core_radius_mm', 'wound_width_mm', 'packing_fraction',
    'density_g_cm3', 'filament_diameter_mm', 'circumference_mm'].map(key => ({key, label: key})),
};
vm.createContext(ctx);
for (const name of ['slotRecoveryDisabled', 'slotActionDisabled', 'slotCancelDisabled', 'applyState', 'bufferControlText', 'remainingProfilePayload', 'retainedSourceConfirmationText', 'slotRecoveryMessage'])
  vm.runInContext(actualFunction(name), ctx);
const ace = {idx: 0, connected: true, protocol: 'v2'};
const slot = {idx: 0, state: 'error', slot_status: 'feed_error'};
for (const action of ['auto', 'check', 'clear'])
  assert.equal(ctx.slotRecoveryDisabled(ace, slot, action), false, `error enables ${action}`);
assert.equal(ctx.slotActionDisabled(ace, slot, 'eject'), false, 'error enables original-slot ejection');
assert.equal(ctx.slotActionDisabled(ace, slot, 'rfid'), true, 'fault still blocks RFID');
assert.equal(ctx.slotCancelDisabled(ace, slot), false, 'stop remains available in error');
for (const printerState of ['printing', 'paused', 'disconnected', null]) {
  state.printer_state = printerState;
  assert.equal(ctx.slotRecoveryDisabled(ace, slot, 'auto'), true, printerState);
}
state.printer_state = 'error';
row.can_probe = false;
assert.equal(ctx.slotRecoveryDisabled(ace, slot, 'clear'), true, 'no short reverse without server permission');
assert.equal(ctx.slotRecoveryDisabled(ace, slot, 'auto'), false);
row.ticket = 'old';
assert.equal(ctx.slotRecoveryDisabled(ace, slot, 'auto'), true, 'stale ticket');
row.ticket = 'a'.repeat(32);
state.flow_version = 14;
assert.equal(ctx.slotRecoveryDisabled(ace, slot, 'auto'), true, 'older firmware cannot receive auto');
state.flow_version = 15;
ctx.slotOperationsBusy = () => true;
assert.equal(ctx.slotRecoveryDisabled(ace, slot, 'auto'), true, 'active motion remains exclusive');
ctx.applyState({klippy: 'disconnected'});
assert.equal(state.printer_state, 'disconnected', 'old dashboard cannot retain actionable idle status');
assert.equal(ctx.loadError.value.includes('重启'), false, 'transient disconnect does not require reboot');
const bufferAce = {connected: true, slots: [{idx: 0, slot_status: 'ready'}],
  buffer_control: {mode: 'native', fresh: true, assist: [{slot: 0, phase: 'armed'}], pulled_any: false}};
assert.equal(ctx.bufferControlText(bufferAce), '缓冲助送料已接管 · 等待拉料');
bufferAce.slots[0].slot_status = 'assisting';
assert.equal(ctx.bufferControlText(bufferAce), '缓冲助送料已接管');
bufferAce.buffer_control.pulled_any = true;
assert.equal(ctx.bufferControlText(bufferAce), '有料槽缓冲已满，具体槽位待确认');
bufferAce.buffer_control.fresh = false;
assert.equal(ctx.bufferControlText(bufferAce), '缓冲信号待核', 'stale full bit does not claim a full buffer');
bufferAce.connected = false;
assert.equal(ctx.bufferControlText(bufferAce), '缓冲设备离线');
bufferAce.connected = true;
bufferAce.buffer_control.fresh = true;
bufferAce.buffer_control.reconnect_hold = [{slot: 0, reason: 'unknown prior source'}];
assert.equal(ctx.bufferControlText(bufferAce), '缓冲助送料等待原料路确认');
const form = {calibrationId: 'measured reel', spacing: '4', fields: {
  core_radius_mm: ['40', '41'], wound_width_mm: ['55', '56'], packing_fraction: ['0.8', '0.9'],
  density_g_cm3: ['1.2', '1.3'], filament_diameter_mm: ['1.73', '1.77'], circumference_mm: ['250', '500'],
}};
const profile = ctx.remainingProfilePayload(form);
assert.equal(profile.calibrated, true);
assert.equal(profile.maximum_observation_spacing_mm, 4);
assert.equal(Object.hasOwn(profile, 'reader_device_uid'), false, 'reader identity is server-owned');
assert.equal(profile.core_radius_mm[0], 40);
form.fields.core_radius_mm = ['', ''];
assert.throws(() => ctx.remainingProfilePayload(form), /实测下限和上限/);
form.fields.core_radius_mm = ['40', '41'];
form.fields.packing_fraction = ['0.8', '1.2'];
assert.throws(() => ctx.remainingProfilePayload(form), /范围无效/);
const confirmation = ctx.retainedSourceConfirmationText(ace, slot,
  {source_head: 0, source_confirmation_prompt: '检测到插拔记录变化，可能已更换耗材。',
   connection_review_message: '旧连接仍保留停止故障和占用，请恢复原 USB 连接，未解锁退料。'});
assert.ok(confirmation.includes('检测到插拔记录变化'));
assert.ok(confirmation.includes('同一台 ACE 1 的原槽 1'));
assert.ok(confirmation.includes('已换料盘、换料或无法确认，请取消'));
assert.ok(confirmation.includes('保留堵料及管内占用记录'));
assert.ok(confirmation.includes('不会自动继续打印'));
assert.ok(confirmation.includes('恢复原 USB 连接，未解锁退料'));
row.connection_review_message = '原连接仍待核，未解锁退料';
row.source_confirmation_prompt = '来源记录已补充';
assert.equal(ctx.slotRecoveryMessage(ace, slot), row.connection_review_message);
console.log('Recovery UI behavioral checks passed');
