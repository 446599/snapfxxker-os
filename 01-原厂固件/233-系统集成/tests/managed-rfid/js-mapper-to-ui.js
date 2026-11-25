const fs = require("fs"),
  vm = require("vm"),
  assert = require("assert");
const state = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const src = fs.readFileSync(process.argv[3], "utf8");
const i = src.indexOf("    const slotActionPending = ref(false);"),
  j = src.indexOf("    async function stopSlotAction()", i);
assert(i >= 0 && j > i);
const ctx = {
  state,
  ref: (v) => ({ value: v }),
  dispIdx: (v) => v + 1,
  cmdQueue: { value: [] },
  cmdQueueRunning: false,
};
vm.createContext(ctx);
vm.runInContext(src.slice(i, j), ctx);
const origin = state.aces[1],
  other = state.aces[2],
  os = origin.slots[0],
  ss = other.slots[0];
assert.equal(os.state, "empty");
assert(ctx.rfidHoldForRoute(1, 0));
assert(ctx.rfidHoldForRoute(2, 0));
assert(ctx.rfidRecoveryAllowed(1, 0));
assert(!ctx.rfidRecoveryAllowed(2, 0));
assert(!ctx.slotActionDisabled(origin, os, "eject"));
assert(ctx.slotActionDisabled(other, ss, "eject"));
assert(ctx.slotActionDisabled(origin, os, "rfid"));
assert(ctx.rfidHoldForHead(0));
assert(!ctx.rfidHoldForHead(1));
assert(!ctx.rfidHoldForRoute(2, 1));
console.log(
  JSON.stringify({
    result: "pass",
    scope: "actual backend _parse_state to actual UI functions",
    cases: [
      "empty exact recovery despite held head",
      "shared route blocked",
      "unaffected route/head available",
      "no gate mutation",
    ],
  }),
);
