"""S03 Pre-close momentum: trade into the closing auction on the intraday move."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.event_study import NOT_EXECUTABLE, _clustered, abnormal
from .base import COMMON_RISK, COMMON_VALID, Strategy, StrategyContext, StrategySpec


class S03PrecloseMomentum(Strategy):
    spec = StrategySpec(
        id="S03", name="Pre-close Momentum",
        hypothesis="Stocks with large intraday moves into the close keep moving (late-day momentum / "
                   "closing-auction continuation); entering in the closing auction on the move earns the "
                   "continuation over the next session.",
        required_data=("daily_bars", "daily_bars.last_continuous_price", "daily_bars.close_auction_price", "borrow"),
        signal_formula="z = ln(P_last_continuous / P_open) / vol_20(previous day); trade if |z| >= z_threshold",
        entry_rule="side = direction x sign(z); closing-auction order submitted after the last continuous price "
                   "is received and BEFORE the random-close window opens; filled at the auction price",
        exit_rule="exit at the next session's opening auction (exit_session=open_auction) or next close",
        order_timing="last_continuous_ts + feed latency + decision latency; must precede random_start",
        order_type="SET ATC / SGX pre-close market / HKEX at-auction (limit) order",
        cost_assumptions="auction impact (square root) + fees; fill capped by realised auction volume",
        hedging="optional futures hedge entered after the cash close", risk_controls=COMMON_RISK,
        validation=COMMON_VALID, failure_modes="the auction itself may already absorb the continuation; "
                                               "late signal misses the order deadline; short-in-auction rules",
        default_params=dict(z_threshold=1.5, direction=1, hold_sessions=1, exit_session="open_auction",
                            max_positions=10, pos_frac=0.05, max_adv_frac=0.01, min_adv=0.0, hedge=False,
                            extra_delay_s=0.0),
        param_grid=dict(z_threshold=[1.0, 1.5, 2.5], exit_session=["open_auction", "close_auction"]),
        primary_param="z_threshold",
    )

    def _signals(self, ctx: StrategyContext) -> pd.DataFrame:
        df = self._base_universe(ctx, ["intraday_ret", "intraday_ret__avail"])
        df = df.sort_values(["symbol", "date"])
        vol_prev = df.groupby("symbol")["vol_20"].shift(1)
        df = df.assign(z=df["intraday_ret"] / vol_prev)
        return df[df["z"].notna() & df["intraday_ret__avail"].notna()]

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        df = self._signals(ctx)
        df = df[df["z"].abs() >= p["z_threshold"]]
        if df.empty:
            return self._intents(df, self.label(), market=[], symbol=[], signal_date=[])
        df = self._top_n(df, df["z"])
        lat = pd.Timedelta(seconds=ctx.decision_latency_s + p["extra_delay_s"])
        return self._intents(
            df, self.label(), market=df["market"], symbol=df["symbol"], signal_date=df["date"],
            signal_ts=df["intraday_ret__avail"], submit_ts=df["intraday_ret__avail"] + lat,
            side=(p["direction"] * np.sign(df["z"])).astype(int), target_notional=self._size(ctx, df),
            ref_price=df["last_continuous_price"], entry_session="close_auction", entry_date=df["date"],
            hold_sessions=p["hold_sessions"], exit_session=p["exit_session"], hedge=p["hedge"],
            beta=df["beta_60"].fillna(1.0), vol_20=df["vol_20"], vol_shares_20=df["vol_shares_20"],
            spread_rel=df["spread_rel"], signal=df["z"],
        )

    def diagnostic(self, ctx: StrategyContext) -> dict:
        f = ctx.features.df
        f = f[ctx.allowed(f["date"]) & ~f["suspended"].astype(bool)].sort_values(["symbol", "date"]).copy()
        f["z"] = f["intraday_ret"] / f.groupby("symbol")["vol_20"].shift(1)
        ev = f[f["z"].abs() >= self.params["z_threshold"]].copy()
        ev["dir"] = np.sign(ev["z"])
        rows = []
        for lab, sc, mc in (("LCP->C0", "fwd_lcp_c0", "fwd_m_lcp_c0"), ("C0->O1", "fwd_o1", "fwd_m_o1"),
                            ("C0->C1", "fwd_c1", "fwd_m_c1"), ("O1->C1", "fwd_o1c1", "fwd_m_o1c1")):
            ev[f"ar_{lab}"] = ev["dir"] * abnormal(ev, sc, mc)
            r = _clustered(ev, f"ar_{lab}", n_boot=300)
            r["horizon"] = lab
            r["executable_window"] = lab in ("C0->O1", "C0->C1")  # entered in the closing auction
            rows.append(r)
        return dict(label=NOT_EXECUTABLE, n_events=int(len(ev)), summary=pd.DataFrame(rows),
                    note="C0->O1 and C0->C1 are executable windows for S03 (entry in the closing auction).")
