"""Causal backtest engine (mode B).

Pipeline per (strategy, market, scenario)::

    features (PIT) -> strategy intents -> leakage checks -> execution simulator
        -> cash-flow accounting -> daily metrics (+ per research period)

The final holdout period is excluded from signal generation unless the
caller passes ``holdout_unlocked=True``, which ``experiments`` only allows
once a frozen strategy-selection record exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..execution.scenarios import SCENARIOS, ExecutionScenario
from ..execution.simulator import ExecutionSimulator, MarketData, SimResult
from ..features.engineering import FeatureView, build_features
from ..rules.adapters import get_adapter
from ..stats.metrics import performance
from .portfolio import Accounting, account

DEFAULT_PERIODS = {
    "discovery": ("2018-01-01", "2021-12-31"),
    "validation": ("2022-01-01", "2023-12-31"),
    "oos": ("2024-01-01", "2025-12-31"),
    "holdout": ("2026-01-01", "2026-12-31"),
}


@dataclass
class MarketBundle:
    market: str
    tables: dict
    features: pd.DataFrame
    md: MarketData
    calendar: pd.DatetimeIndex
    periods_per_year: float


def prepare_market(tables: dict, market: str, *, feed_latency_s: float = 1.0) -> MarketBundle:
    feats = build_features(tables, market, feed_latency_s=feed_latency_s)
    md = MarketData(market, tables["daily_bars"], borrow=tables.get("borrow"), futures=tables.get("futures_daily"),
                    corporate_actions=tables.get("corporate_actions"))
    md.bars = md.bars.merge(tables["security_master"][["market", "symbol", "board_lot"]], on=["market", "symbol"], how="left")
    md.cols["board_lot"] = md.bars["board_lot"].to_numpy()
    cal = md.dates
    yrs = pd.Series(cal.year).value_counts()
    full = yrs[yrs >= 200]
    ppy = float(full.mean()) if len(full) else 250.0
    return MarketBundle(market, tables, feats, md, cal, ppy)


@dataclass
class ScenarioRun:
    scenario: str
    sim: SimResult
    acc: Accounting
    metrics: dict
    period_metrics: dict = field(default_factory=dict)
    yearly: pd.DataFrame | None = None


@dataclass
class StrategyRun:
    strategy_id: str
    label: str
    market: str
    params: dict
    n_intents: int
    runs: dict
    intents: pd.DataFrame
    signal_period_end: str = ""


def signal_dates(cal: pd.DatetimeIndex, periods: dict, *, holdout_name: str = "holdout",
                 holdout_unlocked: bool = False) -> pd.DatetimeIndex:
    if holdout_unlocked or holdout_name not in periods:
        return cal
    a, _ = periods[holdout_name]
    return cal[cal < pd.Timestamp(a)]


def run_strategy(strategy, bundle: MarketBundle, *, capital: float, scenarios: list[ExecutionScenario] | None = None,
                 periods: dict | None = None, holdout_unlocked: bool = False, decision_latency_s: float = 2.0,
                 funding_spread_bps: float = 100.0, n_boot: int = 300, commission_bps: float | None = None,
                 bootstrap: bool = True) -> StrategyRun:
    from ..strategies.base import StrategyContext

    periods = periods or DEFAULT_PERIODS
    scenarios = scenarios or [SCENARIOS["base"]]
    adapter = get_adapter(bundle.market, commission_bps=commission_bps)
    sd = signal_dates(bundle.calendar, periods, holdout_unlocked=holdout_unlocked)
    ctx = StrategyContext(bundle.market, FeatureView(bundle.features), bundle.tables, adapter, bundle.md,
                          capital, decision_latency_s, sd)
    intents = strategy.generate_intents(ctx)
    if not holdout_unlocked and len(intents) and "holdout" in periods:
        ho = pd.Timestamp(periods["holdout"][0])
        assert (pd.to_datetime(intents["signal_date"]) < ho).all(), "holdout leakage: intents in locked holdout"
    runs = {}
    for sc in scenarios:
        if getattr(strategy, "instrument", "stock") == "future":
            if len(intents) and (intents["submit_ts"] < intents["signal_ts"]).any():
                from ..features.engineering import LeakageError
                raise LeakageError("futures intents submitted before signal availability")
            sim = strategy.simulate(intents, bundle.md, sc, adapter, capital)
        else:
            sim = ExecutionSimulator(bundle.md, sc, adapter).run(intents)
        acc = account(sim.fills, bundle.md.bars, capital=capital, corporate_actions=bundle.tables.get("corporate_actions"),
                      futures=bundle.tables.get("futures_daily"), market=bundle.market,
                      funding_spread_bps=funding_spread_bps, calendar=bundle.calendar)
        daily = _full_calendar(acc.daily, bundle.calendar, sd, capital)
        acc = Accounting(daily, acc.trades)
        met = performance(daily, acc.trades, capital=capital, periods_per_year=bundle.periods_per_year,
                          bootstrap=bootstrap, n_boot=n_boot)
        met["fill_rate"] = _fill_rate(sim)
        met["rejections"] = int(len(sim.rejections))
        met["rule_usage"] = sim.rule_usage
        pm = {}
        for name, (a, b) in periods.items():
            if name == "holdout" and not holdout_unlocked:
                continue
            dd = daily[(daily["date"] >= pd.Timestamp(a)) & (daily["date"] <= pd.Timestamp(b))]
            tt = acc.trades[(acc.trades["entry_date"] >= pd.Timestamp(a)) & (acc.trades["entry_date"] <= pd.Timestamp(b))] if not acc.trades.empty else acc.trades
            pm[name] = performance(dd, tt, capital=capital, periods_per_year=bundle.periods_per_year, bootstrap=False)
        yearly = None
        if not daily.empty:
            yearly = daily.assign(year=daily["date"].dt.year).groupby("year").agg(
                pnl_net=("pnl", "sum"), pnl_gross=("pnl_gross", "sum"), days=("pnl", "size")).reset_index()
            yearly["ret_net"] = yearly["pnl_net"] / capital
            yearly["ret_gross"] = yearly["pnl_gross"] / capital
        runs[sc.name] = ScenarioRun(sc.name, sim, acc, met, pm, yearly)
    return StrategyRun(strategy.id, strategy.label(), bundle.market, dict(strategy.params), len(intents), runs, intents,
                       str(sd[-1].date()) if len(sd) else "")


def _full_calendar(daily: pd.DataFrame, cal: pd.DatetimeIndex, sd: pd.DatetimeIndex, capital: float) -> pd.DataFrame:
    """Reindex onto every session of the evaluation span so flat days count as zero return."""
    span = cal[(cal >= sd[0]) & (cal <= sd[-1])] if len(sd) else cal
    if daily is None or daily.empty:
        d = pd.DataFrame({"date": span})
        for c in ("pnl", "pnl_gross", "pnl_long", "pnl_short", "pnl_hedge", "fees", "slippage", "borrow", "funding",
                  "dividends", "long_mv", "short_mv", "hedge_notional", "gross_exposure", "turnover", "n_positions", "ret"):
            d[c] = 0.0
        d["nav"] = capital
        return d
    full = cal[(cal >= min(span[0], daily["date"].min())) & (cal <= max(span[-1], daily["date"].max()))]
    d = daily.set_index("date").reindex(full)
    flow_cols = ["pnl", "pnl_gross", "pnl_long", "pnl_short", "pnl_hedge", "fees", "slippage", "borrow", "funding",
                 "dividends", "turnover", "ret"]
    d[flow_cols] = d[flow_cols].fillna(0.0)
    d[["long_mv", "short_mv", "hedge_notional", "gross_exposure", "n_positions"]] = d[
        ["long_mv", "short_mv", "hedge_notional", "gross_exposure", "n_positions"]].fillna(0.0)
    d["nav"] = capital + d["pnl"].cumsum()
    return d.reset_index().rename(columns={"index": "date"})


def _fill_rate(sim: SimResult) -> float:
    t = sim.trades
    if t is None or t.empty:
        return float("nan")
    req = t["intended_qty"].sum()
    return float(t["entry_qty"].sum() / req) if req > 0 else float("nan")
