"""Performance metrics from daily portfolio P&L (fixed capital base).

Sharpe/Sortino/annualised figures are computed only from the *daily
portfolio return series* including zero-P&L days between trades - never from
per-trade returns.  Trade-level statistics (win rate, profit factor, bps per
trade) are reported separately and are not annualised.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .inference import bootstrap_ci, newey_west_tstat


def _sharpe(r, ppy):
    r = np.asarray(r, float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ppy)) if sd > 0 else float("nan")


def _sortino(r, ppy):
    r = np.asarray(r, float)
    dn = np.sqrt(np.mean(np.minimum(r, 0) ** 2))
    return float(r.mean() / dn * math.sqrt(ppy)) if dn > 0 else float("nan")


def max_drawdown(cum_pnl_frac: np.ndarray) -> float:
    if len(cum_pnl_frac) == 0:
        return float("nan")
    peak = np.maximum.accumulate(np.r_[0.0, cum_pnl_frac])[1:]
    return float((cum_pnl_frac - peak).min())


def performance(daily: pd.DataFrame, trades: pd.DataFrame, *, capital: float, periods_per_year: float,
                bootstrap: bool = True, n_boot: int = 500, seed: int = 0) -> dict:
    out: dict = {}
    if daily is None or daily.empty:
        return {"n_days": 0, "trade_count": 0}
    r = daily["ret"].to_numpy(float)
    rg = (daily["pnl_gross"] / capital).to_numpy(float)
    n = len(r)
    years = n / periods_per_year
    out.update(
        n_days=n, years=years,
        pnl_net=float(daily["pnl"].sum()), pnl_gross=float(daily["pnl_gross"].sum()),
        pnl_long=float(daily["pnl_long"].sum()), pnl_short=float(daily["pnl_short"].sum()),
        pnl_hedge=float(daily["pnl_hedge"].sum()),
        pnl_unhedged=float(daily["pnl"].sum() - daily["pnl_hedge"].sum()),
        fees=float(daily["fees"].sum()), slippage=float(daily["slippage"].sum()),
        borrow=float(daily["borrow"].sum()), funding=float(daily["funding"].sum()),
        mean_daily_ret=float(r.mean()), ann_return=float(r.mean() * periods_per_year),
        ann_return_gross=float(rg.mean() * periods_per_year),
        ann_vol=float(r.std(ddof=1) * math.sqrt(periods_per_year)) if n > 1 else float("nan"),
        sharpe=_sharpe(r, periods_per_year), sharpe_gross=_sharpe(rg, periods_per_year),
        sortino=_sortino(r, periods_per_year),
        max_drawdown=max_drawdown(np.cumsum(r)),
        capital_utilization=float((daily["gross_exposure"] / capital).mean()),
        max_gross_exposure=float((daily["gross_exposure"] / capital).max()),
        turnover_ann=float(daily["turnover"].sum() / capital / max(years, 1e-9)),
        active_day_frac=float((daily["n_positions"] > 0).mean()),
    )
    mu, se, p = newey_west_tstat(r)
    out.update(nw_t=float(mu / se) if se and se > 0 else float("nan"), nw_p=p)
    if bootstrap and n >= 30:
        _, lo, hi = bootstrap_ci(r, np.mean, n_boot=n_boot, seed=seed)
        out.update(ann_return_ci=(lo * periods_per_year, hi * periods_per_year))
        _, slo, shi = bootstrap_ci(r, lambda x: _sharpe(x, periods_per_year), n_boot=n_boot, seed=seed + 1)
        out.update(sharpe_ci=(slo, shi))
    if trades is not None and not trades.empty:
        t = trades[trades["entry_notional"] > 0]
        wins, losses = t.loc[t["pnl_net"] > 0, "pnl_net"].sum(), -t.loc[t["pnl_net"] < 0, "pnl_net"].sum()
        out.update(
            trade_count=int(len(t)), win_rate=float((t["pnl_net"] > 0).mean()) if len(t) else float("nan"),
            profit_factor=float(wins / losses) if losses > 0 else float("inf") if wins > 0 else float("nan"),
            bps_per_trade=float(t["pnl_net"].sum() / t["entry_notional"].sum() * 1e4) if len(t) else float("nan"),
            gross_bps_per_trade=float(t["pnl_gross"].sum() / t["entry_notional"].sum() * 1e4) if len(t) else float("nan"),
            avg_holding_sessions=float(t["holding_sessions"].mean()) if len(t) else float("nan"),
            long_trades=int((t["side"] > 0).sum()), short_trades=int((t["side"] < 0).sum()),
            avg_entry_notional=float(t["entry_notional"].mean()) if len(t) else float("nan"),
        )
    else:
        out.update(trade_count=0)
    return out


def performance_by(daily: pd.DataFrame, key: pd.Series, capital: float, ppy: float) -> pd.DataFrame:
    rows = []
    for k, g in daily.groupby(key):
        r = g["ret"].to_numpy(float)
        rows.append(dict(group=k, n_days=len(g), pnl_net=g["pnl"].sum(), pnl_gross=g["pnl_gross"].sum(),
                         ann_return=r.mean() * ppy, sharpe=_sharpe(r, ppy) if len(r) > 2 else float("nan")))
    return pd.DataFrame(rows)
