"""Explicit, device-bound consent for optional filament scanning movement."""
import copy
import json
import logging
import os
import tempfile

from .ace_path_store import configured_route_heads

RFID_CLEARANCE_MM = 800
REMAINING_CLEARANCE_MM = 1400
SCAN_MM = 700
GRIP_MM = 100
GRIP_SPEED = 50


class FilamentOptions:
    def __init__(self, ace, path):
        self.ace, self.path = ace, path
        self.saved = {'schema': 2, 'rfid': False, 'remaining': False,
                      'auto_rfid': False, 'clearance_mm': 0, 'devices': {}}
        try:
            with open(path) as stream:
                data = json.loads(stream.read(16385))
            if (not isinstance(data, dict) or data.get('schema') != 2
                    or any(type(data.get(k)) is not bool for k in ('rfid', 'remaining'))
                    or type(data.get('clearance_mm')) is not int
                    or not 0 <= data['clearance_mm'] <= 20000
                    or not isinstance(data.get('devices'), dict)):
                raise ValueError('invalid advanced filament options')
            if 'auto_rfid' not in data:
                data['auto_rfid'] = data['rfid']
            elif type(data['auto_rfid']) is not bool:
                raise ValueError('invalid auto RFID option')
            self.saved = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logging.warning('[multiACE] advanced filament options unavailable; disabled')

    def _identity(self, idx):
        if self.ace._connected_per_ace.get(idx) is not True:
            return None
        try:
            port, uid = self.ace._location_identity(idx)
            if not port or not isinstance(uid, (list, tuple)) or len(uid) != 3:
                return None
            if any(type(value) is not int or not 0 <= value <= 0xffffffff for value in uid):
                return None
            return {'port': str(port), 'uid': list(uid)}
        except (RuntimeError, TypeError, ValueError):
            return None

    def identities(self):
        devices = {}
        for idx in self.ace._connected_per_ace:
            identity = self._identity(idx)
            if identity is not None:
                mode, head = self._route_binding(idx)
                if mode == 'multi' or (mode == 'head' and head is not None):
                    devices[str(idx)] = dict(identity, mode=mode, head=head)
        if not devices:
            raise RuntimeError('请等待至少一台 ACE 完成身份确认')
        return devices

    def _route_binding(self, idx):
        mode = getattr(self.ace, '_ace_mode', 'multi')
        if mode == 'multi':
            return mode, None
        if mode == 'head':
            heads = configured_route_heads(self.ace, idx, 0)
            return (mode, heads[0]) if len(heads) == 1 else (mode, None)
        return mode, None

    def _confirmed_unit(self, idx):
        identity = self._identity(idx)
        if identity is None:
            return False
        mode, head = self._route_binding(idx)
        if mode not in ('multi', 'head') or (mode == 'head' and head is None):
            return False
        # Old receipts did not record a mode. They are usable as legacy
        # Multi confirmations, never as evidence of a new Head binding.
        for device in self.saved['devices'].values():
            if (isinstance(device, dict) and all(device.get(key) == val for key, val in identity.items())
                    and device.get('mode', 'multi') == mode and device.get('head') == head):
                return True
        return False

    def enabled(self, feature, idx=None):
        if not self.saved.get('rfid') or not self.saved.get(feature):
            return False
        minimum = REMAINING_CLEARANCE_MM if feature == 'remaining' else RFID_CLEARANCE_MM
        if self.saved['clearance_mm'] < minimum:
            return False
        try:
            if idx is not None:
                return self._confirmed_unit(idx)
            return any(self._confirmed_unit(i) for i in self.ace._connected_per_ace)
        except (RuntimeError, TypeError, ValueError):
            return False

    def require(self, feature='rfid', error=RuntimeError, idx=None):
        if not self.enabled(feature, idx):
            if idx is not None and self.saved.get('rfid') and not self._confirmed_unit(idx):
                raise error('目标 ACE 的料路尚未确认，未启动识别')
            raise error('请先在高级设置 → 耗材高级功能中确认 Y 前可用长度并开启'
                        + (' RFID 识别' if feature == 'rfid' else '余量估计'))

    def set_auto(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动识别设置无效')
        value = dict(self.saved, auto_rfid=enabled)
        self._save(value)
        self.saved = value

    def update(self, feature, enabled, clearance, confirmed):
        if feature not in ('rfid', 'remaining') or type(enabled) is not bool:
            raise ValueError('耗材高级选项无效')
        value = copy.deepcopy(self.saved)
        if enabled:
            minimum = REMAINING_CLEARANCE_MM if feature == 'remaining' else RFID_CLEARANCE_MM
            if (confirmed is not True or type(clearance) is not int
                    or not minimum <= clearance <= 20000):
                raise ValueError('请确认每条支路短吸后的料头到 Y 前至少有 %d mm 空闲长度' % minimum)
            if feature == 'remaining':
                self.require('rfid')
            value.update(clearance_mm=clearance, devices=self.identities())
        value[feature] = enabled
        if feature == 'rfid' and (not enabled or value['clearance_mm'] < REMAINING_CLEARANCE_MM
                                  or value['devices'] != self.saved['devices']):
            value['remaining'] = False
        self._save(value)
        self.saved = value

    def _save(self, value):
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.filament-options-', dir=directory)
        try:
            with os.fdopen(fd, 'w') as target:
                json.dump(value, target, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def status(self):
        return {'rfid': self.enabled('rfid'), 'remaining': self.enabled('remaining'),
                'auto_rfid': self.saved['auto_rfid'],
                'clearance_mm': self.saved['clearance_mm'], 'scan_mm': SCAN_MM,
                'rfid_required_mm': RFID_CLEARANCE_MM,
                'remaining_required_mm': REMAINING_CLEARANCE_MM,
                'confirmation_valid': self.enabled('rfid')}
