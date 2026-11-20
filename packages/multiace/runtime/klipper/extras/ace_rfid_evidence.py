"""Pure analysis used by the live continuous RFID entry.

Motion, source leases and stopping remain in AceTagReader/SlotActions. A card
window is only a candidate; the caller must independently establish ownership.
No default spool geometry or guessed number of revolutions is supplied here.
"""
import math


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _uid(value):
    if (not isinstance(value, str) or len(value) not in (8, 14, 20)
            or any(char not in '0123456789abcdefABCDEF' for char in value)):
        return False
    return set(value.upper()) not in ({'0'}, {'F'})


def analyse_windows(samples):
    """Analyse forward-positive (position-before, position-after, UID) samples.

    None means an RF failure/collision, never absence. '' means a successful
    no-card query. Require two spatially distinct hits and two distinct absence
    observations between windows. A tag visible at the origin has no measured
    entry edge and is not used as a circumference anchor.
    """
    result = {'candidate': None, 'window': None, 'circumference_mm': None,
              'periods': 0, 'reason': 'insufficient_windows'}
    if not samples:
        return result
    previous = -1
    candidates = set()
    for sample in samples:
        if not isinstance(sample, (tuple, list)) or len(sample) != 3:
            result['reason'] = 'invalid_sample'; return result
        before, after, uid = sample
        if (not _number(before) or not _number(after) or before < 0
                or after < before or before < previous):
            result['reason'] = 'nonmonotonic_encoder'; return result
        if uid is None:
            result['reason'] = 'rfid_sample_unknown'; return result
        if uid != '':
            if not _uid(uid):
                result['reason'] = 'invalid_uid'; return result
            candidates.add(uid.upper())
        previous = after
    if len(candidates) != 1:
        result['reason'] = 'ambiguous_uid' if candidates else 'no_uid_in_scan'
        return result
    candidate = next(iter(candidates))
    result['candidate'] = candidate
    windows = []
    i = 0
    while i < len(samples):
        if samples[i][2] == '':
            i += 1; continue
        first = i
        while i + 1 < len(samples) and samples[i + 1][2] != '':
            i += 1
        last = i
        before_absent = []
        j = first - 1
        while j >= 0 and samples[j][2] == '':
            before_absent.append(j); j -= 1
        after_absent = []
        j = last + 1
        while j < len(samples) and samples[j][2] == '':
            after_absent.append(j); j += 1
        has_before = (len(before_absent) >= 2
                      and samples[before_absent[0]][0] > samples[before_absent[-1]][0])
        has_after = (len(after_absent) >= 2
                     and samples[after_absent[-1]][0] > samples[after_absent[0]][0])
        distinct_hits = last > first and samples[last][0] > samples[first][1]
        if distinct_hits and has_after and (has_before or first == 0):
            lower, upper = samples[first][1], samples[last][0]
            target = 0 if first == 0 else (lower + upper) / 2.0
            entry = ((samples[first - 1][0], samples[first][1]) if has_before else None)
            windows.append({'window': (candidate, target, samples[first][0], samples[last][1]),
                            'entry': entry})
        i += 1
    if not windows:
        result['reason'] = 'unconfirmed_entry_or_exit'; return result
    result['window'] = windows[0]['window']
    entries = [w['entry'] for w in windows if w['entry'] is not None]
    periods = []
    for previous, current in zip(entries, entries[1:]):
        low, high = current[0] - previous[1], current[1] - previous[0]
        if low <= 0:
            result['reason'] = 'overlapping_read_windows'; return result
        periods.append((low, high))
    if periods:
        low, high = min(p[0] for p in periods), max(p[1] for p in periods)
        result.update(circumference_mm={'min_mm': low, 'max_mm': high,
                                       'mid_mm': (low + high) / 2.0},
                      measurement_forward_mm={'min_mm': entries[0][0],
                                               'max_mm': entries[-1][1]},
                      periods=len(periods), reason='period_observed_sampling_not_calibrated')
    return result


def _interval(profile, name, maximum=None):
    value = profile.get(name)
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(not _number(v) for v in value)
            or value[0] <= 0 or value[1] < value[0]
            or (maximum is not None and value[1] > maximum)):
        raise ValueError('invalid calibrated profile: ' + name)
    return value


