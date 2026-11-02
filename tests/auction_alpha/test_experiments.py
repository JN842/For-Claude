import numpy as np

from auction_alpha.experiments import summarise, verdict


def _res(sid, mkt, label, mean, p2):
    return dict(strategy=sid, market=mkt, status="unsupported", support="unsupported",
                grid=[dict(label=label, disc_sharpe=np.sign(mean), disc_nw_p=p2, disc_mean=mean, disc_trades=100, params={})])


def test_losing_strategies_do_not_count_as_discoveries():
    """A reliably *negative* strategy must get a one-sided p near 1, not a small two-sided p."""
    res = [_res("S01", "SET", "lose", -1e-3, 1e-8), _res("S02", "SET", "lose2", -1e-3, 1e-8),
           _res("S03", "SET", "win", 1e-4, 0.04)]
    s = summarise(res, {"markets": []}, {"is_synthetic": False})
    t = s["tests"].set_index("label")
    assert t.loc["lose", "p"] > 0.99 and t.loc["lose", "q_bh"] > 0.99
    assert abs(t.loc["win", "p"] - 0.02) < 1e-12
    # with two-sided p-values the losers would have inflated the family; one-sided keeps q = 3 x 0.02
    assert abs(t.loc["win", "q_bh"] - 0.06) < 1e-12


def test_verdict_rules():
    base = dict(status="ran", disc_q_bh=0.01, validation_sharpe=0.5, oos_sharpe=0.4, conservative_sharpe=0.2, placebo_p=0.05)
    assert verdict(base) == "executable_edge_after_costs"
    assert verdict(dict(base, oos_sharpe=-0.1, gross_sharpe=0.2)) == "no_robust_effect"
    assert verdict(dict(base, conservative_sharpe=-0.1, gross_sharpe=1.0)) == "statistical_anomaly_not_executable"
    assert verdict(dict(status="unsupported")) == "untestable_missing_data"
