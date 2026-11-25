"""Atomic, artifact-bound results for offline experiments; no device access."""
import hashlib
import json
import os
from pathlib import Path
import sys


def fingerprints(root, paths):
    return {os.path.relpath(p, root): {
        'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
        'bytes': p.stat().st_size,
    } for p in paths}


def validate_report(root, path):
    report = json.loads(path.read_text())
    if report.get('status') != 'passed' or not report.get('checks'):
        raise ValueError('No completed successful verification for these artifacts')
    if not all(c['passed'] for c in report['checks']) or not report.get('files'):
        raise ValueError('Incomplete or failed checks')
    for name, expected in report['files'].items():
        p = root / name
        if not p.is_file() or fingerprints(root, [p])[name] != expected:
            raise ValueError('Verification is stale: ' + name)
    return report


class VerificationRun:
    def __init__(self, root, inputs):
        self.root = root
        self.path = root / 'output/runtime-verification.json'
        self.inputs = list(inputs)
        self.before = None
        self.files = []
        self.checks = []
        self.tools = {}

    def write(self, status, error=None, files=None):
        data = dict(status=status, error=error, checks=self.checks,
                    files=files or {}, tools=self.tools,
                    scope='Host C doubles, sanitizers, ARM link and limited Thumb model only',
                    physical_proof=False, device_or_network_access=False)
        self.path.parent.mkdir(exist_ok=True)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        temporary.replace(self.path)

    def __enter__(self):
        # Invalidate old PASS before running any compiler or child process.
        self.write('running')
        try:
            self.before = fingerprints(self.root, self.inputs)
        except OSError as error:
            self.write('failed', str(error))
            raise
        return self

    def __exit__(self, kind, error, traceback):
        files = {}
        try:
            if self.before != fingerprints(self.root, self.inputs) and error is None:
                error = RuntimeError('Verification inputs changed during the run')
            files = fingerprints(self.root, self.files)
            if not files and error is None:
                error = RuntimeError('No verification artifacts declared')
        except OSError as missing:
            error = error or missing
        if error is None and (not self.checks or not all(c['passed'] for c in self.checks)):
            error = RuntimeError('Verification did not finish successfully')
        self.write('passed' if error is None else 'failed',
                   None if error is None else type(error).__name__ + ': ' + str(error), files)
        if error is not None and kind is None:
            raise error
        return False


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    try:
        result = validate_report(root, root / 'output/runtime-verification.json')
    except (OSError, ValueError, KeyError) as error:
        sys.exit(str(error))
    print(f"PASS: {len(result['checks'])} recorded steps match current files; offline only")
