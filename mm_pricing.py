"""Reverse-engineer a DW market maker's pricing formula from its bid grid.

KGI publishes, for each DW13 series, the bid it will show at every S50
futures level for the next few trading days (``data/thaiwarrant/
dw_indicative.csv``).  Each cell pins the model price to one 0.01 tick, so
for a candidate convention every cell gives an interval of vols that
reproduce it; the vol inside the most intervals is that series' IV and the
share of cells it covers scores the convention.

Convention searched (forward = F·exp(carry·T), discount exp(-r·T)):
  carry      extra drift on the futures price (the issuer's "carry")
  r          discount rate
  t_end      "last_trade" or "expiry"
  clock      "calendar": T = (calendar days to t_end + day_offset) / 365
             "trading":  T = (SET trading days to t_end + day_offset) / days_per_year
             "weighted<h>" (e.g. "weighted0.4"): trading days count 1, weekends
             and holidays count h; days_per_year is then 252 + 113·h
  rounding   "floor" (bid = floor(theo - spread)) or "round"
  spread     baht taken off the model price before rounding
"""

from itertools import product
from math import erf, sqrt

import numpy as np
import pandas as pd


TICK = 0.01
# SET tick size by price: (lower bound, tick)
TICK_TABLE = [(0.0, 0.01), (2.0, 0.02), (5.0, 0.05), (10.0, 0.10), (25.0, 0.25)]


def tick_size(px):
    px = np.asarray(px, dtype=float)
    tick = np.full(px.shape, TICK_TABLE[0][1])
    for bound, size in TICK_TABLE[1:]:
        tick = np.where(px >= bound - 1e-9, size, tick)
    return tick


def round_to_tick(px, rounding="round"):
    tick = tick_size(px)
    step = np.floor(px / tick + 1e-9) if rounding == "floor" else np.round(px / tick)
    out = step * tick
    # a value rounded across a tick boundary must sit on the coarser grid
    return np.round(np.where(tick_size(out) > tick, np.round(px / tick_size(out)) * tick_size(out), out), 2)
# SET holidays needed to count trading days to Dec-2026 expiries.  13 and 16
# Oct are missing from KGI's grid dates; the rest are the published calendar
# [ต้องยืนยันกับปฏิทิน SET].
SET_HOLIDAYS_2026Q4 = ["2026-10-13", "2026-10-16", "2026-10-23", "2026-12-07",
                       "2026-12-10", "2026-12-31", "2027-01-01"]
_erf = np.frompyfunc(erf, 1, 1)


def _cdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / sqrt(2.0)).astype(float))


