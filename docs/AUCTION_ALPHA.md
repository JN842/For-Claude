# Closing Auction Alpha Research - SET / SGX / HKEX

A reproducible research platform that tests whether closing-auction price
dislocations and structural order flows on the Stock Exchange of Thailand,
Singapore Exchange and Hong Kong Exchanges are statistically significant
**and** economically exploitable after spreads, fees, impact, fill
uncertainty, borrow constraints, financing and hedging.

> **Research and simulation only.** Nothing here connects to a broker or
> submits orders. No credentials are used or stored.
>
> **No real market data is present in this repository or environment.** All
> committed results come from clearly labelled synthetic data. Empirical
> profitability of any closing-auction strategy has **not** been established.

## Installation

Python 3.11+ (developed on 3.13).

```bash
pip install -r requirements-auction.txt
```

or, as a package:

```bash
pip install -e ".[dev]"
```

## Quick start

```bash
python -m auction_alpha data-audit
```

```bash
python -m auction_alpha run-study --strategy S01 --market SET
```

```bash
python -m auction_alpha run-study --strategy S05 --market SGX
```

```bash
python -m auction_alpha run-all --config config/experiments.yaml
```

```bash
python -m auction_alpha report --latest
```

The first command generates the synthetic dataset (deterministic, ~10 s) if it
is missing. `run-all` takes about 5-6 minutes on 4 cores. Results land in
`results/<run_id>/` with `dashboard.html`, `RESEARCH_SUMMARY.md`,
`leaderboard.csv`, `selection.json`, per-study artefacts and the exact config,
data version, rules-registry hash and git commit.

Other commands:

| Command | Purpose |
|---|---|
| `python -m auction_alpha rules [--market SGX]` | print the effective-dated rules registry with verification status |
| `python -m auction_alpha leakage-check` | run the temporal-leakage suite (truncation / perturbation invariance, deadlines, PIT checks) |
| `python -m auction_alpha generate-data --dataset synthetic_null` | build the null-world negative-control dataset |
| `python -m auction_alpha ingest --mapping config/my_mapping.yaml` | ingest licensed vendor files (see `config/ingest_template.yaml`) |
| `python -m auction_alpha docs` | regenerate `MARKET_RULES.md`, `DATA_DICTIONARY.md`, `STRATEGIES.md` |
| `python -m auction_alpha report --latest --offline` | self-contained dashboard (embeds plotly.js) |
| `python -m auction_alpha run-all --unlock-holdout --selection results/<id>/selection.json` | evaluate the frozen selection on the 2026 holdout |

Tests:

```bash
python -m pytest tests/auction_alpha -q
```

## Repository layout

```
auction_alpha/
  rules/            registry.py (effective-dated, verification status), adapters.py (SET/SGX/HKEX),
                    registry_data/*.yaml (the versioned market-rules registry)
  calendars.py      observed / CSV / synthetic trading calendars
  data/             schemas.py, store.py (Parquet + DuckDB + SQLite), validation.py, ingest.py,
                    availability.py (availability & strategy-support matrices), synthetic.py
  features/         point-in-time features with availability timestamps; FeatureView guard
  strategies/       S01-S08 (gap.py, preclose.py, imbalance.py, rebalance.py, basis.py)
  execution/        auction.py (state machines, uncrossing), scenarios.py, simulator.py
  backtest/         engine.py, portfolio.py (cash-flow accounting), event_study.py
  stats/            inference.py (Newey-West, block bootstrap, BH/Holm), metrics.py
  reporting/        dashboard.py (interactive HTML), reports.py (markdown)
  leakage.py        automated temporal-leakage tests
  experiments.py    run-study / run-all protocol
  cli.py
config/             experiments.yaml, experiments_null.yaml, scenarios.yaml, ingest_template.yaml
data/sample/        small committed SYNTHETIC sample dataset
docs/               methodology, rules, data dictionary, feasibility, strategies, assumptions, status
reports/            published artefacts of the reference runs (synthetic)
tests/auction_alpha automated test suite
```

## Documents

| Document | Content |
|---|---|
| [METHODOLOGY.md](METHODOLOGY.md) | diagnostic vs executable, look-ahead controls, execution, accounting, statistics, verdicts |
| [MARKET_RULES.md](MARKET_RULES.md) | the versioned rules registry with sources and verification status |
| [DATA_FEASIBILITY.md](DATA_FEASIBILITY.md) | availability matrix, strategy support, rule verification counts |
| [DATA_DICTIONARY.md](DATA_DICTIONARY.md) | every table and column, with time roles |
| [STRATEGIES.md](STRATEGIES.md) | S01-S08 specifications (hypothesis, data, signal, entry/exit, timing, order type, costs, hedging, risk, validation, failure modes) |
| [ASSUMPTIONS.md](ASSUMPTIONS.md) | every modelling assumption and where to change it |
| [UNRESOLVED_DATA_REQUIREMENTS.md](UNRESOLVED_DATA_REQUIREMENTS.md) | prioritised list of missing data |
| [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) | which hypotheses were supported, rejected or untestable |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | milestones, test results, known limitations |
| [REPRODUCIBILITY.md](REPRODUCIBILITY.md) | how to reproduce every committed number |
