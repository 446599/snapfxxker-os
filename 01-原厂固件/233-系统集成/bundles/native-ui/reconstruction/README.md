# UI.5 source reconstruction

The original unpushed UI.5 checkpoint is unavailable. These patches reconstruct the recorded changes on the saved UI.4 source; they are not the original missing patch.

Each completed component is pushed separately to `codex/u1-native-ui-20261110`. `series.json` lists the ordered source patches and their hashes. The published UI.4 runtime manifest remains the base until a separately verified runtime is built.

## Prepare source

```sh
python3 scripts/native-ui/prepare_ui5_reconstruction.py --check
python3 scripts/native-ui/prepare_ui5_reconstruction.py --output /tmp/u1-ui5-source/helixscreen
```

The preparation command uses the existing pinned UI.4 source workflow, then applies this series. It requires access to the pinned upstream repository and font inputs, just as the existing native build does. Use an empty output directory.

Native build and targeted mock validation are tracked in the series metadata. Earlier UI.5 test results under `docs/native-ui/ui5-review` are historical evidence and must not be reported as results for reconstructed code. No visual similarity percentage or physical-printer acceptance is certified.
