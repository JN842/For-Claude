"""Point-in-time feature engineering.

Every feature column ``x`` has a companion ``x__avail`` holding the earliest
UTC time at which ``x`` could be known by a live system (never earlier than
the receipt time of each input).  Forward-looking columns used only by
*diagnostic* event studies are prefixed ``fwd_`` and are hidden from
strategies by :class:`FeatureView`.

Corporate actions adjust returns only if the action was announced before the
return is used (``announce_ts <= availability``); otherwise the return is set
to NaN and counted (``ca_unannounced``), never silently adjusted with future
knowledge.  Missing data is never back-filled from later observations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURE_DOCS = {
    "gap": ("ln(close_auction_price / last_continuous_price)", "close_auction_receipt_ts (real-time feed)"),
    "gap_eod": ("same as gap", "end-of-day receipt_ts (EOD vendor file)"),
    "preclose_ret": ("ln(last_continuous_price / prev adjusted close)", "last_continuous_ts + feed latency"),
    "intraday_ret": ("ln(last_continuous_price / open)", "last_continuous_ts + feed latency"),
    "spread_rel": ("(preclose_ask - preclose_bid) / mid", "last_continuous_ts + feed latency"),
    "ret_cc": ("adjusted close-to-close log return", "EOD receipt_ts"),
    "vol_20": ("std of ret_cc over last 20 observations (incl. today)", "EOD receipt_ts"),
    "adv_20": ("mean turnover over last 20 observations", "EOD receipt_ts"),
    "cav_20": ("mean closing-auction volume (shares) over last 20 observations", "EOD receipt_ts"),
    "tac_20": ("mean SGX TAC volume over last 20 observations", "EOD receipt_ts"),
    "beta_60": ("rolling 60-obs beta of ret_cc to index return", "EOD receipt_ts"),
    "mcap": ("close x shares_outstanding", "EOD receipt_ts"),
}


class LeakageError(RuntimeError):
    pass


def _adj_logs(d: pd.DataFrame, ca: pd.DataFrame) -> pd.DataFrame:
    """Adjusted log price levels for close / open / LCP on a total-return basis."""
    d = d.copy()
    d["split"] = 1.0
    d["div"] = 0.0
    d["ca_unannounced"] = False
    if ca is not None and not ca.empty:
        c = ca.copy()
        c["ex_date"] = pd.to_datetime(c["ex_date"])
        sp = c[c["action_type"] == "split"][["symbol", "ex_date", "ratio", "announce_ts"]]
        dv = c[c["action_type"] == "cash_dividend"][["symbol", "ex_date", "cash_amount", "announce_ts"]]
        d = d.merge(sp.rename(columns={"ex_date": "date", "announce_ts": "sp_ann"}), on=["symbol", "date"], how="left")
        d = d.merge(dv.rename(columns={"ex_date": "date", "announce_ts": "dv_ann"}), on=["symbol", "date"], how="left")
        # Realised returns (diagnostic labels, portfolio P&L) use every action that actually happened.
        # Point-in-time features may only use an action announced before the session opens.
        open_ref = d["open_avail"]
        sp_ok = d["ratio"].notna() & (d["sp_ann"] <= open_ref)
        dv_ok = d["cash_amount"].notna() & (d["dv_ann"] <= open_ref)
        d["ca_unannounced"] = (d["ratio"].notna() & ~sp_ok) | (d["cash_amount"].notna() & ~dv_ok)
        d.loc[d["ratio"].notna(), "split"] = d.loc[d["ratio"].notna(), "ratio"]
        d.loc[d["cash_amount"].notna(), "div"] = d.loc[d["cash_amount"].notna(), "cash_amount"]
        d = d.drop(columns=["ratio", "cash_amount", "sp_ann", "dv_ann"])
    g = d.groupby("symbol", sort=False)
    prev_close = g["close"].shift(1)
    # previous *available* close: carried forward across suspensions (past info only)
    prev_close_ff = g["close"].transform(lambda s: s.ffill().shift(1))
    d["prev_close"] = prev_close_ff
    d["prev_is_adjacent"] = prev_close.notna()
    gross = d["split"] * (d["close"] + d["div"]) / prev_close_ff
    step = np.log(gross)
    d["ret_cc"] = step.where(~d["ca_unannounced"])  # PIT feature: NaN if the action was not yet announced
    d["L_close"] = step.fillna(0.0).groupby(d["symbol"]).cumsum()
    d.loc[d["close"].isna(), "L_close"] = np.nan
    base = d.groupby("symbol")["L_close"].shift(1).groupby(d["symbol"]).ffill()
    d["L_open"] = base + np.log(d["split"] * (d["open"] + d["div"]) / prev_close_ff)
    d["L_lcp"] = base + np.log(d["split"] * (d["last_continuous_price"] + d["div"]) / prev_close_ff)
    return d


def build_features(tables: dict[str, pd.DataFrame], market: str, *, feed_latency_s: float = 1.0) -> pd.DataFrame:
    d = tables["daily_bars"]
    d = d[d["market"] == market].copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values(["symbol", "date"]).reset_index(drop=True)
    lat = pd.Timedelta(seconds=feed_latency_s)
    master = tables["security_master"]
    master = master[master["market"] == market].set_index("symbol")
    d["sector"] = d["symbol"].map(master["sector"])
    d["board_lot"] = d["symbol"].map(master["board_lot"]).astype(float)
    if "cas_eligible" in master:
        d["cas_eligible"] = d["symbol"].map(master["cas_eligible"])
    # opening price availability: open is known at the open time of that day (approximated as the
    # earliest of LCP-latency and EOD receipt minus the session length; we use EOD receipt for history
    # features, and the adapter open time for order-time checks)
    d["open_avail"] = d["receipt_ts"]
    ca = tables.get("corporate_actions")
    ca = ca[ca["market"] == market] if ca is not None and not ca.empty else ca
    d = _adj_logs(d, ca)

    auc = d["close_method"] == "auction"
    d["gap"] = np.where(auc, np.log(d["close_auction_price"] / d["last_continuous_price"]), np.nan)
    d["gap__avail"] = d["close_auction_receipt_ts"]
    d["gap_eod"] = d["gap"]
    d["gap_eod__avail"] = d["receipt_ts"]
    d["preclose_ret"] = (d["L_lcp"] - d.groupby("symbol")["L_close"].shift(1).groupby(d["symbol"]).ffill()).where(~d["ca_unannounced"])
    d["preclose_ret__avail"] = d["last_continuous_ts"] + lat
    d["intraday_ret"] = np.log(d["last_continuous_price"] / d["open"])
    d["intraday_ret__avail"] = d["last_continuous_ts"] + lat
    mid = (d["preclose_bid"] + d["preclose_ask"]) / 2
    d["spread_rel"] = (d["preclose_ask"] - d["preclose_bid"]) / mid
    d["spread_rel__avail"] = d["last_continuous_ts"] + lat

    g = d.groupby("symbol", sort=False)
    roll = lambda col, w, fn, minp: g[col].transform(lambda s: getattr(s.rolling(w, min_periods=minp), fn)())
    d["vol_20"] = roll("ret_cc", 20, "std", 15)
    d["adv_20"] = roll("turnover", 20, "mean", 15)
    d["cav_20"] = roll("close_auction_volume", 20, "mean", 15)
    d["vol_shares_20"] = roll("volume", 20, "mean", 15)
    if "tac_volume" in d and d["tac_volume"].notna().any():
        d["tac_20"] = roll("tac_volume", 20, "mean", 15)
    else:
        d["tac_20"] = np.nan
    for c in ("ret_cc", "vol_20", "adv_20", "cav_20", "vol_shares_20", "tac_20"):
        d[f"{c}__avail"] = d["receipt_ts"]
    d["mcap"] = d["close"] * d["symbol"].map(master["shares_outstanding"])
    d["mcap__avail"] = d["receipt_ts"]

    # index returns for beta / abnormal returns
    lv = tables.get("index_levels")
    lv = lv[lv["market"] == market].copy() if lv is not None and not lv.empty else None
    if lv is not None and not lv.empty:
        lv["date"] = pd.to_datetime(lv["date"])
        lv = lv.sort_values("date")
        lv["m_ret"] = np.log(lv["close"] / lv["close"].shift(1))
        lv["mL_close"] = np.log(lv["close"])
        lv["mL_open"] = np.log(lv["open"])
        lv["mL_lcp"] = np.log(lv["level_at_lcp"])
        d = d.merge(lv[["date", "m_ret", "mL_close", "mL_open", "mL_lcp"]], on="date", how="left")
        d = d.sort_values(["symbol", "date"]).reset_index(drop=True)
        g = d.groupby("symbol", sort=False)

        def _beta(x: pd.DataFrame) -> pd.Series:
            cov = x["ret_cc"].rolling(60, min_periods=40).cov(x["m_ret"])
            var = x["m_ret"].rolling(60, min_periods=40).var()
            return cov / var

        d["beta_60"] = g[["ret_cc", "m_ret"]].apply(_beta).reset_index(level=0, drop=True).reindex(d.index)
    else:
        d["beta_60"] = np.nan
        d["m_ret"] = np.nan
        d["mL_close"] = d["mL_open"] = d["mL_lcp"] = np.nan
    d["beta_60__avail"] = d["receipt_ts"]

    # ------------------------------------------------ diagnostic labels (fwd_)
    g = d.groupby("symbol", sort=False)
    d["next_date"] = g["date"].shift(-1)
    for k in range(1, 6):
        d[f"fwd_c{k}"] = g["L_close"].shift(-k) - d["L_close"]                # close_t -> close_{t+k}
        d[f"fwd_o1c{k}"] = g["L_close"].shift(-k) - g["L_open"].shift(-1)     # open_{t+1} -> close_{t+k}
        d[f"fwd_m_c{k}"] = g["mL_close"].shift(-k) - d["mL_close"]
        d[f"fwd_m_o1c{k}"] = g["mL_close"].shift(-k) - g["mL_open"].shift(-1)
    d["fwd_o1"] = g["L_open"].shift(-1) - d["L_close"]                        # close_t -> open_{t+1}
    d["fwd_m_o1"] = g["mL_open"].shift(-1) - d["mL_close"]
    d["fwd_lcp_c0"] = d["L_close"] - d["L_lcp"]                               # LCP_t -> close_t (= gap adj.)
    d["fwd_m_lcp_c0"] = d["mL_close"] - d["mL_lcp"]
    # next-day calendar adjacency (selection-bias accounting)
    cal = pd.DatetimeIndex(sorted(d["date"].unique()))
    nxt = pd.Series(cal[1:].append(pd.DatetimeIndex([pd.NaT])), index=cal)
    d["fwd_next_is_adjacent"] = d["next_date"] == d["date"].map(nxt)
    d["market"] = market
    return d


@dataclass
class FeatureView:
    """Read-only access to features for strategies; forward labels are blocked."""

    df: pd.DataFrame
    diagnostic: bool = False

    def __getitem__(self, col):
        cols = [col] if isinstance(col, str) else list(col)
        bad = [c for c in cols if str(c).startswith("fwd_") or str(c).startswith("L_")]
        if bad and not self.diagnostic:
            raise LeakageError(f"strategy attempted to read forward-looking columns {bad}")
        return self.df[col]

    @property
    def columns(self):
        return [c for c in self.df.columns if self.diagnostic or not (str(c).startswith("fwd_") or str(c).startswith("L_"))]

    def frame(self, cols) -> pd.DataFrame:
        return self[list(cols)].copy()

    def avail(self, feature: str) -> pd.Series:
        return self.df[f"{feature}__avail"]

    def __len__(self):
        return len(self.df)
