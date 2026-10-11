import numpy as np
import pandas as pd
import pytest

from mm_pricing import (
    KGI_CONVENTION,
    fit_conventions,
    issuer_implied_vol,
    model_grid,
    round_to_tick,
    tick_size,
    year_fraction,
)

TERMS = pd.DataFrame({
    "symbol": ["SYNC", "SYNP"], "cp": ["C", "P"], "strike": [1100.0, 1000.0],
    "ratio": [10.0, 10.0],
    "last_trade": pd.to_datetime(["2026-12-29"] * 2),
    "expiry": pd.to_datetime(["2027-01-05"] * 2),
})
DATES = pd.to_datetime(["2026-10-12", "2026-10-14", "2026-10-15", "2026-10-19"])
VOLS = {"SYNC": 0.26, "SYNP": 0.36}


def synthetic_grid(**convention):
    cells = []
    for symbol in TERMS["symbol"]:
        base = pd.DataFrame([(symbol, F, d) for F in np.arange(1010, 1090, 0.5) for d in DATES],
                            columns=["symbol", "F", "date"]).assign(bid=0.0)
        term = TERMS.set_index("symbol").loc[symbol]
        grid = model_grid(base, term, VOLS[symbol], **convention)
        cells.append(grid.assign(bid=grid["model_bid"])[["symbol", "F", "date", "bid"]])
    return pd.concat(cells, ignore_index=True)


def test_tick_table():
    assert tick_size([0.5, 1.99, 2.0, 4.99, 5.0]).tolist() == [0.01, 0.01, 0.02, 0.02, 0.05]
    assert round_to_tick(np.array([1.234, 2.031, 1.996])).tolist() == [1.23, 2.04, 2.0]


def test_weighted_clock_counts_holidays_as_half_days():
    # Fri 2026-10-09 -> Mon 2026-10-12: 1 trading day + 2 weekend days at 0.5
    t = year_fraction([pd.Timestamp("2026-10-09")], "2026-10-12", "weighted0.5", holidays=[])
    assert t[0] * (252 + 113 * 0.5) == pytest.approx(2.0)
    t = year_fraction([pd.Timestamp("2026-10-09")], "2026-10-12", "calendar")
    assert t[0] * 365 == pytest.approx(3.0)


def test_fit_recovers_the_convention_that_built_the_grid():
    convention = {k: v for k, v in KGI_CONVENTION.items()}
    grid = synthetic_grid(**convention)
    result = fit_conventions(
        grid, TERMS, carries=(0.0, 0.03), rates=(0.0,), t_ends=("last_trade", "expiry"),
        roundings=("round",), spreads=(0.0,),
        clocks=(("weighted0.50", None), ("calendar", 365.0)))
    best = result.iloc[0]
    assert (best["carry"], best["t_end"], best["clock"]) == (0.0, "last_trade", "weighted0.50")
    assert best["min_match"] == 1.0
    for symbol, vol in VOLS.items():
        assert best["vols"][symbol] == pytest.approx(vol, abs=0.002)


def test_issuer_implied_vol_inverts_the_model():
    grid = synthetic_grid(**{k: v for k, v in KGI_CONVENTION.items()})
    term = TERMS.set_index("symbol").loc["SYNC"]
    cells = grid[grid["symbol"].eq("SYNC")].head(3)
    priced = model_grid(cells, term, 0.26, **KGI_CONVENTION)
    daily = priced.assign(underlying=priced["F"], dw_price=priced["theo"])
    result = issuer_implied_vol(daily, TERMS)
    assert result["iv_mm"].to_numpy() == pytest.approx(0.26, abs=1e-6)
