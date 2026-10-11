"""SET50 DW vs TFEX S50 options: implied vol, pairing and vega-neutral sizing.

Research plan: ``docs/vega_arb_plan.md``.  The trade is long DW (sticky,
issuer-set IV) + short S50 option (market IV) when the option IV rises above
the DW IV, with the leftover delta hedged in S50 futures.

Both legs are priced with Black-76 on the S50 futures price of the matching
expiry, so dividends are already in the forward and the futures delta is the
hedge ratio directly.  Vol is a decimal (0.20 = 20 vol points), ``T`` is in
years.

A DW's price is per warrant; ``ratio`` is warrants per 1 index point of
payoff (term sheet), so ``dw_price * ratio`` is the price of one index-point
option.
"""

from math import erf, exp, log, pi, sqrt
from pathlib import Path

import numpy as np
import pandas as pd


MULTIPLIER = 200          # baht per index point, S50 options/futures [ต้องยืนยัน]
DW_MIN_PRICE = 0.30       # below this the 0.01 tick dominates the price
EVENTS_PATH = Path(__file__).resolve().parent / "refdata" / "vega_events.csv"


def _cdf(x):
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _pdf(x):
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def _cp(cp):
    cp = str(cp).strip().upper()[:1]
    if cp not in ("C", "P"):
        raise ValueError(f"cp must be 'C' or 'P': {cp!r}")
    return cp


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def black76(F, K, T, vol, r=0.0, cp="C"):
    """Price and greeks of one index-point European option on futures ``F``.

    Returns a dict: price, delta (per point of F), gamma, vega (per 1.00 of
    vol), theta (per year).
    """
    cp = _cp(cp)
    df = exp(-r * T)
    if T <= 0 or vol <= 0:
        intrinsic = max(F - K, 0.0) if cp == "C" else max(K - F, 0.0)
        in_money = (F > K) if cp == "C" else (F < K)
        delta = (df if in_money else 0.0) * (1 if cp == "C" else -1)
        return {"price": df * intrinsic, "delta": delta,
                "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    sd = vol * sqrt(T)
    d1 = (log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if cp == "C":
        price = df * (F * _cdf(d1) - K * _cdf(d2))
        delta = df * _cdf(d1)
    else:
        price = df * (K * _cdf(-d2) - F * _cdf(-d1))
        delta = -df * _cdf(-d1)
    gamma = df * _pdf(d1) / (F * sd)
    vega = df * F * _pdf(d1) * sqrt(T)
    theta = -F * df * _pdf(d1) * vol / (2 * sqrt(T)) + r * price
    return {"price": price, "delta": delta, "gamma": gamma,
            "vega": vega, "theta": theta}


def implied_vol(price, F, K, T, r=0.0, cp="C", lo=1e-4, hi=5.0, tol=1e-8):
    """Black-76 implied vol by bisection; NaN when no vol reproduces ``price``."""
    if not (np.isfinite(price) and T > 0 and F > 0 and K > 0):
        return np.nan
    if not black76(F, K, T, lo, r, cp)["price"] <= price <= black76(F, K, T, hi, r, cp)["price"]:
        return np.nan
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if black76(F, K, T, mid, r, cp)["price"] < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def dw_implied_vol(dw_price, ratio, F, K, T, r=0.0, cp="C",
                   min_price=DW_MIN_PRICE):
    """IV of a DW quote; NaN below ``min_price`` (tick-dominated) or intrinsic."""
    if not np.isfinite(dw_price) or dw_price < min_price:
        return np.nan
    return implied_vol(dw_price * ratio, F, K, T, r, cp)


# ---------------------------------------------------------------------------
# Pairing and sizing
# ---------------------------------------------------------------------------

def match_pairs(dw, options, max_moneyness_gap=0.03, max_days_gap=30):
    """Best S50 option for each DW quote on the same date.

    ``dw`` needs date, symbol, cp, strike, expiry, F; ``options`` needs
    date, series, cp, strike, expiry, F.  A pair must share date and cp,
    with |K_dw/F - K_opt/F| <= ``max_moneyness_gap`` and expiries no more
    than ``max_days_gap`` apart.  The closest moneyness wins, then the
    closest expiry.
    """
    dw = dw.assign(cp=dw["cp"].map(_cp), moneyness=dw["strike"] / dw["F"])
    options = options.assign(cp=options["cp"].map(_cp),
                             moneyness=options["strike"] / options["F"])
    pairs = dw.merge(options, on=["date", "cp"], suffixes=("_dw", "_opt"))
    pairs["moneyness_gap"] = (pairs["moneyness_dw"] - pairs["moneyness_opt"]).abs()
    pairs["days_gap"] = (pairs["expiry_dw"] - pairs["expiry_opt"]).dt.days.abs()
    pairs = pairs.loc[pairs["moneyness_gap"].le(max_moneyness_gap)
                      & pairs["days_gap"].le(max_days_gap)]
    pairs = pairs.sort_values(["date", "symbol", "moneyness_gap", "days_gap"])
    return pairs.drop_duplicates(["date", "symbol"]).reset_index(drop=True)


def size_pair(dw_greeks, ratio, opt_greeks, n_opt=1, multiplier=MULTIPLIER):
    """Vega-neutral DW quantity and futures hedge for selling ``n_opt`` options.

    Greeks are per index-point option (from :func:`black76`).  Per unit, a
    DW carries greek/ratio baht and an option contract greek*multiplier.
    Returns n_dw (bought), n_opt (sold), n_fut (signed, + = long) and the
    position's net gamma/theta in baht.
    """
    vega_dw = dw_greeks["vega"] / ratio
    vega_opt = opt_greeks["vega"] * multiplier
    if vega_dw <= 0:
        raise ValueError("DW has no vega")
    n_dw = n_opt * vega_opt / vega_dw
    net_delta = n_dw * dw_greeks["delta"] / ratio - n_opt * opt_greeks["delta"] * multiplier
    return {
        "n_dw": n_dw,
        "n_opt": n_opt,
        "n_fut": -net_delta / multiplier,
        "net_gamma": n_dw * dw_greeks["gamma"] / ratio - n_opt * opt_greeks["gamma"] * multiplier,
        "net_theta": n_dw * dw_greeks["theta"] / ratio - n_opt * opt_greeks["theta"] * multiplier,
    }


# ---------------------------------------------------------------------------
# Research questions
# ---------------------------------------------------------------------------

def iv_beta(iv_dw, iv_opt):
    """H1: OLS of daily ΔIV_dw on ΔIV_opt.  β well below 1 = sticky DW IV."""
    frame = pd.DataFrame({"dw": iv_dw, "opt": iv_opt}).diff().dropna()
    if len(frame) < 3 or frame["opt"].var() == 0:
        return {"alpha": np.nan, "beta": np.nan, "r2": np.nan, "n": len(frame)}
    beta, alpha = np.polyfit(frame["opt"], frame["dw"], 1)
    resid = frame["dw"] - (alpha + beta * frame["opt"])
    total = ((frame["dw"] - frame["dw"].mean()) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / total if total > 0 else np.nan
    return {"alpha": alpha, "beta": beta, "r2": r2, "n": len(frame)}


def gap_frequency(pairs, thresholds=(0.02, 0.05, 0.10)):
    """H3: share and count of pair-days with IV_opt − IV_dw above each threshold.

    ``pairs`` needs iv_dw and iv_opt columns (e.g. :func:`match_pairs`
    output with IVs added).
    """
    gap = (pairs["iv_opt"] - pairs["iv_dw"]).dropna()
    rows = [{"threshold": t, "days": int((gap > t).sum()),
             "share": float((gap > t).mean()) if len(gap) else np.nan}
            for t in thresholds]
    return pd.DataFrame(rows)


def load_events(path=EVENTS_PATH, kind=None, verified_only=False):
    """Event catalog (plan §8).  ``kind`` filters on type, e.g. ``"shock"``."""
    events = pd.read_csv(path, parse_dates=["date", "end_date"])
    events["verified"] = events["verified"].astype(str).str.lower().eq("true")
    if kind is not None:
        events = events.loc[events["type"].eq(kind)]
    if verified_only:
        events = events.loc[events["verified"]]
    return events.reset_index(drop=True)
