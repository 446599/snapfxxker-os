# U1 233 / 1.90.2 full system candidate

This is a build-only integration branch, not a new product feature branch.
The first candidate is `2.1.2-233-1902.1`.

## Pinned inputs
- Full base: released `2.1.2-233-paxx8d8af0d.2`, SHA256
  `0d7b4039696a0a3b98df093b58a333fb0de5b5372ab665bf4d6a9d3165b942bf`.
  This retains the previous released PAXX system and HelixScreen binary; the
  newer separately vendored screen sources are not rebuilt in this round.
- Field-tested MultiACE archive from a967ec0b: `1.90.2-233.20261126`,
  SHA256 `d16535002703f19f43282c990385c1810c48d63a1d0ebff0d25335d924c9faea`.
- The coldboot branch's managed runtime mapping (16fc39f).
- No ACE MCU image is flashed or changed. Preserve existing optional MCU images.

The build repacks the exact base rootfs with the new managed provider.
Only offline Python dependencies and optional firmware image bytes may
be carried forward from the old provider, not the old Python engine or
web backend. The original field-tested ace.py is not changed. The candidate
owns its web service through the baked PAXX S98 service, and sets the
managed web paths in ace.cfg after the upstream config merge.
Back up the old ace.cfg before the first managed migration. Existing slot
state and configuration remain persistent; verify them on hardware.
Only a selected PAXX-managed MultiACE integration upgrades its pin at boot.
If the machine is still a standalone installation, remove conflicts using
the existing supported migration procedure and select managed MultiACE once;
do not mix both startup mechanisms. A persisted old debug overlay may hide
new baked files, so verify the actual running paths after installation.

## Build
GitHub Actions: Build U1 233 1.90.2 system candidate.
On a Linux build host with squashfs tools, GCC, OpenSSL development headers
and qemu-aarch64-static installed:
```sh
sudo bash bundles/full-system-1902/build.sh /path/to/exact-base.bin output
```
The build must pass all gates before exposing the final upgrade.bin:
input hashes, complete runtime mappings, ARM64 offline library import,
A/B rootfs partition capacity (512-byte sectors), unpacked final image and non-root partition
readback, embedded provider checksum, and the actual startup files.
The 3 local synthetic composition tests and 10 capacity tests passed; they are not an actual firmware build or boot test. The first CI run 45746475707 passed software composition and ARM64 dependency checks but stopped at the previous old-image-size gate. The corrected gate derives both slot capacities from the verified base RKAF header (614400 sectors × 512 = 300 MiB each), checks archive padding, and verifies unchanged slot geometry on readback. The output includes rootfs-capacity.json and its SHA256. No new candidate binary exists until CI succeeds.

## Local test agent contract
DeepSeek installs only the exact candidate artifact and checks SHA256 first.
Back up persistent configuration, save_variables and prior firmware.
Do not modify production code or perform ad hoc package upgrades during tests;
only reversible device configuration needed for the documented activation.
No MCU flash, no new functions. Record real boot version, provider version,
running module and web paths/hashes, 4/4 ACE2K connections, stable UID/index
mapping, screen/web functionality, existing load/unload, and two cold boots.
If failure occurs, collect evidence and return it with the source commit,
artifact SHA256, reproduction steps, actual and expected behavior, logs and
whether rollback was required. The build owner makes the next code change
and supplies a new uniquely versioned full package.

Not yet verified: full system cold boot, screen API parity, RFID,
custom complete slot-eject and custom two-image MCU selector.
The candidate is not a production release; never infer that all features
pass from the assembly tests or from an HTTP 200.

## 1902.2 acceptance fixes

