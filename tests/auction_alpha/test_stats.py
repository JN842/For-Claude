import numpy as np
import pandas as pd
import pytest

from auction_alpha.stats.inference import bh_fdr, bonferroni, bootstrap_ci, holm, newey_west_tstat
from auction_alpha.stats.metrics import max_drawdown, performance


def test_newey_west_detects_mean():
    rng = np.random.default_rng(0)
    mu, se, p = newey_west_tstat(rng.normal(0.1, 1, 5000))
    assert p < 1e-6 and abs(mu - 0.1) < 0.05
    _, _, p0 = newey_west_tstat(rng.normal(0.0, 1, 5000))
    assert p0 > 0.001


def test_bootstrap_ci_covers_true_mean():
    rng = np.random.default_rng(1)
    x = rng.normal(0.5, 1, 800)
    est, lo, hi = bootstrap_ci(x, np.mean, n_boot=400)
    assert lo < x.mean() < hi
    assert 0.5 * 2 * 1.96 / np.sqrt(800) < hi - lo < 2.0 * 2 * 1.96 / np.sqrt(800)


def test_multiple_testing_adjustments():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    q = bh_fdr(p)
    assert np.allclose(q, [0.02, 0.04, 0.04, 0.02])
    h = holm(p)
    assert np.allclose(h, [0.03, 0.06, 0.06, 0.02])
    assert np.allclose(bonferroni(p), [0.04, 0.16, 0.12, 0.02])
    assert np.isnan(bh_fdr([np.nan, 0.01])[0])


def test_max_drawdown():
    assert max_drawdown(np.array([0.1, 0.05, 0.12, 0.0])) == pytest.approx(-0.12)


def test_sharpe_uses_daily_series_including_flat_days():
    daily = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=10), "ret": [0.01] + [0.0] * 9})
    for c in ("pnl", "pnl_gross", "pnl_long", "pnl_short", "pnl_hedge", "fees", "slippage", "borrow", "funding",
              "gross_exposure", "turnover", "n_positions"):
        daily[c] = 0.0
    daily["pnl"] = daily["ret"] * 1e6
    daily["pnl_gross"] = daily["pnl"]
    m = performance(daily, None, capital=1e6, periods_per_year=250, bootstrap=False)
    r = daily["ret"].to_numpy()
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(250))
    assert m["ann_return"] == pytest.approx(0.001 * 250)
