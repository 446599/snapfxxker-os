# Four-unit Multi topology checkpoint (not a firmware image)

Multi mode: slot N of each of four ACEs goes to head N. Head number never
identifies which ACE currently supplies it. A clear head switch is not a
parked-route certificate.

Updated policy: verified independently parked native eject reserves only
its target lane. No RFID-reader-wide motion lock. Shared head operation
uses the common head and its same-number paths on four ACEs, not unrelated
RFID neighbours. The MCU's normal RFID arbitration and hardware protection
remain intact. An unknown route cannot be treated as parked.

NativeExitLinkGuard holds a single lane under the actual link I/O lock,
merges each independent lane into the MCU in-use mask, dispatches native
mode 4 without replay and matches actual lane/epoch/seq terminal reports.
Other lanes, including the other lane sharing a reader, can move and do
not participate in target standstill proof. A quiet hold remains available
for a genuine shared head operation. Tested with four native starts on
the same pinned production link before any terminal event.

These are offline integration tests, not an installed provider. The old
route_journal/cloud_route_owner prototypes still need resource-level
reconciliation with actual host motion entry points, durable recovery and
proof of parked tip locations. No firmware image or MCU flash.
