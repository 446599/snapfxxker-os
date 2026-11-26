#!/usr/bin/env python3
"""Compose a 233/1.90.2 candidate in an *extracted* release rootfs; no printer access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile

BASE_PROVIDER = '1.27b-233.20261122-flow.15.112'
BASE_PROVIDER_SHA = '0d21c8d2d4b4477c68588c075a68c9b645d3892e72b4b6befa44ccd4f6957b25'
FIELD_VERSION = '1.90.2-233.20261126'
FIELD_SHA = 'd16535002703f19f43282c990385c1810c48d63a1d0ebff0d25335d924c9faea'
PROVIDER = FIELD_VERSION + '-paxx.3'
APP_ROOT = '/userdata/233-apps/multiace'
APP = APP_ROOT + '/latest'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def replace_once(s, old, new):
    require(s.count(old) == 1, 'source anchor not unique: ' + old[:80])
    return s.replace(old, new, 1)


def unpack(archive, target):
    target.mkdir()
    with tarfile.open(archive, 'r:gz') as t:
        t.extractall(target, filter='data')
    roots = list(target.iterdir())
    require(len(roots) == 1 and roots[0].is_dir(), 'archive needs one provider root')
    return roots[0]


def cfg_values(text, values):
    """Set only scalar keys in [ace], leaving other sections intact."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.strip() == '[ace]')
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r'^\s*\[[^]]+\]\s*$', lines[i])), len(lines))
    result = []
    seen = set()
    for line in lines[start+1:end]:
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:', line)
        if m and m[1] in values:
            if m[1] not in seen:
                result.append(f'{m[1]}: {values[m[1]]}\n')
                seen.add(m[1])
        else:
            result.append(line)
    missing = [f'{key}: {value}\n' for key, value in values.items() if key not in seen]
    return ''.join(lines[:start+1] + missing + result + lines[end:])


WEB_SETTINGS = {'enable_web': 'false', 'web_dir': APP + '/web', 'i18n_dir': APP + '/i18n'}


def patch_preload(text):
    """A failed pre-motion save must not publish a phantom loaded source."""
    start = text.index('        # Provisional pre-load stamp (routing only)')
    end = text.index('        self.gcode.run_script_from_command(\n            "SET_FILAMENT_SENSOR SENSOR=e%d_filament ENABLE=1" % head)', start)
    block = text[start:end]
    block = replace_once(block,
        '        if self.head_uses_ace(head):\n            self._head_source[head] = self._inherit_prev_capture(',
        '''        if self.head_uses_ace(head):
            previous_source = self._head_source.get(head)
            keys = (self.VARS_ACE_HEAD_SOURCE, self.VARS_ACE_REVISION)
            variables = self.save_variables.allVariables
            previous_vars = {key: variables[key] for key in keys if key in variables}
            self._head_source[head] = self._inherit_prev_capture(''')
    block = replace_once(block, '            self._save_head_source()\n',
        '''            try:
                self._save_head_source()
            except Exception:
                # No FEED_AUTO has run yet. Restore the old in-memory routing
                # and the save_variables table, and propagate the real error.
                self._head_source[head] = previous_source
                variables = self.save_variables.allVariables
                for key in keys:
                    if key in previous_vars:
                        variables[key] = previous_vars[key]
                    else:
                        variables.pop(key, None)
                self._last_load_ok = False
                raise
''')
    result = text[:start] + block + text[end:]
    compile(result, 'ace.py', 'exec')
    return result


def patch_klipper_hook(text):
    """Do not abort the parent init script if storage relocation fails."""
    original = '''        if multiace_enabled; then
            if ! multiace_activate; then
                echo "multiACE is enabled but could not be activated; starting stock Klipper paths." >&2
            fi
        else
            multiace_deactivate
        fi
'''
    updated = '''        if multiace_enabled; then
            # The early boot migration may have run before userdata mounted.
            if /etc/init.d/S47multiace-storage start; then
                pkg=/usr/local/bin/extended-pkg
                if ! "$pkg" multiace check || "$pkg" multiace needs_upgrade; then
                    if ! "$pkg" multiace upgrade; then
                        echo "multiACE pinned provider upgrade failed; keeping previous provider" >&2
                    fi
                fi
                if ! multiace_activate; then
                    echo "multiACE is enabled but could not be activated; starting stock Klipper paths." >&2
                fi
            else
                echo "multiACE storage relocation failed; starting stock Klipper paths, no provider extraction" >&2
                multiace_unmount_modules
                # Remove only the integration's own include, never the
                # user's configuration or an unrelated link. Keep the
                # user's multiACE selection for the next successful boot.
                if [ -L "$MULTIACE_CONFIG_LINK" ] &&
                   [ "$(readlink "$MULTIACE_CONFIG_LINK")" = "$MULTIACE_CONFIG_FILE" ]; then
                    rm -f "$MULTIACE_CONFIG_LINK"
                fi
            fi
        else
            multiace_deactivate
        fi
'''
    return replace_once(text, original, updated)


