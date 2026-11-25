# ACE2K native identity and queued-cancel repair (2026-11-25)

Target: `codex/ace4-slot-exit-discover-frame` at `21fc962`. The recorded
failure is ACE4 SLOT=1 with flow.15.116: `discover_device 响应帧无效`.

## Findings and changes
- ACE2K uses Klipper message frames, not V2 protobuf frames. Its discover
  adapter formerly emitted a success envelope even if the cached UID was
  unavailable, substituting three zeros. The host accepted any three ints.
  The native adapter now queries the actual UID on every discovery and
  marks only a valid 12-byte nonzero response as native-verified. The host
  verifies the protocol, response and UID before publishing identity and
  rejects a cached all-zero identity. Waiting for identity also checks the
  original transport before accepting a response.
- The checked-in historical SlotActions._request rejects native envelopes
  because it demands V2 metadata. The staging patch changes only its
  discover_device validation for ACE2K; it preserves the V2 path and never
  fabricates V2 flags. The .116 runtime helper itself is absent from this
  branch, so the patch refuses unfamiliar source.
- A busy-refused ACE2K move may enter `waiting`. Previously a STOP marked
  it stopping but _tick could start it later. The native adapter now
  removes such a pending job and will not start a cancelled retry.

## Staging and verification
The committed provider sources are not the full runtime installed by the
current firmware. The two overlay installers still pin flow.15.113.
Do not install a mixed source tree over a working printer.

Obtain the full .116 managed component from the machine or its build
environment and extract it to a staging directory. From the repository root:

    python3 bundles/ace2k-discovery-hotfix/test_identity.py
    python3 bundles/ace2k-discovery-hotfix/patch_flow116.py /path/to/extracted/.116
    python3 bundles/ace2k-discovery-hotfix/patch_flow116.py /path/to/extracted/.116 --apply

The first command exercises the checked-in provider sources and a slot
guard fixture. The second is check-only and validates every anchor and
Python syntax; --apply is for the staging copy, not the live system.
Rebuild a complete new component with its own version/hash only after
checking all remaining native motion paths. No new .117 binary is claimed.

## Remaining release blockers
- Native `get_status` has lane_mode and presence, but not V2 status-wire
  flags. The checked-in native method allowlist also lacks get_key_state
  and get_feed_info. Older V2-only slot standstill, gate and encoder checks
  may therefore fail immediately after discovery. Inspect the actual .116
  ace_slot_actions, ace_v2_standstill and path helpers. Implement native
  equivalents from fresh sensor/feed/counter reports; do not fake V2 proof.
- Native stop_feed_filament's success currently means a STOP request was
  handled, not that the motor is physically stopped. Stop proof and retry
  behavior must be tested with fresh lane mode, inlet and encoder readings;
  protect the other three slots. update_feeding_speed also returns success
  without changing speed in the checked-in native adapter; audit callers.
- Historical path calibration refresh_identity still has a V2-only guard.
  Audit the .116 equivalent rather than replacing its older implementation.
- The .113 web updater persists protocol routing before post-flash
  reconnection/identity verification. Audit recovery when flashing succeeds
  but the new protocol cannot reconnect, including a stable USB port
  identity and resuming a held port without changing other ACEs.
- Do not auto-enable unattended 220 V drying. Validate both fan feedback
  and heater shutdown on actual hardware separately.

## Physical acceptance
With the printer idle, target ACE4 slot 2 only (ACE=3 SLOT=1). Confirm the
same real UID, valid fresh sensor and motor state before motion. Confirm
motor stop and inlet empty before clearing the route record. Verify slot 4
and head 4 unchanged, then separately test slot 3 (ACE=3 SLOT=2). Repeat
with cancellation, USB disconnect/reconnect and mixed V2/ACE2K. No real
device or full .116 runtime has been tested by this patch.
