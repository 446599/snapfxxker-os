# Narrow preload abort integration experiment

**Not fixed for production. Not flashable. No device was connected.**

> Latest work is in `accessor-experiment/README.md`, revision c132. It combines
> all 29 hooks with a checked, explicitly expanded event-group accessor and
> links within the documented application range. The older fixed-test-RAM /
> `0x08030000` artifacts below remain historical NOFLASH experiments only.

This is actual compiled C plus exact-stock Thumb hook code, not merely the
earlier `work-preload/firmware/preload_failure.c` state model. It demonstrates
specific instruction-level fixes while keeping missing platform evidence
unbound. It does not replace or silently enable the `work-preload` build.

## What now exists

- `src/preload_hooks.c`: separate sticky lane failure latches and per-reader
  explicit `(lane, epoch, owned)` validation. The lane must be supplied; it is
  not guessed as `reader*2`. A stale or sibling failure cannot stop a new owner.
- `src/preload_hooks.S`: ten exact Thumb patches and bounded, synchronous
  stop-effect dispatch. Normal INSERT rising, stock sibling checks and reader8
  checks are preserved. There is no blanket INSERT disable.
- A validated failure latches first, calls stock `F088` to clear motor-active,
  reset its GPIO, and write inverted PWM=1000; clears only that lane's TIM4
  DIER/SR bit; calls stock `9EB8` to disable encoder UIE and clear encoder
  active/length/rollback flags. This bypasses AF84's mutable slot-state gate.
- Gate checks and their associated effects execute under saved/restored PRIMASK.
  They preserve caller registers and displaced-instruction APSR effects.

Exact hooked addresses:

| Address | Scope |
|---|---|
| `0800F726` | Reject latched lane before INSERT queue mutation |
| `0800ECE8` | Reject known-start entry while latched |
| `0800ED38` | Recheck after the 200-tick sleep |
| `0800ED46` | Atomically fence PID-active and motor active=1 writes |
| `0800ED76` | Fence motor-enable GPIO write |
| `0800EE98` | Fence TIM4 DIER enable; re-read DIER under lock |
| `0800EECA` | Fence final start PWM write |
| `0800F012` | Fence final periodic PID PWM write |
| `08009D62` | Atomically fence encoder UIE and encoder active=1 |
| `0800EFA4` | Atomic live-read TIM4 DIER disable, preserving sibling changes |

The live TIM4 DIER read matters: using stock EE96's earlier r1 snapshot can
restore a sibling lane's interrupt bit after its ISR has cleared it. The new
hook preserves sibling changes. Encoder DIER is also re-read under the lock. Stock EFA2/EFA4
DISABLE can likewise restore stale sibling bits if preempted; the EFA4 hook
now performs live-read/BIC/write under the same short saved-PRIMASK section.
This does not assume that failure publishing runs only in thread context.

The post-wait check alone was insufficient: a failure immediately after it
could be followed by stock ED4A restoring active=1. Similarly, fencing EE9A
alone did not prevent EEA4 -> 9D2E -> 9D62 from enabling encoder UIE. The two
added atomic effect gates address those specific windows. The tests retain
executable counterexamples for the original unguarded instructions.

## Build and verification

From this directory run `python run_checks.py`. It uses only existing local
GNU ARM tools and Python. It installs nothing and performs no network access.

Verified against stock SHA-256:
`a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e`

- ARM compile and simulation-only link succeed.
- All ten old instruction byte sequences match. Independent objdump decoding
  confirms each installed branch reaches its exact linked Thumb entry.
- Compilation without `PRELOAD_INTEGRATION_EXPERIMENT=1` fails deliberately.
- The production link fails deliberately for missing `ph_storage` and all five
  platform verification bindings. `build/production-link-denied.log` is evidence.
- Native host ownership/latch tests pass, also with ASan and UBSan. The first
  default LeakSanitizer run was blocked by the environment's ptrace support;
  the successful sanitizer run disables leak checking only. Code allocates no
  dynamic memory.
