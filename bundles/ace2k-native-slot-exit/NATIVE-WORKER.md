# Native exit worker integration checkpoint

The private native_exit_* requests now enter the actual Ace2kLink.write /
_execute path in a staged copy of the pinned field driver. They use the
existing per-link I/O lock and do not wait for motor completion on the
Klipper reactor. Four lanes may have independent active jobs on one ACE.
Prepare reserves the selected lane and waits for real native reports;
start emits mode 4 once only, with no replay of a possibly accepted start.
Status recovers a lost terminal event only after the unit's current seq
matches the task, and returns verified only on kind 6 plus independent
post-event still/empty evidence. STOP has priority and exact lane/seq/
epoch/token matching. STOP alone does not free unknown physical routing.
A verified task holds the lane until private coordinator acknowledgement
of durable completion and another evidence check. Retired tokens cannot
be reused within that connection. Ordinary driver ticks still run while
private status is polled.

This is NOT a public G-code or HTTP endpoint. The host still must supply
actual route/parked evidence, bind a durable claim before prepare,
persist dispatch before start, and reconcile/recover claims across
connection loss and restart. The worker cannot prove those things from
inlet telemetry. Release is only the private transport acknowledgement
of a host-side durable commit; no real durable coordinator caller yet.
The complete screen and compose/install paths remain disconnected.

7 writer-path tests and 97 total offline tests (including the exported
uncommitted prototypes) passed in the cloud sandbox using exact exported
field source fixture. GitHub CI runs against the pinned repository
archive; no printer movement, MCU flash, or firmware image produced.
