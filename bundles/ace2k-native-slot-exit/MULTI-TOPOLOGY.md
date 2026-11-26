# Four-unit Multi topology checkpoint (not a firmware image)

Multi mode, all four ACE units: each unit's lane 0/1/2/3 reaches head
0/1/2/3 respectively. This matches the Multi-mode slot==head invariant in
the 1902.7 engine. It is topology, not proof of the actual loaded strand.

For native exit on (A,S), reserve the corresponding head path on all four
units and both lanes of A's reader pair. Example: exit ACE 1 slot 1
(one-based) requires ACE 1 lanes 0 and 1, ACE 2/3/4 lane 0. Other-reader
operations can remain independent. Two exit plans whose protected masks
overlap cannot run at the same time. Normal motion only needs its actual
resources. Never assume head N means ACE N. A positive head sensor and
the exact recorded source are necessary for the head unload branch; a
missing source and a clear sensor do not establish that a tube is empty.

The new pure planner and exhaustive 16-by-16 conflict tests are deliberately
not installed. Next integration must bind masks to live UID/port/epoch
and apply them through the shared ownership coordinator, host motion
entrances and native links before permitting a start, including reconnect
recovery. No deployment or change to the MCU, RFID algorithm or saved
filament configuration. Physical parked/clear-route evidence is still needed.

Cross-unit primitive: NativeExitLinkGuard now accepts a token-bound quiet
reservation for a peer unit's same-head lane under its existing I/O lock.
Its mask is merged with the native reader-pair mask, and the real driver
guards both ordinary and automatic lane starts. It refuses busy or
unacknowledged reservations, reads fresh same-link lane samples and rejects
stale release tokens and replaced transport. The coordinator must collect
all peer reservations, verify standstill, persist ownership, and then
perform the selected native exit. The individual quiet hold does NOT
perform that orchestration or prove physical parking. Tests run against
the exported/pinned field driver. No production installation.
