# 1902.14 phase 1: park return candidate

Base: dc3a16780ecb2a6e8aa810e4a577572186b46d71. No ACE MCU changes.
Batch head unload and head portion of eject require a verified per-hardware-UID,
slot/head RETRACT_LENGTH; never fall back to 1950 mm. After head clear, fresh
native telemetry must show inlet present and lane ready, or mark recovery.
The separate native parallel eject mechanism is unchanged.

Commands (zero-based; no motion from these helpers):
- ACE_PARK_REFERENCE ACE=0 SLOT=0 CONFIRM_PARKED=1: capture the stationary
  encoder reference ONLY when the filament tip is physically inside ACE.
- ACE_PARK_MEASURE ACE=0 SLOT=0: after the existing supervised SLOT-scope
  calibration reaches its toolhead sensor, measure native encoder distance.
  Proposal only, not an authorized thermal unload distance.
- ACE_PARK_RECORD ACE=0 SLOT=0 RETRACT_LENGTH=<verified_mm> CONFIRM_VERIFIED=1:
  only after a physically verified actual full return into ACE. No guessing.
- ACE_PARK_FORGET ACE=0 SLOT=0: invalidate after changing the tube or splitter.

No route is pre-certified. The 1624/1655 runout readings and 1950 default are
not park calibration. Automated motion/thermal coordinate compensation,
splitter detection, direct non-batch unload and metadata retention on runout
are follow-up work. Do not install as production before actual 16-route
commissioning. CI validates software composition, not hardware.
