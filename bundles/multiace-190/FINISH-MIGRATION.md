# 1.90.2 — finish migration, no new features

The deployed 1.90.2 field-tested engine and archived package remain unchanged.
This follow-up fixes the manual installer: map all packaged Python modules, including
filament_feed_ace.py -> filament_feed.py, filament_switch_sensor_ace.py ->
filament_switch_sensor.py, and extruder_ace.py -> extruder.py. The existing 233
PAXX common.sh establishes these three aliases. Do not change the MCU firmware.

Local sandbox validation of installer blob 768f9c5:
- sh -n passed.
- 7 checks passed with a synthetic package, mocked service and Moonraker:
  read-only --check / all aliases, missing web payload, invalid SHA256,
  valid SHA256, idle install, failed start rollback, paused-print refusal.
- No physical printer or actual archived component was tested in this run.

## Local agent: finish existing functionality now

1. Keep the field-tested machine working. Fetch this installer without replacing
   local work. Run it with --check against the a967ec0b archive. Do not reinstall
   if runtime files already match. Verify actual module paths / hashes including
   all three aliases. If files are PAXX bind-mounted, use the existing provider
   activation mechanism rather than copying over mounts.
2. Check the real 7126 listening process's cwd, command line and environment.
   Upstream 1.90.2 ace.py intentionally leaves an existing standalone web process
   running; restarting Klipper alone does not update the web backend.
   Stage the matching packaged backend AND frontend on /userdata, reuse verified
   existing Python dependencies (no root pip install), and use the existing
   web service to activate that directory. Keep previous web files and bundled
   custom MCU images; no MCU flash. Verify actual listening process and web
   version, not just HTTP 200. Keep the existing screen layout. Check live
   registered G-code against the UI's actual calls; correct only incompatible
   existing call sites and parameters. Do not add placeholder commands or
   return fake success. Do not map a full exit to an ordinary unload.
3. Test the original load/unload buttons from the web and screen, verify
   the physical sensor outcomes, and repeat on another loaded lane. Confirm
   4/4 connections and persistence of device and material records after restart.
   Only test a known-present RFID tag in its reachable position; record failure
   instead of adding a new RFID implementation. Verify existing temperature,
   dryer and stop operations when safe. Missing custom slot-exit and custom
   firmware-selection parity should be reported as gaps, not implemented now.
4. Compare the exact Linux baseline and patched failed-test identities,
   not just counts. For the 3 reported additional failures, inspect contract,
   capabilities and golden traces; only update expected traces when the new
   behavior is proven correct. Run existing tests on the local workstation,
   not by installing more dependencies on the printer.
5. Update the 233 provider pins and activation scripts only once the actual
   candidate and rollback are verified; ensure restart does not re-load
   1.27b. Do not write the official production pin or main branch yet.

Done means the existing supported 1.90.2 controls work via the actual UI,
not just console commands, and software and runtime versions match. No new
features, no architecture rewrite, no MCU flash. Report remaining unsupported
features and untested cases. The manual installer only stages the web tree;
it does not claim to activate the correct web service or fix RFID.
