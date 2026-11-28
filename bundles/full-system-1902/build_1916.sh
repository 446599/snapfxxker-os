#!/usr/bin/env bash
# Input is the pinned Actions ZIP selected by the build profile below.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'run as root to preserve squashfs ownership' >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
BASE_ZIP=$(realpath "${1:?usage: build_1916.sh base.zip output-directory [1917]}")
OUT=$(realpath -m "${2:?output directory required}")
PROFILE=${3:-1916}
case "$PROFILE" in
    1916)
        BASE_VERSION=2.1.2-233-1902.15
        BASE_COMMIT=e8968dfb9e612a43d19d2127b688022095946428
        BASE_SHA=2ab4caeee2665f8083b638fd7a375c9dfbd25e0ba779794857329eda57a7834e
        VERSION=2.1.2-233-1902.16
        ;;
    1917)
        BASE_VERSION=2.1.2-233-1902.16
        BASE_COMMIT=aa8a76573b11f910d3a37de0e9bba2d57fc4c987
        BASE_SHA=f01eb5d4a088bcda7f368ffe4ede1edd1b04c49afc6d7d2b68535b6c06b674e0
        VERSION=2.1.2-233-1902.17
        ;;
    *) echo 'unrecognized candidate profile' >&2; exit 1 ;;
esac
FILE="U1_233_${VERSION}_upgrade.bin"
[[ ! -e "$OUT/$FILE" ]] || { echo 'output image already exists' >&2; exit 1; }
WORK=$(mktemp -d "${TMPDIR:-/tmp}/u1-unified-web.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
printf '%s  %s\n' "$BASE_SHA" "$BASE_ZIP" | sha256sum -c -
unzip -q "$BASE_ZIP" -d "$WORK/base"
(cd "$WORK/base" && sha256sum -c SHA256SUMS.txt)
[[ $(cat "$WORK/base/SOURCE_COMMIT.txt") == "$BASE_COMMIT" ]]
make -C "$REPO/tools" -j2
INPUT="$WORK/input"
"$REPO/scripts/helpers/unpack_firmware.sh" "$WORK/base/U1_233_${BASE_VERSION}_upgrade.bin" "$INPUT" | tee "$WORK/base-unpack.log"
[[ $(cat "$INPUT/UPFILE_VERSION") == "$BASE_VERSION" ]]
BASE_SIZE=$(stat -c %s "$INPUT/rk-unpacked/rootfs.img")
(cd "$INPUT" && find . -maxdepth 1 -type f ! -name rk-rom.img ! -name update.img ! -name UPFILE_VERSION -print0 | sort -z | xargs -0 -r sha256sum) > "$WORK/base-outer.sha256"
ROOT="$WORK/rootfs"
unsquashfs -d "$ROOT" "$INPUT/rk-unpacked/rootfs.img"
[[ $(stat -c '%u:%g' "$ROOT/etc/passwd") == 0:0 ]]
[[ $(stat -c '%u:%g' "$ROOT/home/lava/bin/hwver.sh") == 1000:1000 ]]
COMMIT=$(git -C "$REPO" rev-parse HEAD)
python3 "$HERE/compose_$PROFILE.py" "$ROOT" "$WORK/base/composition.json" "$WORK/composition.json" "$COMMIT"
sh -n "$ROOT/etc/init.d/S58u1-device-service"
bash -n "$ROOT/usr/local/bin/extended-pkg"
mksquashfs "$ROOT" "$WORK/rootfs-new.img" -comp zstd -noappend -processors 2
python3 "$HERE/rootfs_capacity.py" "$WORK/base-unpack.log" "$BASE_SIZE" "$WORK/rootfs-new.img"
mv "$WORK/rootfs-new.img" "$INPUT/rk-unpacked/rootfs.img"
printf '%s\n' "$VERSION" > "$INPUT/UPFILE_VERSION"
"$REPO/scripts/helpers/pack_firmware.sh" "$INPUT" "$WORK/$FILE"
READBACK="$WORK/readback"
"$REPO/scripts/helpers/unpack_firmware.sh" "$WORK/$FILE" "$READBACK" | tee "$WORK/readback.log"
[[ $(cat "$READBACK/UPFILE_VERSION") == "$VERSION" ]]
diff -r "$INPUT/rk-unpacked" "$READBACK/rk-unpacked"
(cd "$READBACK" && sha256sum -c "$WORK/base-outer.sha256")
IMAGE="$READBACK/rk-unpacked/rootfs.img"
python3 "$HERE/rootfs_capacity.py" "$WORK/base-unpack.log" "$BASE_SIZE" "$IMAGE" --readback-log "$WORK/readback.log" --report "$WORK/rootfs-capacity.json"
# Verify all runtime files and both retained archives in the final packed image.
python3 - "$WORK/composition.json" > "$WORK/rootfs.sha256" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    report = json.load(source)
files = dict(report["runtime_file_sha256"])
for key in ("screen", "provider"):
    files[report[key + "_archive"]] = report[key + "_sha256"]
for path, digest in sorted(files.items()):
    print(digest, path)
PY
while read -r expected file; do
    actual=$(unsquashfs -cat "$IMAGE" "$file" | sha256sum | cut -d' ' -f1)
    [[ "$actual" == "$expected" ]] || { echo "readback mismatch: $file" >&2; exit 1; }
done < "$WORK/rootfs.sha256"
mkdir -p "$OUT"
cp "$WORK/composition.json" "$WORK/rootfs-capacity.json" "$WORK/base/screen-provenance.json" "$OUT/"
mv "$WORK/$FILE" "$OUT/$FILE"
printf '%s\n' "$COMMIT" > "$OUT/SOURCE_COMMIT.txt"
(cd "$OUT" && sha256sum "$FILE" composition.json rootfs-capacity.json screen-provenance.json SOURCE_COMMIT.txt > SHA256SUMS.txt)