def compose(root, field, common_source, work):
    root = root.resolve(); work = work.resolve()
    require(root.is_dir() and (root / 'etc/passwd').is_file(), 'not an extracted rootfs')
    require(sha(field) == FIELD_SHA, 'wrong field-tested provider archive')
    definition = root / 'usr/local/share/extended-pkg/multiace'
    olddef = definition.read_text()
    require(f'PKG_VERSION="{BASE_PROVIDER}"' in olddef, 'base provider version mismatch')
    require(f'PKG_SHA256="{BASE_PROVIDER_SHA}"' in olddef, 'base provider checksum pin mismatch')
    oldpkg = root / f'usr/local/share/233-firmware/multiace-managed-{BASE_PROVIDER}.tar.gz'
    require(sha(oldpkg) == BASE_PROVIDER_SHA, 'base embedded provider mismatch')
    old = unpack(oldpkg, work / 'old-provider')
    new = unpack(field, work / 'field-provider')
    require((new / 'VERSION').read_text().strip() == FIELD_VERSION, 'wrong provider VERSION')
    engine = new / 'klipper/extras/ace.py'
    field_engine_hash = sha(engine)
    require('SharedAce2Discovery' in engine.read_text() and 'official or explicitly_configured' in engine.read_text(), 'field discovery patch missing')
    engine.write_text(patch_preload(engine.read_text()))
    engine_hash = sha(engine)
    # Keep the field-tested 1.90.2 code; carry over only offline libraries and
    # optional MCU image files, never the old backend or Klipper Python modules.
    oldvendor = old / 'web/backend/vendor'
    newvendor = new / 'web/backend/vendor'
    newvendor.mkdir(parents=True, exist_ok=True)
    require(oldvendor.is_dir(), 'offline dependency bundle missing in base')
    for src in oldvendor.iterdir():
        if src.name.startswith(('.', 'ace2k')) or src.name == '__pycache__':
            continue
        dst = newvendor / src.name
        if dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
    oldimages = old / 'web/backend/firmware'
    if oldimages.is_dir():
        dst = new / 'web/backend/firmware'
        dst.mkdir(exist_ok=True)
        for image in oldimages.iterdir():
            if image.is_file() and not (dst / image.name).exists():
                shutil.copy2(image, dst / image.name)
    (newvendor / '.paxx-dependencies-ready').unlink(missing_ok=True)
    (new / 'VERSION').write_text(PROVIDER + '\n')
    config = new / 'config/extended/ace.cfg'
    # Device-specific adapter default. Existing explicit user configuration
    # remains owned by the upstream merge helper.
    config.write_text(cfg_values(config.read_text(), {**WEB_SETTINGS,
                     'v2_extra_usb_ids': '1a86:55d5'}))
    # The activation hook enforces web ownership even on a migrated user config.
    # No other existing option is rewritten by this helper.
    helper = new / 'tools/set_managed_web.py'
    helper.write_text('''import os, re, sys, tempfile\nfrom pathlib import Path\n''' +
                      __import__('inspect').getsource(cfg_values) + '\n' +
                      'p=Path(sys.argv[1])\nold=p.read_text()\nnew=cfg_values(old,' + repr(WEB_SETTINGS) + ')\n' +
                      '''if old != new:\n    st=p.stat()\n    fd,name=tempfile.mkstemp(dir=p.parent,prefix='.ace-web-')\n    with os.fdopen(fd,'w') as f: f.write(new)\n    os.chmod(name,st.st_mode & 0o777)\n    os.chown(name,st.st_uid,st.st_gid)\n    os.replace(name,p)\n''')
    # The new mappings were already added in the coldboot branch. Keep the
    # old 1.27b path for rollback and require every new mapping in the archive.
    common = replace_once(common_source.read_text(), 'MULTIACE_INSTALL_DIR="/oem/apps/multiace"', 'MULTIACE_INSTALL_DIR="' + APP_ROOT + '"')
    mapping = common.split("multiace_1902_module_map() {\n    cat <<'MAP'\n",1)[1].split('\nMAP',1)[0]
    required = sorted({'klipper/' + row.split()[0] for row in mapping.splitlines()} |
                      {'VERSION','config/extended/ace.cfg','config/extended/multiace/ace_vars.cfg',
                       'tools/merge_ace_cfg.py','tools/set_managed_web.py','tools/post_process_virtual_toolheads.py',
                       'web/backend/main.py','web/backend/requirements.txt','web/frontend/index.html',
                       'web/backend/vendor/ace2k_flash.pyz','web/backend/ace2k_flasher.py'})
    for name in required:
        require((new / name).is_file(), 'missing 1.90.2 runtime: ' + name)
    # Save the original persistent config once before the first schema merge.
    common = replace_once(common, '    if [ "$config_seeded" = "1" ]; then\n', '''    case "$(cat "$MULTIACE_APP_ROOT/VERSION")" in
        1.90.2|1.90.2-*)
            if [ "$config_seeded" != "1" ] && [ ! -e "$MULTIACE_CONFIG_FILE.pre-1902" ]; then
                cp -p "$MULTIACE_CONFIG_FILE" "$MULTIACE_CONFIG_FILE.pre-1902" || return 1
            fi
            ;;
    esac
    if [ "$config_seeded" = "1" ]; then
''')
    common = replace_once(common, '    if [ ! -e "$MULTIACE_STATE_DIR/ace_vars.cfg" ]; then\n', '''    case "$(cat "$MULTIACE_APP_ROOT/VERSION")" in
        1.90.2|1.90.2-*)
            python3 "$MULTIACE_APP_ROOT/tools/set_managed_web.py" "$MULTIACE_CONFIG_FILE" || return 1
            ;;
    esac
    if [ ! -e "$MULTIACE_STATE_DIR/ace_vars.cfg" ]; then
''')
    (root / 'usr/local/share/multiace/common.sh').write_text(common)
    # Install the pinned provider on boot only when the owner selected PAXX
    # MultiACE; never use clean/download (which remove the prior provider).
    hook = root / 'etc/hooks/klipper.d/10-multiace.sh'
    hook.write_text(patch_klipper_hook(hook.read_text()))
    # The baked S98 service is the only managed web owner. It must not repeat
    # configuration migration or serve an old standalone tree.
    service = root / 'etc/init.d/S98multiace-web'
    service.write_text(replace_once(service.read_text(), '''    if ! multiace_seed_config; then
        echo "$NAME cannot prepare its persistent configuration" >&2
        return 1
    fi
''', '''    if [ ! -e "$MULTIACE_CONFIG_LINK" ]; then
        echo "$NAME provider not activated" >&2
        return 1
    fi
'''))
    for p in (root / 'usr/local/share/multiace/common.sh', hook, service):
        require(p.is_file(), 'missing managed boot file')
    # Offline vendor smoke test will run in the ARM64 base rootfs before repack.
    package = oldpkg.parent / f'multiace-managed-{PROVIDER}.tar.gz'
    with tarfile.open(package, 'w:gz', compresslevel=6) as t:
        t.add(new, arcname='multiace-' + PROVIDER)
    digest = sha(package)
    newdef = replace_once(olddef, f'PKG_VERSION="{BASE_PROVIDER}"', f'PKG_VERSION="{PROVIDER}"')
    newdef = replace_once(newdef, oldpkg.name, package.name)
    newdef = replace_once(newdef, 'PKG_APP_DIR="/oem/apps/multiace"', 'PKG_APP_DIR="' + APP_ROOT + '"')
    newdef = replace_once(newdef, BASE_PROVIDER_SHA, digest)
    newdef, count = re.subn(r'    for required in \\\n.*?; do\n',
                    lambda _: '    for required in \\\n' + ' \\\n'.join('        ' + n for n in required) + '; do\n', newdef, flags=re.S)
    require(count == 1, 'package definition required-file anchor mismatch')
    definition.write_text(newdef)
    # Do not retain two embedded package archives; rollback is the original
    # full release and the existing versioned /oem/apps provider directory.
    oldpkg.unlink()
    require(sha(engine) == engine_hash, 'field-tested engine unexpectedly changed')
    result = {'provider': PROVIDER, 'provider_archive': str(package.relative_to(root)),
              'provider_sha256': digest, 'field_archive_sha256': FIELD_SHA,
              'field_engine_sha256': field_engine_hash, 'engine_sha256': engine_hash, 'provider_install_root': APP_ROOT, 'required_files': required,
              'scope': 'PAXX/233 test candidate; existing screen and non-root partitions unchanged; no MCU flash',
              'hardware_verified': False}
    (work / 'composition.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return new


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('root','field','common','work'):
        p.add_argument(n, type=Path)
    a = p.parse_args()
    compose(a.root, a.field, a.common, a.work)
