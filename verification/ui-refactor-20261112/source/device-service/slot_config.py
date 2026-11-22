#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the native upgrade config section in the current system slot."""

import os
from pathlib import Path
import re
import stat
import sys
import tempfile


SHARED_CONFIG = Path("/oem/printer_data/config/extended/klipper/u1_native_upgrade.cfg")
SYSTEM_CONFIG = Path("/usr/local/share/u1-native-upgrade/klipper.cfg")
SYSTEM_MODULE = Path("/home/lava/klipper/klippy/extras/u1_native_upgrade.py")
OPTIONAL_INCLUDE = "include /usr/local/share/u1-native-upgrade/*.cfg"
SECTION = re.compile(r"^\[([^]]+)\]\s*(?:[#;].*)?$")
MANAGED_NOTE = "# Managed by the 233 native UI upgrade service.\n"


def slot_scoped_content(content):
    """Replace only the optionless legacy section; retain all other content."""
    lines = []
    in_upgrade = False
    found_include = False
    migrated = False
    for line in content.splitlines(keepends=True):
        stripped_header = SECTION.match(line.strip())
        if line[:1].isspace() and stripped_header and stripped_header.group(1).strip() in (
                "u1_native_upgrade", OPTIONAL_INCLUDE):
            # Klipper resolves includes only at column zero. Indented brackets
            # can also be a macro continuation; do not rewrite ambiguous data.
            raise ValueError("indented native upgrade section is ambiguous; "
                             "put its section header at column zero before restarting Klipper")
        match = SECTION.match(line.rstrip("\r\n"))
        if match:
            section = match.group(1).strip()
            in_upgrade = section == "u1_native_upgrade"
            if in_upgrade:
                # Preserve comments, newlines and unrelated sections.
                line = line[:match.start(1)] + OPTIONAL_INCLUDE + line[match.end(1):]
                found_include = migrated = True
            elif section == OPTIONAL_INCLUDE:
                line = line[:match.start(1)] + OPTIONAL_INCLUDE + line[match.end(1):]
                found_include = True
        elif in_upgrade and line.strip() and not line.lstrip().startswith(("#", ";")):
            raise ValueError("legacy [u1_native_upgrade] has custom options; "
                             "remove unsupported options before restarting Klipper")
        lines.append(line)
    result = "".join(lines)
    if not found_include:
        if result and not result.endswith("\n"):
            result += "\n"
        if result:
            result += "\n"
        result += MANAGED_NOTE + "[" + OPTIONAL_INCLUDE + "]\n"
    return result, migrated


def read_regular(path):
    """Do not follow a shared-config symlink or overwrite a nonregular file."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b"", None
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("shared native upgrade config must be a regular file")
        return stream.read(), metadata


def write_temporary(path, content, metadata, prefix):
    fd, name = tempfile.mkstemp(prefix=prefix, dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            mode = stat.S_IMODE(metadata.st_mode) if metadata else 0o644
            os.fchmod(stream.fileno(), mode)
            if metadata:
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink()
        raise


def ensure_slot_config(path=SHARED_CONFIG, system_config=SYSTEM_CONFIG,
                       system_module=SYSTEM_MODULE):
    if not system_config.is_file() or not system_module.is_file():
        raise ValueError("current system slot is missing native upgrade config or module")
    path.parent.mkdir(parents=True, exist_ok=True)
    original, metadata = read_regular(path)
    replacement, migrated = slot_scoped_content(original.decode("utf-8"))
    encoded = replacement.encode("utf-8")
    if encoded == original:
        return
    # Backups deliberately do not end in .cfg, so existing include globs cannot
    # activate the old section when this /oem partition is used by an old slot.
    if migrated:
        write_temporary(path, original, metadata, path.name + ".before-slot-scope.")
    temporary = write_temporary(path, encoded, metadata, "." + path.name + ".tmp.")
    try:
        current, current_metadata = read_regular(path)
        identity = lambda value: (value.st_dev, value.st_ino) if value else None
        if current != original or identity(current_metadata) != identity(metadata):
            raise ValueError("shared native upgrade config changed during migration; retry")
        os.replace(str(temporary), str(path))
        directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    try:
        ensure_slot_config()
    except (OSError, UnicodeError, ValueError) as error:
        print("Native upgrade config migration failed: " + str(error), file=sys.stderr)
        sys.exit(1)
