# 1902.7 -> native ACE2K slot exit development (not a deployable image)

Base: 5c0bcb37bc0bf226b5c802a46566574e03a9e5d9, 1902.7. Field report confirms four units, ACE 1 233c and ACE 2–4 233b, partial manual-save acceptance, 16 API write/read checks. No changes to RFID, persistence, firmware images, motion, or the installed provider in this branch. The report's detailed ACCEPTANCE.md and screen-interface-audit.json are local files, not available here.

## Source-confirmed missing interface

- Screen material_workspace.cpp maps eject -> ACE_SLOT_EXIT ACE/SLOT; material_slot_task.cpp requires connected ace.aces[].slots[].position_unconfirmed and eject_recovery_allowed booleans, top-level slot_action, tag_op.busy, swap_in_progress and unload_all_active. Missing fields invalidate the whole task, rather than proving safe idle. Screen stop targets include epoch, kind, ace, slot, seq. Old device_service.py uses objects/query and multiace/slot_operation_cancel.
- Old ace_slot_actions.py depends on path calibration/physical source, stop fault, location hold and motion ownership, plus V2-specific raw evidence. Do not transplant that code unchanged or invent position_unconfirmed=false.
- 1.90.2 native protocol maps unwind_filament to mode 1 rollback; ACE2K v0.16.0 has native mode 4 UNLOAD: sensor-driven exit + MCU clear, terminal event mode 3 kind 6, length_um=0 native bounded budget. It already handles motor failure/timeout. v0.16.0 feed_last_query can recover a lost terminal event. No success on a rollback done event or empty gate alone.
- Existing lane_contract.py and lane_io.py provide validated native inlet, mode, encoder, FG, independent report times and connection continuity. This staged contract reuses them. No fake V2 flags.

## Staged code

native_eject_contract.py is a pure protocol and acceptance contract, not a transport or a motion API. It is deliberately not wired to the provider, screen, G-code or startup. Its tests cover the real native mode, unit/lane/seq binding, freshness, cancellation, terminal event and verified stillness. A caller must own the existing driver transport and route; never start a second serial driver.

`native_unload_wire.py` is an **unwired development prototype**, not a usable
or deployable motion implementation. It exercises the existing link's
sequence allocator and transport under its I/O lock, the native UNLOAD fields,
a single non-replayed start frame, targeted stop on an uncertain start,
terminal-event matching and a single
last-event query. The tests include the real pinned 1.90.2 link class with a
simulated serial transport. The prototype's per-link reservation is visible
only to itself: the production driver's normal motion, automatic RFID and
background paths do not yet honor it. It has no route lease, durable stop
fault, safe reservation-release protocol, event subscription, screen API,
or packaging hook. Do not call it on hardware or include it in a firmware
candidate until those missing owners and release conditions are implemented
and reviewed. In particular, a `LaneSample` has no embedded lane identity;
the future caller must bind both samples to the actual connection and lane.

## Required integration before any test image

1. Read the field audit/logs and reconcile the exact released screen binary's command/status/stop contracts with the installed 1902.7. Verify actual get_lane_state evidence for 16 lanes, exact unit UID and device mapping. No mutation during this audit.
2. Restore a single route owner and location/stop-fault record, using actual physical sensor evidence and persisted source from 1.90.2; account for staged/background, runout, interrupted load/retract, reconnect, head occupancy, direct/manual feed and possible material in tube. If the route cannot be proven, keep it unknown and request physical confirmation; do not claim every bay is idle. Do not use a global stop or guess source by head number.
3. Use existing driver serialization and sequence allocator. Expose one private native unload job only after the route is proven parked or an explicitly source-bound head unload completes and head sensor is clear. Recheck ownership at the start after yields. Native command mode 4, length_um=0, speed from existing validated bounds; no fixed rollback loop. Treat missing start ACK as potentially moving; STOP selected lane and verify, never blindly start again. Track a task by unit UID/connection/slot/seq; handle lost terminal events via native feed_last_query, reject stale events, verify independent post-event inlet/encoder/FG/mode samples. Only event unloaded plus empty and stopped completes. Do not clear real MCU errors or release unresolved route state.
4. Wire screen compatibility only when the route/stop protections are implemented and tested: ACE_SLOT_EXIT, slot_action, per-bay position fields based on actual evidence, progress, cancel targeted by task epoch/seq. Do not expose RFID or calibration changes in this patch. Keep existing 1902.7 save code and stored labels. Native screen source route patch is not compiled into the released binary; don't claim head-only unload is safe without actual verified source binding.
5. Test no motion on wrong unit/slot or unknown source; lost ACK, lost terminal event, stopped sensors, failed stop, cancel, route changed, busy neighbor, mixed 233b/233c. Test a single parked bay under supervision before any loaded-head route. Verify selected physical spool and all other lanes. Only after review and hardware evidence create uniquely versioned 1902.8 test package.

References: https://github.com/tobecwb/ace2k/blob/v0.16.0/docs/commands.md#acek_unload and https://github.com/tobecwb/ace2k/blob/v0.16.0/docs/protocol.md ; https://github.com/decay71/multiACE/blob/91ba652f6896f22cecb4cf779ee5db06ee8e7840/multiace/klipper/extras/ace_protocol_ace2k.py .
