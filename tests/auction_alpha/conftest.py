"""Shared fixtures: small SYNTHETIC datasets generated in memory (never real data)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auction_alpha.data.synthetic import SyntheticConfig, WorldParams, generate  # noqa: E402

TEST_PERIODS = {
    "discovery": ("2020-01-01", "2020-12-31"),
    "validation": ("2021-01-01", "2021-06-30"),
    "oos": ("2021-07-01", "2021-12-31"),
    "holdout": ("2022-01-01", "2022-12-31"),
}


def _cfg(world):
    return SyntheticConfig(world=world, seed=123).small(n_stocks=12, start="2020-01-02", end="2022-06-30")


@pytest.fixture(scope="session")
def tiny_tables():
    return generate(_cfg(WorldParams()))


@pytest.fixture(scope="session")
def tiny_null_tables():
    return generate(_cfg(WorldParams.null()))


@pytest.fixture(scope="session")
def bundles(tiny_tables):
    from auction_alpha.backtest.engine import prepare_market
    return {m: prepare_market(tiny_tables, m) for m in ("SET", "SGX", "HKEX")}


@pytest.fixture(scope="session")
def large_planted():
    """Bigger sample for statistical-power checks (still synthetic)."""
    cfg = SyntheticConfig(seed=7).small(n_stocks=30, start="2019-01-02", end="2022-12-30")
    cfg.markets = {"SET": cfg.markets["SET"]}
    return generate(cfg)


@pytest.fixture(scope="session")
def large_null():
    cfg = SyntheticConfig(seed=7, world=WorldParams.null()).small(n_stocks=30, start="2019-01-02", end="2022-12-30")
    cfg.markets = {"SET": cfg.markets["SET"]}
    return generate(cfg)
