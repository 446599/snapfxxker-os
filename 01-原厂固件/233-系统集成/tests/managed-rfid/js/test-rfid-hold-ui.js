const fs = require("fs"),
  vm = require("vm"),
  assert = require("assert");
const src = fs.readFileSync(process.argv[2], "utf8"),
  html = fs.readFileSync(process.argv[3], "utf8");
function take(a, b) {
  let i = src.indexOf(a),
    j = src.indexOf(b, i);
  assert(i >= 0 && j > i);
  return src.slice(i, j);
}
const code =
  take(
    "    const slotActionPending = ref(false);",
    "    async function stopSlotAction()",
  ) +
  "\n" +
  take(
    "    function loadSlot(aceIdx, slotIdx) {",
    "    // Load a head without ACE",
  ) +
  "\n" +
  take("    function loadFeederHead(h) {", "    // head mode: the ACE head");
let sent = [];
const slots = () =>
  Array.from({ length: 4 }, (_, idx) => ({
    idx,
    state: "ready",
    position_unconfirmed: false,
    eject_recovery_allowed: false,
  }));
const a0 = { idx: 0, connected: true, protocol: "v2", slots: slots() },
  a1 = { idx: 1, connected: true, protocol: "v2", slots: slots() },
  a2 = { idx: 2, connected: true, protocol: "v2", slots: slots() };
a1.slots[2] = {
  idx: 2,
  state: "empty",
  position_unconfirmed: true,
  eject_recovery_allowed: true,
};
a1.slots[3] = {
  idx: 3,
  state: "ready",
  position_unconfirmed: true,
  eject_recovery_allowed: false,
};
const route = { slot: 2, ace_index: 1 };
const ctx = {
  ref: (v) => ({ value: v }),
  dispIdx: (v) => v + 1,
  state: {
    aces: [a0, a1, a2],
    location_hold: { rfid_routes: [route], rfid_routes_valid: true },
    toolheads: [0, 1, 2, 3].map((idx) => ({
      idx,
      filament_at_extruder: false,
    })),
    printer_state: "standby",
    swap_in_progress: false,
    tag_op: { busy: false },
    slot_action: { state: "idle" },
    calibration: { state: "idle" },
    mode: "head",
  },
  cmdQueue: { value: [] },
  cmdQueueRunning: false,
  picker: {},
  window: { confirm: () => true },
  spoolMacro: async (n, a) => sent.push([n, a]),
  enqueue: (n, a) => sent.push([n, a]),
  setMacroLog: () => {},
  _blockIfPrinting: () => false,
  slotIsEmpty: () => false,
  slotHeadHasNoAce: () => false,
  _confirmCmd: () => true,
  aceHeadForAce: (idx) => (idx === 1 ? 0 : 3),
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
assert(ctx.rfidHoldForRoute(1, 2));
assert(ctx.rfidHoldForRoute(1, 3));
assert(!ctx.rfidHoldForRoute(2, 0));
assert(ctx.rfidRecoveryAllowed(1, 2));
assert(!ctx.rfidRecoveryAllowed(1, 3));
assert(!ctx.slotActionDisabled(a1, a1.slots[2], "eject"));
assert(ctx.slotActionDisabled(a1, a1.slots[3], "eject"));
assert(ctx.slotActionDisabled(a1, a1.slots[3], "rfid"));
assert(ctx.rfidHoldMessage(1, 3).includes("ACE 2 槽 3"));
ctx.loadSlot(1, 3);
assert.equal(sent.length, 0);
ctx.loadSlot(2, 0);
assert.equal(sent.length, 1);
assert.equal(sent[0][0], "ACE_LOAD_HEAD");
ctx.state.toolheads[0].position_unconfirmed = true;
ctx.state.toolheads[3].position_unconfirmed = false;
assert(ctx.rfidHoldForHead(0));
assert(!ctx.rfidHoldForHead(3));
sent = [];
ctx.loadFeederHead(0);
assert.equal(sent.length, 0);
ctx.loadFeederHead(3);
assert.equal(sent.length, 1);
a1.connected = false;
assert(!ctx.rfidRecoveryAllowed(1, 2));
a1.connected = true;
const malformed = a2.slots[0];
delete malformed.eject_recovery_allowed;
assert(ctx.rfidHoldForRoute(2, 0));
assert(!ctx.rfidRecoveryAllowed(2, 0));
delete malformed.position_unconfirmed;
assert(!ctx.rfidHoldForRoute(2, 0));
ctx.state.location_hold = { rfid_routes: [], rfid_routes_valid: false };
assert(ctx.rfidHoldForRoute(2, 0));
assert(!ctx.rfidRecoveryAllowed(1, 2));
ctx.state.location_hold = undefined;
assert(!ctx.rfidHoldForRoute(2, 0));
ctx.state.location_hold = { rfid_routes: [], rfid_routes_valid: true };
ctx.state.aces.forEach((a) =>
  a.slots.forEach((x) => {
    x.position_unconfirmed = false;
    x.eject_recovery_allowed = false;
  }),
);
assert(ctx.rfidAnyHold());
ctx.state.toolheads[0].position_unconfirmed = false;
assert(!ctx.rfidAnyHold());
const pos = src.indexOf("      state.location_hold = s.location_hold"),
  end = src.indexOf(";", pos);
assert(pos >= 0 && end > pos);
a1.slots[3].position_unconfirmed = true;
ctx.s = { location_hold: { rfid_routes: [route], rfid_routes_valid: true } };
vm.runInContext(src.slice(pos, end + 1), ctx);
assert(ctx.rfidHoldForRoute(1, 3));
ctx.s = {};
vm.runInContext(src.slice(pos, end + 1), ctx);
assert(!ctx.rfidHoldForRoute(2, 0));
assert(html.includes("rfidHoldForRoute(displayedAce.idx, slot.idx)"));
assert(html.includes("退出 ACE 恢复"));
assert(!src.includes("rfid_route_heads"));
assert(!src.includes("rfid_identities"));
console.log(
  JSON.stringify({
    result: "pass",
    cases: [
      "authoritative head-mode slot flags block both shared routes",
      "unaffected head remains loadable",
      "exact recovery only including empty gate",
      "disconnected recovery denied",
      "incomplete flags fail closed",
      "old backend compatible",
      "actual ACE label",
      "authoritative feeder head flag blocks forward",
      "UI never infers identity/head",
    ],
  }),
);
