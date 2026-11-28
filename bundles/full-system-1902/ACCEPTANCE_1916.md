# 1902.16 acceptance-fix candidate

Base: successful 1902.15 unified web build e8968dfb9e612a43d19d2127b688022095946428, artifact 15225956077. The 1902.14 on-device report exposed P0 faults not tested by the original CI.

- Convert expected park G-code selection, timeout and storage errors to Klipper CommandError.
- Preserve the intended already-built 1902.14 screen binary; increment package identity and stamp the verified archive digest on successful screen install. Detect stale same-version installs and keep the previous screen until replacement succeeds.
- Adapt OTA idle checks to actual 1902.x native/batch task arrays and the provider's real motion/recovery status; retain legacy schema checks, heater, stock feeder, offline dependency and drying gates.
- Route ordinary ACE2K Multi head unload (direct and batch) through one verified length selection. Do not report completion or clear head mapping if fresh inlet evidence fails. Stock feeder and swap paths are not redesigned.

This candidate does NOT implement full closed-loop thermal unload, automatic motion calibration, splitter measurement, or preserving slot overrides during unintended runout. Existing uncertified routes remain blocked for long head unload. A sensor-present check alone cannot prove precise parking. Do not use guessed distances from prior runout logs. No MCU changes. Verify the actual installed screen hash, OTA guard, and physical routes before production use. Existing 1902.14/15 devices still have the old OTA gate until this upgrade is installed; do not bypass any active-operation protection.

Do not publish user machine identifiers or raw private field logs; tests use synthetic routes and status samples.
