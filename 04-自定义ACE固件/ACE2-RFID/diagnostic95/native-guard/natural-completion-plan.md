# Native completion candidate — not deployed

A single fresh READY is not proof native preload finished. Keep head sensors polled locally throughout. After target has shown PRELOADING, a READY transition starts a bounded candidate quiet window; any non-ready state, decoder movement or missing/malformed telemetry resets that window. Confirm completion only after repeated fresh all-lane READY and identical target encoder over a reviewed minimum duration, with an absolute timeout; if a head triggers or health is unknown, STOP immediately instead. A native phase may change encoder reference, so reject ambiguous changes rather than interpreting them as final distance. Do not use one READY or a cached response as a completion verdict.

The duration and handling of native rollback remain to be reviewed against physical evidence. This is a design note, not an enabled code change.
