"""Load 1-minute bars (xlsx -> parquet cache) and build daily close series."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(os.environ.get("PAIRS_CACHE", Path.home() / ".cache" / "pairs"))


def load_minutes(xlsx: str | Path, symbol: str, cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """1-minute OHLCV for one sheet. Converts the sheet to parquet on first use."""
    xlsx = Path(xlsx)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{xlsx.stem}_{symbol}.parquet"
    if cached.exists() and cached.stat().st_mtime >= xlsx.stat().st_mtime:
        return pd.read_parquet(cached)
    df = pd.read_excel(xlsx, sheet_name=symbol)
    df["DateTime"] = pd.to_datetime(df["DateTime"])
    df = df.sort_values("DateTime").reset_index(drop=True)
    df.to_parquet(cached, index=False)
    return df


def daily_close(minutes: pd.DataFrame) -> pd.Series:
    """Last traded close of each day, indexed by date."""
    close = minutes.groupby(minutes["DateTime"].dt.normalize())["Close"].last()
    close.index.name = "date"
    return close


def load_daily(xlsx: str | Path, symbols: list[str], cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """Daily closes for several sheets, on the dates where all of them traded."""
    cols = {s: daily_close(load_minutes(xlsx, s, cache_dir)) for s in symbols}
    return pd.DataFrame(cols).dropna()
