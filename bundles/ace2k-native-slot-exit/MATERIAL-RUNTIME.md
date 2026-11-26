# Material feature integration (before full image build)

One actual Klipper webhook and native exit G-code, using the existing
HostNativeExit durable controller, and the real Provider HTTP route / device
service forwarding. Batches use explicit selected ACE/slot/UID and one-use
tickets. The parked proof producer reads actual native lane counters and
requires explicit physical inspection of the tip before the common junction.
A head-source unload runs the existing explicit source-bound head G-code
under its own G-code mutex; native independent tasks do not wait for it.
A verified head-unload does not by itself prove a parked tip: the task then
requires a second physical parking confirmation, until branch-length
calibration evidence is available. Head operation cancellation stops at the
existing safe head-operation boundary; native exit cancellation targets one
task. No fake success from HTTP/G-code acceptance.

This checkpoint does not yet enable the composer or build an image. The
integration test assembles the exact 1902.7 provider source chain plus all
new runtime modules and checks the necessary mount-map changes. The screen
needs the final UID parsing, preview text, task status/cancel/parking
confirmation wiring, and a verified complete ARM64 source tree before an
image may be built. No field logs/config/UIDs published.