def validate_profile(profile):
    """Validate a measured spool/reader profile; provide no material defaults."""
    if not isinstance(profile, dict) or profile.get('calibrated') is not True:
        raise ValueError('profile must contain explicit calibration evidence')
    calibration_id = profile.get('calibration_id')
    if (not isinstance(calibration_id, str) or not calibration_id.strip()
            or len(calibration_id) > 128):
        raise ValueError('calibration_id is required')
    # Bounds reject corrupt/overflowing inputs. They are validation ceilings,
    # not assumptions about a spool's geometry or a material's density.
    core = _interval(profile, 'core_radius_mm', 1000)
    _interval(profile, 'wound_width_mm', 1000)
    _interval(profile, 'packing_fraction', 1)
    _interval(profile, 'density_g_cm3', 100)
    _interval(profile, 'filament_diameter_mm', 20)
    circumference = _interval(profile, 'circumference_mm', 20000)
    if circumference[1] < 2 * math.pi * core[0]:
        raise ValueError('circumference calibration is smaller than the core')
    spacing = profile.get('maximum_observation_spacing_mm')
    if not _number(spacing) or not 0 < spacing < circumference[0]:
        raise ValueError('reader observation spacing is required')
    device_uid = profile.get('reader_device_uid')
    if (not isinstance(device_uid, (list, tuple)) or len(device_uid) != 3
            or any(type(value) is not int or not 0 <= value < 1 << 32 for value in device_uid)):
        raise ValueError('reader_device_uid must identify the calibrated ACE')
    if type(profile.get('reader_pair')) is not int or profile['reader_pair'] not in (0, 1):
        raise ValueError('reader_pair must identify the calibrated antenna')
    return profile


def estimate_interval(evidence, profile):
    """Return a geometry interval only with explicitly calibrated parameters.

    A sampling calibration is needed to exclude skipped RFID windows; merely
    seeing one UID twice cannot prove exactly one revolution. Profiles are
    keyed to an already-attributed tag by the live caller.
    """
    bounds = evidence.get('circumference_mm')
    if not isinstance(bounds, dict):
        return {'state': 'unknown', 'reason': 'complete_turn_not_observed'}
    if not isinstance(profile, dict):
        return {'state': 'unknown', 'reason': 'spool_profile_missing'}
    if profile.get('calibrated') is not True or not isinstance(profile.get('calibration_id'), str) or not profile['calibration_id']:
        return {'state': 'unknown', 'reason': 'spool_profile_uncalibrated'}
    try:
        validate_profile(profile)
        core = _interval(profile, 'core_radius_mm')
        width = _interval(profile, 'wound_width_mm')
        packing = _interval(profile, 'packing_fraction', 1)
        density = _interval(profile, 'density_g_cm3')
        diameter = _interval(profile, 'filament_diameter_mm')
        valid_circumference = _interval(profile, 'circumference_mm')
        spacing = profile.get('maximum_observation_spacing_mm')
        if not _number(spacing) or spacing <= 0:
            raise ValueError('missing reader sampling calibration')
        measured_spacing = evidence.get('maximum_observation_spacing_mm')
        if not _number(measured_spacing) or measured_spacing > spacing:
            return {'state': 'unknown', 'reason': 'rfid_sampling_coverage_unconfirmed'}
        low, high = bounds.get('min_mm'), bounds.get('max_mm')
        if (not _number(low) or not _number(high) or not 0 < low <= high
                or low < valid_circumference[0] or high > valid_circumference[1]):
            return {'state': 'unknown', 'reason': 'circumference_outside_calibration'}
        radius = (low / (2 * math.pi), high / (2 * math.pi))
        if radius[1] < core[0]:
            return {'state': 'unknown', 'reason': 'circumference_smaller_than_core'}
        volume = (math.pi * width[0] * packing[0] * max(0, radius[0]**2 - core[1]**2),
                  math.pi * width[1] * packing[1] * max(0, radius[1]**2 - core[0]**2))
        mass = [volume[0] * density[0] / 1000, volume[1] * density[1] / 1000]
        position = evidence.get('measurement_forward_mm') or {}
        low_position, high_position = position.get('min_mm'), position.get('max_mm')
        residual = evidence.get('return_residual_mm')
        if (not _number(low_position) or not _number(high_position)
                or low_position < 0 or high_position < low_position or not _number(residual)):
            return {'state': 'unknown', 'reason': 'measurement_position_unconfirmed'}
        # The observed radius was measured during forward transport. Correct
        # the inventory interval to the actual measured return position.
        transported = (low_position - residual, high_position - residual)
        linear_density = (math.pi * diameter[0]**2 / 4 * density[0] / 1000,
                          math.pi * diameter[1]**2 / 4 * density[1] / 1000)
        corrections = [distance * mass_per_mm for distance in transported for mass_per_mm in linear_density]
        mass[0] = max(0, mass[0] + min(corrections))
        mass[1] = max(0, mass[1] + max(corrections))
        return {'state': 'estimate', 'reason': 'calibrated_geometric_interval',
                'remaining_g_interval': {'min_g': mass[0], 'max_g': mass[1]},
                'complete_turn_confirmed': True, 'calibration_id': profile['calibration_id'],
                'method': 'rotation_geometry_interval',
                'limitation': 'model interval, not a scale measurement; tube inventory is separate'}
    except (ValueError, TypeError, OverflowError):
        return {'state': 'unknown', 'reason': 'invalid_spool_profile'}
