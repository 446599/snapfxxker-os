# Second regression: native ACE2K stall paths

Date: 2026-11-25
Base: upstream MultiACE+ 1.90.2 (91ba652f6896f22cecb4cf779ee5db06ee8e7840)
Candidate parent: 54f90b27891cb51a205df206ea69fc83b10123c5

Local Python unittest: 54 collected, 53 passed, 1 skipped (full checkout AST comparison).
The extracted eight-method fixture is byte-for-byte the candidate source produced
by applying the pinned source patches. The test uses a simulated clock, mocked
hardware and transport, not a connected printer. Source anchors verified separately.

Changes:
- Check the original busy retry deadline before retrying a start, and ignore a late
  DONE for purposes of starting another piece after the current piece deadline.
- Keep running feed/tag/auto-buffer ownership until a terminal event or a recent
  idle/error mode; retry STOP within a finite three-second grace period. Only
  unresolved stop goes through the existing link recovery path.
- Feed STOP cancels the waiting start and addresses the selected lane; a failed
  follow STOP cannot start a competing retract. Explicit invalid indexes cannot
  become an all-lane STOP. A stop of all following lanes also cancels latent wants.
- Speed updates cannot extend a piece past its physical minimum-speed deadline.
  An unchanged speed leaves the existing deadline alone; motion jobs use the
  same clamped speed as the firmware.
- Incomplete/malformed telemetry is treated as unavailable, not an uncaught
  exception; V2 read requests stop on the first missing response instead of
  waiting through three separate timeouts. Connection and driver changes
  invalidate an in-flight result.

Scope: targeted simulated regressions and source anchor validation only. The
complete upstream checkout was not available in the local environment (GitHub
DNS lookup failed); the Actions API returned no run for the previous workflow.
The complete 1.90.2 managed build, custom 233 slot-exit/UI/config migration,
mixed-fleet hardware behavior, and .15.116 printer runtime remain unverified.
The test workflow now checks the actual staged ASTs before full upstream tests.
The existing package pins and main are unchanged. A link recovery can affect
all lanes of the affected device even though STOP is addressed to one lane.
