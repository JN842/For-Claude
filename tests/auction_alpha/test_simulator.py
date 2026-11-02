import numpy as np
import pandas as pd
import pytest

from auction_alpha.execution.scenarios import SCENARIOS
from auction_alpha.execution.simulator import INTENT_COLUMNS, ExecutionSimulator
from auction_alpha.features.engineering import LeakageError


def _intent(b, sym, date, **kw):
    f = b.features
    r = f[(f["symbol"] == sym) & (f["date"] == pd.Timestamp(date))].iloc[0]
    w = __import__("auction_alpha.rules.adapters", fromlist=["get_adapter"]).get_adapter(b.market).close_auction_window(
        pd.Timestamp(date).date(), bool(r["is_halfday"]))
    base = dict(trade_id=f"t|{sym}|{date}", strategy="TEST", market=b.market, symbol=sym, signal_date=pd.Timestamp(date),
                signal_ts=w.auction_start - pd.Timedelta(minutes=1), submit_ts=w.auction_start - pd.Timedelta(seconds=30),
                side=1, target_notional=1e6, ref_price=r["last_continuous_price"], entry_session="close_auction",
                entry_date=pd.Timestamp(date), hold_sessions=1, exit_session="open_auction", hedge=False, beta=1.0,
                vol_20=0.02, vol_shares_20=r["vol_shares_20"], spread_rel=0.002, signal=0.01)
    base.update(kw)
    return pd.DataFrame([base])[INTENT_COLUMNS]


