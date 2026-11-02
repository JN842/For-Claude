import numpy as np
import pandas as pd
import pytest

from auction_alpha.backtest.engine import prepare_market, run_strategy
from auction_alpha.backtest.event_study import gap_event_study
from auction_alpha.execution.scenarios import SCENARIOS
from auction_alpha.strategies import STRATEGIES

from conftest import TEST_PERIODS


def test_specs_are_complete():
    for sid, cls in STRATEGIES.items():
        s = cls.spec
        assert s.id == sid
        for field in ("hypothesis", "signal_formula", "entry_rule", "exit_rule", "order_timing", "order_type",
                      "cost_assumptions", "hedging", "risk_controls", "validation", "failure_modes"):
            assert getattr(s, field), (sid, field)
        assert s.required_data
        cls(**{k: v[0] for k, v in s.param_grid.items()})  # grid params are valid


@pytest.mark.parametrize("market", ["SET", "SGX", "HKEX"])
def test_all_strategies_end_to_end(bundles, market):
    for sid, cls in STRATEGIES.items():
        if cls.spec.markets and market not in cls.spec.markets:
            continue
        s = cls(**({"min_names": 5} if sid == "S06" else {}))
        r = run_strategy(s, bundles[market], capital=1e7, periods=TEST_PERIODS,
                         scenarios=[SCENARIOS["base"], SCENARIOS["stress"]], bootstrap=False)
        for name, run in r.runs.items():
            m = run.metrics
            assert m["n_days"] > 0
            if m.get("trade_count", 0):
                # gross >= net (costs are non-negative) for every study
                assert m["pnl_gross"] >= m["pnl_net"] - 1e-6, (market, sid, name)


def test_stress_never_beats_base_on_costs(bundles):
    r = run_strategy(STRATEGIES["S03"](), bundles["SET"], capital=1e7, periods=TEST_PERIODS,
                     scenarios=[SCENARIOS["base"], SCENARIOS["stress"]], bootstrap=False)
    b, s = r.runs["base"].metrics, r.runs["stress"].metrics
    if b.get("trade_count") and s.get("trade_count"):
        assert s["bps_per_trade"] - s["gross_bps_per_trade"] <= b["bps_per_trade"] - b["gross_bps_per_trade"] + 1e-6


def test_s07_only_sgx():
    assert STRATEGIES["S07"].spec.markets == ("SGX",)


def test_s04_unsupported_without_indicative(tiny_tables):
    t = dict(tiny_tables)
    t["auction_indicative"] = t["auction_indicative"].iloc[:0]
    b = prepare_market(t, "SET")
    r = run_strategy(STRATEGIES["S04"](), b, capital=1e7, periods=TEST_PERIODS, bootstrap=False)
    assert r.n_intents == 0  # never simulated with invented indicative data


def test_short_legs_only_if_borrowable(bundles, tiny_tables):
    r = run_strategy(STRATEGIES["S06"](min_names=5), bundles["SET"], capital=1e7, periods=TEST_PERIODS, bootstrap=False)
    run = r.runs["base"]
    br = tiny_tables["borrow"]
    br = br[br["market"] == "SET"].set_index(["symbol", "date"])["shortable"]
    shorts = run.sim.fills[(run.sim.fills["leg"] == "entry") & (run.sim.fills["side_qty"] < 0)]
    assert all(br.loc[(s, pd.Timestamp(d))] for s, d in zip(shorts["symbol"], shorts["date"]))
    assert (run.sim.rejections["reason"] == "not_shortable").any()


def test_planted_reversal_is_detected_and_null_is_not(large_planted, large_null):
    """Power and false-positive sanity of the diagnostic study (synthetic worlds)."""
    from auction_alpha.features.engineering import build_features
    fp = build_features(large_planted, "SET")
    fn = build_features(large_null, "SET")
    sp = gap_event_study(fp, threshold_bps=50, sign=-1, n_boot=0)["summary"].query("kind == 'market_adjusted'").set_index("horizon")
    sn = gap_event_study(fn, threshold_bps=50, sign=-1, n_boot=0)["summary"].query("kind == 'market_adjusted'").set_index("horizon")
    # planted world: reversal found both in the diagnostic and in the executable window
    assert sp.loc["C0->O1", "mean_bps"] > 0 and sp.loc["C0->O1", "p"] < 0.001
    assert sp.loc["O1->C1", "mean_bps"] > 0 and sp.loc["O1->C1", "p"] < 0.01
    # null world: the executable window shows nothing ...
    assert sn.loc["O1->C1", "p"] > 0.01
    # ... while the C0-anchored diagnostic window still shows a SPURIOUS reversal created by tick
    # discreteness (rounding error in the close reverts at the next print).  This is why diagnostic
    # event studies are never treated as evidence of an executable edge.
    assert sn.loc["C0->O1", "mean_bps"] < sp.loc["C0->O1", "mean_bps"]
    g = fp[(fp["close_method"] == "auction")][["gap", "fwd_o1"]].dropna()
    slope = -np.polyfit(g["gap"], g["fwd_o1"], 1)[0]
    assert 0.25 < slope < 0.6


def test_diagnostics_are_labelled_not_executable(bundles):
    from auction_alpha.features.engineering import FeatureView
    from auction_alpha.rules.adapters import get_adapter
    from auction_alpha.strategies.base import StrategyContext
    b = bundles["SGX"]
    ctx = StrategyContext("SGX", FeatureView(b.features, diagnostic=True), b.tables, get_adapter("SGX"), b.md, 1e7, 2.0, b.calendar)
    for sid in ("S01", "S03", "S04", "S07", "S08"):
        d = STRATEGIES[sid]().diagnostic(ctx)
        assert "NOT EXECUTABLE" in d["label"] or "UNSUPPORTED" in d["label"]
