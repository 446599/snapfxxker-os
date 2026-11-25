import tempfile
import unittest
from pathlib import Path

from multiace.tools import post_process_virtual_toolheads as pp


START = """T4
M104 T4 S130
G28 Z
BED_MESH_CALIBRATE
M109 T4 S220
SM_PRINT_PREEXTRUDE_FILAMENT INDEX=4

; draw the starting line
G1 X10 Y10 E5 F600
;LAYER_CHANGE
G1 X20 Y20 E1
"""
SECOND_TOOL = """; Change Tool 4 -> Tool 1
T1
G1 X30 Y30 E1
"""


class StartAutoLoadTests(unittest.TestCase):
    def process(self, gcode, streaming=True):
        if not streaming:
            rewritten, *_ = pp.rewrite(gcode, auto_load=True)
            return pp.inject_auto_load(rewritten)[0]
        with tempfile.TemporaryDirectory() as tmp:
            src, rewritten, output = (Path(tmp) / name for name in ('src', 'rewrite', 'out'))
            src.write_text(gcode)
            pp.rewrite_to_file(src, rewritten)
            pp.inject_auto_load_to_file(rewritten, output)
            return output.read_text()

    def test_loads_the_assigned_spool_when_no_tool_change_exists(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                out = self.process(START, streaming)
                load = 'ACE_SWAP_HEAD HEAD=0 ACE=1 SLOT=0 INITIAL=1'
                self.assertEqual(out.count(load), 1)
                self.assertNotIn('ACE_SWAP_HEAD HEAD=0 ACE=0 SLOT=0 INITIAL=1', out)
                self.assertLess(out.index('BED_MESH_CALIBRATE'), out.index(load))
                self.assertLess(out.index(load), out.index('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0'))

    def test_multi_primes_after_all_initial_loads(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                out = self.process(START + SECOND_TOOL, streaming)
                self.assertIn('M104 T0 S130\nG28 Z\nBED_MESH_CALIBRATE', out)
                self.assertLess(out.index('ACE_SWAP_HEAD HEAD=1 ACE=0 SLOT=1 INITIAL=1'),
                                out.index('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0'))
                self.assertEqual(out.count('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0'), 1)
                self.assertLess(out.index('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0'),
                                out.index('G1 X10 Y10 E5'))

    def test_no_prime_is_added_to_a_file_without_one(self):
        source = START.replace('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=4\n', '')
        out = self.process(source)
        self.assertIn('ACE_SWAP_HEAD HEAD=0 ACE=1 SLOT=0 INITIAL=1', out)
        self.assertNotIn('SM_PRINT_PREEXTRUDE_FILAMENT', out)

    def test_prime_parameters_and_order_survive_reinjection(self):
        rewritten = START.replace('T4', 'T0').replace(
            'INDEX=4', 'INDEX=0 FORCE=1 TEMP=223')
        rewritten = rewritten.replace('T0\n', 'T0\n; multiACE start tool: HEAD=0 ACE=1 SLOT=0\n', 1)
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = (Path(tmp) / name for name in ('a', 'b', 'c'))
            a.write_text(rewritten)
            pp.inject_auto_load_to_file(a, b)
            pp.inject_auto_load_to_file(b, c)
            self.assertEqual(b.read_text(), c.read_text())
            self.assertIn('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0 FORCE=1 TEMP=223', c.read_text())

    def test_explicit_assignment_loads_single_ace_tool_but_leaves_manual_tool_alone(self):
        for kind in ('ace', 'pin'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                src, rewritten, out = (Path(tmp) / name for name in ('a', 'b', 'c'))
                src.write_text(START)
                assignment = {4: {'kind': kind, 'head': 2, 'ace': 3, 'slot': 2}}
                pp.rewrite_head_mode_to_file(src, rewritten, assignment)
                count = pp.inject_auto_load_to_file(rewritten, out, only_heads={2})
                if kind == 'ace':
                    lines = out.read_text().splitlines()
                    load = lines.index('ACE_SWAP_HEAD HEAD=2 ACE=3 SLOT=2 INITIAL=1')
                    prime = lines.index('SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1')
                    self.assertEqual(count, 1)
                    self.assertLess(load, prime)
                else:
                    self.assertEqual(count, 0)
                    self.assertNotIn('ACE_SWAP_HEAD', out.read_text())
