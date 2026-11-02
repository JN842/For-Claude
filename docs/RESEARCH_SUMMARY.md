# Final Research Summary

_2026-11-02. Reference runs: `reports/synthetic_planted/` (run `20261102T065011Z_dd44d84e8b`) and
`reports/synthetic_null/` (run `20261102T065434Z_948dec5e9e`), code `73e5c66`._

## 1. Bottom line

**Empirical profitability of closing-auction strategies on SET, SGX and HKEX
has not been established.** No real market data - closing-auction prints,
indicative imbalances, borrow, index announcements or futures quotes - was
available in this environment, so every hypothesis is **untestable on real
data** today. What this project delivers is a research platform whose
correctness has been tested on synthetic worlds with known answers, plus a
prioritised list of the data needed to run it for real
(`docs/UNRESOLVED_DATA_REQUIREMENTS.md`).

## 2. Hypothesis status

| ID | Hypothesis | Real data | Planted synthetic world | Null synthetic world |
|---|---|---|---|---|
| S01 | Closing gap reversal | **untestable** | anomaly, not executable (all 3 markets) | anomaly flagged on SET/SGX from tick discreteness only; rejected HKEX |
| S02 | Closing gap continuation | **untestable** | rejected (all) | rejected (all) |
| S03 | Pre-close momentum | **untestable** | rejected (all) - none planted | rejected (all) |
| S04 | Auction imbalance prediction | **untestable** (needs archived indicative data) | anomaly, not executable (all) | rejected SET/HKEX; anomaly SGX (tick artefact) |
| S05 | Index rebalance price pressure | **untestable** (needs PIT index announcements) | rejected - underpowered (≈ 10-30 events/market) | rejected |
| S06 | Cross-sectional closing reversal | **untestable** | anomaly, not executable (gross Sharpe 1.1-1.8, net −4.7 to −7.4) | rejected SET/SGX; anomaly HKEX (gross 0.6) |
| S07 | SGX Trade-at-Close dislocation | **untestable** (needs TAC volumes and real-time auction receipt) | **executable after costs** - capacity-bound | anomaly, not executable |
| S08 | Closing auction vs futures basis | **untestable** (needs aligned futures quotes) | no robust effect (SET/HKEX); anomaly SGX | no robust effect / anomaly |

Verdict definitions are in `docs/METHODOLOGY.md` §7: *executable* requires
one-sided BH q < 0.05 in discovery, positive net Sharpe in validation, OOS
and the conservative scenario, and placebo p ≤ 0.10.

## 3. What the synthetic experiments show (engineering findings, not market findings)

**The protocol has power and controls false positives.**
* Null world: 0 of 135 discovery tests pass BH (one-sided), 0 executable verdicts.
* Planted world: 2 of 135 pass BH; the one study meeting every condition is
  S07 on SGX, which is exactly where the planted overnight reversal can be
  traded at a known price.
* The pipeline recovers the planted reversal fractions: by next open
  0.375 / 0.382 / 0.332 (SET / SGX / HKEX) vs 0.383 planted; by next close
  0.51 / 0.48 / 0.52 vs 0.60 planted (attenuated by tick rounding and
  missing-data exclusions).

**Most of a closing-gap reversal is not executable.** In the planted world
the diagnostic C0→O1 window (trade at the closing price) shows ≈ 51-58 bps per
event; the executable O1→C1 window keeps only 17-29 bps, because most of the
reversal happens overnight, before any order placed on the known close can
fill (except via SGX TAC). After costs, S01's best discovery parameters
leave 5-62 bps per trade with weak, period-unstable Sharpe (validation
−0.40 to 0.32), so it never qualifies.

**Costs dominate high-turnover neutral portfolios.** S06 earns 13 bps gross per
trade on SET in the planted world but turns its capital over ≈ 34x a year;
its net Sharpe is 1.59 frictionless, −1.59 at half the base costs and −4.71 at
base costs.

**SGX TAC is the only venue where trading at a known close is possible - and it
is tiny.** S07 fills only ≈ 10% of intended size (TAC volume capped at 15%
participation; 40% of synthetic days have no TAC trades). Its annual net P&L
saturates near SGD 11-12k regardless of capital - capacity, not signal, is
the binding constraint. In the null world S07 still shows a positive net
Sharpe (0.80, q = 1.0) because trading *at* a tick-rounded print captures the
rounding reversal, and the simulator does not model adverse selection by TAC
counterparties. Real TAC data would be needed to say anything about S07.

**Diagnostic event studies can mislead.** In the null world - no pressure
reversal at all - the C0→O1 window still shows a significant "reversal"
(e.g. ≈ 12 bps, t ≈ 16 in a 30-stock SET-like test sample) because tick
rounding error in the close enters the gap with + sign and the next return
with − sign (the bid-ask-bounce mechanism). The executable window shows
nothing. Real SET (tick 0.25 THB for 25-100 THB stocks) and SGX (S$0.005 below
S$1) gaps are exposed to the same artefact.

**Index-rebalance studies are underpowered.** With a 30-stock index reviewed
twice a year there are only 13-28 events per market in nine years; a planted
150 bps pre-event pressure produced a signed CAR of 343 bps with p = 0.054
(SET, planted) versus 208 bps, p = 0.13 in the null world. Real studies need
pooled indices (SET50/SET100, STI, HSI/HSCEI, MSCI/FTSE) and many years.

**Indicative-price predictability decays with latency.** On synthetic data
the R² of the final auction gap on the last usable indicative gap falls from
0.996 (≤ 5 s latency) to 0.82 (120 s). The level is an artefact of the
generator (the synthetic IEP converges to the final price by construction);
the latency-decay measurement is the reusable part.

## 4. Rules verification

| Market | verified_official | secondary_unverified | assumption | unknown |
|---|---|---|---|---|
| SET | 13 | 5 | 1 | 1 |
| HKEX | 11 | 14 | 1 | 1 |
| SGX | 0 | 21 | 1 | 2 |

SGX's website and rulebook could not be read from this environment, so every
SGX rule - including the Trade-at-Close mechanics that S07 depends on - is
unverified. For SET and HKEX the current rules were read from official pages,
but almost none publish effective dates, so every backtest date before
2026-11-02 that relies on them is flagged `historically_assumed`.

## 5. What it would take to reach an empirical conclusion

In priority order (details in `docs/UNRESOLVED_DATA_REQUIREMENTS.md`):
1. daily bars with the closing-auction decomposition, including delisted securities;
2. actual match timestamps and real-time receipt of auction results;
3. point-in-time borrow availability and short-sale eligibility;
4. archived indicative auction data (S04), SGX TAC trades (S07), PIT index
   announcements (S05), auction-aligned futures quotes (S08);
5. the official SGX rulebook and historical rule-change dates for all three exchanges.

With P1-P3 ingested (`python -m auction_alpha ingest`), S01, S02, S03 and S06
become `supported_real` and the same protocol - selection frozen on
2018-2021, validation 2022-2023, OOS 2024-2025, holdout 2026 YTD - can be run
unchanged.
