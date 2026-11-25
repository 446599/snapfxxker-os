# multiACE+ 1.90.2 candidate / 233 migration

Pinned upstream: decay71/multiACE V1.90.2, 91ba652f6896f22cecb4cf779ee5db06ee8e7840.
Use the upstream driver registry (ACE V1, ACE V2, ACE2K, CFS) rather
than porting the old monolithic 1.27b engine. This is a **staged candidate**:
do not install over flow.15.116 or change the existing overlay pins.

- `lane_contract.py` and `lane_io.py` provide one read-only lane interface:
  identity, inlet, motor state, encoder and motor counter, independent native
  report times and connection continuity. It is not yet wired to the old
  custom SlotActions engine. V1 and CFS keep their existing upstream paths.
- `source-patches.json` adds real ACE2K UID queries, native lane evidence
  (sensor age, signed encoder micrometres, actual FG pulses), V2 evidence
  derived from validated frames, STOP ahead of retry ticks, cancellation of
  waiting jobs and speed updates for finite native jobs. No new alarm types,
  no fake V2 flags on ACE2K, and no printer-wide STOP.
- `prepare.py` checks the exact upstream Git commit and original blob
  hashes, applies the patches to a new staging source tree and syntax-checks
  the changed modules. It never changes the installed firmware.

Run:
```sh
python -m unittest discover -s bundles/multiace-190 -v
git clone https://github.com/decay71/multiACE.git /tmp/multiace-upstream
git -C /tmp/multiace-upstream checkout --detach 91ba652f6896f22cecb4cf779ee5db06ee8e7840
python bundles/multiace-190/prepare.py /tmp/multiace-upstream /tmp/multiace-233-stage
cd /tmp/multiace-233-stage
python -m pip install -r multiace/web/backend/requirements.txt pytest
python -m pytest tests -q
python -m unittest discover -s multiace/managed/tests -v
python multiace/managed/build_package.py --output /tmp/multiace-190-candidate.tar.gz
```

Local tests: 24 isolated evidence/dispatch unit tests passed (Python unittest);
the source anchors were checked against the upstream GitHub source. Full
upstream tests, managed build, old 233 UI, custom slot-exit, config/data
migration and physical tests are not yet verified. The running .116 bundle
is not in this branch; do not copy old .116 ace.py over 1.90.2. The official
1.90.2 tag has no managed release asset; 1.90.1 has one.

The upstream package is not a drop-in replacement: its device registry,
config, web API, feed and swap engine changed. Preserve 233's previous
OpenACE/ACE2K 220V firmware selection, screen, location records and route
safety in the migration. Keep old install/rollback package available;
publish a new version/checksum only after the entire managed build and
actual ACE4 slots 2/3 isolation tests pass.
