"""Diagnostic (mode A) event studies.

These use forward-looking labels and **are not executable strategies**.  In
particular, a closing-gap event study measured from the closing-auction
price assumes a trade at a price that was only known *after* the auction - a
live order could not have been sized and filled at that price (except via
SGX Trade-at-Close, which is tested separately with liquidity caps).

Inference clusters by date: each date's events are averaged first, then the
time series of daily means is tested with Newey-West and a stationary block
bootstrap, because same-day events are cross-sectionally correlated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..stats.inference import bootstrap_ci, newey_west_tstat

NOT_EXECUTABLE = "DIAGNOSTIC - NOT EXECUTABLE (uses prices not known at decision time)"

HORIZONS = [  # (label, stock label col, market label col, executable?)
    ("C0->O1", "fwd_o1", "fwd_m_o1", False),
    ("C0->C1", "fwd_c1", "fwd_m_c1", False),
    ("C0->C2", "fwd_c2", "fwd_m_c2", False),
    ("C0->C3", "fwd_c3", "fwd_m_c3", False),
    ("C0->C5", "fwd_c5", "fwd_m_c5", False),
    ("O1->C1", "fwd_o1c1", "fwd_m_o1c1", True),
    ("O1->C2", "fwd_o1c2", "fwd_m_o1c2", True),
    ("O1->C5", "fwd_o1c5", "fwd_m_o1c5", True),
]


def _clustered(df: pd.DataFrame, col: str, *, n_boot: int = 400, seed: int = 0) -> dict:
    s = df.groupby("date")[col].mean().dropna()
    mu, se, p = newey_west_tstat(s.to_numpy())
    _, lo, hi = bootstrap_ci(s.to_numpy(), np.mean, n_boot=n_boot, mean_block=5, seed=seed) if (len(s) >= 10 and n_boot > 0) else (mu, np.nan, np.nan)
    return dict(mean_bps=mu * 1e4, se_bps=se * 1e4, t=mu / se if se else np.nan, p=p, ci_lo_bps=lo * 1e4,
                ci_hi_bps=hi * 1e4, n_events=int(df[col].notna().sum()), n_dates=int(len(s)))


def abnormal(df: pd.DataFrame, stock_col: str, mkt_col: str) -> pd.Series:
    beta = df["beta_60"].clip(0, 3).fillna(1.0) if "beta_60" in df else 1.0
    m = df[mkt_col].fillna(0.0) if mkt_col in df else 0.0
    return df[stock_col] - beta * m


def gap_event_study(f: pd.DataFrame, *, threshold_bps: float, sign: int = -1, n_boot: int = 400,
                    market: str = "") -> dict:
    """Closing-gap events: |gap| >= threshold.  ``sign=-1`` signs returns for reversal."""
    base = f[(f["close_method"] == "auction") & f["gap"].notna() & ~f["suspended"].astype(bool)].copy()
    ev = base[base["gap"].abs() >= threshold_bps / 1e4].copy()
    n_raw = len(ev)
    ev["dir"] = sign * np.sign(ev["gap"])
    rows = []
    for lab, sc, mc, exe in HORIZONS:
        if sc not in ev:
            continue
        ev[f"ar_{lab}"] = ev["dir"] * abnormal(ev, sc, mc)
        ev[f"rr_{lab}"] = ev["dir"] * ev[sc]
        r = _clustered(ev, f"ar_{lab}", n_boot=n_boot)
        r.update(horizon=lab, executable_window=exe, kind="market_adjusted")
        rows.append(r)
        r2 = _clustered(ev, f"rr_{lab}", n_boot=0)
        r2.update(horizon=lab, executable_window=exe, kind="raw")
        rows.append(r2)
    summary = pd.DataFrame(rows)
    excluded = int(ev["fwd_c1"].isna().sum())
    # matched controls: same date, same liquidity & volatility tercile, small gap
    base["liq_t"] = base.groupby("date")["adv_20"].transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if s.notna().sum() >= 3 else np.nan)
    base["vol_t"] = base.groupby("date")["vol_20"].transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if s.notna().sum() >= 3 else np.nan)
    ctrl = base[base["gap"].abs() < threshold_bps / 4e4]
    cm = ctrl.groupby(["date", "liq_t", "vol_t"])[["fwd_o1", "fwd_c1", "fwd_o1c1"]].mean().add_prefix("ctl_")
    ev = ev.join(base[["liq_t", "vol_t"]]).join(cm, on=["date", "liq_t", "vol_t"])
    matched = {}
    for lab, sc in (("C0->O1", "fwd_o1"), ("C0->C1", "fwd_c1"), ("O1->C1", "fwd_o1c1")):
        ev[f"mc_{lab}"] = ev["dir"] * (ev[sc] - ev[f"ctl_{sc}"])
        matched[lab] = _clustered(ev, f"mc_{lab}", n_boot=0)
    # buckets
    buckets = []
    for name, col in (("volatility", "vol_20"), ("liquidity", "adv_20"), ("mcap", "mcap")):
        if col not in ev or ev[col].notna().sum() < 30:
            continue
        q = pd.qcut(ev[col].rank(method="first"), 3, labels=["low", "mid", "high"])
        for b, g in ev.groupby(q, observed=True):
            for lab in ("C0->C1", "O1->C1"):
                r = _clustered(g, f"ar_{lab}", n_boot=0)
                buckets.append(dict(dimension=name, bucket=str(b), horizon=lab, **r))
    for b, g in ev.groupby("sector"):
        for lab in ("C0->C1", "O1->C1"):
            r = _clustered(g, f"ar_{lab}", n_boot=0)
            buckets.append(dict(dimension="sector", bucket=str(b), horizon=lab, **r))
    # CAR path (mean signed abnormal cumulative return by horizon), plus the gap itself
    path = [dict(step="LCP->C0", mean_bps=float((ev["dir"] * ev["gap"]).mean() * 1e4))]
    for lab in ("C0->O1", "C0->C1", "C0->C2", "C0->C3", "C0->C5"):
        path.append(dict(step=lab, mean_bps=float(ev[f"ar_{lab}"].mean() * 1e4)))
    # yearly
    ev["year"] = pd.to_datetime(ev["date"]).dt.year
    yearly = ev.groupby("year").agg(n=("gap", "size"), c0c1_bps=("ar_C0->C1", lambda x: x.mean() * 1e4),
                                    o1c1_bps=("ar_O1->C1", lambda x: x.mean() * 1e4)).reset_index()
    hist_counts, edges = np.histogram(base["gap"].clip(-0.05, 0.05) * 1e4, bins=80)
    dist = dict(quantiles_bps={str(q): float(base["gap"].quantile(q) * 1e4) for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)},
                abs_mean_bps=float(base["gap"].abs().mean() * 1e4), frac_zero=float((base["gap"] == 0).mean()),
                hist_counts=hist_counts.tolist(), hist_edges_bps=edges.tolist(), n=int(len(base)))
    sample = ev[["date", "symbol", "gap", "dir", "ar_C0->O1", "ar_C0->C1", "ar_O1->C1", "sector"]].copy()
    return dict(label=NOT_EXECUTABLE, market=market, threshold_bps=threshold_bps, sign=sign, n_events=n_raw,
                n_excluded_missing_next_day=excluded, summary=summary, matched=matched,
                buckets=pd.DataFrame(buckets), car_path=pd.DataFrame(path), yearly=yearly, distribution=dist,
                events=sample)


def rebalance_event_study(f: pd.DataFrame, events: pd.DataFrame, *, window=(-15, 10)) -> dict:
    """CAR around the index trade date (effective_date - 1 session) for adds and deletes."""
    if events is None or events.empty:
        return dict(label=NOT_EXECUTABLE, n_events=0, car=pd.DataFrame(), volume=pd.DataFrame())
    f = f.sort_values(["symbol", "date"])
    cal = pd.DatetimeIndex(sorted(f["date"].unique()))
    f = f.assign(ar=f["ret_cc"] - f["beta_60"].clip(0, 3).fillna(1.0) * f["m_ret"].fillna(0.0))
    vol_ratio = f["close_auction_volume"] / f["cav_20"].shift(1)
    f = f.assign(cav_ratio=vol_ratio)
    by_sym = {s: g.set_index("date") for s, g in f.groupby("symbol")}
    rows, vrows = [], []
    for e in events.itertuples(index=False):
        eff = pd.Timestamp(e.effective_date)
        k = cal.searchsorted(eff)
        if k <= 0 or k >= len(cal):
            continue
        trade_day = cal[k - 1]
        g = by_sym.get(e.symbol)
        if g is None:
            continue
        sgn = 1 if e.event_type == "add" else -1
        ann_local = pd.Timestamp(e.announce_ts).tz_convert(None).normalize()
        ka = cal.searchsorted(ann_local)
        for rel in range(window[0], window[1] + 1):
            kk = k - 1 + rel
            if 0 <= kk < len(cal) and cal[kk] in g.index:
                r = g.loc[cal[kk]]
                rows.append(dict(symbol=e.symbol, event_type=e.event_type, rel_day=rel, ar=float(r["ar"]) if np.isfinite(r["ar"]) else np.nan,
                                 signed_ar=sgn * r["ar"] if np.isfinite(r["ar"]) else np.nan,
                                 days_from_announce=kk - ka))
        if trade_day in g.index:
            vrows.append(dict(symbol=e.symbol, event_type=e.event_type, cav_ratio_trade_day=float(g.loc[trade_day, "cav_ratio"])))
    car = pd.DataFrame(rows)
    if car.empty:
        return dict(label=NOT_EXECUTABLE, n_events=0, car=car, volume=pd.DataFrame(vrows))
    agg = car.groupby(["event_type", "rel_day"])["ar"].mean().groupby(level=0).cumsum().rename("car").reset_index()
    agg["car_bps"] = agg["car"] * 1e4
    pre = car[(car["rel_day"] > window[0]) & (car["rel_day"] <= 0)].groupby(["symbol", "event_type"])["signed_ar"].sum()
    post = car[(car["rel_day"] > 0)].groupby(["symbol", "event_type"])["signed_ar"].sum()
    stat = {}
    for name, s in (("pre_event_signed_car", pre), ("post_event_signed_car", post)):
        x = s.dropna().to_numpy()
        mu, se, p = newey_west_tstat(x, lags=0)
        stat[name] = dict(mean_bps=mu * 1e4, t=mu / se if se else np.nan, p=p, n=len(x))
    return dict(label=NOT_EXECUTABLE, n_events=int(len(events)), car=agg, volume=pd.DataFrame(vrows), stats=stat)
