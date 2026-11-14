# Review the batch examples

[한국어](batch-examples.ko.md)

Run these commands from the NanoCoFold source folder after activating its Python environment. Replace `/path/to/workspace` with the workspace selected during installation.

```bash
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/complex_batch.json
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/screen_batch.json
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/mature_batch.json
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/design_batch.json
```

These commands check the input and save a batch for review. They do not start predictions or send sequences to an external service. The screen, mature and design examples use **cached MSA**. Running them requires matching MSA records in the configured cache, including any generated candidates.

To use the public MSA API for a new example instead, add `--allow-public-msa` to the example runner:

```bash
python scripts/run_example.py screen --workspace /path/to/workspace --review-only --allow-public-msa
```

Replace `screen` with `mature` or `design` as needed. The flag selects public MSA and approves transmission of the example's target, VHH and generated sequences to api.colabfold.com. The checked-in files remain unchanged. Remove `--review-only` from the same command to run the reviewed batch. Keep the terminal open to see progress. Repeating the command reopens the saved batch; `--new-run` creates a separate batch after the previous one finishes.

In the web interface, MSA approval is saved once for later analyses and batches. Change it in **Environment & setup**. A saved approval does not change a cache or local-MSA selection to public API.

New CLI, web and complex runs accept seeds from **0 to 2147483647**. Reports still read the original seed in older results. A warning lists changed settings that belong to an unselected engine; select that engine or remove the unused setting before running. Use a dotted override such as `--set score.thresholds.ipsae=0.6` to change one threshold while retaining the others.
