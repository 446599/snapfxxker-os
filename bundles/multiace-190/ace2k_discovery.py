"""Read-only identification of the shared ACE 2 USB adapter."""
import logging
import os
import threading
import time


def probe_shared_ace2(path, v2, ace2k):
    """Return (codec, verified reply), or (None, last probe reply)."""
    for codec in (v2, ace2k):
        if v2.serial_port_in_use(path):
            return None, {'category': 'port_in_use'}
        result = codec.probe_device(path, query_timeout=0.35)
        if result.get('ok') and codec.uid_key(result.get('uid') or ()):
            return codec, result
        # Do not try a second protocol after access errors or a valid V2
        # response that lacks identity. Only silence/foreign framing is a
        # reason to try the other read-only handshake.
        if result.get('category') not in ('no_response', 'invalid_response'):
            return None, result
    return None, result


class SharedAce2Discovery:
    """Bounded worker, one probe budget, no port claim without a real UID."""
    def __init__(self):
        self.cache = {}
        self.pending = {}

    @staticmethod
    def _token(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_dev, st.st_ino, st.st_rdev, st.st_ctime_ns

    def forget(self, path):
        self.cache.pop(os.path.realpath(path), None)

    def identify(self, path, reactor, budget, v2, ace2k):
        real = os.path.realpath(path)
        token = self._token(path)
        if token is None:
            self.cache.pop(real, None)
            return None
        cached = self.cache.get(real)
        if cached and cached[0] == token and time.monotonic() < cached[1]:
            return None if v2.serial_port_in_use(path) else cached[2]
        job = self.pending.get(real)
        if job is not None:
            if not job['done'].is_set():
                return None
        else:
            if budget[0] <= 0 or v2.serial_port_in_use(path):
                return None
            budget[0] -= 1
            job = {'token': token, 'done': threading.Event(), 'result': None}
            self.pending[real] = job

            def worker():
                try:
                    job['result'] = probe_shared_ace2(path, v2, ace2k)
                except Exception as exc:
                    job['result'] = (None, {'category': 'serial_error',
                                             'error': str(exc)})
                finally:
                    job['done'].set()

            threading.Thread(target=worker, daemon=True,
                             name='multiace-shared-ace2-probe').start()
            deadline = reactor.monotonic() + 5.5
            while not job['done'].is_set():
                now = reactor.monotonic()
                if now >= deadline:
                    return None  # keep ownership until the worker closes
                reactor.pause(min(deadline, now + 0.025))
        self.pending.pop(real, None)
        if job['token'] != self._token(path) or job['token'] != token:
            return None
        codec, result = job['result']
        match = (codec, result) if codec is not None else None
        self.cache[real] = (token, time.monotonic() + (30 if match else 15), match)
        logging.info('[multiACE] shared ACE 2 probe %s: %s (%s)', path,
                     codec.NAME if codec else 'unverified', result.get('category'))
        return None if v2.serial_port_in_use(path) else match
