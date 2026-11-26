# ACE2K: same-reader RFID motion arbitration (U1 candidate)

Reference: upstream ace2k v0.16.0, commit 27c564d210170f01aa1a57cd928a60c520247aff. The installed 0.16.0-233b is a separate 220 V/50 Hz and parallel-lane derivative. Its exact MCU source and build recipe were not located in this repository. **Do not flash a binary built from the unmodified upstream baseline as a substitute for 233b.**

Observed symptom: an adjacent lane blinks rapidly during normal feed. 5 Hz indicates feed ERROR, not successful RFID. The upstream scheduler deliberately excludes host in-use lane motion from the shared reader quiet clock and allows an in-use neighbour to be busy. On U1 this allows a pending neighbour's autonomous tie-break to start during a commanded move. This is a source-confirmed scheduling opportunity, not proof of the specific field fault.

Changes: count motion and loading on both lanes for the existing quiet delay; reject a moving/loading neighbour at scheduling; recheck the live neighbour under the feed lock before an autonomous search; defer bounded host feed/rollback/unload with existing BUSY until an already-running same-reader tie-break returns; make same-reader autonomous search yield to follow bursts. A resting in-use neighbour and the other reader remain eligible. Leave UID attribution, explicit RFID reads, grip, speed, electrical safety, and LED/error definitions alone. Existing automatic insert loads, explicit RFID reads and follow bursts are not serialized as bounded host moves; a yield/return may briefly overlap an already moving follow. Do not hide real faults.

Apply to a clean copy of the exact 233b MCU source (including its existing 220 V and parallel lane patches), review any anchor conflict rather than force it:
```sh
python3 bundles/ace2k-rfid-neighbor/apply.py /path/to/ace2k-source
make -C /path/to/ace2k-source/firmware test-c
```
Reference CI runs host C tests, not a flashable firmware build. The patch is not in the 1902.5 system image and changes no ACE on the device.

Hardware acceptance: reproduce with both same-reader pairs (slots 1/2 and 3/4), each direction, adjacent tag pending and already read, feed and retract, ordinary RFID read after quiet, independent other reader, and printing follow. Capture the fault lane's raw feed mode, final event kind/seq and RFID pending/reading state; don't classify the field root cause without that evidence. A seq=0 loading error may be ignored by the current MultiACE host event handler when no host tag-read/job is associated; the MCU lane can still latch ERROR. Do not clear real faults automatically.
