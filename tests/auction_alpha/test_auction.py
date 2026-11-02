import datetime as dt

import pandas as pd

from auction_alpha.execution.auction import AuctionPhase, AuctionStateMachine, uncross
from auction_alpha.rules.adapters import get_adapter


def _ts(d, hm, tz):
    return pd.Timestamp(f"{d} {hm}", tz=tz).tz_convert("UTC")


def test_hkex_state_machine_phases_and_deadlines():
    d = dt.date(2024, 3, 5)
    tz = "Asia/Hong_Kong"
    sm = AuctionStateMachine(get_adapter("HKEX"), d, realized_close=_ts(d, "16:09:00", tz))
    assert sm.phase_at(_ts(d, "15:59", tz)) == AuctionPhase.CONTINUOUS
    assert sm.phase_at(_ts(d, "16:03", tz)) == AuctionPhase.ORDER_ENTRY
    assert sm.phase_at(_ts(d, "16:07", tz)) == AuctionPhase.NO_CANCEL
    assert sm.phase_at(_ts(d, "16:08:30", tz)) == AuctionPhase.RANDOM_CLOSE
    assert sm.phase_at(_ts(d, "16:09:30", tz)) == AuctionPhase.CLOSED
    assert sm.can_cancel(_ts(d, "16:05", tz)) and not sm.can_cancel(_ts(d, "16:07", tz))
    ok, why = sm.can_submit_auction_order(_ts(d, "16:08:30", tz))
    assert not ok and why == "submitted_in_random_window"
    ok, _ = sm.can_submit_auction_order(_ts(d, "16:08:30", tz), deadline="realized")
    assert ok  # only the optimistic, leaking policy accepts it
    ok, why = sm.can_submit_auction_order(_ts(d, "16:09:30", tz), deadline="realized")
    assert not ok and why == "submitted_after_auction_match"


def test_sgx_tac_phase():
    d = dt.date(2024, 3, 5)
    tz = "Asia/Singapore"
    sm = AuctionStateMachine(get_adapter("SGX"), d, realized_close=_ts(d, "17:05:30", tz))
    assert sm.phase_at(_ts(d, "17:10", tz)) == AuctionPhase.TAC
    assert sm.can_submit_tac(_ts(d, "17:10", tz))[0]
    assert not sm.can_submit_tac(_ts(d, "17:20", tz))[0]


def test_random_close_drawn_inside_window_when_not_observed():
    d = dt.date(2024, 3, 5)
    sm = AuctionStateMachine(get_adapter("SET"), d, seed=1)
    assert sm.window.random_start <= sm.realized_close <= sm.window.random_end


def test_uncross_max_volume():
    bids = [(10.2, 100), (10.1, 200), (None, 50)]
    asks = [(10.0, 150), (10.1, 100), (10.3, 300)]
    p, v, imb = uncross(bids, asks, reference_price=10.0)
    assert p == 10.1 and v == 250


def test_uncross_min_imbalance_then_pressure():
    # executable volume 100 at both 10.0 and 10.1; buy surplus on both -> market pressure picks the higher price
    bids = [(10.1, 300)]
    asks = [(10.0, 100)]
    p, v, imb = uncross(bids, asks, reference_price=9.0)
    assert v == 100 and p == 10.1 and imb > 0


def test_uncross_reference_tiebreak_and_no_cross():
    bids = [(10.0, 100)]
    asks = [(10.0, 100)]
    assert uncross(bids, asks, 10.0)[0] == 10.0
    p, v, _ = uncross([(9.0, 100)], [(10.0, 100)], 9.5)
    assert p is None and v == 0
