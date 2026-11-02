import numpy as np
import pandas as pd
import pytest

from auction_alpha.backtest.engine import run_strategy
from auction_alpha.leakage import perturbation_invariance, run_suite, truncation_invariance
from auction_alpha.strategies import STRATEGIES
from auction_alpha.strategies.gap import S01ClosingGapReversal

from conftest import TEST_PERIODS

CUTOFF = "2021-06-15"


class _PeekingStrategy(S01ClosingGapReversal):
    """Deliberately leaky: picks direction from TOMORROW's close (bypasses FeatureView)."""

    def generate_intents(self, ctx):
        out = super().generate_intents(ctx)
        raw = ctx.features.df.sort_values(["symbol", "date"])
        nxt = raw.groupby("symbol")["close"].shift(-1) / raw["close"] - 1
        look = dict(zip(zip(raw["symbol"], raw["date"]), nxt))
        out["side"] = [int(np.sign(np.nan_to_num(look.get((s, pd.Timestamp(d)), 0.0))) or 1) for s, d in zip(out["symbol"], out["signal_date"])]
        return out


@pytest.mark.parametrize("sid", ["S01", "S03", "S04", "S05", "S06", "S08"])
def test_truncation_invariance_set(tiny_tables, sid):
    kw = {"min_names": 5} if sid == "S06" else {}
    r = truncation_invariance(STRATEGIES[sid](**kw), tiny_tables, "SET", CUTOFF)
    assert r["passed"], r


def test_truncation_invariance_sgx_tac(tiny_tables):
    r = truncation_invariance(STRATEGIES["S07"](), tiny_tables, "SGX", CUTOFF)
    assert r["passed"] and r["n_before"] > 0


@pytest.mark.parametrize("sid", ["S01", "S04", "S06"])
def test_perturbation_invariance_hkex(tiny_tables, sid):
    kw = {"min_names": 5} if sid == "S06" else {}
    assert perturbation_invariance(STRATEGIES[sid](**kw), tiny_tables, "HKEX", CUTOFF)["passed"]


def test_leakage_tests_detect_a_peeking_strategy(tiny_tables):
    r1 = truncation_invariance(_PeekingStrategy(threshold_bps=20), tiny_tables, "SET", CUTOFF)
    r2 = perturbation_invariance(_PeekingStrategy(threshold_bps=20), tiny_tables, "SET", CUTOFF)
    assert not (r1["passed"] and r2["passed"]), "leakage suite failed to flag a look-ahead strategy"


def test_full_suite_passes_on_small_sample(tiny_tables):
    small = {k: v for k, v in tiny_tables.items()}
    r = run_suite(small, ["SGX"], {k: STRATEGIES[k] for k in ("S01", "S05", "S07")}, cutoff=CUTOFF)
    assert r["passed"].all(), r[~r["passed"]]


def test_holdout_signals_excluded(bundles):
    r = run_strategy(STRATEGIES["S01"](threshold_bps=30), bundles["SET"], capital=1e7, periods=TEST_PERIODS, bootstrap=False)
    assert len(r.intents) > 0
    assert (pd.to_datetime(r.intents["signal_date"]) < pd.Timestamp(TEST_PERIODS["holdout"][0])).all()
    assert "holdout" not in r.runs["base"].period_metrics
    r2 = run_strategy(STRATEGIES["S01"](threshold_bps=30), bundles["SET"], capital=1e7, periods=TEST_PERIODS,
                      bootstrap=False, holdout_unlocked=True)
    assert (pd.to_datetime(r2.intents["signal_date"]) >= pd.Timestamp(TEST_PERIODS["holdout"][0])).any()


def test_every_intent_submitted_after_signal(bundles):
    for m, b in bundles.items():
        for sid, cls in STRATEGIES.items():
            if cls.spec.markets and m not in cls.spec.markets:
                continue
            r = run_strategy(cls(**({"min_names": 5} if sid == "S06" else {})), b, capital=1e7, periods=TEST_PERIODS, bootstrap=False)
            if len(r.intents):
                assert (r.intents["submit_ts"] >= r.intents["signal_ts"]).all(), (m, sid)
