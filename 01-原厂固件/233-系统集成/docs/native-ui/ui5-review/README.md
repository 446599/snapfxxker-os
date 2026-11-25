# U1 UI.5 recovery checkpoint

Status: **source recovery in progress; 90% visual similarity has not been certified**.

The user requested immediate GitHub submission and incremental pushes for each completed change on 2026-11-10. This checkpoint preserves recovered records so subsequent reconstruction has an auditable starting point.

## What this commit contains

- The original UI.5 review report, manifest and source-checkpoint record recovered from `U1-UI5-review-evidence.zip`.
- The report describes earlier native mock rendering and tests. Those are historical results; they do not certify reconstructed code.
- The archive SHA-256 is `a90a22a49910b11eb92462f9368dbad1a268df5324b10a4be06e9d1addc0453f`.

## Source availability

The GitHub source baseline is UI.4 at `49b8b2205171820f81845751d7f1927a4ec5f7f1` on `codex/u1-native-ui-20261110`.

The historical UI.5 record identifies local checkpoint `171ec32ec623414c9929ac0359f4d475964e68ba` and `bundles/native-ui/patches/0007-u1-ui5-review.patch` (398563 bytes; SHA-256 `76b13794ec269adddaa672e445d5c1ecfc86c2f87b953db88c326416da1c90f9`). That record explicitly states that no remote publication was performed. The source workspace is unavailable in the current environment, the archive excludes source, and the checkpoint object is not present in this GitHub repository.

**This documentation checkpoint does not contain recovered UI.5 source or firmware.** Work will reconstruct the missing changes from the saved UI.4 baseline, approved reference and retained screenshots/behavior records. Reconstructed changes will be identified as such, verified on their own merits and pushed in small increments. They must not be represented as byte-identical recovery of the original local checkpoint.

## Acceptance

Visual similarity remains unmeasured. Original records report a size mismatch between the approved HTML and the physical 480×320 display. Native compilation and functional tests do not establish a visual similarity percentage or physical-printer acceptance.
