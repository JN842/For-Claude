import numpy as np
import pandas as pd
import pytest

from dw_event_study import daily_pnl, holding_pnl
from mm_pricing import KGI_CONVENTION, build_grid, compare_grid, year_fraction, price

TERMS = pd.DataFrame({
    "symbol": ["SYNC"], "cp": ["C"], "strike": [1100.0], "ratio": [40.0],
    "last_trade": pd.to_datetime(["2026-12-29"]), "expiry": pd.to_datetime(["2027-01-05"]),
})


def synthetic_daily(path, vol=0.26):
    dates = pd.bdate_range("2026-09-21", periods=len(path))
    T = year_fraction(dates, "2026-12-29", "weighted0.50")
    px = price(np.array(path, float), 1100.0, T, vol, 0.0, 0.0, 1.0) / 40.0
    return pd.DataFrame({"date": dates, "symbol": "SYNC", "underlying": path, "dw_price": px})


def test_hedged_pnl_splits_into_gamma_and_theta():
    daily = synthetic_daily([1050, 1052, 1030, 1035, 1036])
    pnl = daily_pnl(daily, TERMS).dropna(subset=["pnl_model"])
    assert pnl["vol"].iloc[0] == pytest.approx(0.26, abs=1e-6)
    resid = pnl["pnl_model"] - pnl["gamma_pnl"] - pnl["theta_pnl"]
    # third-order terms are left over on a 2% move
    assert resid.abs().max() < 0.1 * pnl["pnl_model"].abs().max()
    # model and quoted marks coincide when the close is the model price
    assert pnl["pnl_quoted"].to_numpy() == pytest.approx(pnl["pnl_model"].to_numpy())


def test_quiet_market_loses_theta_and_big_move_pays_gamma():
    quiet = daily_pnl(synthetic_daily([1050, 1050, 1050]), TERMS)
    assert quiet["pnl_model"].dropna().lt(0).all()
    shock = daily_pnl(synthetic_daily([1050, 1050 * 0.97]), TERMS)
    assert shock["pnl_model"].iloc[-1] > 0


def test_holding_pnl_charges_one_tick_on_entry():
    pnl = daily_pnl(synthetic_daily([1050, 1052, 1030]), TERMS)
    dates = pnl["date"].tolist()
    held = holding_pnl(pnl, dates[0], dates[-1])
    free = holding_pnl(pnl, dates[0], dates[-1], trade_cost=False)
    assert held["pnl_quoted"].iloc[0] == pytest.approx(free["pnl_quoted"].iloc[0] - 0.01)
    assert held["days"].iloc[0] == 2


def test_build_and_compare_grid():
    term = TERMS.set_index("symbol").loc["SYNC"]
    ours = build_grid(term, 0.26, [1040.0, 1050.0], ["2026-10-12"], KGI_CONVENTION)
    theirs = ours.assign(bid=ours["bid"] + [0.0, 0.01])
    _, summary = compare_grid(ours, theirs)
    assert summary.loc["SYNC", "exact"] == 0.5
    assert summary.loc["SYNC", "within_1"] == 1.0
