import numpy as np
import pandas as pd
import pytest

from auction_alpha.backtest.portfolio import account


def _bars(closes, sym="AAA"):
    dates = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"market": "SET", "symbol": sym, "date": dates, "close": closes}), dates


def _fill(tid, d, q, px, fee=0.0, leg="entry", side=1, fee_bps=0.0, sym="AAA"):
    return dict(trade_id=tid, leg=leg, instrument="stock", market="SET", symbol=sym, date=d, ts=pd.NaT,
                side_qty=q, price=px, base_price=px, fees=fee, slip_cost=0.0, session="x", trade_side=side,
                borrow_fee_bps=fee_bps, multiplier=1.0)


def test_long_round_trip_cash_consistent():
    bars, d = _bars([10.0, 11.0, 12.0])
    f = pd.DataFrame([_fill("a", d[0], 100, 10.0, fee=1.0), _fill("a", d[2], -100, 12.0, fee=1.0, leg="exit")])
    acc = account(f, bars, capital=10_000, funding_spread_bps=0)
    assert acc.daily["pnl"].sum() == pytest.approx(200 - 2)
    assert list(acc.daily["pnl"].round(6)) == [-1.0, 100.0, 99.0]
    assert acc.daily["ret"].sum() == pytest.approx(198 / 10_000)
    t = acc.trades.iloc[0]
    assert t["pnl_net"] == pytest.approx(198) and t["pnl_gross"] == pytest.approx(200)
    assert t["ret_bps"] == pytest.approx(198 / 1000 * 1e4)


def test_short_borrow_fee_accrues_per_calendar_day():
    bars, d = _bars([10.0, 10.0, 10.0, 10.0])
    f = pd.DataFrame([_fill("s", d[0], -100, 10.0, side=-1, fee_bps=365.0), _fill("s", d[3], 100, 10.0, leg="exit", side=-1)])
    acc = account(f, bars, capital=10_000, funding_spread_bps=0)
    # 1000 notional * 3.65% / 365 per day * 3 calendar days
    assert acc.daily["borrow"].sum() == pytest.approx(1000 * 0.0365 / 365 * 3)
    assert acc.daily["pnl_short"].sum() == pytest.approx(-acc.daily["borrow"].sum())


def test_dividend_and_split_on_held_position():
    bars, d = _bars([10.0, 9.5, 4.75, 4.75])
    ca = pd.DataFrame([dict(market="SET", symbol="AAA", action_type="cash_dividend", announce_ts=pd.Timestamp("2023-12-01", tz="UTC"),
                            ex_date=d[1], ratio=np.nan, cash_amount=0.5),
                       dict(market="SET", symbol="AAA", action_type="split", announce_ts=pd.Timestamp("2023-12-01", tz="UTC"),
                            ex_date=d[2], ratio=2.0, cash_amount=np.nan)])
    f = pd.DataFrame([_fill("a", d[0], 100, 10.0), _fill("a", d[3], -200, 4.75, leg="exit")])
    acc = account(f, bars, capital=10_000, corporate_actions=ca, funding_spread_bps=0)
    # price fell by exactly the dividend and then halved on the split: total P&L = 0
    assert acc.daily["pnl"].sum() == pytest.approx(0.0, abs=1e-9)
    assert acc.daily["dividends"].sum() == pytest.approx(50.0)


def test_overlapping_trades_aggregate_at_portfolio_level():
    bars, d = _bars([10.0, 11.0, 12.0])
    f = pd.DataFrame([_fill("a", d[0], 100, 10.0), _fill("a", d[2], -100, 12.0, leg="exit"),
                      _fill("b", d[1], 100, 11.0), _fill("b", d[2], -100, 12.0, leg="exit")])
    acc = account(f, bars, capital=10_000, funding_spread_bps=0)
    assert acc.daily["pnl"].sum() == pytest.approx(300)
    assert len(acc.daily) == 3  # one return per day, not per trade


def test_funding_charged_only_above_capital():
    bars, d = _bars([10.0, 10.0, 10.0])
    f = pd.DataFrame([_fill("a", d[0], 2000, 10.0), _fill("a", d[2], -2000, 10.0, leg="exit")])
    acc = account(f, bars, capital=10_000, funding_spread_bps=365.0)
    # long MV 20k vs 10k capital -> 10k funded at 3.65%/yr for d0->d1 and d1->d2 (1 calendar day each)
    assert acc.daily["funding"].sum() == pytest.approx(10_000 * 0.0365 / 365 * 2)
