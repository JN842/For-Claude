# Research Methodology

This document describes how `auction_alpha` separates historical statistical
anomalies from executable trading strategies, and every safeguard against
look-ahead bias.

## 1. Two modes, never mixed

| | Mode A - diagnostic event study | Mode B - causal backtest |
|---|---|---|
| Purpose | Is there a statistical pattern? | Could a live system have made money? |
| Inputs | may use forward labels (`fwd_*` columns) | only features whose availability time precedes the order |
| Prices | close-to-close, close-to-open | simulated fills under explicit execution scenarios |
| Output label | `DIAGNOSTIC - NOT EXECUTABLE` | net P&L after all costs |
| Code | `backtest/event_study.py`, `Strategy.diagnostic()` | `Strategy.generate_intents()` → `execution/simulator.py` → `backtest/portfolio.py` |

A closing-gap event study that measures returns *from the closing-auction
price* assumes a trade at a price that was only known after the auction
matched. No live order could have been sized and filled at it. The only
exception in these three markets is SGX Trade-at-Close (S07), where orders
trade at the already-known closing price - and there the simulator caps fills
by the realised TAC volume.

For every gap strategy the study reports a **shortfall** block: diagnostic
C0→O1 and C0→C1 means (non-executable), the executable O1→C1 window, and the
realised gross and net bps per trade.

## 2. Time stamps

Three times are stored separately (see `docs/DATA_DICTIONARY.md`):

* **event time** - when it happened (e.g. `close_auction_ts`, the actual random-close match);
* **receipt time** - when the research system received it (`close_auction_receipt_ts` for
  a real-time feed, `receipt_ts` for end-of-day files);
* **availability time** - computed per feature (`<feature>__avail`), never earlier than the
  receipt time of each input, plus a configurable feed latency.

Every order intent carries `signal_ts` (latest availability of its inputs) and
`submit_ts ≥ signal_ts + decision latency`. The simulator raises
`LeakageError` if any intent violates this - it is a code bug, not a
rejection.

## 3. Look-ahead controls (automated - `auction_alpha/leakage.py`, `tests/auction_alpha/test_leakage.py`)

| Risk | Control |
|---|---|
| Future price leakage | `FeatureView` blocks `fwd_*`/`L_*` columns; truncation invariance (intents before a cutoff identical with/without later data); perturbation invariance (scrambling post-cutoff prices changes nothing before it). A deliberately peeking strategy is included in the tests and must be flagged. |
| Future corporate actions | returns across an ex-date use the adjustment only if `announce_ts` precedes the session; otherwise the feature is NaN and `ca_unannounced` is set. Realised P&L uses actual cash flows. |
| Survivorship | universe on each date = securities with a row that date; delisted securities are present before delisting; delisted positions are force-exited at the last price with a scenario haircut. |
| Point-in-time index membership | rebalance trades use announcement **receipt** time; the index trade day is derived from the published effective date and the exchange calendar (treated as known in advance). |
| Post-auction information | closing-auction orders must be in the book before the random-close window opens (`order_deadline=random_start`); the realised close time is never assumed known. |
| Stale / missing data | missing last-continuous prices and missing rows are skipped, never back-filled; selection effects are counted (`n_excluded_missing_next_day`, data-quality report). |
| Selecting on future availability | caught by truncation invariance (S08 originally dropped signals whose next-day futures row was missing - fixed). |
| Holdout snooping | intents dated in the holdout are rejected by the engine unless `--unlock-holdout` is passed **with** a frozen `selection.json`. |

Known limitation: `security_master.shares_outstanding` is a single latest value
(not point-in-time). It is used only for market-cap buckets in diagnostics,
never in strategy signals.

## 4. Execution simulation

Historical order-level data is unavailable, so fills are **scenario-based**
(`execution/scenarios.py`, `config/scenarios.yaml`):

| scenario | auction participation | fill ratio | impact coef | extra slippage | short in auction when rule unverified |
|---|---|---|---|---|---|
| optimistic | 20% | 1.00 | 0.5 | 0 bps | allow |
| base | 10% | 0.90 | 1.0 | 0 bps | allow |
| conservative | 5% | 0.75 | 1.5 | 2 bps | reject |
| stress | 2.5% | 0.50 | 2.5 | 5 bps | reject |

