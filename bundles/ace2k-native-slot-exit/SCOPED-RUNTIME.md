# Scoped runtime changes, 1902.8 staging

Patch actual 1.90.2 engine _feed and _retract at their explicit ACE/slot
wait sites; use the native driver's genuine get_lane_state plus its local
queue/ownership indication. A different lane moving does not force a
device-ready wait or reconnect. A target error, connection replacement,
or timeout is still reported through the existing print-aware error path.
Do not infer readiness from a cached whole-device flag. Other callers
whose lane/route is not explicit retain their existing waits. Legacy
protocols retain device-level waiting.

Native dryer start/stop use the explicitly selected unit without a feed
ready wait or ACE_SWITCH, keep original thermal validation/driver and
exhaust/auto-dry behavior. Homing wait remains for legacy protocols.
Native dryer requests no longer stamp motion busy. This is not a change
to the drying algorithm.

Ten isolated method tests passed on the uploaded exact 1902.7 ace.py and
unchanged driver, and CI will recheck the original field archive. The
patch has not yet been invoked by the release composer. Batch endpoints,
durable cross-motion ownership, full screen binary and real field
verification remain release gates. No firmware artifact or device motion.
