"""Exchange-rule adapters.

Each adapter turns the effective-dated registry into concrete answers for a
trading date: phase timetable, closing-auction window, order-entry cutoffs,
tick size, board lot, fee schedule, price limits and short-sale regime.

Every registry lookup is recorded in ``adapter.usage`` with its verification
label so that a backtest can report how many simulated decisions depended on
unverified or historically-assumed rules.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from .registry import MarketRules, RuleNotFound, default_registry


@dataclass(frozen=True)
class Phase:
    name: str
    start: pd.Timestamp  # UTC
    end: pd.Timestamp    # UTC
    random_end: tuple[pd.Timestamp, pd.Timestamp] | None = None


@dataclass(frozen=True)
class CloseAuctionWindow:
    date: dt.date
    auction_start: pd.Timestamp       # start of pre-close / CAS order phase
    cancel_cutoff: pd.Timestamp       # after this, orders cannot be cancelled
    random_start: pd.Timestamp
    random_end: pd.Timestamp
    tac_start: pd.Timestamp | None
    tac_end: pd.Timestamp | None
    halfday: bool
    rule_label: str


@dataclass(frozen=True)
class FeeSchedule:
    """Per-side costs in basis points of notional (plus fixed per-trade tariff)."""

    commission_bps: float
    exchange_bps: float          # trading + clearing + levies
    stamp_duty_bps: float        # both sides (HKEX), 0 elsewhere
    tax_on_fees_pct: float       # VAT/GST charged on commission + exchange fees
    per_trade_fixed: float = 0.0
    label: str = ""

    def cost(self, notional: float) -> float:
        notional = abs(notional)
        fees = notional * (self.commission_bps + self.exchange_bps) / 1e4
        tax = fees * self.tax_on_fees_pct / 100.0
        stamp = notional * self.stamp_duty_bps / 1e4
        if self.stamp_duty_bps > 0:
            stamp = math.ceil(stamp)  # HK stamp duty rounded up to the dollar
        return fees + tax + stamp + self.per_trade_fixed

    def total_bps(self) -> float:
        return (self.commission_bps + self.exchange_bps) * (1 + self.tax_on_fees_pct / 100) + self.stamp_duty_bps


def _hm(date: dt.date, hhmm: str, tz: ZoneInfo) -> pd.Timestamp:
    h, m = (int(x) for x in hhmm.split(":"))
    return pd.Timestamp(dt.datetime(date.year, date.month, date.day, h, m, tzinfo=tz)).tz_convert("UTC")


class ExchangeAdapter:
    market: str = ""
    order_types_close: tuple[str, ...] = ()

    def __init__(self, rules: MarketRules | None = None, commission_bps: float | None = None):
        self.rules = rules or default_registry()[self.market]
        self.tz = ZoneInfo(self.rules.timezone)
        self.currency = self.rules.currency
        self.commission_override = commission_bps
        self.usage: Counter = Counter()
        self._cache: dict = {}
        self._fee_cache: dict = {}
        self._win_cache: dict = {}

    # -------------------------------------------------------------- lookups
    def _get(self, key: str, date, default=None):
        ck = (key, date)
        if ck not in self._cache:
            try:
                lk = self.rules.lookup(key, date)
                self._cache[ck] = (lk.value, lk.label())
            except RuleNotFound:
                self._cache[ck] = (None, "missing")
        value, label = self._cache[ck]
        self.usage[(key, label)] += 1
        return value if value is not None else default

    def rule_label(self, key: str, date) -> str:
        try:
            return self.rules.lookup(key, date).label()
        except RuleNotFound:
            return "missing"

    # ------------------------------------------------------------- sessions
    def phases(self, date: dt.date, halfday: bool = False) -> list[Phase]:
        key = "session.halfday_phases" if halfday else "session.phases"
        spec = self._get(key, date)
        if spec is None:
            spec = self._get("session.phases", date)
        out = []
        for p in spec:
            re_ = None
            if p.get("random_end"):
                re_ = (_hm(date, p["random_end"][0], self.tz), _hm(date, p["random_end"][1], self.tz))
            out.append(Phase(p["phase"], _hm(date, p["start"], self.tz), _hm(date, p["end"], self.tz), re_))
        return out

    def close_auction_exists(self, date) -> bool:
        return bool(self._get("auction.close.exists", date, True))

    def tac_available(self, date) -> bool:
        return bool(self._get("tac.available", date, False))

    def close_auction_window(self, date: dt.date, halfday: bool = False) -> CloseAuctionWindow:
        k = (date, halfday)
        if k not in self._win_cache:
            self._win_cache[k] = self._close_auction_window(date, halfday)
        return self._win_cache[k]

    def _close_auction_window(self, date: dt.date, halfday: bool = False) -> CloseAuctionWindow:
        raise NotImplementedError

    def open_auction_cutoff(self, date: dt.date) -> pd.Timestamp:
        """Latest time an order for the opening auction can be safely entered."""
        win = self._get("auction.open.random_window", date)
        return _hm(date, win["start"], self.tz)

    def open_time(self, date: dt.date) -> pd.Timestamp:
        win = self._get("auction.open.random_window", date)
        return _hm(date, win["end"], self.tz)

    # --------------------------------------------------------- price grid
    def tick_size(self, price: float, date) -> float:
        table = self._get("tick_size.table", date)
        tick = table[0][1]
        for lower, t in table:
            if price >= lower:
                tick = t
        return float(tick)

    def round_to_tick(self, price: float, date, side: int = 0) -> float:
        t = self.tick_size(price, date)
        n = price / t
        n = math.floor(n + 1e-9) if side > 0 else math.ceil(n - 1e-9) if side < 0 else round(n)
        return round(n * t, 6)

    def board_lot(self, date, security_lot: float | None = None) -> int:
        if security_lot is not None and not (isinstance(security_lot, float) and math.isnan(security_lot)):
            return int(security_lot)
        lot = self._get("board_lot.default", date)
        return int(lot) if lot else 100

    def price_limits(self, prev_close: float, date) -> tuple[float, float] | None:
        pct = self._get("price_limits.daily_pct", date)
        if not pct or prev_close is None or not math.isfinite(prev_close):
            return None
        lo = max(0.01, prev_close * (1 - pct / 100))
        hi = prev_close * (1 + pct / 100)
        return self.round_to_tick(lo, date, -1), self.round_to_tick(hi, date, +1)

    # ----------------------------------------------------------------- fees
    def fee_schedule(self, date) -> FeeSchedule:
        if date in self._fee_cache:
            self.usage[("fees.exchange", self._fee_cache[date].label)] += 1
            return self._fee_cache[date]
        fs = self._fee_schedule(date)
        self._fee_cache[date] = fs
        return fs

    def _fee_schedule(self, date) -> FeeSchedule:
        f = self._get("fees.exchange", date) or {}
        comm = self.commission_override
        if comm is None:
            comm = float(self._get("fees.commission_bps", date, 10.0))
        exch = sum(float(v) for k, v in f.items() if k.endswith("_bps") and k != "stamp_duty_bps")
        return FeeSchedule(
            commission_bps=comm,
            exchange_bps=exch,
            stamp_duty_bps=float(f.get("stamp_duty_bps", 0.0)),
            tax_on_fees_pct=float(f.get("tax_on_fees_pct", 0.0)),
            per_trade_fixed=float(f.get("per_trade_tariff", 0.0)),
            label=self.rule_label("fees.exchange", date),
        )

    def short_regime(self, date) -> dict:
        return self._get("short_selling.regime", date, {}) or {}

    def futures_spec(self, date) -> dict:
        return self._get("futures.index_hedge", date, {}) or {}

    def usage_summary(self) -> dict[str, int]:
        out: Counter = Counter()
        for (_key, label), n in self.usage.items():
            out[label] += n
        return dict(out)


class SETAdapter(ExchangeAdapter):
    market = "SET"
    order_types_close = ("ATC", "LIMIT")

    def _close_auction_window(self, date, halfday=False):
        rw = self._get("auction.close.random_window", date)
        oe = self._get("auction.close.order_entry", date)
        rs, re_ = _hm(date, rw["start"], self.tz), _hm(date, rw["end"], self.tz)
        return CloseAuctionWindow(
            date=date, auction_start=_hm(date, oe["phase_start"], self.tz),
            cancel_cutoff=rs,  # no published pre-close cancel restriction: cancel until random close
            random_start=rs, random_end=re_, tac_start=None, tac_end=None, halfday=False,
            rule_label=self.rule_label("auction.close.random_window", date),
        )


class SGXAdapter(ExchangeAdapter):
    market = "SGX"
    order_types_close = ("MARKET", "LIMIT")

    def _close_auction_window(self, date, halfday=False):
        ph = {p.name: p for p in self.phases(date, halfday)}
        nc = ph["pre_close_non_cancel"]
        tac = ph.get("trade_at_close")
        has_tac = self.tac_available(date) and tac is not None
        return CloseAuctionWindow(
            date=date, auction_start=ph["pre_close"].start, cancel_cutoff=nc.start,
            random_start=nc.random_end[0], random_end=nc.random_end[1],
            tac_start=tac.start if has_tac else None, tac_end=tac.end if has_tac else None,
            halfday=halfday, rule_label=self.rule_label("session.phases", date),
        )


class HKEXAdapter(ExchangeAdapter):
    market = "HKEX"
    order_types_close = ("AT_AUCTION", "AT_AUCTION_LIMIT")

    def _close_auction_window(self, date, halfday=False):
        ph = {p.name: p for p in self.phases(date, halfday)}
        rc = ph["cas_random_closing"]
        return CloseAuctionWindow(
            date=date, auction_start=ph["cas_order_input"].start,
            cancel_cutoff=ph["cas_no_cancellation"].start,
            random_start=rc.random_end[0], random_end=rc.random_end[1],
            tac_start=None, tac_end=None, halfday=halfday,
            rule_label=self.rule_label("session.halfday_phases" if halfday else "session.phases", date),
        )

    def cas_band(self, reference_price: float, date) -> tuple[float, float]:
        oe = self._get("auction.close.order_entry", date)
        pct = oe.get("stage1_band_pct", 5.0) / 100
        return reference_price * (1 - pct), reference_price * (1 + pct)


ADAPTERS = {"SET": SETAdapter, "SGX": SGXAdapter, "HKEX": HKEXAdapter}
MARKETS = tuple(ADAPTERS)


def get_adapter(market: str, **kw) -> ExchangeAdapter:
    return ADAPTERS[market](**kw)
