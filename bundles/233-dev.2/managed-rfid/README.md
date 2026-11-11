# Managed multiACE RFID integration

The input is the repository-contained `base/multiace-managed-1.27b-233.20261109.tar.gz` (SHA-256 `a18a762e4cb1061fe778bb919311a076bbfdfc445e2bb73bf72b5ae26544fc3f`). The original inventory is retained as `base/manifest.json`. Four patches update the automatic RFID path, its reader, the guarded single-cycle command, and the ACE-only eject. Their final source hashes, the hash of every package member, and the archive hash are in `../manifest.json`.

Rebuild the archive and a manifest without touching either overlay:

```sh
python3 bundles/233-dev.2/managed-rfid/build.py --output /tmp/multiace-single.tar.gz --manifest /tmp/multiace-single-manifest.json
python3 -m unittest discover -s tests/managed-rfid -q
```

The rebuild requires Python 3 and the standard `patch` utility. It verifies the base and the patched source hashes, retains the original member modes, sets deterministic archive ownership and timestamps, and contains no runtime configuration, credentials, logs, or printer state. Both overlay archives must match the build byte-for-byte. The tests extract the actual packaged sources, so no file outside this repository is required.

The native RFID fast path and no-extra-host-motion observation were exercised on the device. The observation did not contain an exclusive fast-path trace or a per-command count, so it does not by itself prove which host call ingested the identity or that only one command was issued. Other-tag and shared-reader behavior remains subject to physical validation.

ACE-only eject now uses the locally verified 80 mm/s speed and repeats bounded motor commands until the inlet confirms empty; it does not poll or estimate encoder distance. It keeps the independent overall timeout and STOP/connection/head guards. No device test of this new eject implementation has been performed; 80 mm/s is the highest speed verified on this machine, not a demonstrated hardware maximum.
