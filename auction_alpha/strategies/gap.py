"""Closing-gap strategies: S01 reversal, S02 continuation, S06 cross-sectional, S07 SGX TAC."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.event_study import gap_event_study
from .base import COMMON_RISK, COMMON_VALID, Strategy, StrategyContext, StrategySpec

GAP_DATA = ("daily_bars", "daily_bars.last_continuous_price", "daily_bars.close_auction_price", "borrow")
_COMMON_PARAMS = dict(threshold_bps=100.0, hold_sessions=0, entry_session="open_auction", signal_source="eod",
                      max_positions=10, pos_frac=0.05, max_adv_frac=0.01, min_adv=0.0, hedge=False, long_only=False)


class _GapDirectional(Strategy):
    direction = -1  # -1 reversal, +1 continuation

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        df = self._base_universe(ctx, ["gap", "gap__avail", "gap_eod__avail"])
        df = df[(df["close_method"] == "auction") & df["gap"].notna() & (df["gap"].abs() >= p["threshold_bps"] / 1e4)]
        if p["long_only"]:
            df = df[self.direction * np.sign(df["gap"]) > 0]
        if df.empty:
            return self._intents(df, self.label(), market=[], symbol=[], signal_date=[])
        df = self._top_n(df, df["gap"])
        sig_ts = df["gap_eod__avail"] if p["signal_source"] == "eod" else df["gap__avail"]
        entry_date = self.next_session(ctx, df["date"])
        df = df.assign(entry_date=entry_date, sig_ts=sig_ts).dropna(subset=["entry_date", "sig_ts"])
        lat = pd.Timedelta(seconds=ctx.decision_latency_s)
        submit = np.maximum(df["sig_ts"] + lat, self.pre_open_ts(ctx, df["entry_date"]))
        return self._intents(
            df, self.label(), market=df["market"], symbol=df["symbol"], signal_date=df["date"],
            signal_ts=df["sig_ts"], submit_ts=submit, side=(self.direction * np.sign(df["gap"])).astype(int),
            target_notional=self._size(ctx, df), ref_price=df["close"], entry_session=p["entry_session"],
            entry_date=df["entry_date"], hold_sessions=p["hold_sessions"], exit_session="close_auction",
            hedge=p["hedge"], beta=df["beta_60"].fillna(1.0), vol_20=df["vol_20"], vol_shares_20=df["vol_shares_20"],
            spread_rel=df["spread_rel"], signal=df["gap"],
        )

    def diagnostic(self, ctx: StrategyContext) -> dict:
        f = ctx.features.df
        f = f[ctx.allowed(f["date"])]
        return gap_event_study(f, threshold_bps=self.params["threshold_bps"], sign=self.direction, market=ctx.market)


class S01ClosingGapReversal(_GapDirectional):
    direction = -1
    spec = StrategySpec(
        id="S01", name="Closing Gap Reversal",
        hypothesis="Auction-induced deviations of the closing price from the last continuous price are partly "
                   "temporary liquidity pressure and revert, so a large positive (negative) gap predicts a lower "
                   "(higher) subsequent return.",
        required_data=GAP_DATA,
        signal_formula="gap = ln(P_close_auction / P_last_continuous); trade if |gap| >= threshold",
        entry_rule="side = -sign(gap); enter at the NEXT session's opening auction (the closing price is not "
                   "known early enough to trade at it - that would be look-ahead)",
        exit_rule="exit in the closing auction after hold_sessions sessions (0 = same day close)",
        order_timing="signal available at EOD receipt (or real-time auction receipt); order entered in next pre-open",
        order_type="opening-auction market/limit order; closing-auction ATC / at-auction order for exit",
        cost_assumptions="registry fees + assumed commission per side; square-root impact on auction price; "
                         "participation cap on realised auction volume (scenario dependent)",
        hedging="optional index-futures beta hedge entered/exited with the stock legs",
        risk_controls=COMMON_RISK, validation=COMMON_VALID,
        failure_modes="most of the reversal happens overnight (before the executable entry); gaps on news are "
                      "permanent; small-cap gaps coincide with wide spreads and low opening-auction volume; shorts "
                      "may be unborrowable exactly when gaps are large",
        default_params=dict(_COMMON_PARAMS),
        param_grid=dict(threshold_bps=[50, 100, 200], hold_sessions=[0, 1, 4]),
        primary_param="threshold_bps",
    )


class S02ClosingGapContinuation(_GapDirectional):
    direction = +1
    spec = StrategySpec(
        id="S02", name="Closing Gap Continuation",
        hypothesis="Closing gaps reflect informed order flow and continue (alternative to S01).",
        required_data=GAP_DATA,
        signal_formula="gap = ln(P_close_auction / P_last_continuous); trade if |gap| >= threshold",
        entry_rule="side = +sign(gap); enter at the next session's opening auction",
        exit_rule="exit in the closing auction after hold_sessions sessions",
        order_timing="as S01", order_type="as S01", cost_assumptions="as S01", hedging="as S01",
        risk_controls=COMMON_RISK, validation=COMMON_VALID + "; S01 and S02 are mutually exclusive hypotheses and are "
                                                            "counted as separate tests in the FDR family",
        failure_modes="if gaps are mostly temporary pressure, continuation loses the reversal plus costs",
        default_params=dict(_COMMON_PARAMS),
        param_grid=dict(threshold_bps=[50, 100, 200], hold_sessions=[0, 4]),
        primary_param="threshold_bps",
    )


class S06CrossSectionalReversal(Strategy):
    spec = StrategySpec(
        id="S06", name="Cross-sectional Closing Reversal (market-neutral)",
        hypothesis="Ranking stocks by closing gap each day, a long-short portfolio long the most negative and short "
                   "the most positive gaps earns the reversal while neutralising market exposure.",
        required_data=GAP_DATA,
        signal_formula="z = gap (or gap minus sector median gap when neutral='sector'); long bottom quantile, short top quantile",
        entry_rule="enter both legs at the next session's opening auction; short legs only if borrow is located "
                   "(rejected otherwise - the intended neutrality is then NOT achieved and this is reported)",
        exit_rule="exit both legs in the closing auction after hold_sessions sessions",
        order_timing="EOD signal, next pre-open order entry",
        order_type="opening-auction orders; closing-auction orders for exit",
        cost_assumptions="as S01 plus borrow fees on the short leg (accrued daily)",
        hedging="dollar-neutral, sector-neutral or beta-neutral leg sizing; optional futures hedge of residual beta",
        risk_controls=COMMON_RISK + "; min_names per day; equal names per leg",
        validation=COMMON_VALID, failure_modes="short leg concentrated in unborrowable names; turnover costs; "
                                               "beta neutrality from noisy 60-day betas",
        default_params=dict(quantile=0.1, min_names=10, neutral="dollar", hold_sessions=0, entry_session="open_auction",
                            max_positions=10, pos_frac=0.05, max_adv_frac=0.01, min_adv=0.0, hedge=False,
                            min_abs_gap_bps=20.0),
        param_grid=dict(neutral=["dollar", "sector", "beta"], quantile=[0.1, 0.2]),
        primary_param="quantile",
    )

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        df = self._base_universe(ctx, ["gap", "gap_eod__avail"])
        df = df[(df["close_method"] == "auction") & df["gap"].notna()]
        n = df.groupby("date")["symbol"].transform("size")
        df = df[n >= p["min_names"]].copy()
        if df.empty:
            return self._intents(df, self.label(), market=[], symbol=[], signal_date=[])
        z = df["gap"]
        if p["neutral"] == "sector":
            z = z - df.groupby(["date", "sector"])["gap"].transform("median")
        df["z"] = z
        df["rk"] = df.groupby("date")["z"].rank(pct=True)
        q = p["quantile"]
        legs = df[((df["rk"] <= q) | (df["rk"] > 1 - q)) & (df["z"].abs() >= p["min_abs_gap_bps"] / 1e4)].copy()
        legs["side"] = np.where(legs["rk"] <= q, 1, -1)
        k = max(1, int(p["max_positions"]) // 2)
        longs = legs[legs["side"] == 1].sort_values(["date", "z"]).groupby("date").head(k)
        shorts = legs[legs["side"] == -1].sort_values(["date", "z"]).groupby("date").tail(k)
        legs = pd.concat([longs, shorts]).sort_values(["date", "side"]).reset_index(drop=True)
        notional = self._size(ctx, legs)
        if p["neutral"] == "beta":
            b = legs["beta_60"].clip(0.3, 2.5).fillna(1.0)
            own = b.groupby([legs["date"], legs["side"]]).transform("mean")
            opp = (legs.assign(b=b).groupby(["date", "side"])["b"].mean().rename("opp").reset_index()
                   .assign(side=lambda x: -x["side"]))
            opp_b = legs[["date", "side"]].merge(opp, on=["date", "side"], how="left")["opp"].to_numpy()
            notional = notional * (opp_b / own).fillna(1.0).clip(0.5, 2.0)
        entry_date = self.next_session(ctx, legs["date"])
        legs = legs.assign(entry_date=entry_date, notional=notional).dropna(subset=["entry_date", "gap_eod__avail"])
        lat = pd.Timedelta(seconds=ctx.decision_latency_s)
        submit = np.maximum(legs["gap_eod__avail"] + lat, self.pre_open_ts(ctx, legs["entry_date"]))
        return self._intents(
            legs, self.label(), market=legs["market"], symbol=legs["symbol"], signal_date=legs["date"],
            signal_ts=legs["gap_eod__avail"], submit_ts=submit, side=legs["side"].astype(int),
            target_notional=legs["notional"], ref_price=legs["close"], entry_session=p["entry_session"],
            entry_date=legs["entry_date"], hold_sessions=p["hold_sessions"], exit_session="close_auction",
            hedge=p["hedge"], beta=legs["beta_60"].fillna(1.0), vol_20=legs["vol_20"], vol_shares_20=legs["vol_shares_20"],
            spread_rel=legs["spread_rel"], signal=legs["z"],
        )

    def diagnostic(self, ctx: StrategyContext) -> dict:
        """Daily long-short spread of next-day returns by gap quintile (diagnostic)."""
        f = ctx.features.df
        f = f[ctx.allowed(f["date"]) & (f["close_method"] == "auction") & f["gap"].notna() & ~f["suspended"].astype(bool)]
        f = f[f.groupby("date")["symbol"].transform("size") >= self.params["min_names"]].copy()
        if f.empty:
            return {}
        f["qt"] = f.groupby("date")["gap"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
        out = {}
        for lab, col in (("C0->O1", "fwd_o1"), ("C0->C1", "fwd_c1"), ("O1->C1", "fwd_o1c1")):
            m = f.groupby(["date", "qt"])[col].mean().unstack()
            spread = (m[0] - m[4]).dropna()
            out[lab] = dict(mean_bps=float(spread.mean() * 1e4), t=float(spread.mean() / spread.std(ddof=1) * np.sqrt(len(spread))) if len(spread) > 2 else np.nan,
                            n_dates=int(len(spread)))
            out[f"quintile_means_{lab}"] = (m.mean() * 1e4).to_dict()
        return dict(label="DIAGNOSTIC - NOT EXECUTABLE", quintile_spread=out)


class S07TradeAtCloseDislocation(Strategy):
    spec = StrategySpec(
        id="S07", name="SGX Trade-at-Close Dislocation",
        hypothesis="On SGX the closing price is known before the Trade-at-Close (TAC) phase, so a contrarian order "
                   "at the closing price can capture the overnight reversal of auction pressure - the only market "
                   "here where trading at a known close is possible.  Capacity is bounded by TAC liquidity.",
        required_data=("daily_bars", "daily_bars.close_auction_price", "daily_bars.close_auction_receipt_ts",
                       "daily_bars.tac_volume", "borrow"),
        markets=("SGX",),
        signal_formula="gap = ln(P_close / P_last_continuous) from the real-time auction result",
        entry_rule="side = -sign(gap); TAC order at the closing price, submitted after the auction result is "
                   "received; fill <= tac_participation x realised TAC volume (zero when no TAC trades occurred)",
        exit_rule="exit at next session's opening auction (hold_sessions=1, exit_session=open_auction) or at the "
                  "next close",
        order_timing="auction result receipt + decision latency, must be before TAC end; TAC rules must be in force",
        order_type="TAC limit order at the closing price",
        cost_assumptions="no price impact inside TAC (price fixed) but fill capped by realised TAC volume; exit costs as S01",
        hedging="none by default", risk_controls=COMMON_RISK,
        validation=COMMON_VALID + "; TAC auction matching is separated from post-auction TAC transactions",
        failure_modes="TAC volume is thin or zero; counterparties in TAC are not random (adverse selection); "
                      "TAC introduction date unverified - pre-introduction dates are historically assumed",
        default_params=dict(threshold_bps=100.0, hold_sessions=1, exit_session="open_auction", max_positions=10,
                            pos_frac=0.05, max_adv_frac=0.01, min_adv=0.0, hedge=False),
        param_grid=dict(threshold_bps=[50, 100, 200], exit_session=["open_auction", "close_auction"]),
        primary_param="threshold_bps",
    )

    def generate_intents(self, ctx: StrategyContext) -> pd.DataFrame:
        p = self.params
        df = self._base_universe(ctx, ["gap", "gap__avail", "tac_20"])
        df = df[(df["close_method"] == "auction") & df["gap"].notna() & (df["gap"].abs() >= p["threshold_bps"] / 1e4)]
        df = df.dropna(subset=["gap__avail"])
        if df.empty:
            return self._intents(df, self.label(), market=[], symbol=[], signal_date=[])
        df = self._top_n(df, df["gap"])
        lat = pd.Timedelta(seconds=ctx.decision_latency_s)
        tac_start = self.auction_window_col(ctx, df, "tac_start")
        submit = df["gap__avail"] + lat
        submit = submit.where(tac_start.isna() | (submit >= tac_start), tac_start)
        return self._intents(
            df, self.label(), market=df["market"], symbol=df["symbol"], signal_date=df["date"],
            signal_ts=df["gap__avail"], submit_ts=submit, side=(-np.sign(df["gap"])).astype(int),
            target_notional=self._size(ctx, df), ref_price=df["close"], entry_session="tac", entry_date=df["date"],
            hold_sessions=p["hold_sessions"], exit_session=p["exit_session"], hedge=p["hedge"],
            beta=df["beta_60"].fillna(1.0), vol_20=df["vol_20"], vol_shares_20=df["vol_shares_20"],
            spread_rel=df["spread_rel"], signal=df["gap"],
        )

    def diagnostic(self, ctx: StrategyContext) -> dict:
        f = ctx.features.df
        f = f[ctx.allowed(f["date"])]
        es = gap_event_study(f, threshold_bps=self.params["threshold_bps"], sign=-1, market=ctx.market)
        auc = f[f["close_method"] == "auction"]
        tac = auc["tac_volume"]
        liq = dict(frac_days_with_tac=float((tac > 0).mean()) if tac.notna().any() else np.nan,
                   median_tac_to_auction_volume=float((tac / auc["close_auction_volume"]).replace([np.inf], np.nan).median()),
                   median_tac_turnover=float((tac * auc["close"]).median()),
                   p90_tac_turnover=float((tac * auc["close"]).quantile(0.9)))
        es["tac_liquidity"] = liq
        es["note"] = ("C0->O1 is executable ONLY through TAC and only up to TAC liquidity; "
                      "auction matching and post-auction TAC trades are separate phases")
        return es
