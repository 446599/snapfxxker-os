#!/usr/bin/env bash
# Repack the verified complete 233 base with the field-tested 1.90.2 provider.
# Execute on a Linux build host as root (squashfs ownership preservation).
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'run as root on the build host' >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
BASE=$(realpath "${1:?usage: build_1911.sh base-upgrade.bin output-directory screen.tar.gz screen.json}")
mkdir -p "${2:?output directory required}"
OUT=$(realpath "$2")
VERSION=2.1.2-233-1902.11
SCREEN=$(realpath "${3:?fresh ARM64 screen archive required}")
SCREEN_REPORT=$(realpath "${4:?screen provenance JSON required}")
COMMIT=$(git -C "$REPO" rev-parse HEAD)
BASE_SHA=0d7b4039696a0a3b98df093b58a333fb0de5b5372ab665bf4d6a9d3165b942bf
FIELD="$REPO/bundles/multiace-190/artifacts/multiace-1.90.2-233.20261126.tar.gz"
COMMON="$REPO/overlays/mods/multiace/70-app-multiace/root/usr/local/share/multiace/common.sh"
FILE="U1_233_${VERSION}_upgrade.bin"
[[ ! -e "$OUT/$FILE" ]] || { echo 'output already exists; use a new directory' >&2; exit 1; }
printf '%s  %s\n' "$BASE_SHA" "$BASE" | sha256sum -c -
for tool in make gcc unsquashfs mksquashfs qemu-aarch64-static python3; do command -v "$tool" >/dev/null; done
WORK=$(mktemp -d "${TMPDIR:-/tmp}/u1-system-1902.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
make -C "$REPO/tools" -j2
"$REPO/scripts/helpers/unpack_firmware.sh" "$BASE" "$WORK/input" | tee "$WORK/base-unpack.log"
BASE_ROOTFS_BYTES=$(stat -c '%s' "$WORK/input/rk-unpacked/rootfs.img")
(cd "$WORK/input/rk-unpacked" && find . -type f ! -path ./rootfs.img -print0 | sort -z | xargs -0 -r sha256sum) > "$WORK/base-partitions.sha256"
ROOT="$WORK/rootfs"
unsquashfs -d "$ROOT" "$WORK/input/rk-unpacked/rootfs.img"
[[ $(stat -c '%u:%g' "$ROOT/etc/passwd") == 0:0 ]]
[[ $(stat -c '%u:%g' "$ROOT/home/lava/bin/hwver.sh") == 1000:1000 ]]
python3 "$HERE/compose_1911.py" "$ROOT" "$FIELD" "$COMMON" "$WORK" "$SCREEN" "$SCREEN_REPORT" "$COMMIT"
for path in /usr/local/share/multiace/common.sh /etc/hooks/klipper.d/10-multiace.sh /etc/init.d/S98multiace-web; do
    sh -n "$ROOT$path"
done
bash -n "$ROOT/usr/local/share/extended-pkg/multiace"
# Validate the unchanged vendor ABI on the actual ARM64 rootfs; never pip
# install dependencies on the printer or bake host executables into the image.
SMOKE="$ROOT/233-1902-buildcheck"
mkdir "$SMOKE"
cp "$(command -v qemu-aarch64-static)" "$SMOKE/qemu"
cp -a "$WORK/field-provider/." "$SMOKE/provider"
PROVIDER_PATH=$(find "$SMOKE/provider" -mindepth 1 -maxdepth 1 -type d -printf '%f\n')
chroot "$ROOT" /233-1902-buildcheck/qemu /usr/bin/python3 -B -c "import sys;sys.path.insert(0,'/233-1902-buildcheck/provider/$PROVIDER_PATH/web/backend/vendor');import fastapi,uvicorn,httpx,pydantic,websockets,multipart,serial;print('ARM64 offline dependencies imported')"
# Import the actual new driver/host modules using the printer's Python.
chroot "$ROOT" /233-1902-buildcheck/qemu /usr/bin/python3 -B -c "import sys;sys.path.insert(0,'/233-1902-buildcheck/provider/$PROVIDER_PATH/klipper');from extras import ace_protocol_ace2k,native_exit_worker,host_native_exit,material_batch_runtime;print('ARM64 native material runtime imported')"
# Check the actual native companion imports without running its server.
chroot "$ROOT" /233-1902-buildcheck/qemu /usr/bin/python3 -B -c "import sys;sys.path.insert(0,'/usr/local/lib/u1-device-service');import device_service;print('ARM64 screen companion imports')"
rm -rf "$SMOKE"
# Preserve all other partitions, ownership and file types. The RKAF header
# reports A/B partition sizes in 512-byte sectors, not old image bytes.
mksquashfs "$ROOT" "$WORK/rootfs-new.img" -comp zstd -noappend -processors 2
python3 "$HERE/rootfs_capacity.py" "$WORK/base-unpack.log" "$BASE_ROOTFS_BYTES" "$WORK/rootfs-new.img"
mv "$WORK/rootfs-new.img" "$WORK/input/rk-unpacked/rootfs.img"
printf '%s\n' "$VERSION" > "$WORK/input/UPFILE_VERSION"
"$REPO/scripts/helpers/pack_firmware.sh" "$WORK/input" "$WORK/$FILE"
# Read back the actual final file, not merely the staging folder.
"$REPO/scripts/helpers/unpack_firmware.sh" "$WORK/$FILE" "$WORK/readback" | tee "$WORK/readback-unpack.log"
python3 "$HERE/rootfs_capacity.py" "$WORK/base-unpack.log" "$BASE_ROOTFS_BYTES" "$WORK/readback/rk-unpacked/rootfs.img" --readback-log "$WORK/readback-unpack.log" --report "$WORK/rootfs-capacity.json"
[[ $(cat "$WORK/readback/UPFILE_VERSION") == "$VERSION" ]]
diff -r "$WORK/input/rk-unpacked" "$WORK/readback/rk-unpacked"
(cd "$WORK/readback/rk-unpacked" && sha256sum -c "$WORK/base-partitions.sha256")
cmp "$WORK/input/rk-loader.img" "$WORK/readback/rk-loader.img"
REL=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["provider_archive"])' "$WORK/composition.json")
EXPECTED=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["provider_sha256"])' "$WORK/composition.json")
READBACK="$WORK/readback/rk-unpacked/rootfs.img"
ACTUAL=$(unsquashfs -cat "$READBACK" "$REL" | sha256sum | awk '{print $1}')
[[ "$ACTUAL" == "$EXPECTED" ]]
unsquashfs -cat "$READBACK" usr/local/share/extended-pkg/multiace | grep -F "PKG_SHA256=\"$EXPECTED\""
for path in usr/local/share/multiace/common.sh etc/hooks/klipper.d/10-multiace.sh etc/init.d/S98multiace-web; do
    unsquashfs -cat "$READBACK" "$path" | cmp - "$ROOT/$path"
done
while IFS= read -r path; do
    unsquashfs -cat "$READBACK" "$path" | cmp - "$ROOT/$path"
done < <(python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["runtime_files"]))' "$WORK/composition.json")
SCREEN_PKG=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["screen_archive"])' "$WORK/composition.json")
SCREEN_SHA=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["screen_sha256"])' "$WORK/composition.json")
[[ $(unsquashfs -cat "$READBACK" "$SCREEN_PKG" | sha256sum | cut -d' ' -f1) == "$SCREEN_SHA" ]]
unsquashfs -cat "$READBACK" usr/local/share/extended-pkg/helixscreen | grep -F "PKG_SHA256=\"$SCREEN_SHA\""
cp "$SCREEN_REPORT" "$OUT/screen-provenance.json"
cp "$WORK/composition.json" "$OUT/composition.json"
cp "$WORK/rootfs-capacity.json" "$OUT/rootfs-capacity.json"
mv "$WORK/$FILE" "$OUT/$FILE"
(cd "$OUT" && sha256sum "$FILE" composition.json rootfs-capacity.json screen-provenance.json > SHA256SUMS.txt)
echo "Candidate built and readback checked: $OUT/$FILE (not hardware-validated)"
