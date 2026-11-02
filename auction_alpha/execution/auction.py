"""Exchange-specific closing-auction state machines and a generic uncrossing algorithm.

The state machine answers, for a timestamp on a given trading day: which
phase is the market in, may a closing-auction order of a given type be
entered, and may it be cancelled.  The realised random close is taken from
the data (``close_auction_ts``) when available, else drawn uniformly in the
rule window with a seeded RNG.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from ..rules.adapters import ExchangeAdapter


class AuctionPhase(str, Enum):
    CONTINUOUS = "continuous"
    ORDER_ENTRY = "close_order_entry"
    NO_CANCEL = "no_cancel"
    RANDOM_CLOSE = "random_close"
    MATCHED = "matched"
    TAC = "trade_at_close"
    CLOSED = "closed"


@dataclass
class AuctionStateMachine:
    adapter: ExchangeAdapter
    date: dt.date
    halfday: bool = False
    realized_close: pd.Timestamp | None = None
    seed: int = 0

    def __post_init__(self):
        self.window = self.adapter.close_auction_window(self.date, self.halfday)
        if self.realized_close is None:
            rng = np.random.default_rng(self.seed + self.date.toordinal())
            span = (self.window.random_end - self.window.random_start).value
            self.realized_close = self.window.random_start + pd.Timedelta(int(rng.random() * span), "ns")

    def phase_at(self, ts: pd.Timestamp) -> AuctionPhase:
        w = self.window
        if ts < w.auction_start:
            return AuctionPhase.CONTINUOUS
        if ts < w.cancel_cutoff and ts < w.random_start:
            return AuctionPhase.ORDER_ENTRY
        if ts < w.random_start:
            return AuctionPhase.NO_CANCEL
        if ts < self.realized_close:
            return AuctionPhase.RANDOM_CLOSE
        if w.tac_start is not None and w.tac_start <= ts < w.tac_end:
            return AuctionPhase.TAC
        if w.tac_start is not None and ts < w.tac_start:
            return AuctionPhase.MATCHED
        return AuctionPhase.CLOSED if (w.tac_end is None or ts >= w.tac_end) else AuctionPhase.MATCHED

    def can_submit_auction_order(self, ts: pd.Timestamp, deadline: str = "random_start") -> tuple[bool, str]:
        """Can a closing-auction order submitted at ``ts`` participate?

        deadline='random_start' (default, causal): must be in the book before the random window
        opens, since the realised close time cannot be known in advance.
        deadline='realized': accepted until the actual match (optimistic, leaks the random close).
        """
        ph = self.phase_at(ts)
        if ph in (AuctionPhase.MATCHED, AuctionPhase.TAC, AuctionPhase.CLOSED):
            return False, "submitted_after_auction_match"
        if deadline == "random_start" and ph == AuctionPhase.RANDOM_CLOSE:
            return False, "submitted_in_random_window"
        if deadline == "cancel_cutoff" and ph in (AuctionPhase.NO_CANCEL, AuctionPhase.RANDOM_CLOSE):
            return False, "submitted_after_cancel_cutoff"
        return True, ""

    def can_cancel(self, ts: pd.Timestamp) -> bool:
        return self.phase_at(ts) in (AuctionPhase.CONTINUOUS, AuctionPhase.ORDER_ENTRY)

    def can_submit_tac(self, ts: pd.Timestamp) -> tuple[bool, str]:
        w = self.window
        if w.tac_start is None:
            return False, "tac_not_available"
        if ts >= w.tac_end:
            return False, "submitted_after_tac_end"
        return True, ""


def uncross(bids: list[tuple[float | None, float]], asks: list[tuple[float | None, float]],
            reference_price: float, rule: tuple[str, ...] = ("max_executable_volume", "min_imbalance",
                                                           "market_pressure", "nearest_reference_price"),
            ) -> tuple[float | None, float, float]:
    """Generic call-auction uncrossing.

    ``bids``/``asks`` are (limit_price or None for at-auction/market, quantity).
    Returns (price, executable_volume, imbalance) where imbalance > 0 means a
    buy surplus.  Candidate prices are all limit prices in the book.
    Criteria are applied in the order given by the exchange's rule list.
    """
    prices = sorted({p for p, _ in bids + asks if p is not None})
    if not prices:
        return (reference_price if bids and asks else None), 0.0, 0.0
    rows = []
    for p in prices:
        buy = sum(q for lp, q in bids if lp is None or lp >= p)
        sell = sum(q for lp, q in asks if lp is None or lp <= p)
        rows.append((p, min(buy, sell), buy - sell))
    best = [r for r in rows]
    for crit in rule:
        if len(best) <= 1:
            break
        if crit == "max_executable_volume":
            mv = max(r[1] for r in best)
            best = [r for r in best if r[1] == mv]
        elif crit == "min_imbalance":
            mi = min(abs(r[2]) for r in best)
            best = [r for r in best if abs(r[2]) == mi]
        elif crit == "market_pressure":
            if all(r[2] > 0 for r in best):
                best = [max(best, key=lambda r: r[0])]
            elif all(r[2] < 0 for r in best):
                best = [min(best, key=lambda r: r[0])]
        elif crit in ("nearest_reference_price", "nearest_last_price"):
            md = min(abs(r[0] - reference_price) for r in best)
            best = [r for r in best if abs(r[0] - reference_price) == md]
    p, vol, imb = best[0]
    if vol <= 0:
        return None, 0.0, imb
    return p, float(vol), float(imb)
