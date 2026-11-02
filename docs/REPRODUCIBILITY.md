# Reproducibility

Every committed number is regenerated from code + config with fixed seeds.

1. Install the pinned environment (`requirements-auction.txt`; Python 3.13.18 was used).
2. Regenerate the synthetic datasets (deterministic; seed `20260101` in
   `SyntheticConfig`):

   ```bash
   python -m auction_alpha generate-data --dataset synthetic_planted
   ```

   ```bash
   python -m auction_alpha generate-data --dataset synthetic_null
   ```

   Each dataset's `manifest.json` records the generator configuration, the
   planted effect sizes and a `data_version` hash over the table content.
3. Run the reference experiments:

   ```bash
   python -m auction_alpha run-all --config config/experiments.yaml
   ```

   ```bash
   python -m auction_alpha --config config/experiments_null.yaml run-all
   ```

4. Compare with the committed artefacts in `reports/synthetic_planted/` and
   `reports/synthetic_null/` (`manifest.json` lists the data version, rules
   registry hash, config hash and git commit of each run).

Each results directory contains:

| file | content |
|---|---|
| `config.yaml` | the exact experiment configuration |
| `manifest.json` | run id, dataset, data version, provenance, registry hash, code version, config hash, periods, capital |
| `selection.json` | parameters frozen from the discovery period (required to unlock the holdout) |
| `summary.json`, `leaderboard.csv`, `multiple_tests.csv` | results and multiple-testing table |
| `data_quality.json`, `availability_matrix.csv`, `strategy_support.csv` | data audit for the run |
| `studies/<S>_<MKT>/` | `study.json` (spec, diagnostic, grid, scenario metrics, robustness), `daily_<scenario>.parquet`, `trades_base.parquet`, `rejections_base.parquet`, `intents.parquet` |
| `dashboard.html`, `RESEARCH_SUMMARY.md` | human-readable outputs |

Sources of non-determinism: none in the pipeline (all RNGs are seeded).
Wall-clock timestamps appear only in run ids and manifests. Floating-point
results can differ in the last digits across NumPy/pandas versions.

## Using real data

1. Write a mapping YAML from `config/ingest_template.yaml` (never commit
   vendor data or credentials).
2. `python -m auction_alpha ingest --mapping my_mapping.yaml` - data are
   validated against the canonical schemas (causality checks included) before
   being stored with `is_synthetic: false`.
3. Point `dataset:` in a copy of `config/experiments.yaml` at the new dataset
   and run `data-audit` to see which strategies become `supported_real`.
4. Run `run-all`; the holdout stays locked until you pass a frozen selection.
