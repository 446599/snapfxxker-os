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
conservative rootfs size limit, unpacked final image and non-root partition
readback, embedded provider checksum, and the actual startup files.
The three local synthetic composition tests passed; they are not an actual
firmware build or boot test. No new candidate binary exists until CI succeeds.

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