The 1902.1 image passed two OS reboots with all four ACE2K units. Field testing then exposed OEM storage exhaustion, a pre-motion routing record left in RAM when SAVE_VARIABLE fails, duplicate web startup, and a missing/unhealthy screen companion listener. 1902.2 moves only recognized versioned MultiACE application directories to the separately mounted /userdata/233-apps/multiace, checks every copied entry before freeing the OEM copy and leaves compatibility symlinks; no printer_data, logs, settings, calibration or unrelated package is removed. The next provider is installed on userdata; existing config is preserved. The init scripts serialize service startup and check actual listener readiness. The native companion files are retained from the released base when complete; the recorded release sources are included if missing. The pre-load save error rolls back only the provisional RAM mapping and variable entries, then re-raises the error before FEED_AUTO. Do not consider RFID, wake/touch behavior, translation, thumbnails or drying duration fixed in this candidate. Physical boot and writable-state checks are required.

## 1902.3 migration compatibility fix

On the actual 1902.2 printer, SSH and 7136 recovered, but S47 stopped on a historical versioned symlink to /userdata/multiace-provider-runtime before processing six actual provider directories. Preserve all top-level historical aliases without following them. Ignore unrecognized/incomplete inactive provider directories (do not delete them), while real copy/mount/checksum failures remain errors. Migrate only verified physical provider trees; retain existing rollback aliases. If migration fails, do not run the package installer into an unverified mount or exit the parent Klipper init script. Restore stock module paths and remove only the managed include link; the configured integration remains enabled for a subsequent successful boot. New regression fixtures exercise 50 mixed legacy symlinks with six real directories, a legacy latest, an incomplete provider, and parent init continuation. No motion, RFID, screen, or MCU changes beyond 1902.2. Actual storage recovery still requires hardware verification.

## 1902.4 screen compatibility candidate

The vendored 233 screen bay editor calls POST /api/slot-override/sync but 1.90.2 only registers POST /api/slot-override. Restore this exact path as an adapter to the existing atomic slot override store, retain omitted metadata and report actual disk failures (no successful response if saving fails). The explicit load's tool activation previously could consult the old head_source before FEED_AUTO; skip old-route activation while a load owns the head, recheck the requested active unit after tool selection, and reject deferred feed assist whose captured head/source no longer matches. No new motion strategy or changes to RFID, LEDs, MCU, provider storage or the screen binary. These are source-confirmed compatibility/routing defects; the exact wrong-slot and other-lane blink symptoms still require real command/driver logs and physical reproduction. Keep the verified 1902.3 package for rollback. Tests exercise the exact pinned field archive rather than the latest upstream source. The report includes both engine and web hashes.

## 1902.5 filament routing candidate

The embedded provider is 1.90.2-233.20261126-paxx.5. Web bay cards match the
head's recorded ACE and slot. Bay unloads, queued changes and snapshot restore
carry explicit source/destination addresses. Klipper checks a bay unload again
after waiting for background work. A disconnected retract source or exhausted
command-rejection retries now fail the operation; an unverified unload retains
its source. Cleanup of a single head scopes feed assist to its route. Global
stop and explicit all-head operations remain available. If a previous retract on
the same head was interrupted on a different bay, an explicit load preserves
the original recovery record and returns before moving either spool.

This firmware keeps the 20261124.4 native screen from the pinned release.
Its existing bay-card unload can submit a head-only command and unload a
different ACE supplying that head. Use the MultiACE web bay card for a
slot-specific unload on this build. The native source correction is recorded
in screen_routes.patch against the 02-屏幕层/233-screen tree at
4edbd50feb36e47d072b5cd4e516b9792b8b4ea9. That source snapshot references
a compact print tune overlay whose header and implementation are absent.
A complete source rebuild is needed before claiming the native correction
on a printer. The colors/RFID display issue remains a separate acceptance item.

For hardware acceptance, record each requested, reported and physically
moving ACE/slot. With ACE 1 slot 4 feeding head 4, check that the web card
for ACE 4 slot 4 offers no unload and that unloading the actual source moves
only ACE 1 slot 4. Check the old and new source on a same-head spool change
and preserve the command / device reply when a retract ends early.
