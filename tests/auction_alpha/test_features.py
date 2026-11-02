import numpy as np
import pandas as pd
import pytest

from auction_alpha.features.engineering import FeatureView, LeakageError, build_features


def test_availability_never_before_receipt(bundles):
    f = bundles["SET"].features
    assert (f["gap__avail"].dropna() >= f.loc[f["gap__avail"].notna(), "close_auction_ts"]).all()
    for c in ("vol_20", "adv_20", "beta_60", "ret_cc"):
        assert (f[f"{c}__avail"] == f["receipt_ts"]).all()
    pc = f.dropna(subset=["preclose_ret__avail"])
    assert (pc["preclose_ret__avail"] > pc["last_continuous_ts"]).all()


def test_gap_definition(bundles):
    f = bundles["HKEX"].features
    a = f[f["close_method"] == "auction"].dropna(subset=["gap"])
    assert np.allclose(a["gap"], np.log(a["close_auction_price"] / a["last_continuous_price"]))
    assert f.loc[f["close_method"] != "auction", "gap"].isna().all()


def test_feature_view_blocks_forward_labels(bundles):
    fv = FeatureView(bundles["SET"].features)
    with pytest.raises(LeakageError):
        fv["fwd_c1"]
    with pytest.raises(LeakageError):
        fv[["gap", "fwd_o1"]]
    assert not any(c.startswith("fwd_") for c in fv.columns)
    assert FeatureView(bundles["SET"].features, diagnostic=True)["fwd_c1"] is not None


def test_missing_lcp_is_not_backfilled(bundles):
    f = bundles["SET"].features
    miss = f["last_continuous_price"].isna() & ~f["suspended"]
    assert miss.any()
    assert f.loc[miss, "gap"].isna().all() and f.loc[miss, "preclose_ret"].isna().all()


def test_split_adjusted_returns_are_continuous(tiny_tables):
    ca = tiny_tables["corporate_actions"]
    sp = ca[ca["action_type"] == "split"].iloc[0]
    f = build_features(tiny_tables, sp["market"])
    row = f[(f["symbol"] == sp["symbol"]) & (f["date"] == pd.Timestamp(sp["ex_date"]))]
    if row.empty or row["suspended"].iloc[0]:
        pytest.skip("split landed on a suspended/missing day")
    assert abs(row["ret_cc"].iloc[0]) < 0.25  # a raw 2:1 or 5:1 split would show -50% / -80%


def test_unannounced_corporate_action_not_used(tiny_tables):
    t = {k: v.copy() for k, v in tiny_tables.items()}
    ca = t["corporate_actions"]
    i = ca.index[ca["action_type"] == "split"][0]
    ca.loc[i, "announce_ts"] = pd.Timestamp(ca.loc[i, "ex_date"]).tz_localize("UTC") + pd.Timedelta(days=3)
    f = build_features(t, ca.loc[i, "market"])
    row = f[(f["symbol"] == ca.loc[i, "symbol"]) & (f["date"] == pd.Timestamp(ca.loc[i, "ex_date"]))]
    if row.empty:
        pytest.skip("ex-date row missing")
    assert bool(row["ca_unannounced"].iloc[0])
    assert row["ret_cc"].isna().all()
