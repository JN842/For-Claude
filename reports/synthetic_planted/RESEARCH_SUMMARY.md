# Research Summary - run `20261102T065011Z_dd44d84e8b`

> **SYNTHETIC DATA.** All results below were produced on generated data (world: `planted`). They validate the research machinery; they are **not** empirical findings about SET, SGX or HKEX. Empirical strategy profitability has not been established.

- Dataset `synthetic_planted` (data version `bae44caf3973`), rules registry `b0eb2557b35f`, code `73e5c66`, config `config/experiments.yaml` (hash `dd44d84e8b`).
- Periods: {'discovery': ['2018-01-01', '2021-12-31'], 'validation': ['2022-01-01', '2023-12-31'], 'oos': ['2024-01-01', '2025-12-31'], 'holdout': ['2026-01-01', '2026-12-31']}. Holdout locked (not evaluated).
- Selection: max discovery-period net Sharpe under the base scenario; frozen in `selection.json`.
- Multiple testing: Benjamini-Hochberg q over every grid point x strategy x market tested in discovery (one-sided Newey-West test of mean daily net return > 0).
- Verdict rule: *supported* requires BH q < 0.05 in discovery, positive net Sharpe in validation AND OOS, positive net Sharpe under the conservative scenario, and placebo p <= 0.10.

## Leaderboard (base scenario)

