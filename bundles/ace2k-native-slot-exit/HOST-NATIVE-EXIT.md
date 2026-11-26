# Host native-exit controller checkpoint
A reactor-side task now calls the real ACE2K writer queue and correlates private
prepare/start/status/stop/abort/release responses to its durable claim. A lane
claim with no RFID reader and no shared head resource is independent. Persist
the dispatch marker before start, persist verified completion before sending
release, and remove the journal claim only after the matching release reply.
The source patch hooks the actual engine send_request_to, _lane_masks,
ready/disconnect and get_status; recovered claims are propagated to the actual
driver before its automatic motion tick. No fake screen success. Parked
counter anchors are rechecked on the real driver before start.

Remaining release gates: the actual route verifier that issues ParkedProof
must be connected to real position evidence (user physically verified branch
or calibrated and verified unload/parking transition), not inferred from an
empty head or a UI boolean. A shared-head unload and subsequent native exit
must have that real route evidence and hold the shared path throughout.
The old screen's public endpoints, batch route authority, and complete
ARM screen build are not yet connected. No composer call or firmware build
in this commit. Tests simulate the real driver and staged host hooks.
