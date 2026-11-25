# EXPERIMENTAL DO NOT FLASH

This is a **linked offline integration fixture**, not a working or safety-proven
ACE2 firmware release. No device, network, updater or flash operation was used.
Do not remove the reset barrier, invent a successful platform proof, append an
IAP trailer, or submit this fixture to an updater.

## Reproduce and inspect

Run `python3 verify_all.py` from this directory. It uses the existing adjacent
GNU ARM toolchain and local stock input; it installs nothing. Normal invocation
of `build_offline.py` refuses to emit an image. Only `--offline` is supported;
there is no force, flash, output-name, production or unlock option.

- Linked ELF: `build/EXPERIMENTAL-DO-NOT-FLASH.elf`
- Analysis image: `output/EXPERIMENTAL-DO-NOT-FLASH.offline-image`
- Symbols: `build/symbols.json`
- Link map/disassembly: `build/integration.map`, `build/integration.dis`
- Exact image modifications: `output/analysis-manifest.json`
- Source/object/log hashes and check results: `output/verification-manifest.json`

Final image SHA-256:
`1ffba30b853ece41509c79b885180447d52c8d591be9185e2849ef341a202e6b`

Final ELF SHA-256:
`e4fbade558785e32b97341b6ee4c499c6f8925b73977450a79d7f717d1724286`

Image: 82,729 bytes. Appended linked text: 11,137 bytes. Data/BSS: **zero**.
Unresolved symbols: **zero**. Two consecutive builds produce identical hashes.
The 19 actual hooks, reset-vector replacement and version-label replacement are
all recorded. Reconstructing the stock prefix from those 21 changes reproduces
every byte of the final prefix; there are no unlisted prefix edits.

## Actual integration

| Stock address | Installed behavior |
| --- | --- |
| 0x080116A8 | BL to extended event-group factory; successful return continues at 0x080116AC |
| 0x08008244 and reset vector | Normal control flow enters permanent experiment loop |
| 0x0800F574 | Bounded read, six-byte whole-instruction replacement |
| 0x0800F5D0 | Bounded write, six-byte whole-instruction replacement |
| 0x0800F32C | Six-argument transceive thunk captures true LR for capacity allowlist |
| 0x0800DEB6 | SELECT rejected before unbounded stock scheduler/assertion path |
| 0x0800E108 | Unimplemented HALT/physical cleanup rejected |
| 0x0800E244 | Background RF ownership adapter, currently rejects as unready |
| 0x0800E7A8 / 0x0800E910 | CMD68 / CMD13 return existing failure status 1 |
| 0x0800E74C / 0x0800E70C | CMD14 / requeue refused before blocking queue calls |
| 0x0800EA14 | Outer worker refused before startup GPIO/cache access |
| 0x0800E7DA | Unticketed legacy raw command rejected |
| 0x0800E836 / 0x0800E842 | UID/raw/native fallback cannot publish success |
| 0x0800FE36 / 0x0800FE3C | Background/cache failure sets r6=0 and enters 0x0800FE94, including stock status=3 write |
| 0x0800E228 | Page publication refused, r10=0 and stock failure return |

F574/F5D0 replace the full 2-byte stack instruction plus 4-byte LDRD, never half
of the LDRD. EA14 likewise replaces six complete bytes. The transceive thunk
preserves stack arguments, true LR, r4, return SP and 8-byte call alignment.

The real bridge now reports **all** non-OK transceive outcomes to the shared
fault/STOP metadata, including invalid/zero TX length, malformed response,
timeout, reader error and unsupported command/caller. It does not reset budgets
or clear quarantine on fallback or another reader. Checked publication verifies
owner, boot/generation/connection/session/sequence and STOP epoch.

The decoder block-read call uses an explicit 16-byte capacity and 128-bit
required response instead of the stock unbounded ABI. All three public decoder
entries return distinct fatal result -2 in this fixture. Their installed output
stubs separately reject rather than interpreting that failure as a normal native
or UID fallback. The old cache memcpy has been removed, not merely shortened.

## Event-group storage is used by the real RF binding

The factory hook is at the exact original BL from 0x080116A8 to 0x0801613E.
It calls the original allocator at 0x080132C4, requesting **176 payload bytes**
on ARM: the original 32-byte event group plus a 144-byte checked tail. Minimum
allocator block size is 184 bytes including its eight-byte header. The original
32-byte request needs 40 bytes, so the minimum incremental heap demand is 144
bytes, before allocator rounding/fragmentation effects.

