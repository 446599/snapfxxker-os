# multiACE+ 1.90.2 candidate / 233 migration

**ACE2K auto-detection test branch:** see [ACE2K-AUTO-DISCOVERY.md](ACE2K-AUTO-DISCOVERY.md). The archive already in `artifacts/` predates this fix; rebuild the staged candidate before testing it. The production overlay pins have not been changed.

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
MULTIACE_STAGED=/tmp/multiace-233-stage python -m unittest discover -s bundles/multiace-190 -v
cd /tmp/multiace-233-stage
python -m pip install -r multiace/web/backend/requirements.txt pytest
python -m pytest tests -q
python -m unittest discover -s multiace/managed/tests -v
python multiace/managed/build_package.py --output /tmp/multiace-190-candidate.tar.gz
```

Local regression (2026-11-25): 54 tests collected, 53 passed, 1 skipped
(full staged-checkout AST comparison). Eight native motion methods were
extracted from the pinned upstream source with the candidate patches applied;
the locally executed fixture has identical Git blob content. All replacement
anchors were checked against upstream. The added tests cover deadline ordering,
late DONE events, bounded STOP retry, RFID stop, auto-buffer ownership, invalid
slot indexes, speed deadline bounds, and neighboring-lane isolation. A single
lost STOP acknowledgement is retried; a real unresolved STOP uses the existing
LinkDead/reconnect path. No new alarm category was introduced. Full
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

## 部署到 233 设备（实测要点，2026-11-26）

包内 `klipper/extras/` 的 20 个 .py 必须**整目录**同步到
`/home/lava/klipper/klippy/extras/`，不是只拷 `ace*.py`：

- 包内给的是 `filament_feed_ace.py`，必须**改名**安装为
  `filament_feed.py`（klipper 只按 `[filament_feed ...]` 段名加载）。
  原厂同名文件只有 102 KB、零处 ACE 逻辑；装错时送料状态机在
  `load_prepare` 直接报 `no_filament`，"退出耗材"只做一次短回退。
  用本目录的 `install-to-233.sh` 一次做对。
- 拷完删 `__pycache__/ace*.pyc`、`__pycache__/filament_feed*.pyc`，
  再 `/etc/init.d/S60klipper restart`。
- 校准、料号、`ace_vars.cfg` 都在 `/home/lava/printer_data/config/extended/multiace/`，
  覆盖代码前备份该目录。

根分区只有 16384 个 inode，接近满时 `save_variables` 会报
`Unable to save variable`（Errno 28）。装任何依赖前先 `df -i /`。
