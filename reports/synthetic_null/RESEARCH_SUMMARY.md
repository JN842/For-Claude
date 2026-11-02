# Research Summary - run `20261102T065434Z_948dec5e9e`

> **SYNTHETIC DATA.** All results below were produced on generated data (world: `null`). They validate the research machinery; they are **not** empirical findings about SET, SGX or HKEX. Empirical strategy profitability has not been established.

- Dataset `synthetic_null` (data version `18dc3cde5ba1`), rules registry `b0eb2557b35f`, code `73e5c66`, config `config/experiments_null.yaml` (hash `948dec5e9e`).
- Periods: {'discovery': ['2018-01-01', '2021-12-31'], 'validation': ['2022-01-01', '2023-12-31'], 'oos': ['2024-01-01', '2025-12-31'], 'holdout': ['2026-01-01', '2026-12-31']}. Holdout locked (not evaluated).
- Selection: max discovery-period net Sharpe under the base scenario; frozen in `selection.json`.
- Multiple testing: Benjamini-Hochberg q over every grid point x strategy x market tested in discovery (one-sided Newey-West test of mean daily net return > 0).
- Verdict rule: *supported* requires BH q < 0.05 in discovery, positive net Sharpe in validation AND OOS, positive net Sharpe under the conservative scenario, and placebo p <= 0.10.

## Leaderboard (base scenario)

