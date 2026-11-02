"""S04 Auction imbalance prediction from indicative equilibrium prices."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.event_study import NOT_EXECUTABLE
from .base import COMMON_RISK, COMMON_VALID, Strategy, StrategyContext, StrategySpec


def last_usable_snapshot(ind: pd.DataFrame, daily: pd.DataFrame, ctx: StrategyContext, *, latency_s: float,
                         lead_s: float = 0.0) -> pd.DataFrame:
    """For each (symbol, date): last indicative snapshot whose receipt + latency <= random_start - lead."""
    if ind is None or ind.empty:
        return pd.DataFrame()
    x = ind[ind["market"] == ctx.market].copy()
    x["date"] = pd.to_datetime(x["date"])
    hd = daily[["symbol", "date", "is_halfday"]]
    x = x.merge(hd, on=["symbol", "date"], how="inner")
    rs = Strategy.auction_window_col(ctx, x, "random_start")
    x["deadline"] = rs - pd.Timedelta(seconds=lead_s)
    x = x[x["receipt_ts"] + pd.Timedelta(seconds=latency_s) <= x["deadline"]]
    x = x.sort_values("snapshot_ts").groupby(["symbol", "date"]).tail(1)
    return x


class S04AuctionImbalance(Strategy):
    spec = StrategySpec(
        id="S04", name="Auction Imbalance Prediction",
        hypothesis="The indicative equilibrium price (IEP) during the closing auction reveals order imbalance; "
                   "providing liquidity against a large indicated move earns the subsequent reversal "
                   "(mode='provide'), or following the imbalance predicts the final print (mode='follow').",
        required_data=("daily_bars", "auction_indicative", "daily_bars.close_auction_price", "borrow"),
        signal_formula="iep_gap = ln(IEP_t / P_last_continuous) from the last snapshot received at least "
                       "latency seconds before the random-close window; trade if |iep_gap| >= threshold",
        entry_rule="mode=provide: side = -sign(iep_gap); mode=follow: side = +sign(iep_gap); "
                   "closing-auction order submitted before random_start",
        exit_rule="exit at the next session's opening auction (default)",
        order_timing="indicative receipt + decision latency <= random_start - lead_s",
        order_type="closing-auction limit / at-auction order",
        cost_assumptions="auction impact + fees; our own order would move the IEP (not modelled beyond impact)",
        hedging="none by default", risk_controls=COMMON_RISK,
        validation=COMMON_VALID + "; predictability (R^2) by time-to-close and by quote latency",
        failure_modes="indicative data not archived historically (UNSUPPORTED on real data unless purchased); "
                      "IEP is noisy early in the auction; latency pushes the usable snapshot earlier",
        default_params=dict(threshold_bps=50.0, mode="provide", hold_sessions=1, exit_session="open_auction",
                            max_positions=10, pos_frac=0.05, max_adv_frac=0.01, min_adv=0.0, hedge=False,
                            lead_s=0.0, latency_s=1.0),
        param_grid=dict(threshold_bps=[25, 50, 100], mode=["provide", "follow"]),
        primary_param="threshold_bps",
    )

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        df = self._base_universe(ctx, [])
        snaps = last_usable_snapshot(ctx.tables.get("auction_indicative"), df, ctx,
                                     latency_s=p["latency_s"] + ctx.decision_latency_s, lead_s=p["lead_s"])
        if snaps.empty:
            return self._intents(df.iloc[:0], self.label(), market=[], symbol=[], signal_date=[])
        df = df.merge(snaps[["symbol", "date", "iep", "iev", "imbalance_qty", "receipt_ts", "snapshot_ts"]],
                      on=["symbol", "date"], how="inner")
        df["iep_gap"] = np.log(df["iep"] / df["last_continuous_price"])
        df = df[df["iep_gap"].abs() >= p["threshold_bps"] / 1e4]
        if df.empty:
            return self._intents(df, self.label(), market=[], symbol=[], signal_date=[])
        df = self._top_n(df, df["iep_gap"])
        sgn = -1 if p["mode"] == "provide" else 1
        lat = pd.Timedelta(seconds=ctx.decision_latency_s)
        return self._intents(
            df, self.label(), market=df["market"], symbol=df["symbol"], signal_date=df["date"],
            signal_ts=df["receipt_ts"], submit_ts=df["receipt_ts"] + lat, side=(sgn * np.sign(df["iep_gap"])).astype(int),
            target_notional=self._size(ctx, df), ref_price=df["last_continuous_price"], entry_session="close_auction",
            entry_date=df["date"], hold_sessions=p["hold_sessions"], exit_session=p["exit_session"], hedge=p["hedge"],
            beta=df["beta_60"].fillna(1.0), vol_20=df["vol_20"], vol_shares_20=df["vol_shares_20"],
            spread_rel=df["spread_rel"], signal=df["iep_gap"],
        )

    def diagnostic(self, ctx: StrategyContext) -> dict:
        """Predictability of the final auction price and next session from indicative data."""
        ind = ctx.tables.get("auction_indicative")
        if ind is None or ind.empty or not (ind["market"] == ctx.market).any():
            return dict(label="UNSUPPORTED - no indicative auction data", supported=False)
        f = ctx.features.df
        f = f[ctx.allowed(f["date"]) & (f["close_method"] == "auction")]
        x = ind[ind["market"] == ctx.market].copy()
        x["date"] = pd.to_datetime(x["date"])
        x = x.merge(f[["symbol", "date", "last_continuous_price", "close_auction_price", "is_halfday", "fwd_o1", "fwd_c1"]],
                    on=["symbol", "date"], how="inner")
        rs = Strategy.auction_window_col(ctx, x, "random_start")
        x["secs_to_random"] = (rs - x["snapshot_ts"]).dt.total_seconds()
        x["iep_gap"] = np.log(x["iep"] / x["last_continuous_price"])
        x["final_gap"] = np.log(x["close_auction_price"] / x["last_continuous_price"])
        x["imb_ratio"] = x["imbalance_qty"] / x["iev"].replace(0, np.nan)
        x["bucket"] = (x["secs_to_random"] // 60).clip(lower=-5) * 60

        def reg(g, y, xs):
            g = g[[y] + xs].replace([np.inf, -np.inf], np.nan).dropna()
            if len(g) < 30:
                return dict(n=len(g), slope=np.nan, r2=np.nan, hit=np.nan)
            X = np.column_stack([np.ones(len(g))] + [g[c].to_numpy() for c in xs])
            beta, *_ = np.linalg.lstsq(X, g[y].to_numpy(), rcond=None)
            res = g[y].to_numpy() - X @ beta
            r2 = 1 - res.var() / g[y].var() if g[y].var() > 0 else np.nan
            hit = float((np.sign(g[xs[0]]) == np.sign(g[y]))[g[y] != 0].mean())
            return dict(n=len(g), slope=float(beta[1]), r2=float(r2), hit=hit)

        by_time = pd.DataFrame([dict(secs_to_random_start=b, **reg(g, "final_gap", ["iep_gap"]))
                                for b, g in x.groupby("bucket")]).sort_values("secs_to_random_start", ascending=False)
        lat_rows = []
        for L in (0.0, 1.0, 5.0, 30.0, 60.0, 120.0):
            ok = x[x["receipt_ts"] + pd.Timedelta(seconds=L) <= rs]
            last = ok.sort_values("snapshot_ts").groupby(["symbol", "date"]).tail(1)
            r = reg(last, "final_gap", ["iep_gap"])
            r2 = reg(last, "fwd_o1", ["iep_gap"])
            lat_rows.append(dict(latency_s=L, final_r2=r["r2"], final_slope=r["slope"], final_hit=r["hit"],
                                 next_open_slope=r2["slope"], next_open_r2=r2["r2"], n=r["n"]))
        last = x[x["receipt_ts"] <= rs].sort_values("snapshot_ts").groupby(["symbol", "date"]).tail(1)
        nxt = dict(iep_gap_to_next_open=reg(last, "fwd_o1", ["iep_gap"]),
                   imb_ratio_to_next_open=reg(last, "fwd_o1", ["imb_ratio"]),
                   imb_ratio_to_final_minus_iep=reg(last.assign(resid=last["final_gap"] - last["iep_gap"]), "resid", ["imb_ratio"]))
        return dict(label=NOT_EXECUTABLE, supported=True, by_time_to_close=by_time, latency=pd.DataFrame(lat_rows),
                    next_session=nxt, n_snapshots=int(len(x)))