def price(F, K, T, vol, r, carry, cp_sign):
    """Vectorised model price per index point (cp_sign +1 call, -1 put)."""
    fwd = F * np.exp(carry * T)
    sd = vol * np.sqrt(T)
    d1 = (np.log(fwd / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return np.exp(-r * T) * cp_sign * (fwd * _cdf(cp_sign * d1) - K * _cdf(cp_sign * d2))


def _vol_for_price(target, F, K, T, r, carry, cp_sign, lo=0.01, hi=2.0, iters=45):
    """Vectorised bisection; NaN where target is outside [price(lo), price(hi)]."""
    lo = np.full_like(target, lo, dtype=float)
    hi = np.full_like(target, hi, dtype=float)
    p_lo = price(F, K, T, lo, r, carry, cp_sign)
    p_hi = price(F, K, T, hi, r, carry, cp_sign)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        below = price(F, K, T, mid, r, carry, cp_sign) < target
        lo = np.where(below, mid, lo)
        hi = np.where(below, hi, mid)
    vol = 0.5 * (lo + hi)
    vol = np.where(target <= p_lo, -np.inf, vol)      # any vol above lo fits from below
    return np.where(target >= p_hi, np.inf, vol)


def year_fraction(dates, t_end_date, clock="calendar", day_offset=0,
                  days_per_year=365.0, holidays=SET_HOLIDAYS_2026Q4):
    dates = pd.to_datetime(pd.Series(dates)).dt.normalize()
    calendar = (pd.Timestamp(t_end_date) - dates).dt.days.to_numpy()
    if clock == "calendar":
        return (calendar + day_offset) / days_per_year
    trading = np.busday_count(dates.to_numpy().astype("datetime64[D]"),
                              np.datetime64(pd.Timestamp(t_end_date).date()),
                              holidays=list(holidays))
    if clock == "trading":
        return (trading + day_offset) / days_per_year
    if clock.startswith("weighted"):
        h = float(clock[len("weighted"):])
        return (trading + h * (calendar - trading) + day_offset) / (252.0 + 113.0 * h)
    raise ValueError(f"unknown clock {clock!r}")


def cell_vol_intervals(cells, ratio, strike, cp, t_end_date, carry=0.0, r=0.0,
                       day_offset=0, rounding="floor", spread=0.0,
                       clock="calendar", days_per_year=365.0):
    """[vol_lo, vol_hi) per grid cell that reproduces its bid."""
    T = year_fraction(cells["date"], t_end_date, clock, day_offset, days_per_year)
    F = cells["F"].to_numpy(float)
    bid = cells["bid"].to_numpy(float)
    tick = tick_size(bid)
    if rounding == "floor":
        lo_px, hi_px = bid, bid + tick
    else:
        lo_px, hi_px = bid - tick / 2, bid + tick / 2
    sign = 1.0 if cp == "C" else -1.0
    # bid cell -> model price per point range [lo, hi) after adding back spread
    lo_target = (lo_px + spread) * ratio
    hi_target = (hi_px + spread) * ratio
    return (_vol_for_price(lo_target, F, strike, T, r, carry, sign),
            _vol_for_price(hi_target, F, strike, T, r, carry, sign))


def best_vol(vol_lo, vol_hi):
    """Vol covered by the most [lo, hi) intervals, and that share."""
    ok = np.isfinite(vol_lo) | np.isfinite(vol_hi)
    events = np.concatenate([np.c_[vol_lo[ok], np.ones(ok.sum())],
                             np.c_[vol_hi[ok], -np.ones(ok.sum())]])
    events = events[np.lexsort((events[:, 1], events[:, 0]))]   # ends before starts at ties
    depth = np.cumsum(events[:, 1])
    i = int(np.argmax(depth))
    lo = events[i, 0]
    hi = events[i + 1, 0] if i + 1 < len(events) else lo
    vol = lo if not np.isfinite(hi) else (hi if not np.isfinite(lo) else 0.5 * (lo + hi))
    return float(vol), float(depth[i] / len(vol_lo))


def fit_series(cells, terms, **convention):
    """Best IV for one series under ``convention`` and its exact-match share."""
    t_end = convention.pop("t_end", "last_trade")
    vol_lo, vol_hi = cell_vol_intervals(
        cells, terms["ratio"], terms["strike"], terms["cp"], terms[t_end], **convention)
    return best_vol(vol_lo, vol_hi)


def fit_conventions(grid, terms, carries=np.arange(0.0, 0.0801, 0.005),
                    rates=(0.0, 0.0125), t_ends=("last_trade", "expiry"),
                    day_offsets=(0,), roundings=("floor", "round"),
                    spreads=(0.0, 0.01), clocks=(("calendar", 365.0),)):
    """Score every convention: median and min exact-match share across series."""
    terms = terms.set_index("symbol")
    rows = []
    for carry, r, t_end, offset, rounding, spread, (clock, dpy) in product(
            carries, rates, t_ends, day_offsets, roundings, spreads, clocks):
        shares, vols = {}, {}
        for symbol, cells in grid.groupby("symbol"):
            vols[symbol], shares[symbol] = fit_series(
                cells, terms.loc[symbol], carry=carry, r=r, t_end=t_end,
                day_offset=offset, rounding=rounding, spread=spread,
                clock=clock, days_per_year=dpy)
        share = pd.Series(shares)
        rows.append({"carry": carry, "r": r, "t_end": t_end, "day_offset": offset,
                     "clock": clock, "days_per_year": dpy,
                     "rounding": rounding, "spread": spread,
                     "median_match": share.median(), "min_match": share.min(),
                     "mean_match": share.mean(), "vols": vols, "shares": shares})
    return pd.DataFrame(rows).sort_values(["mean_match", "min_match"], ascending=False,
                                          ignore_index=True)


def model_grid(cells, terms, vol, carry, r=0.0, t_end="last_trade", day_offset=0,
               rounding="floor", spread=0.0, clock="calendar", days_per_year=365.0):
    """Bid the convention predicts for each cell, next to the published bid."""
    T = year_fraction(cells["date"], terms[t_end], clock, day_offset, days_per_year)
    sign = 1.0 if terms["cp"] == "C" else -1.0
    theo = price(cells["F"].to_numpy(float), terms["strike"], T, vol, r, carry, sign)
    theo = theo / terms["ratio"] - spread
    model_bid = round_to_tick(theo, rounding)
    bid = cells["bid"].to_numpy(float)
    return cells.assign(theo=theo, model_bid=model_bid,
                        diff_ticks=np.round((bid - model_bid) / tick_size(bid)).astype(int))


# Convention that reproduces KGI's DW13 bid grid (Oct 2026): 90-100% of cells
# exact, every other cell within one tick (fit_conventions on
# data/thaiwarrant/dw_indicative.csv; see docs/dw_iv_findings.md).
KGI_CONVENTION = {"carry": 0.0, "r": 0.0, "t_end": "last_trade", "day_offset": 0,
                  "clock": "weighted0.50", "rounding": "round", "spread": 0.0}


def issuer_implied_vol(daily, terms, convention=KGI_CONVENTION, min_price=0.0):
    """Back out each day's vol from the DW close under an issuer convention.

    ``daily`` has date, symbol, underlying (the reference S50 futures),
    dw_price; ``terms`` has symbol, cp, strike, ratio and the ``t_end``
    column.  Adds ``iv_mm``; NaN below ``min_price``.
    """
    terms = terms.set_index("symbol")
    out = []
    for symbol, rows in daily.groupby("symbol"):
        term = terms.loc[symbol]
        T = year_fraction(rows["date"], term[convention["t_end"]], convention["clock"],
                          convention["day_offset"], 365.0)
        sign = 1.0 if term["cp"] == "C" else -1.0
        target = (rows["dw_price"].to_numpy(float) + convention["spread"]) * term["ratio"]
        vol = _vol_for_price(target, rows["underlying"].to_numpy(float), term["strike"], T,
                             convention["r"], convention["carry"], sign)
        vol = np.where(np.isfinite(vol) & (rows["dw_price"].to_numpy(float) >= min_price),
                       vol, np.nan)
        out.append(rows.assign(iv_mm=vol))
    return pd.concat(out).sort_index()


def build_grid(term, vol, F_levels, dates, convention=KGI_CONVENTION):
    """Our bid table for one series: every futures level × date, rounded to tick."""
    cells = pd.DataFrame([(F, pd.Timestamp(d)) for d in dates for F in F_levels],
                         columns=["F", "date"]).assign(bid=0.0)
    grid = model_grid(cells, term, vol, **convention)
    return grid.assign(symbol=term.name, bid=grid["model_bid"])[
        ["symbol", "F", "date", "bid", "theo"]]


def compare_grid(ours, theirs):
    """Match ours vs a published grid on (symbol, F, date); diff in ticks."""
    merged = ours.merge(theirs[["symbol", "F", "date", "bid"]], on=["symbol", "F", "date"],
                        suffixes=("_ours", "_theirs"))
    merged["diff_ticks"] = np.round((merged["bid_theirs"] - merged["bid_ours"])
                                    / tick_size(merged["bid_theirs"])).astype(int)
    summary = merged.groupby("symbol").agg(
        cells=("diff_ticks", "size"),
        exact=("diff_ticks", lambda d: (d == 0).mean()),
        within_1=("diff_ticks", lambda d: (d.abs() <= 1).mean()),
        mean_diff=("diff_ticks", "mean"))
    return merged, summary
