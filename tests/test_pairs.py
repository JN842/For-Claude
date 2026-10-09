import numpy as np
import pandas as pd
import pytest

from pairs.corporate import (adjustment_factor, cash_dividends, load_actions, stock_factors,
                             total_return_index)
from pairs.daily_bt import Params, backtest, summarize
from pairs.stats import half_life

DAYS = pd.bdate_range("2021-01-04", periods=1200)


def actions(rows):
    df = pd.DataFrame(rows, columns=["symbol", "x_date", "value", "unit"])
    df["x_date"] = pd.to_datetime(df["x_date"])
    return df


def test_stock_dividend_factors_compound_before_each_x_date():
    ca = actions([("S", "2022-05-03", "10:1", "Share"), ("S", "2023-05-02", "10:1", "Share")])
    f = adjustment_factor(DAYS, ca, "S")
    assert f[pd.Timestamp("2022-05-02")] == pytest.approx((10 / 11) ** 2)
    assert f[pd.Timestamp("2022-05-03")] == pytest.approx(10 / 11)
    assert f[pd.Timestamp("2023-05-02")] == 1.0


def test_shipped_table_gives_sawad_factors_seen_in_data():
    sawad = stock_factors(load_actions(), "SAWAD")
    assert list(sawad["x_date"].dt.strftime("%Y-%m-%d")) == ["2024-05-08", "2025-05-08"]
    assert sawad["factor"].tolist() == pytest.approx([10 / 11, 10 / 11])


def test_cash_dividend_lands_on_x_date_in_adjusted_units():
    ca = actions([("S", "2022-05-03", "1.80", "Baht"), ("S", "2024-05-08", "10:1", "Share")])
    d = cash_dividends(DAYS, ca, "S")
    assert d[pd.Timestamp("2022-05-03")] == pytest.approx(1.80 * 10 / 11)
    assert d.sum() == pytest.approx(1.80 * 10 / 11)


def test_total_return_is_flat_through_a_fully_priced_dividend():
    idx = DAYS[:3]
    price = pd.Series([10.0, 9.0, 9.0], index=idx)
    div = pd.Series([0.0, 1.0, 0.0], index=idx)
    assert total_return_index(price, div).tolist() == pytest.approx([10, 10, 10])


def _pair(mean_reverting, seed=0, n=1200, phi=0.9):
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 0.015, n))
    if mean_reverting:
        s = np.zeros(n)
        for t in range(1, n):
            s[t] = phi * s[t - 1] + rng.normal(0, 0.01)
    else:
        s = np.cumsum(rng.normal(0, 0.01, n))
    a = pd.Series(np.exp(4 + common + s), index=DAYS[:n])
    b = pd.Series(np.exp(4 + common), index=DAYS[:n])
    return a, b


def _run(a, b, **kw):
    p = Params(cost=0.0, borrow=0.0, rf=0.0, funding="ssf", **kw)
    return summarize(*backtest(a, b, a.pct_change().fillna(0), b.pct_change().fillna(0), p))


def test_zero_cost_mean_reverting_pair_makes_money():
    a, b = _pair(True)
    res = _run(a, b, lookback=126, zwin=20, entry=1.5, gate_p=None)
    assert res["trades"] > 30
    assert res["total"] > 0.2 and res["sharpe"] > 1


def test_random_walk_pair_has_no_edge_on_average():
    totals = [_run(*_pair(False, seed), lookback=126, zwin=20, entry=1.5, gate_p=None)["sharpe"]
              for seed in range(20)]
    assert abs(np.mean(totals)) < 0.35


def test_one_day_lag_kills_an_edge_that_reverts_overnight():
    # White-noise spread: everything reverts by the next close, so filling one
    # day late must leave no edge, while filling at the signal close is profitable.
    a, b = _pair(True, seed=3, phi=0.0)
    r0 = _run(a, b, lookback=126, zwin=20, entry=1.5, gate_p=None)
    r1 = _run(a, b, lookback=126, zwin=20, entry=1.5, gate_p=None, lag=1)
    assert r0["sharpe"] > 2
    assert r1["sharpe"] < 0.5


def test_half_life_of_ar1():
    rng = np.random.default_rng(1)
    s = np.zeros(20000)
    for t in range(1, len(s)):
        s[t] = 0.9 * s[t - 1] + rng.normal()
    assert half_life(s) == pytest.approx(np.log(2) / -np.log(0.9), rel=0.1)