| strategy | market | selected | verdict | net_sharpe | gross_sharpe | validation_sharpe | oos_sharpe | conservative_sharpe | stress_sharpe | bps_per_trade | gross_bps_per_trade | trades | fill_rate | disc_q_bh | placebo_p | capacity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S01 | HKEX | S01[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -0.72 | -0.29 | -0.85 | -1.65 | -0.80 | -0.86 | -109.65 | -44.37 | 132 | 0.37 | 1.00 | 0.91 | 0.00 |
| S02 | HKEX | S02[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -0.04 | 0.39 | -0.16 | 0.96 | -0.03 | -0.07 | -5.95 | 57.75 | 138 | 0.40 | 1.00 | 0.18 | 32000000.00 |
| S03 | HKEX | S03[exit_session=close_auction,z_threshold=2.5] | rejected (no robust effect) | -1.92 | 0.06 | -2.40 | -1.88 | -2.30 | -2.47 | -62.11 | 1.77 | 622 | 0.40 | 1.00 | 0.73 | 0.00 |
| S04 | HKEX | S04[threshold_bps=100] | rejected (no robust effect) | -1.77 | 0.27 | -1.95 | -1.58 | -1.74 | -1.72 | -45.38 | 6.04 | 65 | 0.67 | 1.00 | 0.36 | 0.00 |
| S05 | HKEX | S05 | rejected (no robust effect) | -0.44 | -0.15 | -0.47 | -1.09 | -0.54 | -0.53 | -481.40 | -144.46 | 8 | 0.47 | 1.00 | 0.36 | 0.00 |
| S06 | HKEX | S06[neutral=beta,quantile=0.2] | anomaly present but not executable after costs | -9.01 | 0.61 | -10.02 | -9.04 | -9.03 | -9.77 | -54.21 | 3.64 | 13684 | 0.45 | 1.00 | 1.00 | 0.00 |
| S08 | HKEX | S08[threshold_bps=5] | rejected (no robust effect) | -0.12 | 0.00 | -0.17 | -0.88 | -0.48 | -0.99 | -1.54 | 0.06 | 1100 | 1.00 | 1.00 | 0.64 | 0.00 |
| S01 | SET | S01[hold_sessions=4,threshold_bps=200] | anomaly present but not executable after costs | -0.55 | -0.09 | -0.71 | 0.09 | -0.60 | -0.68 | -64.92 | -10.56 | 515 | 0.33 | 1.00 | 0.73 | 0.00 |
| S02 | SET | S02[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -0.42 | 0.06 | -0.95 | -0.50 | -0.11 | -0.20 | -48.16 | 7.32 | 522 | 0.35 | 1.00 | 0.64 | 0.00 |
| S03 | SET | S03[exit_session=close_auction,z_threshold=2.5] | rejected (no robust effect) | -2.13 | 0.01 | -2.17 | -1.92 | -1.71 | -2.02 | -65.33 | 0.40 | 895 | 0.64 | 1.00 | 0.82 | 0.00 |
| S04 | SET | S04[mode=follow,threshold_bps=100] | rejected (no robust effect) | -1.89 | -0.00 | -2.27 | -1.63 | -0.97 | -1.19 | -55.55 | -0.07 | 237 | 0.53 | 1.00 | 0.27 | 0.00 |
| S05 | SET | S05 | rejected (no robust effect) | 0.22 | 0.31 | -0.29 | 0.82 | -0.01 | -0.02 | 136.89 | 199.22 | 25 | 0.28 | 1.00 | 0.27 | 17500000.00 |
| S06 | SET | S06 | rejected (no robust effect) | -6.03 | -0.08 | -5.75 | -6.27 | -3.90 | -4.50 | -53.39 | -0.70 | 14935 | 0.32 | 1.00 | 1.00 | 0.00 |
| S08 | SET | S08 | anomaly present but not executable after costs | 0.42 | 0.58 | 0.47 | 1.36 | 0.08 | -0.43 | 5.48 | 7.49 | 932 | 1.00 | 1.00 | 0.18 | 560000000.00 |
| S01 | SGX | S01[hold_sessions=4,threshold_bps=200] | anomaly present but not executable after costs | -0.48 | 0.17 | -0.62 | -1.19 | -0.53 | -0.61 | -41.99 | 15.21 | 522 | 0.35 | 1.00 | 0.36 | 0.00 |
| S02 | SGX | S02[hold_sessions=4,threshold_bps=200] | rejected (no robust effect) | -0.68 | -0.08 | -0.98 | -0.63 | -0.51 | -0.62 | -63.85 | -7.46 | 480 | 0.35 | 1.00 | 0.45 | 0.00 |
| S03 | SGX | S03[exit_session=close_auction,z_threshold=2.5] | rejected (no robust effect) | -2.23 | -0.26 | -2.91 | -3.49 | -1.84 | -2.29 | -72.54 | -8.41 | 682 | 0.57 | 1.00 | 0.73 | 0.00 |
| S04 | SGX | S04[threshold_bps=100] | anomaly present but not executable after costs | -0.74 | 4.54 | 0.41 | -0.88 | -0.59 | -1.19 | -6.58 | 41.58 | 1093 | 0.58 | 1.00 | 0.09 | 130000.00 |
| S05 | SGX | S05 | rejected (no robust effect) | 0.13 | 0.19 | 0.01 | -0.62 | 0.06 | 0.07 | 152.39 | 212.66 | 12 | 0.11 | 1.00 | 0.55 | 1300000.00 |
| S06 | SGX | S06 | rejected (no robust effect) | -8.49 | -0.31 | -8.78 | -8.35 | -5.61 | -6.25 | -54.42 | -1.95 | 12279 | 0.41 | 1.00 | 1.00 | 0.00 |
| S07 | SGX | S07[threshold_bps=200] | anomaly present but not executable after costs | 0.80 | 1.67 | 1.31 | 0.89 | 0.61 | 0.54 | 28.35 | 64.19 | 307 | 0.09 | 1.00 | 0.09 | 5200000.00 |
| S08 | SGX | S08[exit=open,threshold_bps=20] | anomaly present but not executable after costs | 0.36 | 0.85 | -0.95 | 0.26 | -0.44 | -1.55 | 2.12 | 5.12 | 261 | 1.00 | 0.97 | 0.09 | 20800000.00 |


## Hypotheses

- **S01 Closing Gap Reversal** - HKEX: rejected (no robust effect), SET: anomaly present but not executable after costs, SGX: anomaly present but not executable after costs
- **S02 Closing Gap Continuation** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S03 Pre-close Momentum** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S04 Auction Imbalance Prediction** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: anomaly present but not executable after costs
- **S05 Index Rebalance Price Pressure** - HKEX: rejected (no robust effect), SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S06 Cross-sectional Closing Reversal (market-neutral)** - HKEX: anomaly present but not executable after costs, SET: rejected (no robust effect), SGX: rejected (no robust effect)
- **S07 SGX Trade-at-Close Dislocation** - SGX: anomaly present but not executable after costs
- **S08 Closing Auction vs Futures Basis** - HKEX: rejected (no robust effect), SET: anomaly present but not executable after costs, SGX: anomaly present but not executable after costs

On **real** data every hypothesis is currently **untestable**: no licensed SET/SGX/HKEX auction, indicative, borrow, index-event or futures data is present in this environment.

## Pipeline validation (planted vs recovered)

The generator planted known reversal fractions; the pipeline's estimates from the generated prices:

| market | quantity | planted | estimated |
|---|---|---|---|
| SET | frac_gap_reverting_by_next_open | 0.000 | 0.108 |
| SET | frac_gap_reverting_next_open_to_next_close | 0.000 | -0.008 |
| SET | frac_gap_reverting_by_next_close | 0.000 | 0.099 |
| SGX | frac_gap_reverting_by_next_open | 0.000 | 0.174 |
| SGX | frac_gap_reverting_next_open_to_next_close | 0.000 | -0.001 |
| SGX | frac_gap_reverting_by_next_close | 0.000 | 0.172 |
| HKEX | frac_gap_reverting_by_next_open | 0.000 | 0.022 |
| HKEX | frac_gap_reverting_next_open_to_next_close | 0.000 | 0.019 |
| HKEX | frac_gap_reverting_by_next_close | 0.000 | 0.041 |


## Distinguishing anomalies from executable strategies

For the gap strategies the diagnostic C0->O1 and C0->C1 windows (trade at the closing price) are compared with the executable O1->C1 window and realised gross / net bps per trade in each study's `shortfall` block (dashboard section 12). The closing price is not known early enough to trade at it, except via SGX Trade-at-Close (S07), whose fills are capped by realised TAC volume.

## Multiple testing

135 discovery tests; 0 with BH q < 0.05; 0 with Holm p < 0.05 (one-sided: mean daily net return > 0).

## Artefacts

- `dashboard.html` - interactive dashboard
- `leaderboard.csv`, `multiple_tests.csv`, `selection.json`, `manifest.json`, `config.yaml`
- `studies/<S>_<MKT>/` - study.json, daily_<scenario>.parquet, trades_base.parquet, rejections_base.parquet, intents.parquet
