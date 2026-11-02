"""Statistical inference utilities.

* Newey-West HAC t-statistics for means of autocorrelated series.
* Stationary block bootstrap (Politis-Romano) for confidence intervals of
  any statistic of a daily return series - preserves serial dependence.
* Multiple-testing control: Benjamini-Hochberg FDR, Holm and Bonferroni.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats


def newey_west_tstat(x, lags: int | None = None) -> tuple[float, float, float]:
    """Return (mean, HAC standard error, two-sided p-value)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    if lags is None:
        lags = int(math.floor(4 * (n / 100) ** (2 / 9)))
    mu = x.mean()
    e = x - mu
    s = e @ e / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (e[L:] @ e[:-L]) / n
    se = math.sqrt(max(s, 0) / n)
    if se == 0:
        return float(mu), 0.0, float("nan")
    t = mu / se
    p = 2 * (1 - stats.t.cdf(abs(t), df=n - 1))
    return float(mu), float(se), float(p)


def stationary_bootstrap_indices(n: int, n_boot: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    p = 1.0 / mean_block
    idx = np.empty((n_boot, n), dtype=np.int64)
    starts = rng.integers(0, n, size=(n_boot, n))
    new_block = rng.random((n_boot, n)) < p
    new_block[:, 0] = True
    cur = starts[:, 0].copy()
    for t in range(n):
        cur = np.where(new_block[:, t], starts[:, t], (cur + 1) % n)
        idx[:, t] = cur
    return idx


def bootstrap_ci(x, stat, *, n_boot: int = 1000, mean_block: float = 10.0, alpha: float = 0.05,
                 seed: int = 0) -> tuple[float, float, float]:
    """Point estimate and (1-alpha) percentile CI of ``stat(x)`` under the stationary bootstrap."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 10:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(len(x), n_boot, mean_block, rng)
    vals = np.array([stat(x[i]) for i in idx])
    vals = vals[np.isfinite(vals)]
    return float(stat(x)), float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))


def bootstrap_pvalue_mean(x, *, n_boot: int = 2000, mean_block: float = 10.0, seed: int = 0) -> float:
    """Two-sided bootstrap p-value for H0: mean = 0 (recentred stationary bootstrap)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 10:
        return float("nan")
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(len(x), n_boot, mean_block, rng)
    xc = x - x.mean()
    bm = xc[idx].mean(axis=1)
    return float((np.abs(bm) >= abs(x.mean())).mean())


def bh_fdr(pvals) -> np.ndarray:
    p = np.asarray(pvals, dtype=float)
    out = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    q = p[ok]
    m = len(q)
    if m == 0:
        return out
    order = np.argsort(q)
    ranked = q[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adj = np.empty(m)
    adj[order] = np.minimum(ranked, 1.0)
    out[ok] = adj
    return out


def holm(pvals) -> np.ndarray:
    p = np.asarray(pvals, dtype=float)
    out = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    q = p[ok]
    m = len(q)
    if m == 0:
        return out
    order = np.argsort(q)
    adj = np.maximum.accumulate(q[order] * (m - np.arange(m)))
    res = np.empty(m)
    res[order] = np.minimum(adj, 1.0)
    out[ok] = res
    return out


def bonferroni(pvals) -> np.ndarray:
    p = np.asarray(pvals, dtype=float)
    m = int(np.isfinite(p).sum())
    return np.minimum(p * max(m, 1), 1.0)
