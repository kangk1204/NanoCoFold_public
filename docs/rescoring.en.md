# Re-score a saved analysis

`score` reads saved predictions and recalculates metrics and selection. It does not run a prediction engine. `report` rebuilds the report from saved result tables.

Keep the original analysis folder. To compare a new scoring setup, copy the entire folder to a new location and run `score` on that copy. Use the same settings to check reproducibility; change scoring settings only when you intend a new assessment.

```bash
nanocofold score --run-dir /path/to/analysis-copy
nanocofold report --run-dir /path/to/analysis-copy
```

| Message | What to do |
|---|---|
| Prediction stage configuration differs from the record | `score` cannot create predictions for a different mode, comparison or cascade. Run that analysis in a new folder first. |
| A recorded prediction manifest is missing or changed | Restore the original files from a verified backup, or resume the analysis on a copy with the current code. Do not edit hashes in `run_state.json`. |
| Prediction stage record is damaged | Restore `run_state.json` from the same analysis backup. Keep the damaged copy for diagnosis. |
| Prediction stage record is empty | Update NanoCoFold, then resume the analysis on a copy before re-scoring. Earlier ColabFold runs could omit this record. The program does not guess which files belong to the run. |

A rejected `score` command leaves the previous completed publication available to `report` when its tables and publication state are unchanged. If scoring already started changing that publication, the incomplete state remains visible and report regeneration is blocked until recovery succeeds.

Older folders without prediction-stage records use their primary prediction stage. An old Boltz-2 re-score folder is not selected merely because `screen.boltz2_rescore_top` is enabled. Re-scoring such a folder can therefore differ from an older release; the original files and selection thresholds remain unchanged until you explicitly run a new assessment. Keep the original export for comparison.
