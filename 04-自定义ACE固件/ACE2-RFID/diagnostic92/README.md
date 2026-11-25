# 92O raw diagnostic candidate — device checkpoint

Based on the 91O build inputs, adds CMD68 packed operation 10 only. It is a diagnostic, not a spool assignment or remaining amount. Argument 1 is lane 0–3, argument 2 selects context words at offsets 0, 4, 16. The code returns `0xffffffff` for bad input or any nonzero lane status. This sentinel can overlap a real raw word, so is ambiguous. Each request reads one aligned word; requests are not a coherent snapshot and carry no UID, generation, or provenance. Do not infer circumference, spool identity, or weight from these values.

`GET_STATUS` handler 0x0800B9A4–C4 copies status words from 0x20001004 + lane*4, and the host SLOT_STATES map calls 0 ready. The firmware masks interrupts, tests all four words, copies one fixed word from 0x200018A4 + lane*60 + {0,4,16}, restores original PRIMASK, and returns through 0x0800E904. This only proves the memory status at that instant; it does not prove physical rest, prevent a later move, or prove the meaning/lifecycle of context. Firmware task work must not be inferred from host cache.

Candidate `ACE2-92O-diagnostic.bin`: 78830 bytes, SHA-256 `411079789ce2f64654ef47b6d1be7abddbbf0d4a396c5a7e20304a302df9afcb`. Repeated container build is identical. Relative to 91O the original application range changes only four branch relocation bytes and one version byte; the first 1024 vector bytes and eight-byte image trailer match. Builder CRC init 0: 0x6642; installed OTA library CRC init ffff: 0x3dd3. Its slicing yields 1232 chunks with a 46-byte last chunk; bootloader acceptance of that tail is not proved.

`test_thumb.py` executes actual linked instructions under Unicorn: 200 combinations of lane/field/busy/PRIMASK, no writes within operation, expected response word only in original epilogue and callee-saved registers/SP restored. `test_diag_contract.py` checks diagnostic request packing and sentinel handling. These are offline emulation, not device execution or an OTA test. `rc522-source.diff` is the source change against 91O. Decoder remains unchanged.

## Device checkpoint

After review, 91O→92O OTA completed on UID `808780566 / 17445170 / 942880330`: 1232 chunks acknowledged including 46-byte tail, IAP_FINISH completed, updater reported `verified=true`, `app_alive=true`, and a subsequent exclusive serial identity query reported V1.1.92O. MultiACE was resumed; fresh state says connected, ready, fw_hold false and dryer stopped. `live/flash-result.json`, `identity-after.json`, `flash-console.log`, `post-info.json`, and `diagnostic-words.jsonl` preserve evidence. These are boot/protocol checks, not a physical motion test or final product validation.

Four lane contexts at rest each returned 0x7fc00000 for fields 0/1 and 0 for field 2, consistent with uninitialized samples, not zero filament. No motion or heating was commanded during diagnostic readout. The print state API reports ready.
