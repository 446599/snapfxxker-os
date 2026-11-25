# ACE2-Open RF ownership: isolated offline model

**NOT firmware. NOT a hardware RF mutex. NOT safe-to-flash evidence.**

This directory contains a deterministic Python specification and tests only.
It does not import firmware/host integration code, open a serial/SPI device,
modify firmware, select executable hooks, or assert any MCU memory address.
All hardware work is represented by semantic effects and simulated callbacks.
No existing firmware image is approved by this model.

## Run

Python 3.10+ standard library only:

```sh
cd rf-ownership-model
PYTHONDONTWRITEBYTECODE=1 python -m unittest -v
```

`test-output.txt` records the last run. Time units are arbitrary model ticks,
not proposed hardware milliseconds. Timeout defaults are test inputs, not
measured or recommended reader timings.

## What the model specifies

- One global owner, independent of slot: BACKGROUND, CMD68, or HOST_TUNNEL.
- Background and CMD68 hold ownership over the **whole transaction**, not just
  individual SPI/register calls. A transaction may contain multiple operations.
- Host tunneling uses explicit begin/renew/finish control. The same lease remains
  held across frames and inter-frame serial waits. This is a proposed contract,
  not a claim that current firmware or the existing host protocol supports it.
- Only one reader operation is outstanding. Conflicting owners and overlapping
  operations fail closed; the model does not queue requests or promise fairness.
- Each lease has boot epoch, global RF generation, connection generation, and
  session generation. Each operation/cleanup/reset has its own callback sequence.
- Expiry is checked before requests/callback processing. Renewal cannot revive an
  expired lease, and a hard lifetime cap prevents unlimited host renewal.
- Logical ownership persists while draining, cleaning, quarantined, or recovering.
  There is no force-unlock, elapsed-time reclaim, or release on disconnect alone.
- Cancel/abort/disconnect/expiry suppress successful completion. If a reader
  operation is outstanding, cancellation must establish quiescence first.
- A success/failure operation callback means the **entire exact operation is
  quiescent**, including delayed work. Unknown outcome does not mean stopped.
- Cleanup runs only after operation quiescence, in this semantic order:
  1. Stop the reader engine and timer.
  2. Clear crypto/authentication state.
  3. Flush FIFO and pending events.
  4. Restore reader configuration.
  5. Restore antenna selection and RF baseline.
  6. Verify the quiescent baseline.
- Failure or missing confirmation in any cleanup stage retains ownership in
  UNKNOWN. Recovery requires a separately ticketed reset/quiescence verification,
  then **all cleanup steps again**. Recovery never revives the failed transaction.
- Disconnect during successful cleanup also suppresses success. Exact callbacks
  from a disconnected owner may only drain that still-current owner. Stale callbacks
  never affect a later owner, session, operation, cleanup attempt, or reset attempt.
- `effect_is_current` rejects queued actions after revocation or replacement.
  It is an event-model fence, **not** a solution for the real check/dispatch race.
- A short abstract metadata/command critical section serializes model events.
  Effects are taken and hardware/serial waits are represented only after the
  critical section returns. Logical RF ownership during a wait is intentional;
  keeping a command or RF mutex locked during a wait is forbidden. The model
  never masks IRQs and checks that its simulated wait points are unlocked.

A successful `Result` means only that the caller explicitly finished the modeled
transaction and every cleanup obligation was reported verified. It is not proof
of a tag read, UID, material, authentication, physical slot, or deployed image.
The model does not accept or return RFID payloads. Callers still determine the
correct full transaction boundary; this Python model cannot discover it.

## State transitions

| State | Admits new owner? | Next steps |
|---|---|---|
| IDLE | Yes | Acquire one lease -> ACTIVE |
| ACTIVE | No | Perform operations; finish -> CLEANING; revoke -> DRAINING or CLEANING |
| DRAINING | No | Exact quiescence -> CLEANING; timeout/unknown -> UNKNOWN |
| CLEANING | No | All verified steps -> IDLE; failure/timeout -> UNKNOWN |
| UNKNOWN | No | Explicit recovery attempt -> RECOVERING |
| RECOVERING | No | Verified quiescence -> CLEANING; failure/timeout -> UNKNOWN |

