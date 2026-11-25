# ACE2 V1.1.31 RF owner audit (offline, NOFLASH)

Input SHA256: a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e.
All shorthand addresses below have prefix 0x0800 unless otherwise shown.

## Main finding

Use one global owner for both readers, held across complete logical RF operations, all related GPIO changes, and cache publication. Per-reader locks and byte-level SPI locks do not serialize the shared bus or protect FIFO/configuration/read-modify-write sequences.

99E0 initializes descriptors 0x200014D4 and 0x20001508. Both descriptor[0] are SPI 0x40003800. First CS pair is (0x40010C00, 0x1000); second CS is (0x40011400, 0x0400). Both descriptor+12 pairs are (0x40010C00,0x2000). Descriptor+44 GPIO control pairs are (0x40011400,0x2000) and (0x40011400,0x1000). The electrical meaning of GPIO+44 is not established here, so do not label it a proven physical antenna-off or safe-reset mechanism.

## Logical boundaries and bypasses

- EA14 is a never-returning task, not a function suitable for a normal entry/return wrapper. It invokes DD5A twice at EA20/EA2E, writes GPIO directly at EA38/EA40/EA56/EA5E, initializes reader pointers at EA6A and command registrations/queues, then loops EB20..EC92. Protect its startup separately and its outer loop's cache changes separately, or replace the task with a bounded owner-mediated state machine. Holding an owner forever at EA14 is wrong. Wrapping only E244 misses EA14 startup and cache writes outside E244.
- DD5A..DEB4 sets up clocks/pins, reader descriptor pointer at DE8A, SPI CR1/I2S config at DE9A/DEA2/DEAC; it bypasses F574/F5D0. Shared init flags are 0x20000EA0 and 0x20000FEC. Its only direct callers found are EA20/EA2E.
- E244 must own from before its GPIO/config/start through HALT and cache publication. GPIO direct store E3EE precedes 200-unit scheduler delay E4B6 and reset/select DEB6 at E4C8. RF calls also include E308/E4E2 -> DEB6; E4FA -> E108; E688/E6DE -> FDFC -> FE32 -> E18C; final E6FC tail-branches to E108. A return wrapper must handle that tail call and release only after cleanup.
- E7A8 (CMD68) needs ownership before E7FA GPIO store, through E7FE delay(20), E812->DEB6(reset=1), E82E->E18C, and shared scratch/cache use at 0x20000EA4. Calling E18C or DEB6 behind only leaf locks does not protect its whole transaction.
- E70C writes GPIO at E73A then E744->0x08016630 with timeout 0xffffffff. Direct callers ABEC/ACFA. E74C (CMD14) writes GPIO at E788 then E79A->same indefinite queue send. Do not hold the RF owner while retaining these potentially indefinite queue sends. Replace/mediate the operation, use bounded queue handling and preserve failure semantics; do not silently report success after refusing a conflicting operation.
- E910 (CMD13) is a cache reader, not an RF operation in this image. It copies cache fields from 0x20000054 and 0x20001600. Synchronize snapshot/publication to avoid tearing, but do not invent a direct SPI call from it.
- E9EA (CMD20) writes configuration byte 0x20000098 and dirty flag 0x20001E60. It does not touch SPI directly. Include configuration snapshot/update coherence if owner decisions depend on this state.
- F30A is FIFO flush/read-modify-write; F50C is multi-register timer config. DBA2/DC50 are select/anticollision stages; DEB6 combines reset/config/request/select, E108 HALT setup+transceive, E18C repeated page/block reads using global scratch 0x20000704. These nested operations should require/assert the current owner, not acquire an unrelated lock per byte. Explicit same-owner recursion/context is needed if wrapping nested functions. An ISR must not inherit thread ownership merely because a global 'busy' flag is set.

## Blocking paths that an accessor patch alone does not eliminate

- F574 waits without a counter at F590/F5A0/F5A8/F5B4. F5D0 waits at F5F4/F608/F610/F61C. All callers appear in call-address-evidence.md, including tail branches.
- E108->E182->F32C: replacing read/write alone leaves transceive's tick-based loop. F3E4..F416 uses tick 0x2000820C; frozen tick with no completion can loop indefinitely. Its inner 1200 decrement loop is finite but the outer loop is not independently bounded. F39C payload loop is do-while and underflows on zero length; all ordinary stock call sites inspected supply positive lengths, but replacement API must reject zero/oversized lengths explicitly.
- E244's E4DC..E512 retry loop: successful DEB6 resets the failure counter to zero, then HALT, then checks ticks. If tick freezes and DEB6 succeeds repeatedly, iteration count never caps it. Finite per-transceive work therefore does not imply finite E244 work.
- Scheduler delay 0x08013C70 is used from DEB6 at DED0/DF0E/DF64 (1/100/30), CMD68 E7FE (20), E244 E4B6 (200), EA14 EA44 (100) and EC8E (10). It calls 0x08012618 and 0x080177A4, can context switch via PendSV, and has an explicit infinite trap at 0x08013CA4 if scheduler nesting state is invalid. It is not a cycle-bounded busy delay.
- E70C/E74C queue sends and EA14 event wait EAD6->0x080162F0 use 0xffffffff timeouts. The EA14 outer loop is inherently perpetual.

## Indirect command path

EA6E/EA7C/EA8A register wrappers FEAC/FEC8/FEE4 for commands 0x44/0x0D/0x0E. Wrappers provide descriptors at 0x08018D70/0x08018D7C/0x08018D64 to 9C5C. Handler fields at +8 are E7A9/E911/E74D. 9CA8 loads handler and 9CBE BLX invokes it. CMD20 similarly uses FF00 and descriptor 0x08018D88 with E9EB.

Raw full-image byte-aligned scans found no literal even/Thumb pointer to F574/F5D0/F32C/E108. This is not proof of no computed indirect entrance. The supplied stock.S is linear binary disassembly; full control-flow recovery, data-flow alias analysis, interrupt preemption proof and all GPIO port aliases were not completed. In particular generic GPIO helpers elsewhere write same port registers with other masks and must not be blindly disabled as 'RF'. No claim of exhaustive resource-write closure or physical safety follows from these findings.

## Minimum implementable contract

1. Global owner identity plus logical-operation state, atomically acquired/released without holding interrupts disabled across RF work. No implicit privilege for an unrelated preempting context.
2. Every reachable entry that can mutate RF GPIO/SPI/config requires that owner or defers/rejects with explicit failure before any mutation. Boot init is exclusive and finishes before publication/use.
3. E244/CMD68 run as one bounded transaction/state machine, retaining ownership through all selected-reader operations and cleanup. E70C/CMD14 publish bounded/deferred intentions rather than blocking while holding ownership.
4. Leaf accessors, transceive, retry loops, scheduler-dependent delays and cleanup/HALT each have independent finite budgets and explicit failure propagation. Do not re-enter original blocking HALT on error.
5. Cache readers and outer worker updates share coherent snapshots/publication. No whole-task EA14 lock.
6. Re-audit the final patched binary, including relocated trampolines and all original/tail/indirect entries. This audit alone authorizes no flashing and establishes no STOP, thermal, motor or physical cleanup safety.
