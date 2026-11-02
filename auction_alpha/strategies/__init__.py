"""Strategy catalogue S01-S08."""

from .basis import S08AuctionFuturesBasis
from .gap import S01ClosingGapReversal, S02ClosingGapContinuation, S06CrossSectionalReversal, S07TradeAtCloseDislocation
from .imbalance import S04AuctionImbalance
from .preclose import S03PrecloseMomentum
from .rebalance import S05IndexRebalance

STRATEGIES = {
    "S01": S01ClosingGapReversal,
    "S02": S02ClosingGapContinuation,
    "S03": S03PrecloseMomentum,
    "S04": S04AuctionImbalance,
    "S05": S05IndexRebalance,
    "S06": S06CrossSectionalReversal,
    "S07": S07TradeAtCloseDislocation,
    "S08": S08AuctionFuturesBasis,
}


def make_strategy(sid: str, **params):
    return STRATEGIES[sid](**params)
