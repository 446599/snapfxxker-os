#!/usr/bin/env python3
"""Patch a *staging copy* of the actual flow.15.116 runtime, not live hardware.

The repository does not contain the complete .116 runtime. Exact provider
anchors and the slot request AST are checked before any writes. Do not use
this tool to substitute older modules or to certify motion/STOP semantics.
"""
import argparse
import ast
import hashlib
from pathlib import Path

REPLACEMENTS = {
    "ace.py": [
        [
            "    def _location_identity(self, idx):\n",
            "    def _discovery_uid(self, idx, response):\n        \"\"\"Return an identity only from the selected protocol's verified reply.\"\"\"\n        if (not isinstance(response, dict)\n                or type(response.get('code')) is not int\n                or response['code'] != 0\n                or str(response.get('msg', '')).upper() == 'FORBIDDEN'):\n            return None\n        if self._is_ace2k_idx(idx):\n            if response.get('_ace2k_uid_verified') is not True:\n                return None\n        elif self._is_v2_idx(idx):\n            if (response.get('_v2_frame_valid') is not True\n                    or response.get('_v2_payload_valid') is not True\n                    or type(response.get('_v2_command')) is not int\n                    or response['_v2_command'] != 0):\n                return None\n        else:\n            return None\n        result = response.get('result')\n        if not isinstance(result, dict):\n            return None\n        uid = tuple(result.get('uid%d' % n) for n in (1, 2, 3))\n        if not all(type(v) is int and 0 <= v <= 0xffffffff for v in uid) or not any(uid):\n            return None\n        return uid\n\n    def _location_identity(self, idx):\n"
        ],
        [
            "            uid = entry[1] if entry and entry[0] is self._serials.get(idx) else None\n",
            "            transport = self._serials.get(idx)\n            uid = entry[1] if transport is not None and entry and entry[0] is transport else None\n            if (not isinstance(uid, tuple) or len(uid) != 3\n                    or not all(type(v) is int and 0 <= v <= 0xffffffff for v in uid)\n                    or not any(uid)):\n                uid = None\n"
        ],
        [
            "            if (method == 'discover_device' and isinstance(response,dict)\n                    and response.get('code') == 0\n                    and (self._is_ace2k_idx(idx) or\n                         (response.get('_v2_frame_valid') is True and response.get('_v2_payload_valid') is True\n                          and response.get('_v2_command') == 0))):\n                result = response.get('result') or {}\n                uid = tuple(result.get('uid%d' % n) for n in (1,2,3))\n                if all(type(v) is int for v in uid):\n                    self._location_uid_by_idx[idx] = (request_transport, uid)\n",
            "            if method == 'discover_device':\n                uid = self._discovery_uid(idx, response)\n                if uid is not None:\n                    self._location_uid_by_idx[idx] = (request_transport, uid)\n                elif self._is_ace2k_idx(idx):\n                    # Never retain an identity after a failed native UID query.\n                    self._location_uid_by_idx.pop(idx, None)\n"
        ],
        [
            "        while reactor.monotonic() < deadline:\n            status = self._rfid_identity_status(idx)\n            if status is not None:\n                return status\n            if self._serials.get(idx) is not transport:\n                return None                 # 期间重连了，视为未确认\n            reactor.pause(reactor.monotonic() + 0.05)\n        return self._rfid_identity_status(idx)\n",
            "        while reactor.monotonic() < deadline:\n            if self._serials.get(idx) is not transport:\n                return None                 # 期间重连了，视为未确认\n            status = self._rfid_identity_status(idx)\n            if status is not None:\n                return status\n            reactor.pause(reactor.monotonic() + 0.05)\n        return (self._rfid_identity_status(idx)\n                if self._serials.get(idx) is transport else None)\n"
        ]
    ],
    "ace_protocol_ace2k.py": [
        [
            "    b = bytes(bytearray(uid or b''))\n    if len(b) < UID_LEN:\n        return ()\n    return struct.unpack('<3I', b[:UID_LEN])\n",
            "    try:\n        b = bytes(bytearray(uid or b''))\n    except (TypeError, ValueError, OverflowError):\n        return ()\n    if len(b) != UID_LEN:\n        return ()\n    words = struct.unpack('<3I', b)\n    return words if any(words) else ()\n"
        ],
        [
            "        if method == 'discover_device':\n            words = chip_words(self.uid) or (0, 0, 0)\n            return ok({'uid1': words[0], 'uid2': words[1], 'uid3': words[2],\n                       'firmware': 'ace2k %s' % (self.version or '?')})\n",
            "        if method == 'discover_device':\n            # The UID must be a real reply from this live native link, not a\n            # cached connection-time value or an all-zero placeholder.\n            ans = self._call('ace2k_uid_query', 'ace2k_uid_response')\n            raw = (ans or {}).get('uid', b'')\n            words = chip_words(raw)\n            if not words:\n                self.uid = b''\n                return {'id': rid, 'code': FAILED, 'msg': 'ace2k UID unavailable',\n                        'result': {}}\n            self.uid = bytes(raw)\n            response = ok({'uid1': words[0], 'uid2': words[1], 'uid3': words[2],\n                           'firmware': 'ace2k %s' % (self.version or '?')})\n            response['_ace2k_uid_verified'] = True\n            return response\n"
        ],
        [
            "            if job.get('waiting'):\n                # A start the unit refused as busy (its tie-break).\n",
            "            if job.get('waiting'):\n                # A cancelled pending move must never be started by a later tick.\n                if job.get('stopping'):\n                    job['stopped'] = True\n                    self._end_job(lane, 'cancelled before start', stop='stopped')\n                    continue\n                # A start the unit refused as busy (its tie-break).\n"
        ],
        [
            "                job['stopping'] = True\n                self._stop_lane(ln)\n",
            "                job['stopping'] = True\n                if job.get('waiting'):\n                    # No move was accepted by the unit. Cancel the queued\n                    # retry rather than letting _tick start it after STOP.\n                    job['stopped'] = True\n                    self._end_job(ln, 'cancelled before start', stop='stopped')\n                    continue\n                self._stop_lane(ln)\n"
        ]
    ]
}

