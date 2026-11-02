"""S05 Index-rebalance price pressure."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.event_study import rebalance_event_study
from .base import COMMON_RISK, COMMON_VALID, Strategy, StrategyContext, StrategySpec


class S05IndexRebalance(Strategy):
    spec = StrategySpec(
        id="S05", name="Index Rebalance Price Pressure",
        hypothesis="Announced index additions (deletions) attract predictable buying (selling) by index funds at "
                   "the close before the effective date; prices rise (fall) between announcement and that close "
                   "and partly revert afterwards.",
        required_data=("daily_bars", "index_events", "index_membership", "borrow", "futures_daily"),
        signal_formula="event_type in {add, delete} from point-in-time announcements (receipt_ts known)",
        entry_rule="mode=pre_event: side=+1 add / -1 delete at the opening auction of the first session after "
                   "the announcement was received; mode=post_event_reversal: opposite side in the closing "
                   "auction of the index trade day",
        exit_rule="pre_event: sell/cover into the closing auction of the trade day (effective_date - 1 session); "
                  "post_event_reversal: exit after hold_sessions at the close",
        order_timing="announcement receipt + decision latency; never before the announcement",
        order_type="opening-auction order to enter; closing-auction order on the index trade day",
        cost_assumptions="fees + impact; closing-auction participation cap applies on the trade day when the "
                         "auction volume spikes",
        hedging="index-futures beta hedge by default (hedge=True)",
        risk_controls=COMMON_RISK + "; skip events whose trade day is before the entry date",
        validation=COMMON_VALID + "; few events per year -> wide CIs; announcement vs effective date separated",
        failure_modes="real indices (SET50, STI, HSI, FTSE, MSCI) need licensed point-in-time announcements - "
                      "unavailable here; pre-announcement anticipation; crowding of the trade",
        default_params=dict(mode="pre_event", hold_sessions=5, max_positions=20, pos_frac=0.05, max_adv_frac=0.02,
                            min_adv=0.0, hedge=True),
        param_grid=dict(mode=["pre_event", "post_event_reversal"], hedge=[True, False]),
        primary_param="mode",
    )

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        ev = ctx.tables.get("index_events")
        if ev is None or ev.empty:
            return self._intents(pd.DataFrame(), self.label(), market=[], symbol=[], signal_date=[])
        ev = ev[(ev["market"] == ctx.market) & ev["event_type"].isin(["add", "delete"])].copy()
        cal = ctx.md.dates
        f = ctx.features.frame(["symbol", "date", "close", "last_continuous_price", "last_continuous_ts", "adv_20",
                                "vol_20", "vol_shares_20", "beta_60", "spread_rel", "is_halfday", "suspended", "market"])
        f = f.set_index(["symbol", "date"])
        rows = []
        lat = pd.Timedelta(seconds=ctx.decision_latency_s)
        for e in ev.itertuples(index=False):
            eff = pd.Timestamp(e.effective_date)
            k = cal.searchsorted(eff)
            if k <= 0:
                continue
            trade_day = cal[k - 1]
            rec_local = pd.Timestamp(e.receipt_ts).tz_convert(ctx.adapter.tz).tz_localize(None).normalize()
            sig_date = cal[min(cal.searchsorted(rec_local, side="right") - 1, len(cal) - 1)]
            if ctx.signal_dates is not None and sig_date not in ctx.signal_dates:
                continue
            side = 1 if e.event_type == "add" else -1
            if (e.symbol, sig_date) not in f.index:
                continue
            base = f.loc[(e.symbol, sig_date)]
            if p["mode"] == "pre_event":
                entry = cal[cal.searchsorted(sig_date, side="right")] if cal.searchsorted(sig_date, side="right") < len(cal) else None
                if entry is None or entry > trade_day:
                    continue
                hold = int(cal.searchsorted(trade_day) - cal.searchsorted(entry))
                submit = max(pd.Timestamp(e.receipt_ts) + lat, ctx.adapter.phases(entry.date())[0].start)
                rows.append(dict(symbol=e.symbol, date=sig_date, entry_date=entry, side=side, hold=hold, sess="open_auction",
                                 exit_sess="close_auction", sig_ts=pd.Timestamp(e.receipt_ts), submit=submit,
                                 ref=base["close"], row=base))
            else:
                if (e.symbol, trade_day) not in f.index:
                    continue
                td = f.loc[(e.symbol, trade_day)]
                if not np.isfinite(td["last_continuous_price"]):
                    continue
                aw = ctx.adapter.close_auction_window(trade_day.date(), bool(td["is_halfday"]))
                sig_ts = max(pd.Timestamp(e.receipt_ts), pd.Timestamp(td["last_continuous_ts"]) + pd.Timedelta(seconds=1))
                rows.append(dict(symbol=e.symbol, date=sig_date, entry_date=trade_day, side=-side, hold=p["hold_sessions"],
                                 sess="close_auction", exit_sess="close_auction", sig_ts=sig_ts,
                                 submit=max(sig_ts + lat, aw.auction_start), ref=td["last_continuous_price"], row=td))
        if not rows:
            return self._intents(pd.DataFrame(), self.label(), market=[], symbol=[], signal_date=[])
        df = pd.DataFrame(rows)
        meta = pd.DataFrame([r for r in df.pop("row")]).reset_index(drop=True)
        df = pd.concat([df.reset_index(drop=True), meta.drop(columns=["close"], errors="ignore")], axis=1)
        df["market"] = ctx.market
        notional = np.minimum(ctx.capital * p["pos_frac"], p["max_adv_frac"] * df["adv_20"].fillna(0))
        return self._intents(
            df, self.label(), market=df["market"], symbol=df["symbol"], signal_date=df["date"], signal_ts=df["sig_ts"],
            submit_ts=df["submit"], side=df["side"].astype(int), target_notional=notional, ref_price=df["ref"],
            entry_session=df["sess"], entry_date=df["entry_date"], hold_sessions=df["hold"], exit_session=df["exit_sess"],
            hedge=p["hedge"], beta=df["beta_60"].fillna(1.0), vol_20=df["vol_20"], vol_shares_20=df["vol_shares_20"],
            spread_rel=df["spread_rel"], signal=df["side"].astype(float),
        )

    def diagnostic(self, ctx: StrategyContext) -> dict:
        ev = ctx.tables.get("index_events")
        if ev is None or ev.empty:
            return dict(label="UNSUPPORTED - no index events", n_events=0)
        ev = ev[ev["market"] == ctx.market]
        f = ctx.features.df
        if ctx.signal_dates is not None:
            ev = ev[pd.to_datetime(ev["effective_date"]) <= ctx.signal_dates[-1]]
        return rebalance_event_study(f, ev)
