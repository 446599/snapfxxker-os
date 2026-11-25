# ACE2-Open bounded SPI: isolated offline prototype

This is not wired into firmware and must not be flashed or treated as a tested fix.
No device, network, secrets, or motor/heater operations were used.

The portable C prototype models a two-byte SPI exchange using a single finite
poll budget. Timeout, cancellation, failed callbacks, and cleanup failure do not
return fabricated input data. Every failure after select quarantines the state,
even if line restoration succeeds, and rejects later requests until a separate
verified recovery. Cleanup failure is also quarantined. Host fake-device tests inject stalls in all four poll stages,
write/read errors, cancellation, select failure and failed cleanup. Host ASan/UBSan
(with LeakSanitizer disabled because the environment does not support it) and
an ARM Cortex-M3 freestanding compile passed.

These are logical tests, not hardware or real-time guarantees. Each adapter
callback must return in bounded time; this prototype cannot bound a stalled CPU
bus, implement owner arbitration, prove a safe clock-independent timeout, or
ensure that interrupts, watchdog, motion or heater control remain responsive.

Required before use: reverse-engineer and preserve both GPIO chip-select/control
line semantics; cover ALL calls from stock SELECT/transceive and background; add
a reliable per-transaction error channel despite the old uint8-only return ABI;
prove FIFO/crypto/register restoration and latched fault handling; implement a
single device-wide RF owner with safe interaction for CMD14, background, raw
multi-command tunnel and CMD13 cache; ensure STOP/thermal paths are not blocked;
verify target task stack, clocks, timeout latency and every failure mode under
controlled hardware tests. A simple wrapper around the new Bambu decoder does not
cover stock calls and must not be represented as a fix.

Cancellation requires an independently preemptible source: a STOP sitting in
the same blocked command queue cannot toggle the callback. The target adapter
must additionally handle and verify SPI BSY/OVR/MODF, residual RXNE, and CS
setup/hold timing. Tests inject real STM32 TXE=2 and RXNE=1 bits independently.

A second cancellation check follows line restoration, but a production owner
must still verify the same transaction generation and STOP epoch atomically at
commit. A later STOP can race any standalone check.
