# Cold boot: finish the existing 1.90.2 migration

This test branch adds the missing 1.90.2 PAXX runtime module mapping, based on
the upstream 1.90.2 managed manifest and optional bg_concurrency_probe.py.
The old 1.27b pin and rollback overlay are unchanged. No printer deployment,
production pin change, or MCU firmware modification is included.

Six isolated shell checks passed for the new mapping functions (mocked bind
and unmount, not actual mounts): all 24 required destinations, three stock
aliases, reject a missing required module before binding, unmount all 25
candidate paths, propagate bind error, and mount optional diagnostic when
present. Full boot integration and actual package/host compatibility remain
untested. Compare the mapping against the real candidate archive before
activating it. The current old provider package definition requires 1.27b
files and dependencies and must not be reused for a 1.90.2 archive.

## Diagnose the cold boot before changing the discovery engine

The repaired scan in source-patches.json already includes configured 55d5
in auto_shared. With the correct loaded module, both drivers enabled and
auto-detect enabled, an unverified adapter cannot become V2 merely because
the first probe timed out. No shared-probe log plus protocol=v2 is therefore
not sufficient evidence of a boot-timing bug. First verify the effective
code/config/path and which startup service selected them.

On the authorized local machine, after restoring SSH via the normal device
settings, collect the boot log from the latest actual boot, compare
sha256sum of ace.py and the three stock aliases against the verified archive,
inspect /proc/mounts and /oem/apps/multiace/latest (if using PAXX), and query
the actual configfile status for enable_ace_v2, enable_ace2k, ace2k_auto_detect,
v2_extra_usb_ids, and ace2k_ports. Confirm the 1.90.2 build banner and source.
Do not include credentials in logs or Git commits.

If PAXX is the active integration, use one persistent provider, update its
candidate pin/definition on a test overlay only, preserve the previous
provider and configuration, validate the candidate package's real offline
dependencies, and use the same provider root for the 25 runtime paths and web
files. The PAXX extended-pkg manager uses /oem/apps/multiace/latest and
preserves prior versions on upgrade. Do not use its clean/download paths to
replace a working installation. Preserve the custom MCU firmware assets; do
not flash them. Use the updated common.sh mapping only after the actual
package contents and activation/rollback path are verified.

If the machine is running a standalone installation instead, use its
existing persistent startup mechanism; do not introduce PAXX just to fix
a standalone installation. A temporary /etc edit is not a cold-boot fix.
Pick one persistent web owner. With a verified baked PAXX init service that
starts the matching web tree, set the engine's enable_web to false to stop
its redundant init-start request; do not disable it on a standalone setup
without another proven web startup path. Verify the owner, cwd, version and
one listening process on 7126 after actual power cycles, not merely HTTP 200.

Only after the same code and config survive cold boot, reproduce any remaining
startup timing problem. An unverified 55d5 port must remain unclaimed and
retry within the existing bounded discovery mechanism. Do not increase
timeouts or repeatedly restart Klipper to conceal the issue.

Acceptance: two full cold boots without USB replug or manual commands,
4/4 ACE2K connected with unchanged UID/device indices; correct actual module
hashes, one 1.90.2 web listener, and a successful existing load/unload cycle.
No new functions, no motion/RFID algorithm changes, and no firmware flash.
