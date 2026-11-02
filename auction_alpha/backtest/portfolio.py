"""Cash-flow-consistent portfolio accounting.

Each trade is a position with its own cash ledger::

    trade_cash   = sum(-qty * price * multiplier) - fees + dividends - borrow fees
    trade_value  = qty * multiplier * mark_price
    trade_pnl(t) = trade_cash(t) + trade_value(t)

Portfolio NAV(t) = capital + sum(trade_pnl(t)) - cumulative funding cost.
Daily returns are P&L divided by the *fixed* capital base (no compounding of
position size), so overlapping trades are aggregated correctly at the
portfolio level and Sharpe ratios are computed from daily portfolio returns,
never from per-trade returns.

Marks: official close, carried forward across suspensions (past data only);
futures marked at their daily close.  Splits scale quantity on the ex-date;
cash dividends are credited to longs and debited to shorts on the ex-date.
Borrow fees accrue per calendar day on short market value.  Funding is
charged on long market value in excess of capital (``funding_spread_bps``
over the cash rate - all returns are excess returns over cash).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Accounting:
    daily: pd.DataFrame
    trades: pd.DataFrame


def account(fills: pd.DataFrame, bars: pd.DataFrame, *, capital: float,
            corporate_actions: pd.DataFrame | None = None, futures: pd.DataFrame | None = None,
            market: str | None = None, funding_spread_bps: float = 100.0,
            calendar: pd.DatetimeIndex | None = None) -> Accounting:
    cols = ["date", "pnl", "pnl_gross", "pnl_long", "pnl_short", "pnl_hedge", "fees", "slippage",
            "borrow", "funding", "dividends", "long_mv", "short_mv", "hedge_notional", "gross_exposure",
            "turnover", "n_positions", "nav", "ret"]
    if fills is None or fills.empty:
        return Accounting(pd.DataFrame(columns=cols), pd.DataFrame())
    b = bars if market is None else bars[bars["market"] == market]
    b = b.assign(date=pd.to_datetime(b["date"]))
    close = b.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index().ffill()
    cal = calendar if calendar is not None else close.index
    f = fills.copy()
    f["date"] = pd.to_datetime(f["date"])
    start, end = f["date"].min(), f["date"].max()
    dates = cal[(cal >= start) & (cal <= end)]
    sym_col = {s: k for k, s in enumerate(close.columns)}
    cvals = close.reindex(dates).to_numpy()
    fut_close = None
    if futures is not None and not futures.empty:
        fu = futures if market is None else futures[futures["market"] == market]
        fut_close = fu.assign(date=pd.to_datetime(fu["date"])).set_index("date")["close"].reindex(dates).ffill()
    ca_by_date: dict = {}
    if corporate_actions is not None and not corporate_actions.empty:
        ca = corporate_actions if market is None else corporate_actions[corporate_actions["market"] == market]
        for r in ca.itertuples(index=False):
            ca_by_date.setdefault(pd.Timestamp(r.ex_date), []).append(r)
    if "multiplier" not in f:
        f["multiplier"] = 1.0
    f["multiplier"] = f["multiplier"].fillna(1.0)
    fills_by_date = {d: g for d, g in f.sort_values(["date", "ts"], na_position="last").groupby("date")}

    pos: dict = {}       # key -> dict(symbol, qty, mult, book, fee_bps, inst)
    tr: dict = {}        # trade_id -> accumulators
    rows = []
    prev_d = None
    prev_total = 0.0
    cum_funding = 0.0
    trade_val_prev: dict = {}
    active: set = set()
    for k, d in enumerate(dates):
        days = (d - prev_d).days if prev_d is not None else 0
        div_t = borrow_t = 0.0
        # corporate actions on positions held into the ex-date
        for r in ca_by_date.get(d, []):
            for key, p in pos.items():
                if p["inst"] != "stock" or p["symbol"] != r.symbol or p["qty"] == 0:
                    continue
                if r.action_type == "split" and np.isfinite(r.ratio):
                    p["qty"] *= r.ratio
                elif r.action_type == "cash_dividend" and np.isfinite(r.cash_amount):
                    amt = p["qty"] * r.cash_amount
                    tr[p["tid"]]["cash"] += amt
                    tr[p["tid"]]["dividends"] += amt
                    div_t += amt
        # borrow accrual on shorts held overnight (previous marks)
        if days > 0:
            for key, p in pos.items():
                if p["inst"] == "stock" and p["qty"] < 0 and p["fee_bps"] > 0:
                    mv = -p["qty"] * p["mark"]
                    cst = mv * p["fee_bps"] / 1e4 * days / 365.0
                    tr[p["tid"]]["cash"] -= cst
                    tr[p["tid"]]["borrow"] += cst
                    borrow_t += cst
        # fills
        fees_t = slip_t = turn_t = 0.0
        g = fills_by_date.get(d)
        if g is not None:
            for r in g.itertuples(index=False):
                key = (r.trade_id, r.instrument)
                t = tr.setdefault(r.trade_id, dict(cash=0.0, fees=0.0, slip=0.0, borrow=0.0, dividends=0.0,
                                                   entry_notional=0.0, side=int(r.trade_side), symbol=r.symbol if r.instrument == "stock" else None,
                                                   first=d, last=d, hedge_pnl_cash=0.0))
                mult = float(r.multiplier)
                flow = -r.side_qty * r.price * mult - r.fees
                if r.instrument == "future":
                    t["hedge_pnl_cash"] += flow
                else:
                    t["cash"] += flow
                    t["slip"] += r.slip_cost
                    if r.leg == "entry":
                        t["entry_notional"] += abs(r.side_qty * r.price * mult)
                    turn_t += abs(r.side_qty * r.price * mult)
                t["fees"] += r.fees
                t["last"] = d
                fees_t += r.fees
                slip_t += r.slip_cost
                p = pos.setdefault(key, dict(tid=r.trade_id, symbol=r.symbol, qty=0.0, mult=mult, inst=r.instrument,
                                             fee_bps=float(r.borrow_fee_bps), mark=r.price))
                p["qty"] += r.side_qty
                if abs(p["qty"]) < 1e-9:
                    p["qty"] = 0.0
        # marks
        long_mv = short_mv = hedge_n = 0.0
        for key, p in pos.items():
            if p["inst"] == "stock":
                j = sym_col.get(p["symbol"])
                if j is not None:
                    m = cvals[k, j]
                elif fut_close is not None and str(p["symbol"]).endswith("_FUT"):
                    m = float(fut_close.iloc[k])
                else:
                    m = np.nan
                if np.isfinite(m):
                    p["mark"] = m
                v = p["qty"] * p["mark"] * p["mult"]
                if v > 0:
                    long_mv += v
                else:
                    short_mv -= v
            else:
                if fut_close is not None and np.isfinite(fut_close.iloc[k]):
                    p["mark"] = float(fut_close.iloc[k])
                hedge_n += abs(p["qty"] * p["mark"] * p["mult"])
        funding_t = max(long_mv - capital, 0.0) * funding_spread_bps / 1e4 * days / 365.0
        cum_funding += funding_t
        # per-trade P&L and attribution (only trades still open or touched today)
        pnl_l = pnl_s = pnl_h = 0.0
        tv: dict = {}
        for key, p in pos.items():
            tv[key] = p["qty"] * p["mark"] * p["mult"]
        if g is not None:
            active.update(g["trade_id"].unique())
        for tid in list(active):
            t = tr[tid]
            t["pnl"] = t["cash"] + tv.get((tid, "stock"), 0.0)
            t["hedge_pnl"] = t["hedge_pnl_cash"] + tv.get((tid, "future"), 0.0)
            prev = trade_val_prev.get(tid, (0.0, 0.0))
            dp, dh = t["pnl"] - prev[0], t["hedge_pnl"] - prev[1]
            if t["side"] > 0:
                pnl_l += dp
            else:
                pnl_s += dp
            pnl_h += dh
            trade_val_prev[tid] = (t["pnl"], t["hedge_pnl"])
        total = prev_total + pnl_l + pnl_s + pnl_h
        pnl = total - prev_total - funding_t
        prev_total = total
        n_open = sum(1 for p in pos.values() if p["inst"] == "stock" and p["qty"] != 0)
        rows.append(dict(date=d, pnl=pnl, pnl_long=pnl_l, pnl_short=pnl_s, pnl_hedge=pnl_h, fees=fees_t,
                         slippage=slip_t, borrow=borrow_t, funding=funding_t, dividends=div_t, long_mv=long_mv,
                         short_mv=short_mv, hedge_notional=hedge_n, gross_exposure=long_mv + short_mv,
                         turnover=turn_t, n_positions=n_open))
        # drop closed positions (their trade cash stays in tr)
        for key in [kk for kk, p in pos.items() if p["qty"] == 0]:
            del pos[key]
        still = {p["tid"] for p in pos.values()}
        active.intersection_update(still)
        prev_d = d
    daily = pd.DataFrame(rows)
    daily["pnl_gross"] = daily["pnl"] + daily["fees"] + daily["slippage"] + daily["borrow"] + daily["funding"]
    daily["nav"] = capital + daily["pnl"].cumsum()
    daily["ret"] = daily["pnl"] / capital
    pos_idx = {d: i for i, d in enumerate(dates)}
    trades = pd.DataFrame([
        dict(trade_id=tid, symbol=t["symbol"], side=t["side"], entry_date=t["first"], exit_date=t["last"],
             holding_sessions=pos_idx.get(t["last"], 0) - pos_idx.get(t["first"], 0),
             entry_notional=t["entry_notional"], pnl_net=t["pnl"] + t["hedge_pnl"], pnl_stock=t["pnl"],
             pnl_hedge=t["hedge_pnl"], fees=t["fees"], slippage=t["slip"], borrow=t["borrow"], dividends=t["dividends"],
             pnl_gross=t["pnl"] + t["hedge_pnl"] + t["fees"] + t["slip"] + t["borrow"])
        for tid, t in tr.items()
    ])
    if not trades.empty:
        trades["ret_bps"] = np.where(trades["entry_notional"] > 0, trades["pnl_net"] / trades["entry_notional"] * 1e4, np.nan)
        trades["ret_gross_bps"] = np.where(trades["entry_notional"] > 0, trades["pnl_gross"] / trades["entry_notional"] * 1e4, np.nan)
    return Accounting(daily[cols], trades)
