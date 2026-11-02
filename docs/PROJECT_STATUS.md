# Project Status

_Last updated 2026-11-02._

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| **M1** Market rules and data feasibility | done (rules partly verified) | Effective-dated registry for SET, SGX, HKEX with per-rule verification status. SET and HKEX official pages read (trading hours, closing price calculation, order types, price bands, tick sizes, board lots; CAS FAQ, trading hours, VCM and fees pages). **SGX could not be read** (site returned an unsupported-browser shell, rulebook URLs 404) - all SGX rules are `secondary_unverified`. Data feasibility: no real data available; availability matrix and strategy-support matrix generated (`docs/DATA_FEASIBILITY.md`). |
| **M2** Data pipeline and validation | done | Canonical schemas (12 tables) with event / receipt / availability time; Parquet + DuckDB + SQLite store with content-hashed data versions; schema and causality validation; vendor ingestion via mapping YAML; synthetic generator (planted and null worlds) with edge cases; committed sample. |
| **M3** Core event studies and backtesting | done | Point-in-time features; S01 baseline end to end first, then S02, S03, S06; date-clustered event studies with matched controls; cash-flow accounting. |
| **M4** Advanced auction strategies and execution scenarios | done (synthetic only) | S04 (indicative imbalance, latency curve), S05 (index rebalance), S07 (SGX TAC), S08 (futures basis); exchange state machines; four execution scenarios; rejection log with reason codes; futures hedging. |
| **M5** Robustness, reporting, dashboard | done | Discovery-only selection frozen in `selection.json`; locked holdout; bootstrap CIs; one-sided BH/Holm; placebo; cost / fill / participation / latency / entry-session / capacity / hedge / regime sensitivity; interactive dashboard; markdown reports; null-world negative control. |

## Engineering acceptance

| Criterion | Status |
|---|---|
| Exchange adapters for all three markets | ✓ `auction_alpha/rules/adapters.py` |
| Historical rules effective-dated where verified | ✓ (where any date is known; unknown start dates are flagged `historically_assumed` at every lookup) |
| Market data schemas validated | ✓ `data/validation.py` (schema, PK, tz-aware timestamps, receipt ≥ event, auction ts in window, listing life, gaps) |
| Synthetic datasets cover normal and edge cases | ✓ suspensions, delistings, late listings, missing rows / LCP, no-match auctions, HKEX non-CAS, SGX zero-TAC days, half-days, splits, dividends, borrow restrictions, SET limit clipping |
| Point-in-time safeguards pass automated tests | ✓ `leakage-check`: 78/78 on the tiny dataset; pytest leakage tests incl. a deliberately peeking strategy that must be caught |
| Supported strategies execute end-to-end | ✓ S01-S08 on all applicable markets (synthetic) |
| Unsupported strategies documented | ✓ strategy-support matrix; on real data every strategy is untestable today; S04 requires indicative data, S07 real-time auction receipt and TAC volume, S08 aligned futures quotes |
| Costs and execution assumptions auditable | ✓ `config/scenarios.yaml`, registry fees with status, per-fill fees/slippage, rejection log, rule-usage counts per study |
| Research outputs reproducible | ✓ seeded generator, content-hashed data versions, config hash, git commit in every manifest |
| Dashboard renders actual experiment results | ✓ built from saved artefacts; checked in headless Chromium (no console errors, light and dark, 390 px width) |
| Full test suite passes | ✓ 111 tests (84 new + 27 existing dividend-model tests) |
| Installation and run instructions | ✓ `docs/AUCTION_ALPHA.md`, `docs/REPRODUCIBILITY.md` |
| No real-money orders | ✓ no broker connectivity exists in the code |
| No credentials committed | ✓ none used |

## Research acceptance

Not achievable without real data. See `docs/RESEARCH_SUMMARY.md`: on real
markets every hypothesis is **untestable**; on synthetic data the pipeline
recovers planted effects and produces no false "executable edge" in the null
world.

## Bugs found by the platform's own checks during development

* Validation rejected generated data where tick rounding after price-limit
  clipping put the high below the close - generator fixed.
* The truncation-invariance test caught S08 dropping signals whose next-day
  futures row was missing (selection on future data) - fixed.
* The first truncation test used an inclusive cutoff and missed a one-day
  look-ahead at the boundary - now strict, with a peeking strategy in the tests.
* Funding was accrued on end-of-day instead of overnight exposure - fixed, with a test.
* Two-sided p-values let reliably losing strategies inflate the BH family
  (94/135 "discoveries" in the null world) - switched to one-sided tests.

## Known limitations

* No real data; every number in `reports/` is synthetic.
* SGX rules unverified; HKEX tick-size changes around 2025 not captured;
  SET random-close and short-sale rule history unknown.
* Execution is scenario-based (no order-book replay); TAC counterparties are
  not modelled as adverse selection, which flatters S07.
* Index-rebalance studies have very few events (~10-30 per market), so power
  is low even for a 150 bps planted effect.
* `shares_outstanding` is not point-in-time (diagnostic buckets only).
* No FX conversion; markets are evaluated separately in local currency.
