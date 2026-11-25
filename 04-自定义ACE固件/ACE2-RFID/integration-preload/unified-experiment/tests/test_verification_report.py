import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verification_report import VerificationRun, validate_report


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.c'
        self.source.write_text('source before')
        self.report = self.root / 'output/runtime-verification.json'

    def passed(self):
        with VerificationRun(self.root, [self.source]) as run:
            run.checks.append(dict(name='example', passed=True))
            run.files = [self.source]

    def test_changed_source_and_missing_artifact_reject_old_pass(self):
        self.passed()
        validate_report(self.root, self.report)
        self.source.write_text('source after')
        with self.assertRaisesRegex(ValueError, 'stale'):
            validate_report(self.root, self.report)
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, 'stale'):
            validate_report(self.root, self.report)

    def test_failed_rerun_replaces_pass_before_first_check(self):
        self.passed()
        with self.assertRaisesRegex(RuntimeError, 'compiler failed'):
            with VerificationRun(self.root, [self.source]):
                self.assertEqual(json.loads(self.report.read_text())['status'], 'running')
                with self.assertRaises(ValueError):
                    validate_report(self.root, self.report)
                raise RuntimeError('compiler failed')
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')

    def test_interrupt_never_leaves_pass(self):
        self.passed()
        with self.assertRaises(KeyboardInterrupt):
            with VerificationRun(self.root, [self.source]):
                raise KeyboardInterrupt()
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')

    def test_concurrent_change_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
            with VerificationRun(self.root, [self.source]) as run:
                run.checks.append(dict(name='example', passed=True))
                self.source.write_text('changed during run')
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')

    def test_no_checks_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, 'did not finish'):
            with VerificationRun(self.root, [self.source]) as run:
                run.files = [self.source]

    def test_missing_declared_artifact_is_failure(self):
        self.passed()
        with self.assertRaises(FileNotFoundError):
            with VerificationRun(self.root, [self.source]) as run:
                run.checks.append(dict(name='example', passed=True))
                run.files = [self.source, self.root / 'missing-image']
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')

    def test_missing_input_invalidates_previous_pass(self):
        self.passed()
        with self.assertRaises(FileNotFoundError):
            with VerificationRun(self.root, [self.source, self.root / 'missing-input']):
                self.fail('Missing input must prevent checks from starting')
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
