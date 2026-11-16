"""Per-load Bowden travel, independent of absolute position and calibration.

Only completed finite ACE moves count. Printing/thermal assistance is excluded:
its encoder includes consumed filament and cannot be a return-to-start distance.
A persisted in-flight move becomes unknown on restart, never a guessed length.
"""
import copy
import math
import json
import logging
import os
import tempfile


class FilamentTravel:
    LIMIT_MM = 20000.0
    # The finite-move ledger does not include hot tip-form/assist and is not
    # an inlet position. Keep part of the first measured feed in reserve.
    PARK_RESERVE_MM = 250.0

    def __init__(self, saved=None, persist=None):
        self.persist = persist or (lambda value: None)
        self.records = {}
        self.active = {}
        self.receipts = set()
        saved = saved if isinstance(saved, dict) else {}
        parks = saved.get('_park_references', {})
        self.park_references = {}
        if isinstance(parks, dict):
            for route, distance in parks.items():
                if (isinstance(route, str) and self._valid(distance)
                        and 0 <= distance <= self.LIMIT_MM):
                    self.park_references[route] = distance
        for key, value in saved.items():
            if str(key) not in ('0', '1', '2', '3'):
                continue
            if (not isinstance(value, dict) or type(value.get('ace')) is not int
                    or value['ace'] < 0 or type(value.get('slot')) is not int
                    or not 0 <= value['slot'] < 4):
                continue
            row = copy.deepcopy(value)
            if (row.get('known') is not True or type(row.get('samples')) is not int
                    or row['samples'] < 1 or row.get('phase') not in
                    ('loading', 'loaded', 'unloading', 'parked', 'head_empty')):
                row.update(known=False, reason='invalid_record')
            if row.get('pending'):
                row.update(known=False, reason='interrupted_move', pending=False)
            if not self._valid(row.get('remaining_mm')):
                row.update(known=False, remaining_mm=0.0)
            self.records[str(key)] = row

    @classmethod
    def from_path(cls, path):
        try:
            with open(path) as source:
                saved = json.loads(source.read(65537))
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError):
            logging.warning('[multiACE] previous load distances unavailable')
            saved = {}

        def persist(records):
            directory = os.path.dirname(path)
            os.makedirs(directory, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.head-travel-', dir=directory)
            try:
                with os.fdopen(fd, 'w') as target:
                    json.dump(records, target, allow_nan=False)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, path)
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return cls(saved, persist)

    @classmethod
    def _valid(cls, value):
        return type(value) in (int, float) and math.isfinite(value) and abs(value) <= cls.LIMIT_MM

    def _save(self):
        try:
            saved = copy.deepcopy(self.records)
            if self.park_references:
                saved['_park_references'] = self.park_references.copy()
            self.persist(saved)
        except Exception:
            for row in self.records.values():
                row.update(known=False, reason='persistence_failed')
            raise

    def _row(self, head, ace, slot):
        row = self.records.get(str(head))
        return row if row and (row.get('ace'), row.get('slot')) == (ace, slot) else None

    def begin_load(self, head, ace, slot):
        row = self._row(head, ace, slot)
        # A failed load can resume from its measured partial progress. A new
        # load after unloading gets its own starting point.
        if not row or row.get('phase') != 'loading':
            row = dict(ace=ace, slot=slot, phase='loading', known=True,
                       remaining_mm=0.0, samples=0, pending=False, reason='')
            self.records[str(head)] = row
        self._save()

    def finish_load(self, head, ace, slot):
        row = self._row(head, ace, slot)
        if row:
            if not row.get('samples') or row['remaining_mm'] <= 0:
                row.update(known=False, reason='no_measured_feed')
            row['phase'] = 'loaded'
            row['loaded_mm'] = row['remaining_mm']
            self._save()

    def begin_unload(self, head, ace, slot):
        row = self._row(head, ace, slot)
        if not row:
            row = dict(ace=ace, slot=slot, known=False, remaining_mm=0.0,
                       pending=False, reason='no_load_record')
            self.records[str(head)] = row
        row['phase'] = 'unloading'
        self._save()
        return self.remaining(head, ace, slot)

    def remaining(self, head, ace, slot):
        row = self._row(head, ace, slot)
        if not row or not row.get('known') or row.get('pending'):
            return None
        return max(0.0, row['remaining_mm'])

    @staticmethod
    def _park_key(head, ace, slot):
        return '%d:%d:%d' % (ace, slot, head)

    def _park_reference(self, row, head, ace, slot):
        return self.park_references.get(
            self._park_key(head, ace, slot),
            max(0.0, row['loaded_mm'] - self.PARK_RESERVE_MM))

    def park_distance(self, head, ace, slot):
        row = self._row(head, ace, slot)
        if (not row or not row.get('known') or row.get('pending')
                or not self._valid(row.get('loaded_mm')) or row['loaded_mm'] <= 0):
            return None
        # The verified park is referenced to the nozzle, not to the starting
        # position of the next load. Subtracting the same reserve every time
        # would progressively move the parked filament farther into the tube.
        already_retracted = max(0.0, row['loaded_mm'] - row['remaining_mm'])
        wanted = max(0.0, self._park_reference(row, head, ace, slot) - already_retracted)
        # A measured lower bound remains useful even if the first encoder
        # sample was delayed. An older park reference must not enlarge it.
        return min(wanted, max(0.0, row['remaining_mm']))

    def finish_park(self, head, ace, slot):
        row = self._row(head, ace, slot)
        if self.park_distance(head, ace, slot) is None:
            raise ValueError('回停计距未确认')
        key = self._park_key(head, ace, slot)
        self.park_references.setdefault(key, self._park_reference(row, head, ace, slot))
        retracted = max(0.0, row['loaded_mm'] - row['remaining_mm'])
        reference = self._park_reference(row, head, ace, slot)
        row['park_reference_reached'] = retracted >= reference - 2.0
        row['phase'] = 'parked' if row['park_reference_reached'] else 'head_empty'
        self._save()

    def forget_park(self, ace, slot):
        prefix = '%d:%d:' % (ace, slot)
        expired = [key for key in self.park_references if key.startswith(prefix)]
        for key in expired:
            del self.park_references[key]
        if expired:
            self._save()

    def motion_started(self, token, head, ace, slot, kind):
        if token in self.active or token in self.receipts:
            return
        row = self._row(head, ace, slot)
        if not row or row.get('phase') not in ('loading', 'unloading'):
            return
        if kind not in ('feed', 'retract', 'unload'):
            return
        self.active[token] = (str(head), ace, slot)
        row['pending'] = True
        self._save()

    def motion_finished(self, token, travelled_mm, confirmed=True, receipt=None):
        identity = self.active.pop(token, None)
        if identity is None:
            return
        self.receipts.add(token)
        if len(self.receipts) > 256:
            self.receipts = {token}
        key, ace, slot = identity
        row = self._row(int(key), ace, slot)
        if row is None:
            return
        row['pending'] = False
        if not confirmed or not self._valid(travelled_mm):
            row.update(known=False, reason='unconfirmed_motion')
        elif row.get('known'):
            distance = row['remaining_mm'] + travelled_mm
            if not self._valid(distance):
                row.update(known=False, reason='invalid_distance')
            else:
                # Preserve signed net travel across retries. Clamp only when
                # querying a retreat budget, not after every reverse movement.
                row['remaining_mm'] = distance
                row['samples'] = row.get('samples', 0) + 1
                if receipt is not None:
                    row['measurement_basis'] = receipt['basis']
                    row['load_receipt'] = copy.deepcopy(receipt)
        self._save()

    def unknown(self, head, ace, slot, reason):
        row = self._row(head, ace, slot)
        if row:
            row.update(known=False, pending=False, reason=reason)
            self._save()

    def finish_unload(self, head, ace, slot, cleared):
        row = self._row(head, ace, slot)
        if row and cleared:
            row['phase'] = 'parked' if row.get('known') and row['remaining_mm'] <= 2 else 'head_empty'
            self._save()

    def reset_route(self, ace, slot):
        self.forget_park(ace, slot)
        changed = False
        for key, row in list(self.records.items()):
            if (row.get('ace'), row.get('slot')) == (ace, slot):
                del self.records[key]
                changed = True
        for token, identity in list(self.active.items()):
            if identity[1:] == (ace, slot):
                del self.active[token]
        if changed:
            self._save()

    def status(self):
        return copy.deepcopy(self.records)
