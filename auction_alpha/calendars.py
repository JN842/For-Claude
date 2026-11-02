"""Trading calendars.

Official holiday calendars for 2018-2026 were NOT loaded in this project (the
registry marks ``calendar.holidays`` as unknown).  Two calendar sources are
therefore supported:

* ``TradingCalendar.from_dates`` - the trading days observed in a market's
  daily price file (the default for real data);
* ``synthetic_calendar`` - weekdays minus a small set of fixed-date holidays
  plus synthetic half-days, used only for synthetic datasets and clearly
  labelled as such.

Official holiday CSVs can be supplied with ``TradingCalendar.from_holiday_csv``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class TradingCalendar:
    market: str
    days: pd.DatetimeIndex
    halfdays: set = field(default_factory=set)
    source: str = "observed"

    def __post_init__(self):
        self.days = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(self.days).normalize())))
        self._pos = {d: i for i, d in enumerate(self.days)}

    @classmethod
    def from_dates(cls, market: str, dates, halfdays=(), source: str = "observed") -> "TradingCalendar":
        return cls(market, pd.DatetimeIndex(pd.to_datetime(pd.Series(dates).unique())),
                   {pd.Timestamp(d).normalize() for d in halfdays}, source)

    @classmethod
    def from_holiday_csv(cls, market: str, path: Path | str, start, end) -> "TradingCalendar":
        """CSV columns: date, kind ('holiday'|'halfday'), source_url."""
        df = pd.read_csv(path, parse_dates=["date"])
        hol = set(df.loc[df["kind"] == "holiday", "date"].dt.normalize())
        half = set(df.loc[df["kind"] == "halfday", "date"].dt.normalize())
        days = [d for d in pd.bdate_range(start, end) if d not in hol]
        return cls(market, pd.DatetimeIndex(days), half, source=f"csv:{path}")

    def is_trading_day(self, d) -> bool:
        return pd.Timestamp(d).normalize() in self._pos

    def is_halfday(self, d) -> bool:
        return pd.Timestamp(d).normalize() in self.halfdays

    def offset(self, d, n: int):
        """Trading day n sessions after d (d must be a trading day)."""
        i = self._pos[pd.Timestamp(d).normalize()] + n
        if i < 0 or i >= len(self.days):
            return None
        return self.days[i]

    def next_day(self, d):
        d = pd.Timestamp(d).normalize()
        i = self.days.searchsorted(d, side="right")
        return self.days[i] if i < len(self.days) else None

    def years(self) -> dict[int, int]:
        return pd.Series(self.days.year).value_counts().sort_index().to_dict()

    def sessions_per_year(self) -> float:
        yrs = pd.Series(self.days.year).value_counts()
        full = yrs[yrs >= 200]
        return float(full.mean()) if len(full) else 250.0


# Fixed-date closures used ONLY for synthetic calendars.  Not an official list.
_SYNTH_FIXED = {
    "SET": [(1, 1), (4, 6), (4, 13), (4, 14), (4, 15), (5, 1), (7, 28), (8, 12), (10, 13), (10, 23), (12, 5), (12, 10), (12, 31)],
    "SGX": [(1, 1), (5, 1), (8, 9), (12, 25)],
    "HKEX": [(1, 1), (5, 1), (7, 1), (10, 1), (12, 25), (12, 26)],
}
# Synthetic "lunar new year" style closures (fixed per year, NOT real dates).
_SYNTH_LUNAR_DAY = {2018: (2, 16), 2019: (2, 5), 2020: (1, 27), 2021: (2, 12), 2022: (2, 1),
                    2023: (1, 23), 2024: (2, 12), 2025: (1, 29), 2026: (2, 17)}


def synthetic_calendar(market: str, start: str, end: str) -> TradingCalendar:
    """Weekdays minus synthetic holidays; half-days on synthetic eves (SGX/HKEX)."""
    bdays = pd.bdate_range(start, end)
    years = range(bdays[0].year, bdays[-1].year + 1)
    hol = set()
    for y in years:
        for m, d in _SYNTH_FIXED[market]:
            hol.add(pd.Timestamp(y, m, d))
        if market in ("SGX", "HKEX") and y in _SYNTH_LUNAR_DAY:
            ld = pd.Timestamp(y, *_SYNTH_LUNAR_DAY[y])
            hol.update({ld, ld + pd.Timedelta(days=1)})
    days = [d for d in bdays if d not in hol]
    half = set()
    if market in ("SGX", "HKEX"):
        dset = set(days)
        for y in years:
            eves = [pd.Timestamp(y, 12, 24), pd.Timestamp(y, 12, 31)]
            if y in _SYNTH_LUNAR_DAY:
                eves.append(pd.Timestamp(y, *_SYNTH_LUNAR_DAY[y]) - pd.Timedelta(days=1))
            half.update(e for e in eves if e in dset)
    return TradingCalendar(market, pd.DatetimeIndex(days), half, source="synthetic")


def chronological_periods(cal_days: pd.DatetimeIndex, periods: dict[str, tuple[str, str]]) -> pd.Series:
    """Label each date with its research period name ('' when outside all)."""
    lab = pd.Series("", index=cal_days, dtype=object)
    for name, (a, b) in periods.items():
        mask = (cal_days >= pd.Timestamp(a)) & (cal_days <= pd.Timestamp(b))
        lab[mask] = name
    return lab


def np_busday_count(a, b) -> int:
    return int(np.busday_count(pd.Timestamp(a).date(), pd.Timestamp(b).date()))
