import atexit, importlib.util, pathlib, sys, tarfile, tempfile, types, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = (
    REPO
    / "overlays/233-dev.2-perms.1/root/usr/local/share/233-firmware/multiace-managed-1.27b-233.20261111-single.5.tar.gz"
)
TMP = tempfile.TemporaryDirectory(prefix="manual-return-package-")
atexit.register(TMP.cleanup)
ROOT = pathlib.Path(TMP.name)
with tarfile.open(ARCHIVE) as archive:
    for member in archive:
        if (
            member.isfile()
            and "/klipper/extras/" in member.name
            and member.name.endswith(".py")
        ):
            (ROOT / member.name.rsplit("/", 1)[-1]).write_bytes(
                archive.extractfile(member).read()
            )
SOURCE = ROOT / "ace_rc522.py"
spec = importlib.util.spec_from_file_location("manual_return_candidate", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Test(unittest.TestCase):
    def reader(self, reset=True):
        clock = types.SimpleNamespace(value=0.0)
        clock.monotonic = lambda: clock.value
        r = mod.AceTagReader(types.SimpleNamespace(reactor=clock))
        r._target_motion_allowed = lambda *a: True
        r._wait_motion_ready = lambda *a: True
        r._neighbour_signature = lambda *a: ("ready", 1, 1, 1, True)
        r._rc = lambda *a: 1
        r._pause = lambda seconds: setattr(clock, "value", clock.value + seconds)
        r._remember_encoder_direction = lambda *a: None
        r.last = (-30, {"steps": 376, "length": 30, "_decoder_present": True})
        r.initial = False
        r.reset = reset
        r.old_frames = 0
        r.calls = []
        r.feeds = iter([-11, -11, -5, -5, -2])
        r.reverse = iter([36])

        def fresh(*args):
            if r.initial:
                if r.old_frames > 0:
                    r.old_frames -= 1
                    v, row = r.previous
                elif r.reset:
                    r.initial = False
                    v, row = (0, {"steps": 7, "length": 0, "_decoder_present": False})
                else:
                    v, row = r.previous
            else:
                v, row = r.last
            r._encoder_diagnostic = {"row": dict(row)}
            return v

        def move(idx, slot, length, mode, respond):
            r.calls.append((length, mode))
            r.previous = r.last
            r.initial = True
            raw = next(r.reverse) if mode else next(r.feeds)
            r.last = (
                raw,
                {
                    "steps": int(length * 12.35) + 6,
                    "length": length,
                    "_decoder_present": True,
                },
            )
            key = (idx, slot)
            r._motion_ledger[key] = r._motion_ledger.get(key, 0) + (
                -length if mode else length
            )
            return True

        r._fresh_encoder = fresh
        r._move = move
        return r

    def test_full_reader_observed_residual_is_closed_loop(self):
        r = self.reader()
        messages = []
        r.read_slot(1, 0, messages.append, max_mm=50)
        self.assertEqual(r.calls, [(20, 0), (20, 0), (10, 0), (27, 1), (9, 0), (4, 0)])
        self.assertEqual(r._encoder_ledger[(1, 0)], 2)
        self.assertTrue(any("relative encoder return confirmed" in x for x in messages))

    def test_zero_baseline_first_post_ack_sample_already_moving(self):
        r = self.reader()
        r.last = (0, {"steps": 7, "length": 0, "_decoder_present": False})

        def fresh(*args):
            if r.initial:
                r.initial = False
                v = -1 if r.last[0] < 0 else 1
                row = {"steps": 20, "length": 1, "_decoder_present": True}
            else:
                v, row = r.last
            r._encoder_diagnostic = {"row": dict(row)}
            return v

        r._fresh_encoder = fresh
        r.read_slot(1, 0, lambda x: None, max_mm=50)
        self.assertLessEqual(abs(r._encoder_ledger[(1, 0)]), 2)

    def test_last_leg_settles_after_one_changing_terminal(self):
        r = self.reader()
        fresh = r._fresh_encoder
        seen = [False]

        def varying(*args):
            was_initial = r.initial
            v = fresh(*args)
            if len(r.calls) == 3 and not was_initial and not seen[0]:
                seen[0] = True
                r._encoder_diagnostic["row"]["steps"] -= 2
                r._encoder_diagnostic["row"]["length"] -= 1
                return v - 1
            return v

        r._fresh_encoder = varying
        r.read_slot(1, 0, lambda x: None, max_mm=50)
        self.assertTrue(seen[0])
        self.assertLessEqual(abs(r._encoder_ledger[(1, 0)]), 2)

    def test_continuous_terminal_change_never_claims_success(self):
        r = self.reader()
        fresh = r._fresh_encoder
        count = [0]

        def varying(*args):
            was_initial = r.initial
            v = fresh(*args)
            if len(r.calls) == 3 and not was_initial:
                count[0] += 1
                r._encoder_diagnostic["row"]["steps"] += count[0]
            return v

        r._fresh_encoder = varying
        with self.assertRaises(mod.MotionUncertain):
            r.read_slot(1, 0, lambda x: None, max_mm=50)
        self.assertEqual(count[0], 12)
        self.assertEqual(r.calls, [(20, 0), (20, 0), (10, 0)])

    def test_untrusted_terminal_raises_without_assuming_stability(self):
        r = self.reader()
        fresh = r._fresh_encoder

        def bad(*args):
            was_initial = r.initial
            v = fresh(*args)
            if len(r.calls) == 3 and not was_initial:
                r._encoder_diagnostic["row"]["_decoder_present"] = False
            return v

        r._fresh_encoder = bad
        with self.assertRaises(mod.MotionUncertain):
            r.read_slot(1, 0, lambda x: None, max_mm=50)
        self.assertEqual(r.calls, [(20, 0), (20, 0), (10, 0)])

    def test_stability_deadline_uses_short_ready_timeout(self):
        r = self.reader()
        clock = r.ace.reactor
        timeouts = []
        counter = [0]
        r._encoder_diagnostic = {
            "row": {"steps": 100, "length": 9, "_decoder_present": True}
        }

        def ready(idx, slot, timeout):
            timeouts.append(timeout)
            clock.value += timeout
            return True

        def fresh(*args):
            counter[0] += 1
            r._encoder_diagnostic = {
                "row": {
                    "steps": 100 + counter[0],
                    "length": 9,
                    "_decoder_present": True,
                }
            }
            return -10

        r._wait_motion_ready = ready
        r._fresh_encoder = fresh
        with self.assertRaises(mod.MotionUncertain):
            r._manual_stable_terminal(1, 0, 10, -10)
        self.assertTrue(timeouts)
        self.assertLessEqual(max(timeouts), 0.5)
        self.assertLessEqual(clock.value, 6.0)

    def test_inflight_reply_past_deadline_is_rejected(self):
        r = self.reader()
        clock = r.ace.reactor
        r._encoder_diagnostic = {
            "row": {"steps": 100, "length": 9, "_decoder_present": True}
        }

        def slow(idx, slot, timeout):
            clock.value += 6.5
            return True

        r._wait_motion_ready = slow
        with self.assertRaisesRegex(mod.MotionUncertain, "timeout"):
            r._manual_stable_terminal(1, 0, 10, -10)

    def test_no_motion_read_failure_needs_no_terminal(self):
        r = self.reader()
        r._rc = lambda *a: None
        messages = []
        r.read_slot(1, 0, messages.append, max_mm=50)
        self.assertFalse(r.calls)
        self.assertFalse(r._motion_ledger)
        self.assertTrue(any("missing or ambiguous" in x for x in messages))

    def test_no_motion_card_budget_refusal_needs_no_terminal(self):
        r = self.reader()
        r._rc = lambda *a: mod.SELECT_OK
        r._observed_uid = lambda *a: "1234"
        messages = []
        r.read_slot(1, 0, messages.append, max_mm=50)
        self.assertFalse(r.calls)
        self.assertFalse(r._motion_ledger)
        self.assertTrue(any("insufficient movement budget" in x for x in messages))

    def test_delayed_reset_after_ack_is_observed(self):
        r = self.reader()
        r.old_frames = 3
        r.read_slot(1, 0, lambda x: None, max_mm=50)
        self.assertLessEqual(abs(r._encoder_ledger[(1, 0)]), 2)

    def test_no_reset_refuses_and_never_guesses_return(self):
        r = self.reader(reset=False)
        with self.assertRaises(mod.MotionUncertain):
            r.read_slot(1, 0, lambda x: None, max_mm=50)
        self.assertEqual(r.calls, [(20, 0)])
        self.assertIn("reset", r._motion_uncertain)


if __name__ == "__main__":
    unittest.main(verbosity=2)
