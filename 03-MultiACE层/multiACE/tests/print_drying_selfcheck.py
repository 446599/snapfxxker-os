#!/usr/bin/env python3
"""Lifecycle checks for print-scoped ACE drying in ace.py."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import types

logging.disable(logging.INFO)


def find_extras():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("MULTIACE_KLIPPY_EXTRAS"),
        os.path.join(os.path.dirname(here), "multiace", "klipper", "extras"),
    ]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "ace.py")):
            return path
    return None


EXTRAS = find_extras()
if EXTRAS is None:
    print("[FAIL] ace.py not found")
    sys.exit(2)

package = types.ModuleType("print_drying_selfcheck_extras")
package.__path__ = [EXTRAS]
sys.modules[package.__name__] = package
try:
    ace_module = importlib.import_module(package.__name__ + ".ace")
except Exception as exc:
    print("[FAIL] cannot import ace.py: %s" % exc)
    sys.exit(2)


class FakeReactor:
    NEVER = 1e18

    def __init__(self):
        self.now = 1000.0
        self.next_id = 0
        self.timers = {}

    def monotonic(self):
        return self.now

    def register_timer(self, callback, when):
        self.next_id += 1
        self.timers[self.next_id] = (callback, when)
        return self.next_id

    def unregister_timer(self, timer):
        self.timers.pop(timer, None)


class FakeGcmd:
    def __init__(self, enabled):
        self.enabled = enabled

    def get_int(self, name, default=None, minval=None, maxval=None):
        assert name == "ENABLE"
        assert minval <= self.enabled <= maxval
        return self.enabled


class FakePrinter:
    def __init__(self):
        self.state = "printing"

    def lookup_object(self, name, default=None):
        if name == "print_stats":
            return types.SimpleNamespace(state=self.state)
        return default


def make_ace():
    ace = ace_module.MultiAce.__new__(ace_module.MultiAce)
    ace.reactor = FakeReactor()
    ace.printer = FakePrinter()
    ace._print_dry_requested = False
    ace._print_dry_arm_timer = None
    ace._print_dry_aces = set()
    ace._print_dry_refresh_timer = None
    ace._head_source = {
        0: {"ace_index": 0, "slot": 0},
        1: {"ace_index": 0, "slot": 1},
        2: {"ace_index": 1, "slot": 0},
        3: {"ace_index": 2, "slot": 0},
    }
    ace.head_uses_ace = lambda head: head != 3
    ace._ace_devices = ["ace0", "ace1", "ace2"]
    ace._connected_per_ace = {0: True, 1: True, 2: False}
    ace._info_per_ace = {
        0: {"dryer_status": {"status": "stop"}},
        1: {"dryer_status": {"status": "keeping"}},
        2: {"dryer_status": {"status": "stop"}},
    }
    ace._auto_dry_started = set()
    ace.dry_auto_roll = False
    ace.sent = []
    ace.exhaust_open = []
    ace.exhaust_close = []
    ace.errors = []
    ace._schedule_dry_exhaust_open = lambda idx, why: ace.exhaust_open.append((idx, why))
    ace._close_dry_exhaust = lambda idx, why: ace.exhaust_close.append((idx, why))
    ace.log_error = ace.errors.append
    ace._t = lambda key, **params: key.format(**params)

    def send_request_to(idx, request, callback=None):
        ace.sent.append((idx, request))
        if callback:
            callback(ace, {"code": 0})

    ace.send_request_to = send_request_to
    return ace


def check(name, condition):
    print("[%s] %s" % ("ok" if condition else "FAIL", name))
    return condition


checks = []
ace = make_ace()
ace.cmd_ACE_PRINT_DRYING_CONTROL(FakeGcmd(1))
arm_timer = ace._print_dry_arm_timer
checks.append(check("enable arms one next-print request", ace._print_dry_requested and arm_timer))
ace._expire_print_dry_arm(ace.reactor.now + 600)
checks.append(check("unused request expires", not ace._print_dry_requested))

ace = make_ace()
ace.cmd_ACE_PRINT_DRYING_CONTROL(FakeGcmd(1))
checks.append(check("mapped connected ACEs are deduplicated", ace._print_drying_targets() == [0, 1]))
ace._start_print_drying()
checks.append(check("starts only an idle mapped ACE at 55C", len(ace.sent) == 1
                    and ace.sent[0][0] == 0
                    and ace.sent[0][1]["params"]["temp"] == 55
                    and ace.sent[0][1]["params"]["duration"] == 600))
checks.append(check("existing drying cycle is not taken over", ace._print_dry_aces == {0}))
checks.append(check("long print refresh timer is armed", ace._print_dry_refresh_timer is not None))

refresh_at = ace.reactor.timers[ace._print_dry_refresh_timer][1]
next_at = ace._refresh_print_drying(refresh_at)
checks.append(check("drying is renewed while printing", len(ace.sent) == 2
                    and next_at == refresh_at + ace_module.PRINT_DRYING_REFRESH_SECONDS))
refresh_timer = ace._print_dry_refresh_timer
ace._stop_print_drying()
checks.append(check("print end stops only the cycle started by this feature",
                    ace.sent[-1][1]["method"] == "drying_stop"
                    and ace.sent[-1][0] == 0
                    and ace.exhaust_close == [(0, "print ended")]
                    and not ace._print_dry_aces))
checks.append(check("print end cancels refresh timer",
                    ace._print_dry_refresh_timer is None
                    and refresh_timer not in ace.reactor.timers))

sys.exit(0 if all(checks) else 1)
