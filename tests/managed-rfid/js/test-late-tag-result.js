const fs = require("fs"),
  vm = require("vm"),
  assert = require("assert");
const p = process.argv[2],
  src = fs.readFileSync(p, "utf8");
function take(a, b) {
  let i = src.indexOf(a),
    j = src.indexOf(b, i);
  assert(i >= 0 && j > i);
  return src.slice(i, j);
}
let watcher;
const ctx = {
  ref: (v) => ({ value: v }),
  computed: (f) => ({
    get value() {
      return f();
    },
  }),
  watch: (get, cb) => {
    watcher = { get, cb };
  },
  state: { tag_op: { busy: false, result: null } },
  dispIdx: (v) => v + 1,
  setTimeout: () => 1,
  clearTimeout: () => {},
};
vm.createContext(ctx);
vm.runInContext(
  take('    const macroLog = ref("");', "    const dryerCfg =") +
    "\n" +
    take(
      "    function markTagReadUnknown(",
      "    function pickerTagReadDisabled()",
    ) +
    "\n" +
    take(
      "    const _tagWriteSeen = ref(0);",
      "    function dismissTagWriteOutcome()",
    ),
  ctx,
);
const call = (x) => vm.runInContext(x, ctx);
function emit(ace, slot, seq, ok) {
  ctx.state.tag_op = {
    busy: false,
    result: { kind: "read", ace, slot, seq, ok },
  };
  watcher.cb(watcher.get());
}
call("markTagReadUnknown(1,0,4)");
let text = call("macroLog.value");
assert(text.includes("ACE 2 槽 1"));
emit(2, 0, 5, true);
assert.equal(call("macroLog.value"), text);
emit(1, 0, 4, true);
assert.equal(call("macroLog.value"), text);
emit(1, 0, 6, false);
assert.equal(call("macroLog.value"), "");
call("markTagReadUnknown(1,0,6)");
call('setMacroLog("其他错误", true)');
emit(1, 0, 7, true);
assert.equal(call("macroLog.value"), "其他错误");
call("markTagReadUnknown(1,0,7)");
emit(1, 0, 8, true);
assert.equal(call("macroLog.value"), "");
console.log(
  JSON.stringify({
    result: "pass",
    cases: [
      "late same-slot failure clears its warning",
      "late same-slot success clears",
      "other slot or old seq leaves warning",
      "unrelated error is never cleared",
    ],
    scope: "actual tag outcome watcher and logging functions",
  }),
);
