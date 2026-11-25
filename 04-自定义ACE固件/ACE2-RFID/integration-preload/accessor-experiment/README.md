# Combined checked-accessor experiment, revision c132

**Offline only. No device was connected. No usable firmware was produced.**

Stable image SHA-256:
`c132f10a9a91f4bf7a5a5917772a61ea123e74d4d7a8e9b7fb8cb65161a89750`

Stable ELF SHA-256:
`c17e3689ab1313a0e14ab4024f621e4d316f5c11aafab9c4e0df989ba10e0d8f`

Run from this directory: `python verify_accessor.py`.
The actual build command is `python build_accessor.py --offline`. Default
invocation, `--force`, and arbitrary `--out` are rejected.

## Actual integration

This single ARM ELF links 19 RF/startup hooks and 10 preload hooks. The source
is an independent copy, not edits to `integration-rf-spi`. `frozen-inputs.json`
records every frozen source/test/ELF input hash. The confirmed RF baseline is
image `1ffba30b853ece41509c79b885180447d52c8d591be9185e2849ef341a202e6b`.

- ARM event tail: 144 -> 184 bytes; requested payload: 176 -> 216 bytes;
  minimum allocator block, including its header: 184 -> 224 bytes.
- Original offsets remain: RF state 16, experimental state 76. The new 40-byte
  preload state starts at tail offset 144. ARM static assertions enforce all
  offsets/sizes. Host pointer sizes differ, so host `sizeof` is not ARM proof.
- Magic changes from EXT1 to EXT2 and ready from RDY1 to RDY2. The accessor
  validates allocated header size before reading tail metadata. Old RF blocks
  (184/192/200 bytes) cannot pass the new 224-byte minimum.
- Factory initializes the original 32-byte event ABI, clears the entire
  expanded tail, sets both unowned preload reader lanes to UINT32_MAX, executes
  DMB, and writes ready last. Existing handles cannot be reconstructed to erase
  faults. `blocked=0` means no recorded owned fault, **not** motor/RF readiness.
- All preload state-taking hooks call `ph_platform_state`, which calls
  `ex_storage_current`, while holding the same saved-PRIMASK lock used for the
  subsequent latch read/effect. C entry points use the same checked accessor.
  There is no absolute `ph_storage` symbol, cached global state pointer, BSS,
  initialized RAM segment, or public fault-clearing initializer.
- Code is linked continuously after the stock trailer location, beginning
  `0x080197A0`. The entire image ends at `0x0801C911`, strictly before documented
  application end `0x08024000`. This replaces the older experiment's placement
  at `0x08030000`, which lies in staging and was never a valid deployment layout.
  Being within this documented range is not hardware flash-capacity proof.

## Missing or corrupt storage

Simply skipping a future enable write did not stop an already-running motor.
This revision adds `ph_raw_lane_disable` for a hook whose **actual lane is
known** but checked storage returns NULL. It uses only exact stock flash tables
and bounded lane-specific GPIO/PWM/TIM writes; it does not dereference the
event object or an encoder object pointer. It resets the lane enable, writes
PWM=1000, clears that lane's TIM4 DIER/status bit and encoder UIE, and clears
the stock motor-active byte. It does not claim controller/mechanical rest.

Unknown/out-of-range lane values produce no guessed lane or sibling stop.
The nominal TIM4-disable hook remains an atomic live-read/BIC/write operation.

**The failure publisher is still not a closed safety path.** When storage is
invalid, `(reader,lane,epoch)` cannot establish which current operation owns
that lane. `ph_publish_failure` therefore returns PH_INVALID without inventing
owned success or selecting another lane. The known-site raw stop occurs when
a guarded site runs, not as a proven deadline after loss of state. Existing
motion may persist until such a site runs. A real owned failure source and
storage-lifetime invariant are still required before this is a usable fix.

## Repeated verification against c132

`verify_accessor.py` passes:

- ARM compile/link; exact original bytes, hook overlap detection and independent
  branch decoding/objdump; documented application bound and BSS/data assertions.
- Default-build rejection and intentional production-link rejection for all
  five unbound platform proof names. Offline links use clearly named sentinel
  proof values, not real evidence.
- 33,925 host storage assertions and separate preload/accessor ownership,
  fallback32, invalid-header, old-magic/ready/size, and sticky-fault tests.
- The host suites with AddressSanitizer/UndefinedBehaviorSanitizer. Leak checks
  are disabled because LeakSanitizer is unavailable under this environment's
  ptrace setup; no tested code allocates via host malloc.
- 4,380 finite Thumb instruction checks over the actual new ELF code, including
  the checked accessor, all four lanes/NZCVQ values, aligned BL calls, missing
  and invalid storage, exact stock stop leaves, and effect-boundary faults.
- Nine current-image tests, including complete stock-prefix reconstruction,
  startup BL identity, no blanket INSERT disable, reset/trailer barriers,
  duplicate/partial startup-hook conflicts and exact test-result image hash.

The finite interpreter is not a full CPU/board/RTOS emulator. It models only
explicitly supported instructions and memory/register writes. It does not
model real interrupts, FPU exception stacking, electrical state or mechanics.
Current observations: 135 masked instructions within the worst tested gate,
149 in the tested failure-publisher path, and 80 bytes of added gate stack.
These are neither hardware-time bounds nor complete interrupt-stack budgets.
Do not reuse earlier 32-instruction or d160-image results for this revision.

An independent reviewer also ran 90,022 **literal ARM payload216/block224**
boundary checks and 22 publication/version/getter checks, with normal and
sanitized host builds. Its files are in the sibling `integration-accessor-review`.
That review does not establish physical safety or allocator/boot lifetime.

## Unchanged release blockers

- Reset is still an immutable self-loop, original startup remains gated,
  the trailer is `NOFLASH!`, and RF-ready remains false. Exception paths are not
  claimed quarantined. Normal INSERT source instructions and stock sibling /
  reader8 guards remain unchanged, but this reset-blocked image cannot operate.
- Real AUTH/transport failure classification and operation ownership are not
  connected to preload. The generic RF stop request is **not** wired to a
  permanent lane latch. Ordinary non-Bambu tags, tagless spools, transient read
  errors and spool removal must not be blindly classified as terminal failure.
- Latches remain sticky; safe retry/reinsertion rearm and stale-job retirement
  have no verified contract. This is not the final user-requested behavior.
- Allocation/header checks are not free/reuse/ABA or reboot-identity proof.
  The extra 40 bytes are explicitly allocated, but heap/stack margin and startup
  exclusivity remain unproved. Relative to stock, new blocks consume 184 more
  bytes. No reserve or capacity margin is invented.
- The F726 admission-to-queue race, all remaining motion writers, pending /
  in-flight ISR retirement, hardware register effect timing, and physical stop
  remain unproved. No PL_STOP_PROVEN or equivalent success is fabricated.

`output/manifest.json` and `build/dynamic-instruction-results.json` preserve
these limitations and the exact artifact hash. Old snapshots remain available
for comparison and are not evidence about this revised executable.
