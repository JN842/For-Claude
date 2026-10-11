import numpy as np
import pandas as pd
import pytest

from issuer_sites import _dated, _long, sweep_iv
from mm_pricing import KGI_CONVENTION, build_grid


def test_dated_rolls_year_and_reads_weekday_labels():
    dates = _dated(["Mon  28 Dec", "Tue  29 Dec", "Mon  4 Jan"], "2026-12-25")
    assert list(dates.values()) == [pd.Timestamp("2026-12-28"), pd.Timestamp("2026-12-29"),
                                    pd.Timestamp("2027-01-04")]


def test_long_format_from_wide_table():
    wide = pd.DataFrame({"S50Z26": ["1,062.50", "1062.00"], "12 Oct": [0.18, 0.18],
                         "14 Oct": [0.18, 0.17]})
    long = _long(wide, "SET5019C2612A", "S50Z26", "2026-10-09")
    assert len(long) == 4
    row = long[(long["F"] == 1062.0) & (long["date"] == pd.Timestamp("2026-10-14"))]
    assert row["bid"].item() == 0.17


def test_sweep_iv_ranks_cheaper_vol_first():
    terms = pd.DataFrame({
        "symbol": ["CHEAP", "DEAR"], "cp": ["C", "C"], "strike": [1150.0, 1150.0],
        "ratio": [20.0, 20.0], "last_trade": pd.to_datetime(["2026-12-29"] * 2)})
    t = terms.set_index("symbol")
    grids = pd.concat([build_grid(t.loc["CHEAP"], 0.18, [1048.5], ["2026-10-12"], KGI_CONVENTION),
                       build_grid(t.loc["DEAR"], 0.26, [1048.5], ["2026-10-12"], KGI_CONVENTION)])
    listing = pd.DataFrame({"symbol": ["DEAR", "CHEAP"], "issuer": ["X", "Y"],
                            "price": [np.nan, np.nan], "implied_vol": [0.26, 0.18]})
    out = sweep_iv(listing, terms, grids, 1048.5, "2026-10-12")
    assert out["symbol"].tolist() == ["CHEAP", "DEAR"]
    assert (out["iv_ask"] > out["iv_bid"]).all()
    assert out.loc[0, "iv_bid"] == pytest.approx(0.18, abs=0.02)


def test_macquarie_ric():
    from issuer_sites import macq_ric
    assert macq_ric("SPX28C2612A") == "SPX28C612A.BK"
    assert macq_ric("NIKKEI28P2612D") == "NIKKEI28P612D.BK"


def test_fit_scaled_recovers_vol_and_fx_scale():
    from mm_pricing import fit_scaled, price, round_to_tick
    F = np.arange(7500, 8200, 5.0)
    T = np.full(F.shape, 0.19)
    scale = 33.9 / 10000
    bid = round_to_tick(scale * price(F, 8000.0, T, 0.24, 0.0, 0.0, 1.0), "floor")
    cells = pd.DataFrame({"F": F, "bid": bid})
    fit = fit_scaled(cells, 8000.0, "C", T, "floor", vols=np.arange(0.15, 0.35, 0.0005))
    assert fit["vol"] == pytest.approx(0.24, abs=0.01)
    assert fit["scale"] == pytest.approx(scale, rel=0.05)
    assert fit["within_1"] == 1.0
