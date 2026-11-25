"""Dependency-free adaptation of WatchingWatches/Filament-approximation.

Upstream: 5ad5b3a1ce291a3f736bd2aa09756de5d23659d6, Filament_approx.py.
Copyright (c) 2024 Benedikt Jansson. MIT; see licenses/Filament-approximation.txt.
Changes: circumference input, input validation, closed-form sum, structured output.
The upstream 0.9 safety multiplier and one-row deduction are preserved.
"""

import math


def estimate_remaining(circumference_mm, core_diameter_mm, winding_width_mm,
                       filament_diameter_mm=1.75, density_g_cm3=1.24):
    """Conservative layer model; geometry and density must be supplied.

    This estimates mass from a measured rotation, not from consumed length.
    Unknown partial layers and winding irregularity are not measured here.
    """
    values = (circumference_mm, core_diameter_mm, winding_width_mm,
              filament_diameter_mm, density_g_cm3)
    if any(not math.isfinite(x) or x <= 0 for x in values):
        raise ValueError('all dimensions and density must be finite and positive')
    core_radius = core_diameter_mm / 2
    radius = circumference_mm / (2 * math.pi)
    if radius <= core_radius:
        raise ValueError('measured winding radius must be outside the core')
    layers = math.floor((radius - core_radius) / filament_diameter_mm)
    turns_per_layer = math.floor(winding_width_mm / filament_diameter_mm - 1)
    if layers > 80 or turns_per_layer < 1:
        raise ValueError('spool outside the upstream layer model limits')
    # Sum_{k=1..n} 2*pi*(r_core + d*k - d/2)*rows, as in upstream.
    length_mm = 2 * math.pi * turns_per_layer * (
        layers * core_radius + filament_diameter_mm * layers ** 2 / 2)
    length_m = length_mm * .9 / 1000
    mass_g = length_m * math.pi * (filament_diameter_mm / 2) ** 2 * density_g_cm3
    return {'method': 'WatchingWatches/Filament-approximation',
            'source_commit': '5ad5b3a1ce291a3f736bd2aa09756de5d23659d6',
            'remaining_m': length_m, 'remaining_g': mass_g,
            'whole_layers': layers, 'turns_per_layer': turns_per_layer,
            'safety_multiplier': .9,
            'assumptions': {'circumference_mm': circumference_mm,
                            'core_diameter_mm': core_diameter_mm,
                            'winding_width_mm': winding_width_mm,
                            'filament_diameter_mm': filament_diameter_mm,
                            'density_g_cm3': density_g_cm3},
            'limitation': 'conservative model; partial layers, irregular winding and measurement errors are not resolved'}
