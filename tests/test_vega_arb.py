import numpy as np
import pandas as pd
import pytest

from vega_arb import (
    black76,
    dw_implied_vol,
    gap_frequency,
    implied_vol,
    iv_beta,
    load_events,
    match_pairs,
    size_pair,
)

F, T, R = 900.0, 0.25, 0.02


def test_put_call_parity():
    call = black76(F, 880, T, 0.2, R, "C")
    put = black76(F, 880, T, 0.2, R, "P")
    assert call["price"] - put["price"] == pytest.approx(np.exp(-R * T) * (F - 880))
    assert call["delta"] - put["delta"] == pytest.approx(np.exp(-R * T))


def test_greeks_match_finite_differences():
    base = black76(F, 920, T, 0.22, R, "P")
    h = 0.01
    up = black76(F + h, 920, T, 0.22, R, "P")["price"]
    down = black76(F - h, 920, T, 0.22, R, "P")["price"]
    assert base["delta"] == pytest.approx((up - down) / (2 * h), rel=1e-5)
    assert base["gamma"] == pytest.approx((up - 2 * base["price"] + down) / h**2, rel=1e-3)
    vega_fd = (black76(F, 920, T, 0.2201, R, "P")["price"]
               - black76(F, 920, T, 0.2199, R, "P")["price"]) / 0.0002
    assert base["vega"] == pytest.approx(vega_fd, rel=1e-5)
    theta_fd = (black76(F, 920, T - 1e-4, 0.22, R, "P")["price"] - base["price"]) / 1e-4
    assert base["theta"] == pytest.approx(theta_fd, rel=1e-3)


@pytest.mark.parametrize("cp,K,vol", [("C", 900, 0.18), ("P", 850, 0.30), ("C", 1000, 0.12)])
def test_implied_vol_round_trip(cp, K, vol):
    price = black76(F, K, T, vol, R, cp)["price"]
    assert implied_vol(price, F, K, T, R, cp) == pytest.approx(vol, abs=1e-6)


def test_implied_vol_below_intrinsic_is_nan():
    assert np.isnan(implied_vol(10.0, F, 850, T, R, "C"))


def test_dw_iv_uses_ratio_and_tick_floor():
    point_price = black76(F, 900, T, 0.25, R, "C")["price"]
    ratio = 50.0
    assert dw_implied_vol(point_price / ratio, ratio, F, 900, T, R, "C") == pytest.approx(0.25, abs=1e-6)
    assert np.isnan(dw_implied_vol(0.25, ratio, F, 900, T, R, "C"))


def test_size_pair_is_vega_and_delta_neutral():
    ratio, M = 40.0, 200
    dw = black76(F, 900, 0.30, 0.24, R, "C")
    opt = black76(F, 900, 0.25, 0.30, R, "C")
    size = size_pair(dw, ratio, opt, n_opt=5, multiplier=M)
    vega = size["n_dw"] * dw["vega"] / ratio - 5 * opt["vega"] * M
    delta = size["n_dw"] * dw["delta"] / ratio - 5 * opt["delta"] * M + size["n_fut"] * M
    assert vega == pytest.approx(0, abs=1e-6)
    assert delta == pytest.approx(0, abs=1e-6)
    assert size["n_dw"] > 0


def test_match_pairs_same_cp_closest_moneyness_within_limits():
    date = pd.Timestamp("2026-10-12")
    dw = pd.DataFrame({
        "date": [date, date], "symbol": ["S5001C2612A", "S5001P2612A"],
        "cp": ["C", "P"], "strike": [900.0, 860.0],
        "expiry": pd.to_datetime(["2026-12-28", "2026-12-28"]), "F": [F, F],
    })
    options = pd.DataFrame({
        "date": [date] * 4,
        "series": ["S50Z26C900", "S50Z26C925", "S50Z26P900", "S50H27P860"],
        "cp": ["C", "C", "P", "P"], "strike": [900.0, 925.0, 900.0, 860.0],
        "expiry": pd.to_datetime(["2026-12-29"] * 3 + ["2027-03-30"]), "F": [F] * 4,
    })
    pairs = match_pairs(dw, options)
    # Call matches the closest strike; the put's only strike match is a
    # quarter away and the same-expiry put is >3% away, so it is dropped.
    assert pairs[["symbol", "series"]].values.tolist() == [["S5001C2612A", "S50Z26C900"]]


def test_iv_beta_recovers_stickiness():
    rng = np.random.default_rng(0)
    opt = 0.20 + np.cumsum(rng.normal(0, 0.01, 250))
    dw = 0.30 + 0.1 * (opt - 0.20)
    result = iv_beta(pd.Series(dw), pd.Series(opt))
    assert result["beta"] == pytest.approx(0.1, abs=1e-9)
    assert result["n"] == 249


def test_gap_frequency_counts_thresholds():
    pairs = pd.DataFrame({"iv_opt": [0.30, 0.25, 0.20, np.nan], "iv_dw": [0.22, 0.22, 0.22, 0.2]})
    result = gap_frequency(pairs)
    assert result["days"].tolist() == [2, 1, 0]


def test_event_catalog_loads():
    events = load_events()
    assert {"scheduled", "shock"} <= set(events["type"])
    assert not events["verified"].any()
    assert (events["end_date"] >= events["date"]).all()
    assert len(load_events(kind="shock")) > 0
