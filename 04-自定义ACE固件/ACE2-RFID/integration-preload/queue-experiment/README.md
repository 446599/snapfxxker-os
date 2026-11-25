# Queue association experiment, frozen offline inputs

This is a separate copy of the c132 accessor experiment. It is **NOFLASH**, not
usable firmware: reset stays trapped, RF-ready stays false, the IAP trailer is
absent, and seven production proof symbols remain unbound. No device, network,
installation or flash operation is used. The old accessor and RF branches are
unchanged. Normal INSERT branch bytes remain present; this does not demonstrate
normal automatic rotation on hardware or implement the requested final behavior.

## Real code changes

The original RFID queues remain depth two, item size one. Ten additional Thumb
hooks cover job link/unlink, the four confirmed send sites, the generic copy/count
commit, receive copy/count commit, and both preload command6 call sites. All patch
bytes and decoded branches are checked against stock V1.1.31. The combined image
contains 39 hooks. See output/manifest.json for exact bytes, symbols and hashes.

A checked event-group tail stores per-lane generations, two per-reader journals,
and eight bounded pending/context entries. ARM offsets are rf16/ex76/ph144/queue184;
tail608, payload640, minimum allocator block648. EXT4/RDY4 replaces older layouts.
Initialization clears the full tail, keeps RF unknown/revoked, then publishes ready
last. Allocation metadata ready is not RF or motor readiness. Heap margin is unknown.

The journal is committed inside a short added PRIMASK interval that includes the
actual stock one-byte copy and count update. A potentially blocking stock send
never runs with an added PRIMASK held. Failed/full sends create no committed token.
Non-RFID handles are exact native pass-through. Unknown producers keep native byte
traffic but poison association, rather than invent lane zero or a current owner.

Three audited counterexamples have regression tests:

1. Pending identity includes current TCB, IPSR=0, stock commit return 0x080167FB,
   and the saved sender return equal to pq_stock_send_return|1. That saved LR is
   read at SP+44 only for the known stock task-send caller. Another task, ISR, or
   unknown caller sharing the same item pointer cannot borrow a preload ticket.
2. A sibling lane merely entering the job list does not revoke the consumed lane's
   candidate. Actual unlink/reuse of that same lane invalidates its generation.
3. ABEC/ACFA install a same-task requeue context around the existing RF adapter.
   Command6 preserves the candidate only for the matching live lane/generation.
   Unknown/wrong operation command6 does not inherit that association.

pq_candidate is only an observation of a consumed queue job. It does not identify
a physical tag or authorize ph_claim_reader, ph_publish_failure, RF success, or
motor STOP. There is deliberately no terminal-failure producer connected.

## Reproduce

From /workspace/scratch/f283fdd55202:

    python ace2-open-build/integration-preload/queue-experiment/verify_queue.py

The entry rebuilds twice and checks deterministic output, default/production
refusals, nine host suites with ASan/UBSan, 4,380 old PH finite instruction cases,
534 queue hook/ABI cases, 56 actual ARM sideband + stock copy/count window cases,
128 measured instruction windows, and nine image/conflict tests. The host storage
checks include 95,660 literal ARM640/648 boundary/version assertions independent
of host pointer width. Queuing host tests have 315 assertions.

The finite interpreter is not a complete CPU/RTOS emulator. Queue wait results
are explicit success/failure endpoints. ISR cases are synthetic contexts. The
command6 continuation test explicitly supplies dispatch while the real RF-ready
flag remains false. Unknown instruction/read/branch fails. No mechanical, IRQ,
CPU-cycle, whole-scheduler or full-interleaving claim is made.

## Critical section and stack cost

Duplicate allocator-header checks inside the same masked commit were removed;
the initial allocation check, all queue fields, identity and atomic copy/count
checks remain. Receive now inspects token/candidate in place under the same lock,
consumes the ring record last, and avoids two 20-byte temporary copies.

The measured longest *added* thread-mode mask region is 446 instructions, for
lane2 with seven other pending entries at enqueue commit. Breakdown:
ph_irq_enter/leave4, enqueue C210, storage5, current-accessor27, allocation-header68,
allocation-size16, reader-filter13, queue-fields68, original trampoline2,
stock enqueue19, stock copy14. Full per-path records are in
build/queue-cost-results.json. This is not a universal upper bound or time bound.

The separate 56-case test reaches 748 cumulative instructions when the fixture
already has PRIMASK=1; that is not the added thread-mode masking cost. Maximum
stack depth below fixture entry is 240 bytes and includes native frames plus the
explicit requeue dispatch fixture, not a net hook-only stack increment. ARM .su
files provide individual C frames. Real nested RF, task and exception stacks,
HCLK, flash wait states/prefetch, bus timing, NVIC priorities, deadlines and
NMI/DMA behavior must be established before judging realtime safety.

## Remaining concrete blockers

- Fixed job nodes, queue handles and TCB values can be reused; reset/free/ABA and
  all producers/receivers/cancel/reorder paths are not proven. Exact known source
  rejection is implemented, not proof that unknown sources cannot occur.
- E308/DEB6 is SELECT, not AUTH. FDFC/E18C short-read/cache state3 and reader state2/4
  do not distinguish brand authentication from ordinary tags/transient failures.
  A successful SELECT can allow READ failures to retry indefinitely.
- Reader state6/7 is scanning, not terminal failure. Job state11 waits motor event
  bit2 and does not stop power. Calling full AF84 in E244 can wait on its own queue.
- Safe physical STOP, all GPIO/PWM/TIM/encoder writers, mechanical quiescence,
  reader-to-spool identity, recovery/unlock policy, and heap/stack/latency margin
  remain unproved. Permanent sticky fault on ordinary reads is not a final design.
- Missing accessor state still cannot safely infer an unknown publisher's lane.

For RF-runtime/typed-diagnostics merging, use output/merge-contract.json. The merge
must be a new directory and new storage version, preserve the RF branch's ex+0
owner_task/busy semantics, and must not turn this candidate into production owner.