The new factory reproduces every initialized field in the original first 32
bytes, preserves the originally untouched padding, clears/initializes the tail,
executes DMB and writes the storage-ready marker last. It returns the original
object base; the unchanged stock instructions publish it at 0x20001E64 before
the first observed task-creation call at 0x080116CE.

Every RF state lookup reads that real handle, then validates:

1. Nonzero, eight-byte alignment and the exact audited heap range.
2. Header at p-8 has next=0; p-4 has allocated bit set.
3. Total block length includes header, is aligned, is at least the rounded
   requested size, at most 16 bytes larger, and does not cross heap end.
4. Only after those checks: tail magic, size, self pointer and final ready marker.
5. Handle is unchanged at the end of lookup.

Allocation-size inputs are bounded before addition/rounding, including refusal
of the stock allocator's uint32 overflow range. A failed extended allocation
calls the original 32-byte factory. Its possible total block sizes 40/48/56 can
never pass the extended-state accessor; no tail is read. If that fallback also
fails, the assembly wrapper enters the experiment loop instead of starting stock
tasks with a null object. An existing handle or corrupt newly allocated header
also fails closed. There is no guessed-RAM/global fallback and no implicit state
reset/recovery. The ELF has zero data/BSS.

Storage-ready is **not RF-ready**. `rf_platform_ready()` remains false;
quiescence returns UNVERIFIED and cleanup returns false. In a missing-state case
there is nowhere trustworthy to persist a STOP latch, so the request fails with
no RF access rather than writing to guessed memory. Existing-state faults set
the sticky STOP/unknown-owner metadata. Neither case implements physical STOP.

## What intentionally does not work

This fixture prevents normal startup and disables **all RF operations**, even
when storage allocation succeeded. It is not a compatibility-preserving release.

- Live identification, cached identification responses, stock SELECT, HALT,
  autonomous scanning, control/requeue and all old packed raw-tunnel operations
  are rejected.
- UID hex success, unknown-tag sentinels, native parsing fallback and all
  OpenSpool/FilaMan/Prusament/Creality/Bambu result publication are disabled.
- The E216/E21C/E220 extended-read pokes are absent. Original stock page limits
  and failure branches remain; the publication hook still returns failure. The
  192-byte tail-read behavior used by some formats is unavailable.
- F6F8/F724 remain stock, so the previous dual-grab busy-check NOPs are not applied.
- A normal reset vector or direct entry through the original reset function
  reaches a two-byte self-loop. **Exception vectors remain stock.** The limited
  branch check proves no more than ordinary sequential reset control flow. It
  does not prove interrupt/NMI/HardFault containment or physical output safety.
- The trailer is deliberately `NOFLASH!`, not valid IAP magic. The experimental
  version label is not a normal release version. No updater whitelist or
  compatible patch.json is produced.

## Verification and limits

All 33 verifier steps pass, including:

- 33,925 event-storage assertions: success, exact first-32-byte ABI, ready-last,
  fallback32, double failure, existing handle, malformed/truncated/free/overflow
  headers, every allowed payload length, heap-boundary sweeps and invalid pointers.
- 76,460 integration assertions using the actual integration C: original-handle
  lookups, fallback/missing state rejection, every SPI fault/STOP poll boundary,
  all budget cutoffs, frozen reply IRQ, caller/capacity limits, unchanged outputs
  on failure, stale tokens, decoder fatal outputs and shared quarantine.
- Existing core/bridge/owner/adapter regression suites, including 100,000 owner
  events and all 256 legacy raw command values.
- All six host suites repeated under ASan/UBSan. Leak detection is disabled under
  this runtime's ptrace; no memory allocation is performed by candidate C apart
  from the explicit original-allocator factory binding.
- Eight static/limited-model checks of the actual linked bytes: complete prefix,
  branch destinations, normal-reset loop, refusal gates, thunk ABI, publication
  failure branches, no unresolved/globals, and factory success/failure routing.

Host MMIO, IRQ and original allocator/factory functions are explicit doubles.
The limited Thumb check interprets only selected branches/stack operations.
**No actual ARM CPU emulator, original RTOS, peripheral, device, physical STOP,
thermal liveness, or flash validation ran.**

Remaining deployment blockers include event-group free/reuse/lifetime and boot
uniqueness; allocator/startup blocking and failure-output behavior; measured
heap/stack headroom; a real user STOP path and heater/motor/thermal scheduling;
complete checked HALT/crypto/FIFO/config/antenna cleanup; elimination of other
stock blocking waits; and end-to-end publication/preload behavior. Header/magic
validation alone cannot solve concurrent free/reuse or authenticate a boot.
The normal-reset barrier must remain until those contracts are independently
established. It is not itself an emergency-stop implementation.
