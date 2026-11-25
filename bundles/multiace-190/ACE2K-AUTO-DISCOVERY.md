# ACE2K automatic discovery — 1.90.2 candidate only

Base: decay71/multiACE V1.90.2, 91ba652f6896f22cecb4cf779ee5db06ee8e7840.
The layered 233screen_with_PAXX_Multiace tree (c120aeb) still contains
the old 1.27b provider; do not overwrite 1.90.2 with it. This branch extends
the staged 1.90.2 candidate from eab1c84. The older candidate archive in
artifacts/ is NOT rebuilt and does not include these changes.

Upstream ACE2K is opt-in, and its USB path must otherwise be explicitly
declared. The candidate enables ACE2K and uses an idle-only read-only
V2 identity probe followed, only on silence or invalid framing, by an
ACE2K dictionary/UID probe. Only a verified nonzero UID claims the port.
Explicit ace2k_ports and exclude_ports remain authoritative. The automatic
path covers only official ACE 2 USB identifiers when V2 and ACE2K are
both enabled, not arbitrary RS485 adapters. An existing installation
needs enable_ace2k: true and ace2k_auto_detect: true in its [ace] config.
Setting ace2k_auto_detect: false preserves the explicit-port path.

One identification job per scan shares the upstream probe budget; no
new alarm category. The worker waits at most 5.5s on the reactor with
25ms cooperative pauses and retains pending ownership after a timeout.
A verified port is cached for 30s, misses for 15s; active ports clear the
cache and retain their already-open driver. A changed tty identity
invalidates the cache. Reflash through ACE_FW_SET_DRIVER or restart
after a manual reflash. Automatic reclassification during printing is
not attempted.

Validation: 13 standalone simulated tests passed for the helper, and
all six new source anchors matched the pinned upstream files. Full
staged integration, the full upstream suite, package rebuilding,
physical USB and mixed-fleet reconnection tests remain unverified.
Do not deploy this as a production component until those pass.
