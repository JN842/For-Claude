"""Cointegration / mean-reversion diagnostics on log prices."""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller, coint, kpss

warnings.filterwarnings("ignore", message="(adfuller|kpss) currently returns", category=FutureWarning)


def ols_beta(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """(alpha, beta) of y = alpha + beta x."""
    xm, ym = x.mean(), y.mean()
    beta = ((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum()
    return ym - beta * xm, beta


def adf_p(spread: np.ndarray) -> float:
    return adfuller(spread, maxlag=1, regression="c", autolag=None)[1]


def half_life(spread: np.ndarray) -> float:
    """From ds_t = a + b s_{t-1}: half-life = -ln 2 / ln(1 + b)."""
    s = np.asarray(spread)
    _, b = ols_beta(np.diff(s), s[:-1])
    return np.inf if b >= 0 else -np.log(2) / np.log1p(b)


def pair_diagnostics(log_a: pd.Series, log_b: pd.Series) -> dict:
    a, b = log_a.to_numpy(), log_b.to_numpy()
    alpha, beta = ols_beta(a, b)
    spread = a - alpha - beta * b
    with np.errstate(all="ignore"):
        kpss_p = kpss(spread, regression="c", nlags="auto")[1]
    return {
        "beta": beta,
        "eg_coint_p": coint(a, b)[1],
        "adf_p": adfuller(spread, autolag="AIC")[1],
        "kpss_p": kpss_p,  # null = stationary; small p rejects stationarity
        "half_life_d": half_life(spread),
        "ret_corr": np.corrcoef(np.diff(a), np.diff(b))[0, 1],
    }


def rolling_diagnostics(log_a: pd.Series, log_b: pd.Series, window: int = 252, step: int = 21) -> pd.DataFrame:
    rows = []
    a, b = log_a.to_numpy(), log_b.to_numpy()
    for end in range(window, len(a) + 1, step):
        ya, xb = a[end - window:end], b[end - window:end]
        alpha, beta = ols_beta(ya, xb)
        s = ya - alpha - beta * xb
        rows.append((log_a.index[end - 1], beta, adfuller(s, autolag="AIC")[1], half_life(s)))
    return pd.DataFrame(rows, columns=["end", "beta", "adf_p", "half_life_d"]).set_index("end")
