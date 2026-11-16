"""Candidate for per-command ACE encoder accounting; no device access."""

class MeasurementError(ValueError):
    pass


def signed_decoder(raw):
    if type(raw) is not int or not 0 <= raw < 1 << 64:
        raise MeasurementError('invalid decoder')
    return raw - (1 << 64) if raw >= (1 << 63) else raw


class Measurement:
    """Strict: observe explicit per-command zero then signed travel and stable end.

    Counter continuity and device/command identity must be guaranteed by caller.
    This class does not command movement and is not a deployment-ready extension.
    """
    def __init__(self):
        self.net_mm = 0
        self.current = None
        self.valid = True

    def begin(self, direction):
        if not self.valid or self.current is not None or direction not in (1, -1):
            raise MeasurementError('invalid segment start')
        self.current = {'direction': direction, 'saw_zero': False, 'last': None,
                        'stable': 0, 'moved': False}

    def sample(self, raw, valid_frame, valid_row, present):
        s = self.current
        if s is None or not self.valid or valid_frame is not True or valid_row is not True or type(present) is not bool:
            self.valid = False
            raise MeasurementError('invalid sample')
        value = signed_decoder(raw)
        if not present and value != 0:
            self.valid = False
            raise MeasurementError('invalid default zero')
        if not s['saw_zero']:
            if value != 0:
                self.valid = False
                raise MeasurementError('unobserved counter reset')
            s['saw_zero'] = True
        elif value * s['direction'] < 0 or (s['last'] is not None and (value - s['last']) * s['direction'] < 0):
            self.valid = False
            raise MeasurementError('counter reversed')
        if value:
            if not present:
                self.valid = False
                raise MeasurementError('missing moving decoder')
            s['moved'] = True
        s['stable'] = s['stable'] + 1 if s['last'] == value else 1
        s['last'] = value

    def finish(self, ready, stopped):
        s = self.current
        if not self.valid or s is None or ready is not True or stopped is not True or not s['moved'] or s['stable'] < 3:
            self.valid = False
            raise MeasurementError('segment end unconfirmed')
        self.net_mm += s['last']
        self.current = None
        return self.net_mm

    def retract_budget(self, reserve=100):
        if not self.valid or self.current is not None or type(reserve) is not int or reserve < 0:
            raise MeasurementError('measurement incomplete')
        return max(0, self.net_mm - reserve)

class ForwardLowerBound:
    """One isolated, acknowledged forward command; explicit samples only.

    Caller must reject concurrent moves and transport changes, identify fresh
    motion-start samples, poll the sensor independently and STOP immediately.
    A stale pre-command counter must never be passed here.
    """
    def __init__(self):
        self.lower_bound_mm = 0
        self.moving_observed = False
        self.stop_requested = False
        self.valid = True
        self.after_stop_reset = False

    def sample(self, raw, valid_frame, valid_row, present):
        if not self.valid or valid_frame is not True or valid_row is not True or type(present) is not bool:
            self.valid = False
            raise MeasurementError('invalid sample')
        value = signed_decoder(raw)
        if (not present and value != 0) or value < 0:
            self.valid = False
            raise MeasurementError('invalid forward decoder')
        if self.after_stop_reset and value != 0:
            self.valid = False
            raise MeasurementError('counter moved after reset')
        if value < self.lower_bound_mm:
            if not self.stop_requested or value != 0:
                self.valid = False
                raise MeasurementError('unexpected counter reset')
            self.after_stop_reset = True
        if value > 0:
            if not present:
                self.valid = False
                raise MeasurementError('missing decoder')
            self.moving_observed = True
            self.lower_bound_mm = max(self.lower_bound_mm, value)

    def stop(self):
        self.stop_requested = True

    def finish(self, stop_confirmed, ready):
        if not self.valid or not self.stop_requested or stop_confirmed is not True or ready is not True or not self.moving_observed:
            self.valid = False
            raise MeasurementError('forward motion unconfirmed')
        return self.lower_bound_mm

class ForwardSpan:
    """Lower bound from first and last fresh samples of one observed move."""
    def __init__(self):
        self.first = None
        self.last = None
        self.valid = True
        self.count = 0

    def add(self, raw, *, frame_valid, row_valid, present, steps, length):
        if (not self.valid or frame_valid is not True or row_valid is not True
                or type(present) is not bool or type(steps) is not int
                or type(length) is not int or steps < 0 or length < 0):
            self.valid = False
            raise MeasurementError('invalid frame or motor telemetry')
        value = signed_decoder(raw)
        if value < 0 or (not present and value != 0):
            self.valid = False
            raise MeasurementError('invalid forward count')
        if not present:
            if self.last is not None:
                self.valid = False
                raise MeasurementError('forward counter reset')
            return
        if self.last is not None and value < self.last:
            self.valid = False
            raise MeasurementError('forward counter reset or reversed')
        if self.first is None:
            self.first = value
        self.last = value
        self.count += 1

    def lower_bound(self):
        if not self.valid or self.count < 2 or self.last <= self.first:
            raise MeasurementError('no verified forward progress')
        return self.last - self.first
