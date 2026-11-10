# Native UI third-party materials

Recorded on 2026-11-09. Preserve this directory with the native UI runtime and its corresponding source/build records.

- `FONT-COPYRIGHTS.txt` records authors, versions, SHA256 values, and exact asset sources for the original fonts and icons.
- `OFL-1.1.txt`, `MDI-LICENSE.txt`, `Apache-2.0.txt`, and `Spoolman-MIT.txt` retain the applicable font/icon license texts and notices.
- `debian/*/copyright` retains notices from the actual private Debian build inputs. These are not a claim that every file in every package was linked.
- `LGPL-2.1.txt`, `GPL-2.0.txt`, and `GPL-3.0.txt` provide full license texts referenced by these materials.
- `native-third-party-manifest.json` pins the font assets and actual Debian libnl provider, including checked source URLs and hashes. Bind this inventory to the final runtime binary hashes in the release build/source manifest.

The actual libnl static libraries are Debian ARM64 `3.7.0-2`; the vendored libnl `3.9.0` source snapshot is not the source of those static archives. The manifest identifies the corresponding Debian original-source and patch archives. Those archives are not included in this notice directory; retain a compliant, accessible source delivery method when distributing binaries.

The parent HelixScreen `LICENSE` and `COPYRIGHT` and the separately retained vendored component licenses remain applicable. This directory is a focused supplement, not a declaration that every component of a complete Snapmaker system firmware image is open source or redistributable.
