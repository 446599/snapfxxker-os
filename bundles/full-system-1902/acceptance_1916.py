"""Apply exact acceptance fixes to the already composed 1902.15 provider."""
from pathlib import Path


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('1902.16 acceptance anchor mismatch: ' + old[:100])
    return text.replace(old, new, 1)


def patch_engine(text):
    # Patch the installed engine, not the different providers/ source.
    anchor = "            'material_batch_tasks': self._material_batch.status() if hasattr(self,'_material_batch') else [],\n"
    text = replace_once(text, anchor, anchor +
        "            'material_motion_idle': self._material_batch.ota_motion_idle(),\n")
    start = text.index('    def _cmd_ACE_UNLOAD_HEAD_body(self, gcmd):')
    end = text.index('\n    cmd_ACE_TEST_help', start)
    body = text[start:end]
    # Both direct and batch head unload enter this shared body. The existing
    # explicit route validation and stock/manual/swap paths remain intact.
    anchor = '        self._wait_bg_op(head, gcmd)\n'
    body = replace_once(body, anchor, anchor + '''        park_selection = None
        try:
            park_selection, retract_override = self._material_batch.head_park_plan(
                head, requested_ace, requested_slot, retract_override)
        except (ValueError, RuntimeError, OSError) as exc:
            self._last_unload_ok = False
            raise gcmd.error(str(exc)) from exc
''')
    anchor = '        if unload_verified:\n'
    body = replace_once(body, anchor, '''        # Do not clear the mapping on runout or a stale inlet report.
        if park_selection is not None:
            if not unload_verified or self._head_sensor_present(head) is not False:
                self._last_unload_ok = False
                raise gcmd.error('[multiACE] Head return not verified; inspect selected route')
            try:
                self._material_batch._fresh_park_sample(
                    park_selection, self.reactor.monotonic())
            except (ValueError, RuntimeError, OSError) as exc:
                self._last_unload_ok = False
                self._last_unload_reason = 'park_return'
                raise gcmd.error(str(exc)) from exc
''' + anchor)
    text = text[:start] + body + text[end:]
    compile(text, 'ace.py', 'exec')
    return text


def patch_package_manager(text):
    # Restrict checksum migration to the screen package. Other apps keep
    # their own install/rollback policies.
    text = replace_once(text,
        '        [[ "$(readlink "$PKG_APP_DIR/latest")" != "$PKG_PREFIX-$PKG_VERSION" ]]\n',
        '''        [[ "$(readlink "$PKG_APP_DIR/latest")" != "$PKG_PREFIX-$PKG_VERSION" ]] && return 0
        if [[ "$PKG" == "helixscreen" ]]; then
            installed || return 0
            [[ -f "$INSTALL_DIR/.233-archive-sha256" ]] || return 0
            [[ "$(cat "$INSTALL_DIR/.233-archive-sha256")" != "$PKG_SHA256" ]] && return 0
        fi
        return 1
''')
    text = replace_once(text, '    if [[ "$PKG" == "multiace" ]]; then\n',
        '''    if [[ "$PKG" == "helixscreen" ]]; then
        # This marker is written only after the archive passed SHA256 and
        # the staging directory passed the binary checks.
        printf '%s\\n' "$PKG_SHA256" > "$STAGING_DIR/.233-archive-sha256" || return 1
        chmod 644 "$STAGING_DIR/.233-archive-sha256" || return 1
    fi
    if [[ "$PKG" == "multiace" ]]; then
''')
    text = replace_once(text, '''    else
        # shellcheck disable=SC2086
        rm -rf "$PKG_APP_DIR/$PKG_PREFIX-"* $PKG_LEGACY_DIR
''', '''    elif [[ "$PKG" == "helixscreen" ]]; then
        # Keep the old screen until the new directory and symlink commit.
        local backup="$PKG_APP_DIR/.screen-prev-$$"
        local next_link="$PKG_APP_DIR/.latest-next-$$"
        if [[ -e "$VERSIONED_DIR" ]]; then
            mv "$VERSIONED_DIR" "$backup" || return 1
        fi
        if ! mv "$STAGING_DIR" "$VERSIONED_DIR"; then
            [[ ! -e "$backup" ]] || mv "$backup" "$VERSIONED_DIR"
            return 1
        fi
        if ! ln -s "$PKG_PREFIX-$PKG_VERSION" "$next_link" ||
                ! mv -Tf "$next_link" "$PKG_APP_DIR/latest"; then
            rm -f "$next_link"
            rm -rf "$VERSIONED_DIR"
            [[ ! -e "$backup" ]] || mv "$backup" "$VERSIONED_DIR"
            return 1
        fi
        rm -rf "$backup"
        local old
        for old in "$PKG_APP_DIR/$PKG_PREFIX-"*; do
            [[ "$old" == "$VERSIONED_DIR" ]] || rm -rf "$old"
        done
    else
        # shellcheck disable=SC2086
        rm -rf "$PKG_APP_DIR/$PKG_PREFIX-"* $PKG_LEGACY_DIR
''')
    text = replace_once(text, '''    download)
        if check; then
            exit 0
        fi
''', '''    download)
        if check; then
            if [[ "$PKG" != "helixscreen" ]] || ! needs_upgrade; then
                exit 0
            fi
            enter_actions_cgroup
            if ! download; then
                rm -rf "$TMP_DIR"
                exit 1
            fi
            exit 0
        fi
''')
    return text