The model intentionally sacrifices availability when hardware state is unknown.
There is no guarantee of eventual release if cleanup/recovery cannot be verified.

## Verification performed

The suite exercises 27 tests, including:

- Every owner pair, with conflicts both during operations and between operations.
- Host inter-frame lease retention and wait/lock separation.
- Operation failure, abort, disconnect/reconnect, connection replacement, and
  disconnect during cleanup; background work is independent of host disconnect.
- Expiry both between frames and during operation; operation timeout and lost
  cancellation; exact-boundary callbacks; bounded renewal and no resurrection.
- Failure at each of all six cleanup stages, cleanup timeout, unverified reset,
  recovery timeout, and successful recovery followed by complete cleanup.
- Stale/duplicate operation, cleanup, recovery, connection, session, and boot
  tokens; overlapping commands; malformed callbacks; stale queued effects.
- All **5,040 permutations** of seven racing events: operation callback, expiry,
  abort, disconnect, reconnect, background acquisition, CMD68 acquisition.
- **6,000 events** across 30 fixed random seeds covering faults and stale events.
- Invariant and trace checks after interleavings: no second owner until release,
  no cleanup before quiescence, no release before the six verified cleanup steps,
  one terminal result per lease, and no lock held at modeled wait/dispatch points.

These are finite event-level tests. They are neither exhaustive model checking
nor proof about actual interrupt timing, CPU memory ordering, or firmware.

## Integration and hardware blockers: not guaranteed here

1. **Complete call-site coverage.** Identify and verify every firmware path that
   touches the RF reader, antenna selector, cached reader state, DMA, timer, or
   interrupt handler. Route all autonomous/background scans, CMD68 work, and raw
   host commands through one enforced owner. A lock around just CMD68 is insufficient.
   This directory contains no hook identification and no integration patch.
2. **Full transaction boundaries.** Determine the actual select/authenticate/read/
   decode/restore sequence for each reader path and enforce ownership throughout it.
   Native code, helper calls, retry paths, and callbacks must not bypass the gate.
3. **Cancellation and late effects.** Establish how a real in-flight reader job,
   SPI/DMA transfer, delayed queue item, ISR, or scheduled retry is canceled/drained.
   A worker reporting success/failure must guarantee that no later side effect of
   that ticket can run. Synchronize effect dispatch with revocation, and drain any
   already-claimed job; a standalone `effect_is_current` check is insufficient.
4. **Verified restoration.** Identify the complete real reader/antenna baseline,
   including crypto/auth state, FIFO, pending events, timer, bit framing, IRQ/DMA
   state, and antenna switching. Prove the cleanup order and readback semantics
   on hardware. The semantic labels here do not establish register addresses,
   values, safe reset behavior, or that any write actually took effect.
5. **RTOS/ISR and latency.** Implement atomic owner updates using verified scheduler/
   atomic primitives without masking IRQs or waiting while holding command/RF
   locks. Confirm memory ordering, reentrancy, priority inversion, stack margin,
   watchdog behavior, and worst-case latencies. Python execution proves none of these.
6. **Transport/session protocol.** Add and verify acquisition, lease tokens, renewal,
   release, error responses, disconnect detection, and host compatibility. Use a
   meaningful fresh boot epoch and bounded-counter wrap strategy. Model integers
   do not overflow; the default boot epoch is intentionally offline-only. Tokens
   are correlation data, not cryptographic authentication or firmware attestation.
7. **Hardware timing and fail-safe recovery.** Measure operation, cancellation,
   cleanup, and host-frame timing. Test unplug/replug, reset/power loss, missing
   reader responses, stuck SPI, DMA/IRQ races, and failed restoration. A timer must
   run even when the host or command loop stalls. Confirm resetting the reader
   actually quiesces every possible old operation before reuse.
8. **Physical correctness.** RF ownership alone does not establish antenna/slot/tag
   attribution, UID stability, anti-collision correctness, reader authenticity,
   spool binding, temperature safety, or safe filament motion. Those require
   separate verified integration and hardware checks.

Until these blockers are resolved, this model must not be used to set deployment
flags such as `rf_exclusion_verified` or `register_restore_verified`, to accept a
candidate image as trusted, or to claim hardware mutual exclusion works.
