"""Long-vol P&L of SET50 DWs around large moves, delta-hedged with S50 futures.

Each day a long DW position is marked two ways:
  model  — repriced with the issuer convention at the issuer's fitted
           (constant) vol, so P&L is gamma vs theta without tick noise;
  quoted — the published close (rounded to tick), bought one tick above it
           (the ask) and sold at it (the bid) when ``trade_cost`` is on.
The hedge is the model delta, rebalanced at each close.  All figures are in
baht per DW and as % of the DW price at entry.
"""

import numpy as np
import pandas as pd

from mm_pricing import KGI_CONVENTION, issuer_implied_vol, price, tick_size, year_fraction


def _model(F, K, T, vol, cp, ratio, convention):
    sign = 1.0 if cp == "C" else -1.0
    bump = 0.01 * F
    p = price(F, K, T, vol, convention["r"], convention["carry"], sign) / ratio
    up = price(F + bump, K, T, vol, convention["r"], convention["carry"], sign) / ratio
    down = price(F - bump, K, T, vol, convention["r"], convention["carry"], sign) / ratio
    return p, (up - down) / (2 * bump), (up - 2 * p + down) / bump**2


def daily_pnl(daily, terms, convention=KGI_CONVENTION, vols=None):
    """One row per series-day: hedged P&L split into gamma, theta and residual.

    ``vols`` maps symbol -> vol; default is each series' median issuer IV
    (``issuer_implied_vol``), which for DW13 is constant to ~0.1 vol pt.
    """
    daily = issuer_implied_vol(daily, terms, convention)
    if vols is None:
        vols = daily.groupby("symbol")["iv_mm"].median().to_dict()
    terms = terms.set_index("symbol")
    out = []
    for symbol, rows in daily.sort_values("date").groupby("symbol"):
        term = terms.loc[symbol]
        T = year_fraction(rows["date"], term[convention["t_end"]], convention["clock"],
                          convention["day_offset"], 365.0)
        F = rows["underlying"].to_numpy(float)
        p, delta, gamma = _model(F, term["strike"], T, vols[symbol], term["cp"],
                                 term["ratio"], convention)
        frame = rows[["date", "symbol", "underlying", "dw_price", "iv_mm"]].assign(
            cp=term["cp"], vol=vols[symbol], T=T, model=p, delta=delta, gamma=gamma)
        dF = np.diff(F, prepend=np.nan)
        hedge = -np.roll(delta, 1) * dF
        frame["dF"] = dF
        frame["ret"] = dF / np.roll(F, 1)
        frame["pnl_model"] = np.diff(p, prepend=np.nan) + hedge
        frame["pnl_quoted"] = np.diff(frame["dw_price"].to_numpy(float), prepend=np.nan) + hedge
        frame["gamma_pnl"] = 0.5 * np.roll(gamma, 1) * dF**2
        # theta over the step at unchanged F (issuer clock)
        p_aged, _, _ = _model(np.roll(F, 1), term["strike"], T, vols[symbol], term["cp"],
                              term["ratio"], convention)
        frame["theta_pnl"] = p_aged - np.roll(p, 1)
        frame["entry_price"] = np.roll(frame["dw_price"].to_numpy(float), 1)
        frame.iloc[0, frame.columns.get_indexer(
            ["dF", "ret", "pnl_model", "pnl_quoted", "gamma_pnl", "theta_pnl", "entry_price"])] = np.nan
        out.append(frame)
    return pd.concat(out, ignore_index=True)


def holding_pnl(pnl, start, end, trade_cost=True):
    """Hedged P&L of buying at the close of ``start`` and selling at ``end``.

    Returns one row per series with model and quoted P&L (baht per DW and
    % of entry price).  With ``trade_cost`` the quoted leg pays one tick on
    entry (buy at ask = close + tick).
    """
    window = pnl[(pnl["date"] > pd.Timestamp(start)) & (pnl["date"] <= pd.Timestamp(end))]
    entry = pnl[pnl["date"] == pd.Timestamp(start)].set_index("symbol")["dw_price"]
    summary = window.groupby("symbol").agg(
        cp=("cp", "first"), days=("date", "size"), ret=("ret", lambda r: (1 + r).prod() - 1),
        pnl_model=("pnl_model", "sum"), pnl_quoted=("pnl_quoted", "sum"),
        gamma=("gamma_pnl", "sum"), theta=("theta_pnl", "sum"))
    summary["entry"] = entry
    if trade_cost:
        summary["pnl_quoted"] -= tick_size(summary["entry"].to_numpy())
    for column in ("pnl_model", "pnl_quoted", "gamma", "theta"):
        summary[column + "_pct"] = summary[column] / summary["entry"] * 100
    return summary.dropna(subset=["entry"])