| strategy | market | selected | verdict | net_sharpe | gross_sharpe | validation_sharpe | oos_sharpe | conservative_sharpe | stress_sharpe | bps_per_trade | gross_bps_per_trade | trades | fill_rate | disc_q_bh | placebo_p | capacity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S01 | HKEX | S01[hold_sessions=4,threshold_bps=200] | anomaly present but not executable after costs | 0.04 | 0.49 | -0.06 | -0.43 | -0.14 | -0.21 | 5.12 | 69.45 | 130 | 0.36 | 1.00 | 0.09 | 4000000.00 |
| S02 | HKEX | S02[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -0.77 | -0.34 | -0.37 | -0.40 | -0.72 | -0.74 | -112.08 | -48.49 | 134 | 0.40 | 1.00 | 0.91 | 0.00 |
| S03 | HKEX | S03[exit_session=close_auction,z_threshold=2.5] | rejected (no robust effect) | -1.88 | -0.03 | -2.32 | -1.83 | -2.26 | -2.76 | -65.24 | -1.04 | 604 | 0.38 | 1.00 | 0.91 | 0.00 |
| S04 | HKEX | S04[threshold_bps=100] | anomaly present but not executable after costs | 0.03 | 1.78 | 0.01 | -0.32 | -0.23 | -0.42 | 0.77 | 52.17 | 62 | 0.67 | 1.00 | 0.09 | 128000000.00 |
| S05 | HKEX | S05 | rejected (no robust effect) | -0.27 | -0.00 | -0.74 | 0.07 | -0.33 | -0.38 | -268.11 | -0.58 | 9 | 0.31 | 1.00 | 0.73 | 0.00 |
| S06 | HKEX | S06[neutral=beta] | anomaly present but not executable after costs | -7.43 | 1.83 | -8.73 | -6.72 | -7.67 | -8.46 | -46.80 | 11.35 | 11789 | 0.42 | 1.00 | 0.09 | 0.00 |
| S08 | HKEX | S08 | rejected (no robust effect) | 0.13 | 0.22 | -0.06 | -0.36 | -0.10 | -0.44 | 2.52 | 4.12 | 461 | 1.00 | 1.00 | 0.45 | 32000000.00 |
| S01 | SET | S01[hold_sessions=4,threshold_bps=200] | anomaly present but not executable after costs | 0.58 | 1.09 | 0.32 | 0.81 | 0.41 | 0.32 | 62.06 | 118.38 | 523 | 0.33 | 0.94 | 0.09 | 17500000.00 |
| S02 | SET | S02[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -1.60 | -1.09 | -1.82 | -1.53 | -1.04 | -1.10 | -175.15 | -118.18 | 521 | 0.34 | 1.00 | 1.00 | 0.00 |
| S03 | SET | S03[exit_session=close_auction,z_threshold=2.5] | rejected (no robust effect) | -2.13 | 0.03 | -2.05 | -1.63 | -1.65 | -2.00 | -66.71 | 0.81 | 858 | 0.66 | 1.00 | 0.82 | 0.00 |
| S04 | SET | S04[threshold_bps=100] | anomaly present but not executable after costs | -0.23 | 1.62 | 0.41 | -0.18 | -0.40 | -0.65 | -7.22 | 51.90 | 233 | 0.71 | 1.00 | 0.09 | 140000000.00 |
| S05 | SET | S05 | rejected (no robust effect) | -0.12 | -0.02 | -0.76 | -0.67 | -0.32 | -0.28 | -84.31 | -14.67 | 24 | 0.28 | 1.00 | 0.55 | 3500000.00 |
| S06 | SET | S06[neutral=sector,quantile=0.2] | anomaly present but not executable after costs | -4.71 | 1.59 | -5.25 | -5.25 | -3.05 | -3.72 | -39.74 | 13.28 | 15039 | 0.32 | 1.00 | 0.09 | 0.00 |
| S08 | SET | S08[threshold_bps=20] | rejected (no robust effect) | 0.31 | 0.39 | 0.56 | 1.29 | 0.11 | -0.16 | 7.20 | 9.20 | 288 | 1.00 | 1.00 | 0.18 | 560000000.00 |
| S01 | SGX | S01[hold_sessions=4,threshold_bps=200] | anomaly present but not executable after costs | 0.38 | 0.97 | -0.40 | 1.05 | -0.04 | -0.10 | 36.70 | 93.47 | 422 | 0.36 | 1.00 | 0.09 | 20800000.00 |
| S02 | SGX | S02[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -1.26 | -0.73 | -1.63 | -1.32 | -1.13 | -1.04 | -129.57 | -74.36 | 375 | 0.35 | 1.00 | 1.00 | 0.00 |
| S03 | SGX | S03[exit_session=close_auction,z_threshold=2.5] | rejected (no robust effect) | -2.36 | -0.37 | -2.77 | -4.11 | -2.04 | -2.50 | -75.92 | -11.60 | 674 | 0.59 | 1.00 | 0.73 | 0.00 |
| S04 | SGX | S04[threshold_bps=100] | anomaly present but not executable after costs | 1.52 | 6.68 | 2.38 | 1.73 | 0.54 | -0.27 | 13.36 | 62.20 | 1105 | 0.61 | 0.81 | 0.09 | 20800000.00 |
| S05 | SGX | S05 | rejected (no robust effect) | -0.26 | -0.18 | -0.11 | -0.70 | -0.24 | -0.08 | -194.92 | -135.21 | 13 | 0.20 | 1.00 | 0.64 | 0.00 |
| S06 | SGX | S06 | anomaly present but not executable after costs | -7.07 | 1.06 | -5.66 | -7.26 | -4.54 | -5.13 | -45.79 | 6.79 | 12331 | 0.41 | 1.00 | 0.18 | 0.00 |
| S07 | SGX | S07 | supported (executable after costs) | 2.64 | 5.62 | 3.19 | 3.28 | 2.17 | 1.83 | 26.12 | 61.66 | 2172 | 0.10 | 0.00 | 0.09 | 20800000.00 |
| S08 | SGX | S08[threshold_bps=20] | anomaly present but not executable after costs | 0.44 | 0.56 | 0.15 | 0.06 | 0.25 | -0.02 | 11.08 | 14.08 | 260 | 1.00 | 0.81 | 0.09 | 20800000.00 |


## Hypotheses

- **S01 Closing Gap Reversal** - HKEX: anomaly present but not executable after costs, SET: anomaly present but not executable after costs, SGX: anomaly present but not executable after costs
- **S02 Closing Gap Continuation** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S03 Pre-close Momentum** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S04 Auction Imbalance Prediction** - HKEX: anomaly present but not executable after costs, SET: anomaly present but not executable after costs, SGX: anomaly present but not executable after costs
- **S05 Index Rebalance Price Pressure** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S06 Cross-sectional Closing Reversal (market-neutral)** - HKEX: anomaly present but not executable after costs, SET: anomaly present but not executable after costs, SGX: anomaly present but not executable after costs
- **S07 SGX Trade-at-Close Dislocation** - SGX: supported (executable after costs)
- **S08 Closing Auction vs Futures Basis** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: anomaly present but not executable after costs

On **real** data every hypothesis is currently **untestable**: no licensed SET/SGX/HKEX auction, indicative, borrow, index-event or futures data is present in this environment.

## Pipeline validation (planted vs recovered)

The generator planted known reversal fractions; the pipeline's estimates from the generated prices:

| market | quantity | planted | estimated |
|---|---|---|---|
| SET | frac_gap_reverting_by_next_open | 0.383 | 0.375 |
| SET | frac_gap_reverting_next_open_to_next_close | 0.212 | 0.136 |
| SET | frac_gap_reverting_by_next_close | 0.595 | 0.511 |
| SGX | frac_gap_reverting_by_next_open | 0.383 | 0.382 |
| SGX | frac_gap_reverting_next_open_to_next_close | 0.212 | 0.100 |
| SGX | frac_gap_reverting_by_next_close | 0.595 | 0.482 |
| HKEX | frac_gap_reverting_by_next_open | 0.383 | 0.332 |
| HKEX | frac_gap_reverting_next_open_to_next_close | 0.212 | 0.191 |
| HKEX | frac_gap_reverting_by_next_close | 0.595 | 0.523 |


## Distinguishing anomalies from executable strategies

For the gap strategies the diagnostic C0->O1 and C0->C1 windows (trade at the closing price) are compared with the executable O1->C1 window and realised gross / net bps per trade in each study's `shortfall` block (dashboard section 12). The closing price is not known early enough to trade at it, except via SGX Trade-at-Close (S07), whose fills are capped by realised TAC volume.

## Multiple testing

135 discovery tests; 2 with BH q < 0.05; 1 with Holm p < 0.05 (one-sided: mean daily net return > 0).

## Artefacts

- `dashboard.html` - interactive dashboard
- `leaderboard.csv`, `multiple_tests.csv`, `selection.json`, `manifest.json`, `config.yaml`
- `studies/<S>_<MKT>/` - study.json, daily_<scenario>.parquet, trades_base.parquet, rejections_base.parquet, intents.parquet
