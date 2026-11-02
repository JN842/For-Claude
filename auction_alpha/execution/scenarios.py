"""Scenario-based execution assumptions.

Historical order-level data is not available, so fill probability and market
impact cannot be measured exactly.  Instead every backtest is run under four
explicit, auditable scenarios.  None of them is "the truth"; the spread of
results across them is the honest uncertainty band.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ExecutionScenario:
    name: str
    auction_participation: float     # max fraction of realised auction volume we may take
    open_participation: float        # max fraction of opening-auction volume
    continuous_participation: float  # max fraction of daily volume for continuous-session fills
    tac_participation: float         # max fraction of realised SGX TAC volume
    fill_ratio: float                # fraction of eligible quantity that actually matches
    impact_coef: float               # square-root impact coefficient (x daily vol x sqrt(q/ADV))
    spread_mult: float               # multiplier on observed half-spread for continuous fills
    extra_slippage_bps: float        # flat additional slippage per fill
    limit_lock_fill: float           # fill fraction when the auction prints at the price limit
    borrow_fee_mult: float
    short_in_auction_unknown: str    # 'allow' | 'reject' when the short-in-auction rule is unverified
    delist_haircut: float            # loss on forced exit of a delisted position
    futures_cost_mult: float
    max_exit_delay_days: int = 10
    order_deadline: str = "random_start"

    def to_dict(self) -> dict:
        return asdict(self)


SCENARIOS: dict[str, ExecutionScenario] = {
    "optimistic": ExecutionScenario("optimistic", 0.20, 0.15, 0.05, 0.30, 1.00, 0.5, 0.75, 0.0, 0.5, 0.75, "allow", 0.0, 0.75),
    "base": ExecutionScenario("base", 0.10, 0.10, 0.02, 0.15, 0.90, 1.0, 1.00, 0.0, 0.0, 1.00, "allow", 0.10, 1.00),
    "conservative": ExecutionScenario("conservative", 0.05, 0.05, 0.01, 0.08, 0.75, 1.5, 1.25, 2.0, 0.0, 1.50, "reject", 0.25, 1.25),
    "stress": ExecutionScenario("stress", 0.025, 0.025, 0.005, 0.03, 0.50, 2.5, 1.50, 5.0, 0.0, 2.00, "reject", 0.50, 1.50),
}


def load_scenarios(path: Path | str | None = None) -> dict[str, ExecutionScenario]:
    if path is None or not Path(path).exists():
        return dict(SCENARIOS)
    raw = yaml.safe_load(Path(path).read_text()) or {}
    out = dict(SCENARIOS)
    for name, kv in raw.items():
        base = out.get(name, SCENARIOS["base"])
        out[name] = replace(base, name=name, **kv)
    return out
