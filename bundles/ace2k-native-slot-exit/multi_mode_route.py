"""Explicit U1 Multi-mode topology, NOT a proof of filament position.

Four ACEs, four slots each, slot N reaches head N across all units.
Indices are zero based. Reserve the target head's path on every unit and
both lanes of the target unit's RFID reader. Never derive the active source
ACE from the head index. This module plans resources; a host coordinator
must atomically hold them across all motion entrances before sending a frame.
"""
from dataclasses import dataclass


def valid_index(value):
    return type(value) is int and 0 <= value < 4


@dataclass(frozen=True)
class ExitRoute:
    ace: int
    slot: int

    def __post_init__(self):
        if not valid_index(self.ace) or not valid_index(self.slot):
            raise ValueError('ACE and SLOT must be 0..3')

    @property
    def head(self):
        return self.slot

    @property
    def masks(self):
        common = 1 << self.slot
        reader = 3 << (2 * (self.slot // 2))
        return tuple(reader if idx == self.ace else common for idx in range(4))

    def blocks_motion(self, ace, slot):
        if not valid_index(ace) or not valid_index(slot):
            raise ValueError('unknown motion route')
        return bool(self.masks[ace] & (1 << slot))

    def conflicts(self, other):
        if not isinstance(other, ExitRoute):
            raise TypeError('explicit exit route required')
        return any(a & b for a, b in zip(self.masks, other.masks))

    def source_state(self, head_source, detected):
        """Describe recorded source/sensor, not whether a tube is parked.

        Exact source plus positive sensor permits the *head-unload planning*
        branch only. No source, even with an empty sensor, never authorizes a
        native exit. A different source must never be unloaded for this task.
        Caller must revalidate actual UID, route, occupancy and idle state.
        """
        if not isinstance(head_source, dict) or type(detected) is not bool:
            return 'unknown'
        if self.head not in head_source:
            return 'unknown'
        src = head_source[self.head]
        if src is None:
            return 'unknown'  # clear sensor is not a parking certificate
        if not isinstance(src, dict):
            return 'unknown'
        ace, slot = src.get('ace_index'), src.get('slot')
        if not valid_index(ace) or not valid_index(slot) or slot != self.head:
            return 'unknown'
        if ace != self.ace:
            return 'other_source' if detected else 'unknown'
        if src.get('load_failed') or not detected:
            return 'unknown'
        return 'selected_head_loaded'


def multi_exit_route(mode, ace, slot):
    if mode != 'multi':
        raise ValueError('this topology is only valid in Multi mode')
    return ExitRoute(ace, slot)
