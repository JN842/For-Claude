"""S08 Closing auction versus index-futures basis."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..backtest.event_study import NOT_EXECUTABLE
from ..execution.simulator import SimResult
from ..stats.inference import newey_west_tstat
from .base import COMMON_VALID, Strategy, StrategyContext, StrategySpec


class S08AuctionFuturesBasis(Strategy):
    instrument = "future"
    spec = StrategySpec(
        id="S08", name="Closing Auction vs Futures Basis",
        hypothesis="The cash index's closing-auction move contains temporary pressure.  Index futures that keep "
                   "trading after the cash close price only part of the expected reversal, so a futures position "
                   "against the index auction gap, entered after the cash close, earns the residual.",
        required_data=("futures_daily", "index_levels", "daily_bars.close_auction_price"),
        signal_formula="G = ln(index_close / index_at_last_continuous_prices); trade if |G| >= threshold",
        entry_rule="side = -sign(G) in front-month index futures at the first futures price observed after the "
                   "cash auction result (price_post_auction)",
        exit_rule="exit at next session's futures close (exit='close') or open (exit='open')",
        order_timing="post_auction_ts (cash close + futures quote) + decision latency",
        order_type="futures limit/market order in the post-close futures session",
        cost_assumptions="futures cost_bps per side from registry (spread + fees, assumption) x scenario multiplier; "
                         "contract rounding; max_contracts cap",
        hedging="the position IS the hedge instrument; no stock legs",
        risk_controls="contracts = round(capital x pos_frac / (F x multiplier)) <= max_contracts",
        validation=COMMON_VALID,
        failure_modes="requires intraday futures quotes time-aligned to the cash auction (rarely archived); "
                      "futures may close before the cash auction in some markets/eras; basis noise",
        default_params=dict(threshold_bps=10.0, exit="close", pos_frac=0.5, max_contracts=200),
        param_grid=dict(threshold_bps=[5, 10, 20], exit=["open", "close"]),
        primary_param="threshold_bps",
    )

    def _signals(self, ctx: StrategyContext) -> pd.DataFrame:
        lv, fu = ctx.tables.get("index_levels"), ctx.tables.get("futures_daily")
        if lv is None or fu is None or lv.empty or fu.empty:
            return pd.DataFrame()
        lv = lv[lv["market"] == ctx.market].assign(date=lambda x: pd.to_datetime(x["date"]))
        fu = fu[fu["market"] == ctx.market].assign(date=lambda x: pd.to_datetime(x["date"]))
        x = lv.merge(fu, on=["market", "date"], suffixes=("_idx", ""))
        x = x.sort_values("date").reset_index(drop=True)
        x["G"] = np.log(x["close_idx"] / x["level_at_lcp"])
        x["next_open"] = x["open"].shift(-1)
        x["next_close"] = x["close"].shift(-1)
        x["next_date"] = x["date"].shift(-1)
        if ctx.signal_dates is not None:
            x = x[x["date"].isin(ctx.signal_dates)]
        return x

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        x = self._signals(ctx)
        if x.empty:
            return pd.DataFrame(columns=["trade_id", "signal_ts", "submit_ts", "signal_date"])
        # NB: no filter on next-day data - selecting on the existence of future rows would be look-ahead.
        x = x[(x["G"].abs() >= p["threshold_bps"] / 1e4) & x["price_post_auction"].notna()]
        lat = pd.Timedelta(seconds=ctx.decision_latency_s)
        cal = ctx.md.dates
        k = cal.searchsorted(x["date"].to_numpy(), side="right")
        exit_date = [cal[i] if i < len(cal) else pd.NaT for i in k]
        return pd.DataFrame({
            "trade_id": [f"{self.label()}|{ctx.market}|{d.date()}" for d in x["date"]],
            "strategy": self.label(), "market": ctx.market, "symbol": f"{ctx.market}_FUT",
            "signal_date": x["date"], "signal_ts": x["post_auction_ts"], "submit_ts": x["post_auction_ts"] + lat,
            "side": (-np.sign(x["G"])).astype(int), "price_in": x["price_post_auction"],
            "exit_date": exit_date, "multiplier": x["multiplier"], "signal": x["G"],
        }).reset_index(drop=True)

    def simulate(self, intents: pd.DataFrame, md, scenario, adapter, capital: float) -> SimResult:
        p = self.params
        fills, rejects, trades = [], [], []
        col = "open" if p["exit"] == "open" else "close"
        fut = md.fut
        for it in intents.itertuples(index=False):
            px_out = np.nan
            if fut is not None and pd.notna(it.exit_date) and pd.Timestamp(it.exit_date) in fut.index:
                px_out = float(fut.loc[pd.Timestamp(it.exit_date), col])
            spec = adapter.futures_spec(pd.Timestamp(it.signal_date).date())
            cost_bps = float(spec.get("cost_bps", 1.0)) * scenario.futures_cost_mult + scenario.extra_slippage_bps
            n = min(math.floor(capital * p["pos_frac"] / (it.price_in * it.multiplier)), p["max_contracts"])
            if n <= 0 or not np.isfinite(px_out):
                rejects.append(dict(trade_id=it.trade_id, strategy=it.strategy, market=it.market, symbol=it.symbol,
                                    date=it.signal_date, leg="entry", reason="futures_size_or_price_unavailable",
                                    requested_qty=0.0, filled_qty=0.0, scenario=scenario.name))
                continue
            for leg, d, px, q in (("entry", it.signal_date, it.price_in, it.side * n), ("exit", it.exit_date, px_out, -it.side * n)):
                fills.append(dict(trade_id=it.trade_id, leg=leg, instrument="stock", market=it.market, symbol=it.symbol,
                                  date=pd.Timestamp(d), ts=pd.NaT, side_qty=float(q), price=float(px), base_price=float(px),
                                  fees=abs(q) * px * it.multiplier * cost_bps / 1e4, slip_cost=0.0, session="futures",
                                  trade_side=int(it.side), borrow_fee_bps=0.0, multiplier=float(it.multiplier)))
            trades.append(dict(trade_id=it.trade_id, strategy=it.strategy, market=it.market, symbol=it.symbol, side=int(it.side),
                               signal_date=it.signal_date, signal=it.signal, status="filled", intended_qty=float(n),
                               entry_qty=float(n), entry_date=it.signal_date, exit_date=it.exit_date, scenario=scenario.name,
                               entry_session="futures_post_close", exit_session=p["exit"]))
        f = pd.DataFrame(fills)
        r = pd.DataFrame(rejects, columns=["trade_id", "strategy", "market", "symbol", "date", "leg", "reason",
                                           "requested_qty", "filled_qty", "scenario"])
        return SimResult(scenario.name, f, r, pd.DataFrame(trades), {})

    def diagnostic(self, ctx: StrategyContext) -> dict:
        x = self._signals(ctx)
        if x.empty:
            return dict(label="UNSUPPORTED - no futures / index data", supported=False)
        x = x.dropna(subset=["next_close"])
        x["fut_post_to_next_close"] = np.log(x["next_close"] / x["price_post_auction"])
        x["fut_lcp_to_post"] = np.log(x["price_post_auction"] / x["price_at_cash_lcp"])
        out = {}
        for lab, y in (("futures post->next close", "fut_post_to_next_close"), ("futures LCP->post", "fut_lcp_to_post")):
            g = x[["G", y]].dropna()
            beta = np.polyfit(g["G"], g[y], 1)[0] if len(g) > 10 else np.nan
            signed = (-np.sign(x["G"]) * x[y]).dropna()
            mu, se, pv = newey_west_tstat(signed.to_numpy())
            out[lab] = dict(slope_on_G=float(beta), signed_mean_bps=mu * 1e4, t=mu / se if se else np.nan, p=pv, n=len(g))
        return dict(label=NOT_EXECUTABLE, supported=True, results=out,
                    gap_quantiles_bps={str(q): float(x["G"].quantile(q) * 1e4) for q in (0.05, 0.5, 0.95)})

