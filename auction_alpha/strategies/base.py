"""Strategy framework.

A strategy turns point-in-time features into *order intents*.  It never sees
forward-looking columns (``FeatureView`` blocks them) and must stamp each
intent with ``signal_ts`` (latest availability time of every input) and
``submit_ts`` (>= signal_ts + decision latency).  The simulator - not the
strategy - decides fills.

Each strategy also exposes a *diagnostic* study (mode A) that may use forward
labels.  Diagnostic output is always labelled NOT EXECUTABLE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..execution.simulator import INTENT_COLUMNS, MarketData
from ..features.engineering import FeatureView
from ..rules.adapters import ExchangeAdapter


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    hypothesis: str
    required_data: tuple[str, ...]
    signal_formula: str
    entry_rule: str
    exit_rule: str
    order_timing: str
    order_type: str
    cost_assumptions: str
    hedging: str
    risk_controls: str
    validation: str
    failure_modes: str
    markets: tuple[str, ...] = ()          # empty = all markets
    default_params: dict = field(default_factory=dict)
    param_grid: dict = field(default_factory=dict)
    primary_param: str = ""                 # parameter used for the threshold heatmap

    def as_markdown(self) -> str:
        p = lambda k, v: f"- **{k}:** {v}"
        return "\n".join([
            f"### {self.id}: {self.name}", "",
            p("Hypothesis", self.hypothesis),
            p("Required data", ", ".join(f"`{d}`" for d in self.required_data)),
            p("Markets", ", ".join(self.markets) if self.markets else "SET, SGX, HKEX"),
            p("Signal formula", self.signal_formula),
            p("Entry rule", self.entry_rule),
            p("Exit rule", self.exit_rule),
            p("Order timing", self.order_timing),
            p("Executable order type", self.order_type),
            p("Cost assumptions", self.cost_assumptions),
            p("Hedging assumptions", self.hedging),
            p("Risk controls", self.risk_controls),
            p("Statistical validation", self.validation),
            p("Failure modes", self.failure_modes),
            p("Default parameters", f"`{self.default_params}`"),
            p("Parameter grid (discovery period only)", f"`{self.param_grid}`"),
            "",
        ])


@dataclass
class StrategyContext:
    market: str
    features: FeatureView
    tables: dict[str, pd.DataFrame]
    adapter: ExchangeAdapter
    md: MarketData
    capital: float
    decision_latency_s: float = 2.0
    signal_dates: pd.DatetimeIndex | None = None   # dates on which signals may be generated

    def allowed(self, dates: pd.Series) -> pd.Series:
        if self.signal_dates is None:
            return pd.Series(True, index=dates.index)
        return pd.to_datetime(dates).isin(self.signal_dates)


COMMON_RISK = ("max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), "
               "ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers")
COMMON_VALID = ("event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; "
                "Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal "
                "dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked")


class Strategy:
    spec: StrategySpec

    def __init__(self, **params: Any):
        p = dict(self.spec.default_params)
        unknown = set(params) - set(p)
        if unknown:
            raise ValueError(f"{self.spec.id}: unknown params {unknown}")
        p.update(params)
        self.params = p

    @property
    def id(self) -> str:
        return self.spec.id

    def label(self) -> str:
        diff = {k: v for k, v in self.params.items() if v != self.spec.default_params.get(k)}
        return self.spec.id + ("" if not diff else "[" + ",".join(f"{k}={v}" for k, v in sorted(diff.items())) + "]")

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        raise NotImplementedError

    def diagnostic(self, ctx: StrategyContext) -> dict:
        return {}

    # ------------------------------------------------------------ helpers
    def _base_universe(self, ctx: StrategyContext, extra: list[str]) -> pd.DataFrame:
        cols = ["market", "symbol", "date", "sector", "board_lot", "suspended", "close_method", "close",
                "last_continuous_price", "open", "is_halfday", "adv_20", "adv_20__avail", "vol_20", "vol_20__avail",
                "vol_shares_20", "beta_60", "beta_60__avail", "spread_rel", "spread_rel__avail", "cav_20", "tac_20", "mcap"] + extra
        cols = list(dict.fromkeys(c for c in cols if c in ctx.features.columns))
        df = ctx.features.frame(cols)
        df = df[~df["suspended"].astype(bool)]
        df = df[ctx.allowed(df["date"])]
        min_adv = self.params.get("min_adv", 0.0)
        df = df[df["adv_20"].fillna(0) >= min_adv]
        return df

    def _size(self, ctx: StrategyContext, df: pd.DataFrame) -> pd.Series:
        pos_frac = self.params.get("pos_frac", 0.05)
        max_adv = self.params.get("max_adv_frac", 0.02)
        return np.minimum(ctx.capital * pos_frac, max_adv * df["adv_20"].fillna(0))

    def _top_n(self, df: pd.DataFrame, score: pd.Series, by: str = "date") -> pd.DataFrame:
        n = int(self.params.get("max_positions", 10))
        df = df.assign(_s=score.abs())
        return df.sort_values([by, "_s"], ascending=[True, False]).groupby(by).head(n).drop(columns="_s")

    @staticmethod
    def _intents(df: pd.DataFrame, strategy_label: str, **cols) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        for k, v in cols.items():
            out[k] = v
        out["strategy"] = strategy_label
        out["trade_id"] = [f"{strategy_label}|{m}|{s}|{pd.Timestamp(d).date()}|{i}"
                           for i, (m, s, d) in enumerate(zip(out["market"], out["symbol"], out["signal_date"]))]
        for c in INTENT_COLUMNS:
            if c not in out:
                out[c] = np.nan
        return out[INTENT_COLUMNS].reset_index(drop=True)

    @staticmethod
    def next_session(ctx: StrategyContext, dates: pd.Series) -> pd.Series:
        cal = ctx.md.dates
        k = cal.searchsorted(pd.to_datetime(dates).to_numpy(), side="right")
        out = pd.Series([cal[i] if i < len(cal) else pd.NaT for i in k], index=dates.index)
        return out

    @staticmethod
    def pre_open_ts(ctx: StrategyContext, dates: pd.Series) -> pd.Series:
        ad = ctx.adapter
        cache: dict = {}
        out = []
        for d in dates:
            if pd.isna(d):
                out.append(pd.NaT)
                continue
            if d not in cache:
                cache[d] = ad.phases(pd.Timestamp(d).date())[0].start
            out.append(cache[d])
        return pd.Series(out, index=dates.index)

    @staticmethod
    def auction_window_col(ctx: StrategyContext, df: pd.DataFrame, attr: str) -> pd.Series:
        ad = ctx.adapter
        cache: dict = {}
        out = []
        for d, h in zip(df["date"], df["is_halfday"]):
            key = (d, bool(h))
            if key not in cache:
                cache[key] = getattr(ad.close_auction_window(pd.Timestamp(d).date(), bool(h)), attr)
            out.append(cache[key])
        return pd.Series(out, index=df.index)
