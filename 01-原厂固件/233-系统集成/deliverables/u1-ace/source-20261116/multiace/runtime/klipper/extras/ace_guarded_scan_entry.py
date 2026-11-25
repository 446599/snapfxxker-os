"""Opt-in ACE_TAG_READ entry. No evidence can be supplied via G-code flags.

A validated runtime provider must be installed separately; currently none is
provided. Missing provider blocks before any request. Existing command defaults
are unchanged. This module does not turn historical pipe length into clearance.
"""
from .ace_rc522 import AceTagReader
from .ace_guarded_scan import ScanPlan


def start_guarded_command(ace, gcmd):
    if gcmd.get_int('CANCEL', 0, minval=0, maxval=1):
        sequence = getattr(ace, '_guarded_scan_sequence', None)
        if sequence is not None:
            sequence.cancel()
            gcmd.respond_info('Guarded scan cancellation requested; STOP acknowledgment is not physical standstill')
        else:
            gcmd.respond_info('No guarded scan sequence exists')
        return sequence
    idx = gcmd.get_int('ACE', ace._active_device_index, minval=0, maxval=3)
    slot = gcmd.get_int('SLOT', minval=0, maxval=3)
    pair = gcmd.get_int('PAIR', 0, minval=0, maxval=1)
    cap = gcmd.get_int('MAX_MM', 600, minval=1, maxval=600)
    provider = getattr(ace, '_guarded_scan_provider', None)
    if provider is None or not callable(getattr(provider, 'prepare', None)):
        raise gcmd.error('Guarded scan unavailable: current clearance, head pin health, '
                         'physical mapping, buffer polarity and transport admission are not validated')
    if getattr(ace, '_tag_read_busy', False):
        raise gcmd.error('A tag read or guarded scan is already running')
    slots = (slot & ~1, slot | 1) if pair else (slot,)
    sequence = GuardedScanSequence(ace, provider, idx, slots, cap)
    ace._guarded_scan_sequence = sequence
    sequence.start()
    if sequence.current is None or sequence.current.state == 'blocked':
        raise gcmd.error(sequence.reason or 'Guarded scan preflight blocked')
    gcmd.respond_info('Guarded bounded scan preflight started; slot binding remains disabled; RF availability follows validated plan')
    return sequence


class GuardedScanSequence:
    def __init__(self, ace, provider, idx, slots, cap):
        self.ace, self.provider, self.idx = ace, provider, idx
        self.slots, self.cap = tuple(slots), cap
        self.current = None
        self.position = 0
        self.reason = None
        self.results = []
        self.cancelled = False

    def start(self):
        self.ace._tag_read_busy = True
        self._start_one()
        if self.current is not None and self.current.state not in self.current.TERMINAL:
            self.ace.reactor.register_timer(self._watch, self.ace.reactor.monotonic()+.05)
        else:
            self.ace._tag_read_busy = False

    def _start_one(self):
        slot = self.slots[self.position]
        try:
            supplied = self.provider.prepare(self.idx, slot, self.cap, self.ace.reactor.monotonic())
            if not isinstance(supplied, dict):
                raise ValueError('validated provider returned no plan')
            plan = supplied.get('plan')
            if (not isinstance(plan, ScanPlan) or plan.ace_index != self.idx
                    or plan.slot != slot or plan.length_mm > self.cap):
                raise ValueError('validated plan does not match command target and cap')
            reader = AceTagReader(self.ace)
            self.current = reader.new_guarded_continuous_scan(plan,
                supplied.get('health_provider'), supplied.get('mapping_provider'),
                supplied.get('session_check'), supplied.get('rf_context_provider'))
            self.current.start()
            if self.current.state == 'blocked':
                self.reason = self.current.reason
        except Exception as exc:
            self.reason = str(exc)
            self.current = None

    def _watch(self, eventtime):
        if self.current is None:
            self.ace._tag_read_busy = False
            return self.ace.reactor.NEVER
        if self.current.state not in self.current.TERMINAL:
            return eventtime+.05
        self.results.append(self.current.report())
        # Only a normal finite-command endpoint may lead to a fresh second
        # preflight. Any sensor event, cancellation or uncertainty ends pair.
        normal = (self.current.state == 'telemetry_rest'
                  and self.current.reason == 'bounded motion ended; verify telemetry rest')
        # A bounded endpoint is not a full revolution. Preserve unknown
        # classification, but an authorized paired scan may independently
        # preflight the next slot after a normal finite endpoint only.
        self.position += 1
        if normal and not self.cancelled and self.position < len(self.slots):
            self._start_one()
            if self.current is not None:
                return eventtime+.05
        self.ace._tag_read_busy = False
        self.reason = self.reason or (self.current.reason if self.current else 'provider blocked next slot')
        return self.ace.reactor.NEVER

    def cancel(self):
        self.cancelled = True
        if self.current is not None:
            self.current.cancel()