def _pick(b, cond):
    f = b.features
    rows = f[cond(f)]
    return rows.iloc[len(rows) // 2]


def test_submit_before_signal_raises(bundles):
    b = bundles["SET"]
    r = _pick(b, lambda f: (f["close_method"] == "auction") & f["vol_shares_20"].notna())
    it = _intent(b, r["symbol"], r["date"])
    it["submit_ts"] = it["signal_ts"] - pd.Timedelta(seconds=1)
    with pytest.raises(LeakageError):
        ExecutionSimulator(b.md, SCENARIOS["base"]).run(it)


def test_order_in_random_window_rejected(bundles):
    b = bundles["SET"]
    r = _pick(b, lambda f: (f["close_method"] == "auction") & f["vol_shares_20"].notna())
    it = _intent(b, r["symbol"], r["date"])
    from auction_alpha.rules.adapters import get_adapter
    w = get_adapter("SET").close_auction_window(pd.Timestamp(r["date"]).date())
    it["signal_ts"] = w.random_start
    it["submit_ts"] = w.random_start + pd.Timedelta(seconds=10)
    res = ExecutionSimulator(b.md, SCENARIOS["base"]).run(it)
    assert res.fills.empty
    assert res.rejections["reason"].iloc[0] in ("submitted_in_random_window", "submitted_after_auction_match")


def test_participation_cap_and_lot_rounding(bundles):
    b = bundles["SET"]
    r = _pick(b, lambda f: (f["close_method"] == "auction") & (f["close_auction_volume"] > 10000) & f["vol_shares_20"].notna())
    it = _intent(b, r["symbol"], r["date"], target_notional=1e12)
    res = ExecutionSimulator(b.md, SCENARIOS["base"]).run(it)
    e = res.fills[res.fills["leg"] == "entry"].iloc[0]
    cap = r["close_auction_volume"] * SCENARIOS["base"].auction_participation
    assert e["side_qty"] <= cap + 1e-9
    assert e["side_qty"] % 100 == 0
    assert "partial_fill" in set(res.rejections["reason"])


def test_below_board_lot_rejected(bundles):
    b = bundles["SET"]
    r = _pick(b, lambda f: (f["close_method"] == "auction") & f["vol_shares_20"].notna())
    it = _intent(b, r["symbol"], r["date"], target_notional=r["last_continuous_price"] * 50)
    res = ExecutionSimulator(b.md, SCENARIOS["base"]).run(it)
    assert res.fills.empty and res.rejections["reason"].iloc[0] == "below_board_lot"


def test_short_requires_borrow(bundles, tiny_tables):
    b = bundles["SET"]
    br = tiny_tables["borrow"]
    br = br[(br["market"] == "SET") & ~br["shortable"]]
    f = b.features.merge(br[["symbol", "date"]], on=["symbol", "date"])
    f = f[(f["close_method"] == "auction") & f["vol_shares_20"].notna()]
    r = f.iloc[0]
    it = _intent(b, r["symbol"], r["date"], side=-1)
    res = ExecutionSimulator(b.md, SCENARIOS["base"]).run(it)
    assert res.fills.empty and res.rejections["reason"].iloc[0] == "not_shortable"


def test_hkex_non_cas_security_rejected(bundles):
    b = bundles["HKEX"]
    r = _pick(b, lambda f: (f["close_method"] == "reference_price") & f["last_continuous_price"].notna())
    it = _intent(b, r["symbol"], r["date"])
    res = ExecutionSimulator(b.md, SCENARIOS["base"]).run(it)
    assert res.fills.empty and res.rejections["reason"].iloc[0] == "not_cas_eligible"


def test_conservative_rejects_unverified_short_in_auction(bundles, tiny_tables):
    b = bundles["SET"]
    br = tiny_tables["borrow"]
    br = br[(br["market"] == "SET") & br["shortable"]]
    f = b.features.merge(br[["symbol", "date"]], on=["symbol", "date"])
    r = f[(f["close_method"] == "auction") & f["vol_shares_20"].notna()].iloc[0]
    it = _intent(b, r["symbol"], r["date"], side=-1)
    res = ExecutionSimulator(b.md, SCENARIOS["conservative"]).run(it)
    assert res.rejections["reason"].iloc[0] == "short_in_auction_rule_unverified"


def test_exit_delayed_through_suspension_and_forced_on_delisting(bundles, tiny_tables):
    b = bundles["SGX"]
    f = b.features.sort_values(["symbol", "date"])
    f["next_susp"] = f.groupby("symbol")["suspended"].shift(-1)
    r = f[(f["close_method"] == "auction") & (f["next_susp"] == True) & f["vol_shares_20"].notna()]
    if r.empty:
        pytest.skip("no auction day followed by suspension in fixture")
    r = r.iloc[0]
    it = _intent(b, r["symbol"], r["date"], target_notional=r["last_continuous_price"] * 200)
    res = ExecutionSimulator(b.md, SCENARIOS["optimistic"]).run(it)
    reasons = set(res.rejections["reason"])
    assert any(x.startswith("exit_delayed") or x.startswith("forced_exit") for x in reasons)
    ex = res.fills[res.fills["leg"] == "exit"]
    assert ex["side_qty"].sum() == -res.fills[res.fills["leg"] == "entry"]["side_qty"].sum()


def test_tac_order_needs_known_close(bundles):
    b = bundles["SGX"]
    r = _pick(b, lambda f: (f["close_method"] == "auction") & (f["tac_volume"] > 0) & f["vol_shares_20"].notna())
    good = _intent(b, r["symbol"], r["date"], entry_session="tac", signal_ts=r["close_auction_receipt_ts"],
                   submit_ts=r["close_auction_receipt_ts"] + pd.Timedelta(seconds=2), ref_price=r["close"])
    res = ExecutionSimulator(b.md, SCENARIOS["optimistic"]).run(good)
    e = res.fills[res.fills["leg"] == "entry"]
    if len(e):
        assert e["price"].iloc[0] == r["close"]  # TAC trades exactly at the close
        assert e["side_qty"].iloc[0] <= r["tac_volume"] * SCENARIOS["optimistic"].tac_participation
    early = _intent(b, r["symbol"], r["date"], entry_session="tac", signal_ts=r["close_auction_ts"] - pd.Timedelta(minutes=2),
                    submit_ts=r["close_auction_ts"] - pd.Timedelta(minutes=1), ref_price=r["close"])
    res2 = ExecutionSimulator(b.md, SCENARIOS["optimistic"]).run(early)
    assert res2.fills.empty and res2.rejections["reason"].iloc[0] == "tac_order_before_close_known"


def test_fees_charged_on_every_leg(bundles):
    b = bundles["HKEX"]
    r = _pick(b, lambda f: (f["close_method"] == "auction") & f["vol_shares_20"].notna())
    res = ExecutionSimulator(b.md, SCENARIOS["base"]).run(_intent(b, r["symbol"], r["date"], target_notional=2e5))
    assert (res.fills["fees"] > 0).all()
