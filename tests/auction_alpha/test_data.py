import numpy as np
import pandas as pd
import pytest

from auction_alpha.data.availability import availability_matrix, strategy_support
from auction_alpha.data.ingest import derive_last_continuous, map_table
from auction_alpha.data.schemas import ALL_SCHEMAS, data_dictionary_markdown
from auction_alpha.data.store import DataStore
from auction_alpha.data.validation import validate_dataset
from auction_alpha.strategies import STRATEGIES


def test_synthetic_matches_schemas_and_validates(tiny_tables):
    for name, df in tiny_tables.items():
        missing = set(ALL_SCHEMAS[name].column_names) - set(df.columns)
        assert not missing, (name, missing)
    rep = validate_dataset("t", tiny_tables)
    assert rep.ok(), rep.to_frame()


def test_synthetic_covers_edge_cases(tiny_tables):
    d = tiny_tables["daily_bars"]
    assert d["suspended"].any()
    assert (d["close_method"] == "last_trade").any()
    assert (d.loc[d["market"] == "HKEX", "close_method"] == "reference_price").any()
    assert d["is_halfday"].any()
    assert (~d["suspended"] & d["last_continuous_price"].isna()).any()
    assert ((d["market"] == "SGX") & (d["tac_volume"] == 0)).any()
    ca = tiny_tables["corporate_actions"]
    assert set(ca["action_type"]) == {"split", "cash_dividend"}
    b = tiny_tables["borrow"]
    assert (~b["shortable"]).any() and b["shortable"].any()
    ind = tiny_tables["auction_indicative"]
    j = ind.merge(d[["market", "symbol", "date", "close_auction_ts"]], on=["market", "symbol", "date"])
    assert (j["snapshot_ts"] < j["close_auction_ts"]).all()


def test_validation_catches_injected_errors(tiny_tables):
    bad = {k: v.copy() for k, v in tiny_tables.items()}
    d = bad["daily_bars"]
    d = pd.concat([d, d.iloc[:3]], ignore_index=True)                       # duplicate keys
    i = d.index[d["close_method"] == "auction"][0]
    d.loc[i, "close_auction_receipt_ts"] = d.loc[i, "close_auction_ts"] - pd.Timedelta(seconds=5)
    j = d.index[~d["suspended"]][5]
    d.loc[j, "close"] = -1.0
    bad["daily_bars"] = d
    ai = bad["auction_indicative"]
    ai.loc[ai.index[0], "receipt_ts"] = ai.loc[ai.index[0], "snapshot_ts"] - pd.Timedelta(seconds=1)
    rep = validate_dataset("bad", bad)
    checks = {i.check for i in rep.errors}
    assert "duplicate_primary_key" in checks
    assert "auction_receipt_before_event" in checks
    assert "non_positive:close" in checks
    assert "receipt_before_snapshot" in checks


def test_validation_rejects_naive_timestamps(tiny_tables):
    bad = dict(tiny_tables)
    d = tiny_tables["daily_bars"].copy()
    d["receipt_ts"] = d["receipt_ts"].dt.tz_localize(None)
    bad["daily_bars"] = d
    assert any(i.check == "naive_timestamp:receipt_ts" for i in validate_dataset("x", bad).errors)


def test_store_roundtrip_version_and_duckdb(tmp_path, tiny_tables):
    st = DataStore(tmp_path)
    v1 = st.write_dataset("synthetic_x", tiny_tables, is_synthetic=True, provenance={"seed": 1})
    v2 = st.write_dataset("synthetic_x", tiny_tables, is_synthetic=True, provenance={"seed": 1})
    assert v1 == v2  # deterministic data version
    back = st.read("synthetic_x", "daily_bars", markets=["SET"])
    assert set(back["market"]) == {"SET"}
    n = st.query("synthetic_x", "SELECT count(*) AS n FROM daily_bars WHERE market='SGX'")["n"].iloc[0]
    assert n == (tiny_tables["daily_bars"]["market"] == "SGX").sum()
    assert st.manifest("synthetic_x")["is_synthetic"] is True


def test_availability_and_support(tmp_path, tiny_tables):
    st = DataStore(tmp_path)
    t = dict(tiny_tables)
    t["auction_indicative"] = t["auction_indicative"].iloc[:0]
    st.write_dataset("synthetic_noind", t, is_synthetic=True, provenance={})
    av = availability_matrix(st)
    assert set(av["status"]) <= {"synthetic_only", "missing", "not_applicable"}
    sup = strategy_support(av, STRATEGIES)
    s04 = sup[sup["strategy"] == "S04"]
    assert (s04["support"] == "unsupported").all()
    assert (sup[(sup["strategy"] == "S07") & (sup["market"] != "SGX")]["support"] == "not_applicable").all()
    assert (sup[(sup["strategy"] == "S01")]["support"] == "synthetic_only").all()


def test_ingest_mapping_assigns_conservative_receipt():
    raw = pd.DataFrame({"Sym": ["AAA"], "Dt": ["2024-01-02"], "C": [10.0], "LCP": [9.9], "AUC": [10.0],
                        "AUCV": [1000.0], "V": [5000.0], "T": [50000.0]})
    spec = {"timezone": "Asia/Bangkok", "constants": {"market": "SET"}, "receipt_ts_rule": "eod+18:00",
            "columns": {"symbol": "Sym", "date": "Dt", "close": "C", "last_continuous_price": "LCP",
                        "close_auction_price": "AUC", "close_auction_volume": "AUCV", "volume": "V", "turnover": "T"}}
    out = map_table(raw, "daily_bars", spec)
    assert out["receipt_ts"].iloc[0] == pd.Timestamp("2024-01-02 11:00", tz="UTC")
    assert out["close_method"].iloc[0] == "auction"


def test_derive_last_continuous_excludes_auction(tiny_tables):
    tr = tiny_tables["intraday_trades"]
    d = tiny_tables["daily_bars"]
    from auction_alpha.rules.adapters import get_adapter
    key = tr[["market", "symbol", "date"]].drop_duplicates()
    starts = []
    for r in key.itertuples(index=False):
        w = get_adapter(r.market).close_auction_window(pd.Timestamp(r.date).date())
        starts.append(((r.market, r.symbol, r.date), w.auction_start))
    s = pd.Series({k: v for k, v in starts})
    s.index = pd.MultiIndex.from_tuples(s.index, names=["market", "symbol", "date"])
    lc = derive_last_continuous(tr, s)
    j = lc.merge(d[["market", "symbol", "date", "last_continuous_price"]], on=["market", "symbol", "date"], suffixes=("", "_bar"))
    assert np.allclose(j["last_continuous_price"], j["last_continuous_price_bar"], rtol=1e-3)
    m = lc.merge(s.rename("st").reset_index().assign(date=lambda x: pd.to_datetime(x["date"])), on=["market", "symbol", "date"])
    assert len(m) == len(lc) and (m["last_continuous_ts"] < m["st"]).all()


def test_data_dictionary_mentions_every_table():
    md = data_dictionary_markdown()
    for name in ALL_SCHEMAS:
        assert f"`{name}`" in md
