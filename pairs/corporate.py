"""Corporate actions: exact price-adjustment factors, raw prices and total return.

Convention (verified against FinancePair.xlsx): a vendor-adjusted series equals
raw price x f(t), where f(t) is the product of the stock-dividend factors of
every stock dividend with X-date *after* t. Cash dividends are not adjusted.
A stock dividend "a : b" gives b new shares per a held, so prices before the
X-date are scaled by a / (a + b).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ACTIONS_CSV = Path(__file__).with_name("corporate_actions.csv")


def load_actions(path: str | Path = ACTIONS_CSV) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"value": str})
    df["x_date"] = pd.to_datetime(df["x_date"])
    return df


def stock_factors(actions: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """One row per stock dividend: x_date and the factor applied before it."""
    rows = actions.loc[actions["symbol"].eq(symbol) & actions["unit"].eq("Share")]
    out = []
    for x_date, value in zip(rows["x_date"], rows["value"]):
        held, new = (float(v) for v in value.split(":"))
        out.append((x_date, held / (held + new)))
    return pd.DataFrame(out, columns=["x_date", "factor"]).sort_values("x_date")


def adjustment_factor(index: pd.DatetimeIndex, actions: pd.DataFrame, symbol: str) -> pd.Series:
    """f(t) such that adjusted = raw x f(t)."""
    f = pd.Series(1.0, index=index)
    for x_date, factor in stock_factors(actions, symbol).itertuples(index=False):
        f[index < x_date] *= factor
    return f


def cash_dividends(index: pd.DatetimeIndex, actions: pd.DataFrame, symbol: str,
                   adjusted: bool = True) -> pd.Series:
    """Cash DPS on each X-date (0 elsewhere). adjusted=True puts DPS in the units
    of the adjusted price series (DPS x f on the X-date)."""
    rows = actions.loc[actions["symbol"].eq(symbol) & actions["unit"].eq("Baht")]
    d = pd.Series(0.0, index=index)
    f = adjustment_factor(index, actions, symbol) if adjusted else pd.Series(1.0, index=index)
    for x_date, value in zip(rows["x_date"], rows["value"]):
        on = index[index >= x_date]
        if len(on) == 0 or (on[0] - x_date).days > 5:
            continue  # X-date outside the price sample
        d[on[0]] += float(value) * f[on[0]]
    return d


def to_adjusted(price: pd.Series, actions: pd.DataFrame, symbol: str, is_adjusted: bool) -> pd.Series:
    """Stock-dividend-adjusted price, whatever the input convention."""
    return price if is_adjusted else price * adjustment_factor(price.index, actions, symbol)


def to_raw(adjusted: pd.Series, actions: pd.DataFrame, symbol: str) -> pd.Series:
    return adjusted / adjustment_factor(adjusted.index, actions, symbol)


def total_return_index(adjusted: pd.Series, dividends: pd.Series) -> pd.Series:
    """Cash dividends reinvested at the X-date close; starts at the first price."""
    gross = (adjusted + dividends) / adjusted.shift()
    gross.iloc[0] = 1.0
    return adjusted.iloc[0] * gross.cumprod()


def price_panel(close: pd.DataFrame, actions: pd.DataFrame, adjusted_symbols: set[str]) -> dict[str, pd.DataFrame]:
    """For each symbol: raw, stock-adjusted ('adj'), dividend (adj units), total return ('tr')."""
    out = {}
    for sym in close.columns:
        adj = to_adjusted(close[sym], actions, sym, sym in adjusted_symbols)
        div = cash_dividends(close.index, actions, sym)
        out[sym] = pd.DataFrame({
            "raw": to_raw(adj, actions, sym),
            "adj": adj,
            "div": div,
            "tr": total_return_index(adj, div),
        })
    return out


def check_against_table(panel: dict[str, pd.DataFrame], table: pd.DataFrame) -> pd.DataFrame:
    """Compare reconstructed raw closes with the 'Price Before X-Date' column."""
    rows = []
    for sym, before, x_date in table[["symbol", "price_before", "x_date"]].itertuples(index=False):
        raw = panel[sym]["raw"]
        prev = raw[raw.index < x_date]
        if len(prev):
            rows.append((sym, x_date.date(), before, round(prev.iloc[-1], 4)))
    out = pd.DataFrame(rows, columns=["symbol", "x_date", "table", "reconstructed"]).drop_duplicates()
    out["diff"] = out["reconstructed"] - out["table"]
    out["ok"] = np.isclose(out["diff"], 0, atol=0.011)
    return out
