"""Walk-forward daily z-score pair backtest, with optional rolling-ADF gate.

Spread = log A - beta log B. Decision at the close of day t uses data up to t
only; the trade fills at the close of t + lag. P&L is on the return series you
pass in (price-only or total return), with constant weights wA = 1/(1+beta),
wB = beta/(1+beta) (gross exposure 1). A positive z means A is rich: short A,
long B.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .stats import adf_p, ols_beta


@dataclass(frozen=True)
class Params:
    lookback: int | None = 252     # beta/ADF window; None = fixed beta (ratio spread)
    fixed_beta: float = 1.0        # used when lookback is None
    zwin: int = 40
    entry: float = 2.0
    stop: float = 3.5
    max_hold: int = 40
    gate_p: float | None = 0.10    # open only if rolling ADF p < gate_p; None = ungated
    reestimate: int = 5            # re-fit beta/ADF every n days
    cost: float = 0.0015           # per side per leg, fraction of leg notional
    funding: str = "spot"          # "spot": borrow fee on short leg; "ssf": legs earn r - rf
    borrow: float = 0.03           # annual, spot mode
    rf: float = 0.02               # annual financing embedded in futures, ssf mode
    lag: int = 0                   # fill at close of t + lag


def _rolling_moments(la: np.ndarray, lb: np.ndarray, w: int):
    s = pd.DataFrame({"a": la, "b": lb, "aa": la * la, "bb": lb * lb, "ab": la * lb}).rolling(w).mean()
    return (s[c].to_numpy() for c in ("a", "b", "aa", "bb", "ab"))


def _z(la, lb, ma, mb, maa, mbb, mab, t, beta):
    mean = ma[t] - beta * mb[t]
    var = (maa[t] - ma[t] ** 2) + beta ** 2 * (mbb[t] - mb[t] ** 2) - 2 * beta * (mab[t] - ma[t] * mb[t])
    return (la[t] - beta * lb[t] - mean) / np.sqrt(var) if var > 0 else np.nan


def _fit_path(la, lb, p: Params):
    """beta_t and gate-open_t for every day, re-estimated every p.reestimate days."""
    n = len(la)
    beta = np.full(n, np.nan)
    gate = np.zeros(n, dtype=bool)
    if p.lookback is None:
        beta[:] = p.fixed_beta
        gate[:] = True
        return beta, gate
    b, g = np.nan, False
    for t in range(p.lookback - 1, n):
        if (t - p.lookback + 1) % p.reestimate == 0:
            ya, xb = la[t - p.lookback + 1:t + 1], lb[t - p.lookback + 1:t + 1]
            alpha, b = ols_beta(ya, xb)
            g = p.gate_p is None or adf_p(ya - alpha - b * xb) < p.gate_p
        beta[t], gate[t] = b, g
    return beta, gate


def _execute(order, pos, when, pnl, t, trades, cost):
    """Fill an order at the close of day t; returns the new position (or None)."""
    if order[0] == "open" and pos is None:
        _, side, b, _ = order
        pnl[t] -= cost  # cost x (wa + wb) = cost
        return {"side": side, "beta": b, "wa": 1 / (1 + b), "wb": b / (1 + b),
                "entry": when, "pnl": -cost, "days": 0}
    if order[0] == "close" and pos is not None:
        pnl[t] -= cost
        pos["pnl"] -= cost
        trades.append({**pos, "exit": when, "reason": order[1]})
        return None
    return pos


def backtest(sig_a: pd.Series, sig_b: pd.Series, ret_a: pd.Series, ret_b: pd.Series, p: Params):
    """Returns (daily pnl Series, trades DataFrame). sig_* are price levels for the
    signal, ret_* simple daily returns for P&L, all on the same index."""
    idx = sig_a.index
    la, lb = np.log(sig_a.to_numpy()), np.log(sig_b.to_numpy())
    ra, rb = ret_a.to_numpy(), ret_b.to_numpy()
    mom = tuple(_rolling_moments(la, lb, p.zwin))
    beta_path, gate = _fit_path(la, lb, p)
    start = max(p.zwin, p.lookback or 0) - 1
    n = len(idx)
    pnl = np.zeros(n)
    trades = []
    pos = None          # dict when in a trade (after the fill)
    pending = None      # ("open", side, beta, decided_t) or ("close", reason, decided_t)
    dt = 1 / 252

    for t in range(start, n):
        # 1. carry the open position from t-1 close to t close
        if pos is not None:
            wa, wb, s = pos["wa"], pos["wb"], pos["side"]
            if p.funding == "ssf":
                day = s * (wa * (ra[t] - p.rf * dt) - wb * (rb[t] - p.rf * dt))
            else:
                day = s * (wa * ra[t] - wb * rb[t]) - p.borrow * dt * (wb if s > 0 else wa)
            pnl[t] += day
            pos["pnl"] += day
            pos["days"] += 1

        # 2. execute an order decided `lag` days ago
        if pending is not None and t - pending[-1] >= p.lag:
            pos = _execute(pending, pos, idx[t], pnl, t, trades, p.cost)
            pending = None

        if pending is not None or t == n - 1:
            continue

        # 3. decide at close t
        if pos is None:
            b = beta_path[t]
            if not gate[t] or not b > 0:
                continue
            z = _z(la, lb, *mom, t, b)
            if abs(z) > p.entry:
                pending = ("open", -np.sign(z), b, t)
        else:
            z = _z(la, lb, *mom, t, pos["beta"])
            reason = None
            if pos["side"] * z >= 0:
                reason = "mean"
            elif abs(z) > p.stop:
                reason = "stop"
            elif pos["days"] >= p.max_hold:
                reason = "time"
            if reason:
                pending = ("close", reason, t)
        if pending is not None and p.lag == 0:
            pos = _execute(pending, pos, idx[t], pnl, t, trades, p.cost)
            pending = None

    if pos is not None:  # mark open trade to market at the end, no exit cost
        trades.append({**pos, "exit": idx[-1], "reason": "open"})
    daily = pd.Series(pnl, index=idx).iloc[start:]
    cols = ["entry", "exit", "side", "beta", "days", "pnl", "reason"]
    return daily, pd.DataFrame(trades, columns=cols)


def summarize(daily: pd.Series, trades: pd.DataFrame) -> dict:
    eq = (1 + daily).cumprod()
    sd = daily.std()
    closed = trades.loc[trades["reason"].ne("open")] if len(trades) else trades
    years = (daily.index[-1] - daily.index[0]).days / 365.25
    return {
        "trades": len(trades),
        "trades_per_yr": len(trades) / years if years else np.nan,
        "total": eq.iloc[-1] - 1,
        "cagr": eq.iloc[-1] ** (1 / years) - 1 if years else np.nan,
        "sharpe": daily.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
        "max_dd": (eq / eq.cummax() - 1).min(),
        "win_rate": (closed["pnl"] > 0).mean() if len(closed) else np.nan,
        "avg_trade_bp": trades["pnl"].mean() * 1e4 if len(trades) else np.nan,
        "stops": int((trades["reason"] == "stop").sum()) if len(trades) else 0,
    }


def by_year(daily: pd.Series) -> pd.Series:
    return daily.groupby(daily.index.year).apply(lambda x: (1 + x).prod() - 1)


def params_dict(p: Params) -> dict:
    return asdict(p)
