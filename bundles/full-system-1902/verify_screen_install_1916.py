"""Exercise the actual patched package functions on a temporary screen app.

No machine paths are written. The real downloader is replaced only in this
fixture by a copy of a locally constructed, checksum-pinned tar archive.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


def verify_screen_install(manager):
    start=manager.index('installed() {')
    end=manager.index('# Move this process',start)
    funcs=manager[start:end]
    if funcs.count('/usr/local/bin/curl')!=1:
        raise ValueError('unexpected screen download implementation')
    funcs=funcs.replace('/usr/local/bin/curl','fixture_copy')
    with tempfile.TemporaryDirectory(prefix='1916-screen-install-') as td:
        root=Path(td)
        source=root/'source'
        binary=source/'bin/helix-screen'
        binary.parent.mkdir(parents=True)
        binary.write_text('new screen fixture\n')
        binary.chmod(0o755)
        archive=root/'screen.tar.gz'
        with tarfile.open(archive,'w:gz') as out:
            out.add(source,arcname='helixscreen')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        app=root/'apps'
        versioned=app/'helixscreen-233-test'
        old=versioned/'bin/helix-screen'
        old.parent.mkdir(parents=True)
        old.write_text('old screen fixture\n')
        old.chmod(0o755)
        (app/'latest').symlink_to(versioned.name)
        variables=dict(PKG='helixscreen',PKG_LABEL='screen fixture',
                       PKG_APP_DIR=str(app),PKG_PREFIX='helixscreen-233',
                       PKG_VERSION='test',PKG_SHA256=digest,PKG_URL=str(archive),
                       PKG_BINARIES='bin/helix-screen',PKG_STRIP_COMPONENTS='1',
                       PKG_LEGACY_DIR='',PKG_CLEAN_PATHS='',
                       INSTALL_DIR=str(app/'latest'),VERSIONED_DIR=str(versioned),
                       TMP_DIR=str(app/'tmp'),STAGING_DIR=str(app/'tmp'/versioned.name))
        program='set -eu\n'+funcs+'''
fixture_copy() { cp "$4" "$3"; }
fail() { echo "screen install fixture: $*" >&2; exit 1; }
# Same version without a verified digest must not be considered current.
needs_upgrade || fail "missing marker skipped"
download || fail "initial install failed"
[[ "$(cat "$INSTALL_DIR/bin/helix-screen")" == "new screen fixture" ]] || fail "old executable"
[[ "$(cat "$INSTALL_DIR/.233-archive-sha256")" == "$PKG_SHA256" ]] || fail "marker missing"
if needs_upgrade; then fail "matching installed digest requested reinstall"; fi
# Same version with a different digest must be repaired.
printf 'old-digest\\n' > "$INSTALL_DIR/.233-archive-sha256"
needs_upgrade || fail "digest mismatch skipped"
download || fail "same version install failed"
if needs_upgrade; then fail "same version remained stale"; fi
# Broken/missing executable cannot count as an installed current screen.
chmod -x "$INSTALL_DIR/bin/helix-screen"
needs_upgrade || fail "missing executable skipped"
chmod +x "$INSTALL_DIR/bin/helix-screen"
# Simulate a failure after renaming the old version but before staging
# is moved. The rollback must restore the old directory and link.
mv() {
    if [[ "$1" == "$STAGING_DIR" && "$2" == "$VERSIONED_DIR" ]]; then
        return 1
    fi
    command mv "$@"
}
if download; then fail "failed replacement returned success"; fi
[[ -x "$INSTALL_DIR/bin/helix-screen" ]] || fail "rollback lost executable"
[[ "$(cat "$INSTALL_DIR/.233-archive-sha256")" == "$PKG_SHA256" ]] || fail "rollback lost digest"
if needs_upgrade; then fail "rollback left a stale installation"; fi
echo "Actual screen package install/digest/rollback contracts passed"
'''
        subprocess.run(['bash','-c',program],env={**os.environ,**variables},check=True)
