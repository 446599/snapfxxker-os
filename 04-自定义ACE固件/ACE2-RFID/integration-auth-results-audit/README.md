# Offline typed AUTH / block-READ result experiment

This directory is independent of `integration-preload/accessor-experiment`.
The c132 image remains unchanged:
`c132f10a9a91f4bf7a5a5917772a61ea123e74d4d7a8e9b7fb8cb65161a89750`.
No device, network, installation, flash operation, production-entry change or
slot/preload connection was performed. There is no new firmware image.

Run `python build_offline.py --offline` for host tests, ASan/UBSan tests and
Cortex-M3 compilation. Default invocation refuses. Without the explicit
`AUTH_RESULTS_OFFLINE_TEST=1` macro the typed module does not compile. The ARM
output is a **relocatable NOFLASH object**, not a bootable linked image. Its
platform enter/leave/operation-validation contracts intentionally remain
unresolved. Never flash it or use the macro as a production approval.

## What changed

`firmware/typed_results.c` adds two isolated interfaces, with an explicit
`struct ar_result`: SELECT/setup/AUTH/READ stage, classified outcome, raw typed
BR result and its validity, original SELECT return, checked register snapshot,
poll count, and attempted versus acknowledged AUTH-command issuance.

- `ar_auth_checked` requires a caller-supplied successful single-size SELECT
  result, matching five-byte UID/BCC, current transaction validation, and a
  shared finite driver budget. Caller-supplied SELECT is **not proof** of its
  freshness or that this tag is Bambu. No source currently binds that evidence.
- It calls existing typed `br_read`/`br_write`, never the byte-return/void-write
  stock wrappers. Every typed error returns immediately. Even if a failing
  read supplies 0xff, that byte is never interpreted.
- BR_OK does not make 0xff trustworthy. Stale Crypto/IRQ must first read as
  zero. During AUTH, only Status2 values 0/8 and IRQ combinations within 0x11
  are recognized. Other combinations, including 0xff, are unknown snapshots.
- `AR_AUTH_STATUS_ACCEPTED` means only the narrow observation
  `Status2=8, ComIrq=0x10, ErrorReg=0` within the same checked transaction.
  It does not prove Bambu identity, a correct key, physical response, or safe
  completion of the whole RF operation. The recognizer is deliberately
  restrictive and may reject valid hardware states.
- Timer/no-reply, software poll limit, SPI timeout, cancellation, reader error,
  UID mismatch, SELECT failure and an idle non-authenticated terminal state
  remain different outcomes. **AR_AUTH_NEGATIVE is reserved and never emitted.**
  In particular ordinary NTAG page-read failure is not an AUTH observation.
- `ar_read_checked` stages exactly 16 bytes through the existing typed
  `br_transceive_exact`, checks bit count/ErrorReg, then uses its checked
  publication primitive. Output stays unchanged on every tested failure.
  It does not mint an AUTH capability; the caller must retain the same actual
  authenticated operation. A copied result structure is not authorization.
- No failure is sent to `ph_publish_failure`, and no lane is inferred.

`vendor/bounded_rc522.[ch]` are byte-for-byte copies of c132's existing checked
transport, recorded in `build/manifest.json`. Their SPI status/error handling
provides typed validity that the legacy register API discards. This new module
does not silently convert a failed or unattempted operation into BR_OK.
For argument/SELECT failures, `transport_status_valid=0`: the placeholder
transport field must not be interpreted as an observed SPI fault.

## Verification scope

Tests use explicit doubles for the existing typed transport and publication
contracts. They cover every I/O position with every nonzero BR result; stale
ownership at each validation point; poison bytes on failed reads; every
Status2 byte and every IRQ byte; every nonzero ErrorReg byte; BR_OK+0xff;
no-reply and poll exhaustion; original SELECT return preservation; failed
READ output non-publication; and unchanged prior IRQ-mask nesting.

The actual checked transport is ARM-compiled and included in the relocatable
object, but these tests do not execute its SPI peripheral implementation.
ASan/UBSan cover host doubles. LeakSanitizer is disabled because these tests
perform no host dynamic allocation and this environment uses ptrace.

No datasheet or hardware evidence was obtained in this task. The precise
MFRC522 completion/state combinations, register side effects, timer setup,
reader clone behavior, analog RF and bus electrical behavior remain unverified.
Cross-checking registers is not proof against all coherent electrical faults.
The module does not program or assert a millisecond timer: its bound is poll
count plus the existing transaction's finite SPI budget, excluding CPU-bus stalls.
AUTH may leave Crypto1/configuration active for later reads. Cleanup, safe retry,
owner release, DMA/ISR retirement and physical stop are explicitly unimplemented.
Failure does not silently retry AUTH block 7 or release the operation.

## Why c132's native Bambu path stays disabled

Evidence is from c132's `firmware/` and `build/combined.dis`:

- FE36/FE3C hooks go to `rawtag_extract_stub`/`rawtag_cache_stub` and reject via
  FE94. E836/E842 hooks go to rejection stubs. They do not invoke native decoders.
- `ex_decoder_ready` at 0x08019B54 returns zero; decoder C entry points return
  -2. SELECT at DEB6 branches to `ex_reject_select` at 0x08019B60, returning 1.
  RF-ready remains zero and normal reset remains gated.
- The dormant source path is `decode_native_tag` (short page read) or
  `decode_cmd68_uid_tag` -> checked UID comparison after SELECT ->
  `decode_bambu_classic` -> KDF -> MFAuthent for block 4, then block 7 fallback
  -> repeated reads of blocks 4/5 -> payload validation -> unchecked cleanup.
- `rc522_mf_authent` at source lines 389-442 reduces every outcome to boolean;
  final Status2/IRQ/Error/FIFO are read then discarded. A legacy read's error
  sentinel 0xff contains the Crypto1 bit. The subsequent block path can still
  fail closed, but that does not make the AUTH boolean reliable.
- `br_stock_transceive_sized` compresses BR outcomes to 0/1, and
  `selection_matches` compresses SELECT/UID mismatch to a boolean. Neither
  supplies an authenticated failure classification.
- `rc522_auth_stop` is void and does not verify cleanup. KDF scratch aliases
  the global page buffer at 0x20000704. Real exclusive ownership is mandatory.
- `ph_claim_reader`, `ph_release_reader`, `ph_publish_failure` have no native
  decoder callers. Background wrapper `index*2` is not proof of the particular
  active lane among a shared reader's siblings.

## Next required integration, not performed

Preserve this typed result through `decode_bambu_classic` rather than collapsing
it to boolean. Bind a fresh real SELECT and its UID to one actual lane, reader,
boot/generation/operation and cancellation epoch. Validate that identity again
at reporting; never derive a lane just from reader ID. Only then decide which
owned failures may feed the preload consumer. No AUTH_NEGATIVE producer should
be added until an independently justified and tested terminal criterion exists.
Keep no-tag, unsupported tag, normal NTAG failure, transport uncertainty and
stale work distinct. Do not open c132's production gates for this experiment.
