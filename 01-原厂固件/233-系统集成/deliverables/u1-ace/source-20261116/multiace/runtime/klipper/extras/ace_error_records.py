"""Durable, compare-before-release records for explicit operator recovery."""
import hashlib, json, os, stat, time, shutil
from .ace_location_hold import PATH, PORT, UID
from .ace_stop_guard import FAULT_PATH as STOP_PATH, LEGACY_FAULT_PATH as LEGACY_PATH


def signature(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    result = [info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_uid]
    if stat.S_ISREG(info.st_mode) and info.st_size <= 131072:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                result.append(hashlib.sha256(stream.read(131073)).hexdigest())
        except OSError:
            result.append('unreadable')
    return result


def sync_directory(path):
    fd = os.open(os.path.dirname(path), os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic_json(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temp = path + '.tmp-' + os.urandom(8).hex()
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path); sync_directory(path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def stop_files(guard):
    paths = [root + suffix for root in (STOP_PATH, LEGACY_PATH)
             for suffix in ('', '.released', '.additional-fault', '.release-pending')]
    if guard.store_path: paths.append(guard.store_path)
    return {path: signature(path) for path in paths}


def release_stop(guard, generation, expected):
    """Caller has stopped every affected unit; a newer fault always wins."""
    with guard._lock:
        if guard._generation != generation or stop_files(guard) != expected:
            raise RuntimeError('停止错误已变化，请重新检测')
        if guard._fault is None:
            return False
        pending = STOP_PATH + '.release-pending'
        nonce = '.before-manual-' + os.urandom(8).hex()
        os.makedirs(os.path.dirname(STOP_PATH), mode=0o700, exist_ok=True)
        archived = []
        try:
            # A crash during migration must still restore a STOP latch.
            if os.path.lexists(pending):
                os.rename(pending, pending + nonce); archived.append((pending, pending + nonce))
            atomic_json(pending, {'state': 'checking', 'generation': generation})
            for path, sig in expected.items():
                if path == pending or path == guard.store_path or sig is None: continue
                if signature(path) != sig: raise RuntimeError('停止记录已变化')
                os.rename(path, path + nonce); archived.append((path, path + nonce))
                sync_directory(path)
            if guard.store_path and expected.get(guard.store_path) is not None:
                # Keep the exact previous journal without an interval lacking a journal.
                fd = os.open(guard.store_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as stream: old = stream.read(131073)
                if len(old) > 131072: raise RuntimeError('停止记录过大，保留错误')
                fd = os.open(guard.store_path + nonce, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(old); stream.flush(); os.fsync(stream.fileno())
            guard._persist([])
            os.unlink(pending); sync_directory(pending)
        except Exception:
            # Restore old names where possible; the pending mark remains fail-closed.
            for path, archive in reversed(archived):
                if path != pending and os.path.lexists(archive) and not os.path.lexists(path):
                    os.rename(archive, path)
            raise
        guard._fault = None; guard._faults = []; guard.persistence_error = None
        guard._generation += 1
        return True


def location_files(hold):
    return {'routes': signature(hold.routes_path), 'legacy': signature(PATH),
            'pending': json.dumps(hold._pending, sort_keys=True), 'transaction': signature(hold.routes_path + '.reset-pending')}


def release_locations(hold, expected, port, uid, slot, all_empty, identities):
    """Release only the operator-confirmed, freshly verified empty scope."""
    with hold._locked():
        if location_files(hold) != expected:
            raise RuntimeError('位置记录已变化，请重新检查')
        if all_empty:
            raise RuntimeError('不允许清除未经逐路确认的全局位置记录')
        routes, _ = hold._snapshot()
        remaining = [r for r in routes if not (r['uid'] == list(uid) and r['slot'] == slot)]
        legacy = all_empty or (tuple(uid) == UID and slot == 3 and port == PORT)
        nonce = '.before-manual-' + os.urandom(8).hex()
        backups = []
        old_state = (hold.routes, hold._pending, hold.routes_valid, hold.active, hold.valid_marker)
        paths = [hold.routes_path] + ([PATH] if legacy else [])
        pending = hold.routes_path + '.reset-pending'
        try:
            atomic_json(pending, {'state': 'resetting', 'uid': list(uid), 'slot': slot})
            for path in paths:
                os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
                if os.path.lexists(path):
                    shutil.copyfile(path, path + nonce)
                    os.chmod(path + nonce, 0o600)
                    with open(path + nonce, 'rb') as stream: os.fsync(stream.fileno())
                    sync_directory(path)
                    backups.append((path, path + nonce))
                else: backups.append((path, None))
            hold._write_routes(remaining)
            if legacy:
                proof = {'reason': 'position_unconfirmed_after_seek', 'ace_uid': list(UID),
                    'slot': 3, 'head': 3, 'port': PORT, 'state': 'operator_reset_empty',
                    'operator_confirmed': True, 'heads_clear': True, 'stopped': True,
                    'all_inputs_empty': bool(all_empty), 'target_input_empty': True,
                    'target_identity_verified': not all_empty, 'identities': identities,
                    'time': time.time()}
                atomic_json(PATH, proof)
                hold.active = False; hold.valid_marker = True
            hold._pending = [] if all_empty else [r for r in hold._pending
                if not (r['uid'] == list(uid) and r['slot'] == slot)]
            os.unlink(pending); sync_directory(pending)
            hold.routes = remaining; hold.routes_valid = True
        except Exception:
            for path, backup in reversed(backups):
                if backup is not None: os.replace(backup, path)
                elif os.path.lexists(path): os.unlink(path)
                sync_directory(path)
            hold.routes, hold._pending, hold.routes_valid, hold.active, hold.valid_marker = old_state
            if os.path.exists(pending): os.unlink(pending); sync_directory(pending)
            raise
        return [archive for _, archive in backups if archive]
