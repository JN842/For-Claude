# Assumptions Register

Every assumption that can move a result, where it lives, and how to change it.
Rule-level assumptions and their verification status are in
`docs/MARKET_RULES.md`; this file covers modelling choices.

## Data

| # | Assumption | Where | Notes |
|---|---|---|---|
| D1 | No real market data is available; all results so far are on synthetic data | `config/experiments.yaml: dataset` | Ingest licensed data via `python -m auction_alpha ingest` |
| D2 | Trading calendar = dates present in the daily file (real data) or a synthetic calendar | `calendars.py` | Official holiday calendars not loaded (`calendar.holidays: unknown`) |
| D3 | Exchange calendars are known in advance (used to compute future exit / index trade dates) | `leakage.py` docstring | True in practice (holiday schedules are published annually) |
| D4 | When a vendor gives no receipt time, receipt = end-of-day 18:00 local | `config/ingest_template.yaml` | Conservative: never earlier than the event |
| D5 | Shares outstanding are not point-in-time | `security_master` | Only used for diagnostic market-cap buckets |
| D6 | Synthetic random close drawn per security uniformly in the rule window | `data/synthetic.py` | Whether the real random close is per security or market-wide is not verified |

## Execution

| # | Assumption | Default (base) | Where |
|---|---|---|---|
| E1 | Max share of realised closing-auction volume we can take | 10% | `config/scenarios.yaml` |
| E2 | Max share of opening-auction volume | 10% | scenarios |
| E3 | Max share of daily volume for continuous-session fills | 2% | scenarios |
| E4 | Max share of realised SGX TAC volume | 15% | scenarios |
| E5 | Fraction of eligible quantity that matches | 0.90 | scenarios |
| E6 | Square-root impact `k·σ20·√(q/ADV20)`, k = 1.0 (0.5 / 1.5 / 2.5 in other scenarios) | | `simulator._impact_px` |
| E7 | Continuous-session fills pay the pre-close half-spread × multiplier | | `simulator._session_px_vol` |
| E8 | Closing-auction orders must be submitted before the random-close window | `order_deadline: random_start` | scenarios |
| E9 | Short sales in auctions allowed where the rule is unverified (base/optimistic); rejected (conservative/stress) | | scenarios |
| E10 | SET daily price-limit lock: auction print at the limit fills 0% (base) / 50% (optimistic) | | scenarios |
| E11 | Exits delayed through suspensions up to 10 sessions, then forced at last close with haircut 10% (base) | | scenarios |
| E12 | Futures hedge: per-trade beta hedge, whole contracts, cost bps from registry (assumption) | | `simulator._hedge` |
| E13 | Commission per side: SET 8 bps, SGX 8 bps, HKEX 5 bps | registry `fees.commission_bps` (status `assumption`) | |
| E14 | Decision latency 2 s; feed latency 1 s for last-continuous-price features | `config/experiments.yaml`, `features` | Sensitivity runs 0-300 s |

## Accounting and capital

| # | Assumption | Default |
|---|---|---|
| A1 | Capital per market (≈ USD 1m): SET 35m THB, SGX 1.3m SGD, HKEX 8m HKD | `config/experiments.yaml` |
| A2 | Returns are excess of the cash rate; funding spread 100 bps p.a. on long exposure above capital | |
| A3 | Short proceeds earn no rebate | `portfolio.py` |
| A4 | Borrow fee fixed at entry for the life of a trade | `simulator` |
| A5 | No FX conversion: each market is evaluated in local currency; cross-market figures are not summed | |
| A6 | Per-position size = min(capital × 5%, 1% of ADV20) (strategy param) | strategy defaults |

## Statistical protocol

| # | Assumption | Default |
|---|---|---|
| S1 | Selection metric: discovery net Sharpe, base scenario, ≥ 20 trades | `selection` |
| S2 | FDR family = all grid points × strategies × markets in discovery | `experiments.summarise` |
| S3 | Bootstrap mean block length 10 days (5 for event studies) | `stats/inference.py` |
| S4 | Placebo = 10 random-sign replications | `robustness.placebo_n` |
| S5 | Verdict thresholds: q < 0.05, validation & OOS & conservative Sharpe > 0, placebo p ≤ 0.10 | `experiments.verdict` |
