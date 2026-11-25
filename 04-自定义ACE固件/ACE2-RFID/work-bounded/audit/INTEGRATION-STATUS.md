# Bounded SPI / transceive status

## Result

This work does **not** produce flash-ready firmware. Current changes compile to
an ARM relocatable object, not a linked application. No hardware was contacted,
no network access was used, and no flash operation was attempted.

`python3 run_checks.py` rebuilds all current tests and ARM objects and emits
`output/current/manifest.json` binding each source, object, stack report and log
by SHA-256. Earlier files directly under `arm/` and `output/` are obsolete prior
experiments. In particular, `PARTIAL-GUARDS-DO-NOT-FLASH.bin` contains neither the
current bounded driver nor the checked bridge. Its two hooks leave stock SPI
unbounded. It must not be used as evidence of these fixes being installed.

## Implemented and tested

- Finite status-poll budget shared across SPI register reads, writes, transceive,
  and cleanup. No SysTick read inside this driver. A fault permanently quarantines
  the supplied shared state. It never clears the fault to retry or fall back.
- Error checking, CS/auxiliary latch restoration and refusal to return a fabricated
  successful read after a transport error.
- Transceive validates FIFO count against caller capacity, checks all ErrorReg
  bits including CRC/collision, stages data and checks required bit length before
  publication. Partial results are not copied on failure.
- Publication enters a mandatory short platform metadata critical section,
  checks the live owner and STOP generation, then copies at most 64 bytes. No
  hardware, delay or scheduling work is done in that section.
- An **offline-only** stock ABI bridge preserves the byte/void signatures while
  requiring a shared sticky fault. Its SELECT wrapper stages the 22-byte result
  and rejects stock success after a hidden read/write fault. Fallback does not
  replenish the poll budget or clear quarantine.
- Four exact stock caller return PCs constrain output to SAK 1 byte/8 bits,
  anticollision 5 bytes/40 bits, ATQA 2 bytes/16 bits and page read 16 bytes/128
  bits. Unknown callers and the stock command-4 path are refused, not silently
  treated as transceive. Appended decoder callers need an explicit-capacity API.
- The assembly thunk passes the true LR and original six arguments to the checked
  bridge. It was assembled and inspected, **not executed in an ARM emulator**.

Current host results: **34,624 assertions** in the combined core/bridge suite,
including every status-poll fault and STOP boundary of a full transfer, every
budget cutoff, malformed response lengths, four stock caller FIFO sweeps from
0 through 255, cancellation at commit, and false stock success after a void
write failure. ASan and UBSan pass. LeakSanitizer cannot run under this runtime's
ptrace and was disabled; the tested C has no heap allocation. Stock SELECT and
MMIO in these tests are explicit test doubles, not original firmware execution.

Current ARM compile: **1,688 text bytes, zero data/BSS**, relocatable object only.
Seven intentionally unresolved symbols remain:
`br_platform_commit_valid`, `br_platform_denied`, `br_platform_enter`,
`br_platform_leave`, `br_platform_transaction`, `memset`, `stock_select_resume`.
The bridge refuses compilation without `BR_OFFLINE_TEST`. Stack reports are
per-function and do not prove whole-task headroom. The biggest new individual
frame is `br_transceive_exact` at 112 bytes; nested stock/decoder/IRQ frames must
still be included. No firmware ELF, new patch specification or firmware image
was produced by the current builder.

## Exact unresolved requirements

1. **Persistent storage and startup.** No persistent SRAM address is supplied.
   Stock heap setup loads 0x20002694 at 0x08013300, aligns to 0x20002698 and writes
   its initial header at 0x08013348. The four-byte alignment gap is not a proven
   storage reservation. Changing heap origin alone is insufficient because
   range checks/free paths also encode the original allocation range. Do not
   overwrite either location to make a link succeed.
2. **Whole-operation owner.** Both readers, stock SELECT, the appended decoder,
   fallback, background worker, host raw commands and all cache publications
   need the same live rf_operation identity and shared transport latch. A byte
   return of 0xff is still a valid byte; the bridge alone cannot make unwrapped
   consumers safe. `BR_OK` is not `RF_QUIESCENT` and no br_result alone proves an
   authentication rejection.
3. **STOP and thermal liveness.** The bounded driver has no tick-dependent loop,
   but the surrounding stock SELECT still calls 0x08013C70. Its nested-state
   assertion path sets BASEPRI=80 at 0x08013C98 and loops forever at 0x08013CA4.
   Therefore the wrapper is not a proof that STOP/thermal work remains runnable.
   No user-visible STOP integration or thermal heartbeat monitoring was installed.
4. **Cleanup and halted/unknown state.** The driver only checks CommandReg Idle,
   StartSend cleared and FIFO empty. It does not prove crypto off, analog/RF
   baseline restored, physical pin voltage, absence of pending DMA, or timer and
   interrupt quiescence. Stock command 4 is deliberately not implemented.
5. **Actual-image caller coverage.** A future read/write hook must replace whole
   instructions: stock entries start with a 2-byte stack instruction followed by
   a 4-byte LDRD. A four-byte patch would split that LDRD. The current bridge does
   not install any of these hooks. All new decoder calls must use capacity-aware
   checked interfaces; final response/cache commits also need owner/fault checks.
6. **Hardware bounds and configuration.** A finite poll count is not a measured
   millisecond bound. CPU speed, peripheral timing, SPI byte mode, DMA ownership,
   GPIO semantics and task priority are not proven by a host model. A wedged CPU
   bus cannot be bounded by a C loop. Electrical safety is not verified.

## Bounded next steps

Obtain a supported linker/startup memory map (or complete the coherent allocator
reservation proof) and a genuine owner/STOP binding first. Then implement missing
cleanup/halt and eliminate or bound surrounding scheduler/SELECT waits. Rebuild
the final combined application, enumerate every call/publication path and run
instruction-level tests using the actual image with faulted SPI and frozen tick.
Only after this passes should a controlled bench verify STOP response time,
thermal-task liveness, power/reset and shared-reader behavior. Do not flash the
present partial image to discover these properties.
