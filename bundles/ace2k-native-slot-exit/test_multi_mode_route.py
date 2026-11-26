import itertools
import unittest
from multi_mode_route import ExitRoute, multi_exit_route


class MultiModeRouteTests(unittest.TestCase):
    def test_all_sixteen_map_to_their_own_slot_number_not_ace(self):
        for ace, slot in itertools.product(range(4), repeat=2):
            with self.subTest(ace=ace, slot=slot):
                route = multi_exit_route('multi', ace, slot)
                self.assertEqual(route.head, slot)
                self.assertTrue(all(route.blocks_motion(a, slot) for a in range(4)))
                self.assertTrue(route.blocks_motion(ace, slot ^ 1))
                self.assertEqual(sum(mask.bit_count() for mask in route.masks), 5)

    def test_every_ordinary_motion_route(self):
        for ace, slot, a, s in itertools.product(range(4), repeat=4):
            expected = s == slot or (a == ace and s == (slot ^ 1))
            self.assertEqual(ExitRoute(ace, slot).blocks_motion(a,s), expected)

    def test_every_pair_of_exit_plans(self):
        for ace, slot, a, s in itertools.product(range(4), repeat=4):
            first, second = ExitRoute(ace,slot), ExitRoute(a,s)
            # Even different heads in the same reader pair overlap because
            # each needs the other's selected-unit peer to stay still.
            expected = slot // 2 == s // 2
            self.assertEqual(first.conflicts(second), expected)
            self.assertEqual(second.conflicts(first), expected)

    def test_actual_source_never_inferred_from_head(self):
        for ace, slot in itertools.product(range(4), repeat=2):
            route = ExitRoute(ace,slot)
            self.assertEqual(route.source_state({slot: {'ace_index': ace, 'slot': slot}}, True), 'selected_head_loaded')
            self.assertEqual(route.source_state({slot: {'ace_index': (ace+1)%4, 'slot': slot}}, True), 'other_source')
            self.assertEqual(route.source_state({slot: None}, False), 'unknown')
            self.assertEqual(route.source_state({slot: {'ace_index': ace, 'slot': slot}}, False), 'unknown')
            self.assertEqual(route.source_state({slot: {'ace_index': ace, 'slot': (slot+1)%4}}, True), 'unknown')
            self.assertEqual(route.source_state({slot: {'ace_index': ace, 'slot': slot, 'load_failed': True}}, True), 'unknown')

    def test_missing_evidence_and_wrong_mode(self):
        for mode in ('head','normal',None):
            with self.assertRaises(ValueError):
                multi_exit_route(mode,0,0)
        for bad in (True,4,-1,'0',None):
            with self.assertRaises(ValueError):
                ExitRoute(bad,0)
        self.assertEqual(ExitRoute(2,1).source_state({}, True),'unknown')
        self.assertEqual(ExitRoute(2,1).source_state({1:None},None),'unknown')


if __name__ == '__main__':
    unittest.main()