def apply_pairs(source, pairs):
    for old, new in pairs:
        if source.count(new) == 1:
            continue
        if source.count(old) != 1:
            raise ValueError("provider source drift: " + old[:100])
        source = source.replace(old, new, 1)
    return source


def patch_slot_guard(source):
    tree = ast.parse(source)
    cls = next((n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "SlotActions"), None)
    if cls is None:
        raise ValueError("SlotActions class missing")
    method = next((n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_request"), None)
    if method is None:
        raise ValueError("SlotActions._request missing")
    guards = [n for n in ast.walk(method) if isinstance(n, ast.If)
              and any(isinstance(s, ast.Raise) and "响应帧无效" in ast.unparse(s)
                      for s in n.body)]
    if len(guards) != 1:
        raise ValueError("unexpected slot frame guard count")
    old = ast.get_source_segment(source, guards[0].test)
    if old is None:
        raise ValueError("missing frame guard expression")
    if "_discovery_uid" in old:
        if ("_is_ace2k_idx(idx)" not in old
                or "method == 'discover_device'" not in old):
            raise ValueError("unknown discovery patch")
        return source
    required = ("_v2_frame_valid", "_v2_payload_valid", "_v2_command",
                "command", "r.get(")
    if not all(t in old for t in required):
        raise ValueError("slot frame guard drift")
    # ACE2K discovery is checked with the native proof, not a V2 flag.
    # Leave all V2 and non-discovery checks exactly as they were.
    new = ("(self.ace._discovery_uid(idx, r) is None "
           "if method == 'discover_device' and self.ace._is_ace2k_idx(idx) "
           "else (" + old + "))")
    if source.count(old) != 1:
        raise ValueError("slot guard is not unique")
    return source.replace(old, new, 1)


def plan(root):
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not version.endswith("flow.15.116"):
        raise ValueError("expected extracted flow.15.116, got " + version)
    extras = root / "klipper" / "extras"
    result = {}
    for name, pairs in REPLACEMENTS.items():
        path = extras / name
        source = path.read_text(encoding="utf-8")
        result[path] = apply_pairs(source, pairs)
    path = extras / "ace_slot_actions.py"
    result[path] = patch_slot_guard(path.read_text(encoding="utf-8"))
    for path, source in result.items():
        compile(source, str(path), "exec")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path, help="extracted staging package root")
    parser.add_argument("--apply", action="store_true",
                        help="write to the staging copy after all checks pass")
    args = parser.parse_args()
    result = plan(args.runtime)
    for path, source in result.items():
        before = path.read_bytes()
        after = source.encode("utf-8")
        print(path.name, "unchanged" if before == after else "patched",
              hashlib.sha256(after).hexdigest())
    if args.apply:
        for path, source in result.items():
            path.write_text(source, encoding="utf-8")
        print("Staging sources patched; no device or archive installed.")
    else:
        print("Check only. Use --apply on a staging copy after review.")


if __name__ == "__main__":
    main()