- 2,300 finite instruction checks execute byte-verified assembled code,
  compiler-generated failure code and the stock stop leaves. They cover four
  lanes, every NZCVQ combination, initially masked/unmasked IRQ state, stale
  ownership, sticky latches, effect-gate instruction-boundary fault injection,
  stale shared-DIER read, active/UIE race regressions, and the TIM4 handler leaf
  F070 after active=0 with several move counts.
- The model observes at most 32 instructions inside an individual gate's
  masked section. This is **not** a latency bound, cycle count, complete stop
  dispatch bound or measurement on the MCU.

The finite interpreter is not Unicorn/QEMU, a full CPU, a board emulator, or an
RTOS model. It rejects unknown instructions/reads. MMIO is a write log and the
fault event is synthetic. It does not model electrical behavior or real IRQ
delivery. Its test name and output explicitly preserve those limits.

## Artifacts

- `build/simulation-only.elf`, `hooks.dis`, `hook-payload.bin`
- `build/SIMULATION-ONLY-NO-IAP.bin`: sparse offline test address space, not a
  deployable decoder/preload firmware. It deliberately omits the IAP trailer.
- `build/manifest.json`, `instruction-results.json`, individual check logs.

Simulation-only storage is `0x21000000`, deliberately outside the MCU SRAM
range. The five test proof symbols are explicit `0x0BADF00D` sentinels, not
claims of verified hardware or lifecycle properties. Do not copy them into
any firmware linker script. No production RAM address has been invented.

## Exact blockers still preventing an actual fix/flash

1. **No real failure producer is wired.** `ph_publish_failure` is called by
   tests only. `native_tag_decoder.c:rc522_mf_authent` currently returns a bool
   that merges no reply/timeout/transport behavior; `decode_native_tag == -1`
   also includes no-tag, unsupported payload and read failure. None proves
   owned terminal authentication rejection. Stopping on every `-1` would
   incorrectly change normal tagless/unsupported-spool behavior. A bounded
   owned RF result must supply the real lane, lifetime/epoch and reason.
2. **No proven RAM/startup lifetime.** There is no production `ph_storage`,
   exclusive-startup hook, reset/reinitialization rule or failure-safe
   allocation binding. A potential allocation extension at `080116A8` needs
   independent capacity/lifecycle/allocation-failure proof before use.
3. **Queue ownership/atomicity remains incomplete.** F726 checks a sticky
   failure, but a failure can publish after that check and before stock F76A–
   F792 links the job. Later known start/effect gates prevent those specific
   starts from taking effect, but the queued job itself is not retired or tied
   to a generation. No new code clears reader state9 as a fake solution.
4. **Not all writers/lifetimes are proved.** ED0E/ED14 toggle shared GPIO bit9,
   ED94 changes direction GPIO, EE7E writes TIM4 status, and EE3E/EE6E/EEA4
   invoke RTOS/virtual methods. The encoder callback table at `08018C34` has
   `+8=08009D1B`, whose code clears counters, but object/vtable lifetime and all
   alternate indirect paths are not proved. NVIC/shared pending, capture races,
   IRQ priorities and clock/latency effects remain hardware concerns.
5. **Controller and mechanical stop proof is absent.** F070's active=0 path
   performs stop writes in the limited instruction model; that does not prove
   every ISR is quiescent. EXTI0 `08008BA0` independently toggles GPIOC bit8 at
   `08008DD8/08008DE0`; its physical relation to preload must be established,
   not guessed. No mechanical rest, braking, jam, no-late-enable or complete
   controller-retirement flag is fabricated. Return `PH_APPLIED` means only
   the enumerated software stop writes were performed.
6. **Recovery is deliberately absent.** A fault remains sticky for that lane.
   Normal, healthy lanes still auto-rotate; a verified stopped/new operation
   policy is needed to safely rearm the failed lane without accepting an old
   queued job. Epoch exhaustion does not wrap into reuse.

All five verification symbols remain unresolved in production. Do not remove
the gates to make a link succeed. This experiment advances the actual hook
implementation but does not satisfy the user's requested safe final firmware.