Per order: board-lot rounding with an **ex-ante** price; exchange-specific
state machine (SET pre-close/ATC, SGX non-cancel/TAC, HKEX CAS stages);
eligibility (suspension, HKEX CAS eligibility, TAC in force, no auction
match); short-sale check (borrow located with receipt before submit,
quantity available, HKEX CAS short-price rule, unverified auction short
rules); price-limit lock (SET); fill = min(qty × fill_ratio, cap × realised
session volume); square-root impact `k·σ·√(q/ADV)`; registry fees and taxes;
exits delayed through suspensions and carried forward when liquidity is
insufficient, forced after `max_exit_delay_days`. **Every rejected or reduced
order is logged with a reason code.** Each lookup of a market rule is counted
with its verification label (`verified_official`, `secondary_unverified`,
`+historically_assumed`) and reported per study.

## 5. Accounting

`backtest/portfolio.py`: each trade keeps its own cash ledger; NAV = capital +
Σ trade P&L − funding. Daily returns = P&L / fixed capital (no compounding),
including flat days. Splits rescale quantity, dividends credit longs and debit
shorts on the ex-date, borrow fees accrue per calendar day on short value,
funding is charged on overnight long exposure above capital. Futures hedges
are separate positions marked at the futures close. Long, short and hedge
P&L are attributed separately; gross P&L adds back fees, slippage/impact,
borrow and funding.

Sharpe, Sortino, drawdown and annualised returns come only from the daily
portfolio series. Win rate, profit factor and bps per trade are trade-level
and are never annualised.

## 6. Statistics

* Newey-West HAC t-statistics on daily returns; event studies cluster by date
  (average events per date first).
* Stationary block bootstrap (Politis-Romano) CIs for mean return and Sharpe.
* Multiple testing: BH-FDR and Holm over **every** grid point × strategy ×
  market tested in discovery, using **one-sided** Newey-West p-values for
  H1: mean daily net return > 0. (A first version used two-sided p-values; in
  the null world that produced 94/135 "discoveries" because strategies that
  reliably *lose* money after costs are highly significant two-sided, which
  also loosens the BH threshold for every other test.)
* Placebo: identical timing and sizing with random signs.
* Sensitivities: cost multiplier (fees, spread, impact together), fill ratio,
  participation cap, decision latency, entry session (opening auction vs
  continuous), capital sweep (capacity), hedged vs unhedged, volatility and
  direction regimes.
* Chronological split: discovery 2018-2021, validation 2022-2023, OOS
  2024-2025, holdout 2026 YTD (data end 2026-10-30; 2026 is **not** a full year).
* Matched controls in the gap event study: same date, same liquidity and
  volatility tercile, small gap.

## 7. Verdicts

Per (strategy, market):

* **supported - executable after costs**: discovery BH q < 0.05, positive net
  Sharpe in validation AND OOS, positive net Sharpe in the conservative
  scenario, placebo p ≤ 0.10.
* **anomaly present but not executable**: gross Sharpe > 0.5, or a diagnostic
  effect with p < 0.01 *in the hypothesised direction*, without passing the
  executable test.
* **rejected - no robust effect**: neither.
* **untestable**: required data missing (on real data: every strategy, today).

## 8. A lesson from the null world: tick discreteness

The synthetic **null** world has no auction-pressure reversal at all, yet the
diagnostic C0→O1 window shows a highly significant "reversal" (≈ 12 bps, t ≈
16 in a 30-stock SET-like sample). Cause: prices are rounded to the tick grid
independently at each print, so the rounding error in the close enters the
measured gap with a + sign and the next return with a − sign - the same
mechanism as bid-ask bounce. The executable O1→C1 window shows nothing (p ≈
0.25). Real closing-gap studies on SET (tick 0.25 THB for prices 25-100 ≈
25-100 bps) and SGX (S$0.005 below S$1) are exposed to exactly this artefact,
which is why a significant diagnostic event study is never treated as
evidence of an edge.
