import numpy as np
import pandas as pd
import pytest

from vine_arb import (
    EmpiricalMarginal,
    QuadrupleModel,
    VineArbParams,
    backtest,
    conditional_cdf,
    fit_periods,
    fit_vine,
    held_position,
    live_signals,
    quadruple_pnl,
    select_quadruples,
    signal_path,
    to_returns,
)

FAST = ("indep", "gaussian", "student", "clayton", "gumbel")


def correlated_uniforms(n=600, seed=0):
    rng = np.random.default_rng(seed)
    cov = np.array([[1, .6, .5, .4], [.6, 1, .5, .3],
                    [.5, .5, 1, .4], [.4, .3, .4, 1]])
    x = rng.multivariate_normal(np.zeros(4), cov, n)
    # skewed tails so non-Gaussian families get picked too
    x[:, 1] = np.where(x[:, 1] < 0, 1.5 * x[:, 1], x[:, 1])
    ranks = pd.DataFrame(x).rank().to_numpy()
    return ranks / (n + 1)


def numeric_conditional(vine, u):
    """Independent check: adaptive quadrature of the density over column 0."""
    from scipy.integrate import quad

    def density(x, row):
        point = row.copy()
        point[0] = x
        return float(vine.pdf(point[None, :])[0])

    out = []
    for row in u:
        total = quad(density, 0, 1, args=(row,), limit=200)[0]
        out.append(quad(density, 0, row[0], args=(row,), limit=200)[0] / total)
    return np.array(out)


def synthetic_prices(n_days=700, n_noise=4, kappa=0.5, seed=1):
    """Four stocks share a factor; the first carries a mean-reverting gap."""
    rng = np.random.default_rng(seed)
    factor = rng.normal(0, 0.012, n_days)
    returns = {}
    for name in ["P1", "P2", "P3"]:
        returns[name] = factor + rng.normal(0, 0.006, n_days)
    partner_mean = np.mean([returns[n] for n in ["P1", "P2", "P3"]], axis=0)
    gap = np.zeros(n_days)
    shocks = rng.normal(0, 0.008, n_days)
    for t in range(1, n_days):
        gap[t] = (1 - kappa) * gap[t - 1] + shocks[t]
    returns["TGT"] = partner_mean + np.diff(gap, prepend=0.0)
    for i in range(n_noise):
        returns[f"N{i}"] = rng.normal(0, 0.015, n_days)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    return 100 * (1 + pd.DataFrame(returns, index=dates)).cumprod()


# --- copula ------------------------------------------------------------------

@pytest.mark.parametrize("vine", ["cvine", "dvine", "rvine"])
def test_conditional_cdf_matches_numeric_integration(vine):
    u = correlated_uniforms()
    model = fit_vine(u, VineArbParams(vine=vine, families=FAST))
    sample = u[:12]
    exact = conditional_cdf(model, sample)
    assert np.allclose(exact, numeric_conditional(model, sample), atol=1e-3)


def test_conditional_cdf_is_uniform_in_sample():
    u = correlated_uniforms(n=1500)
    model = fit_vine(u, VineArbParams(families=FAST))
    h = conditional_cdf(model, u)
    assert abs(h.mean() - 0.5) < 0.02
    assert abs(h.std() - np.sqrt(1 / 12)) < 0.02


def test_cvine_puts_most_correlated_partner_at_the_root():
    model = fit_vine(correlated_uniforms(), VineArbParams(families=FAST))
    assert list(model.order)[0] == 1          # target conditioned last
    assert list(model.order)[-1] == 2         # column 1 is the root


def test_empirical_marginal_uses_mid_ranks_for_ties():
    marginal = EmpiricalMarginal([0.0, 0.0, 0.0, 0.01])
    assert marginal.cdf([0.0])[0] == pytest.approx(2 / 5)
    assert marginal.cdf([0.005])[0] == pytest.approx(3.5 / 5)
    assert marginal.cdf([1.0])[0] == pytest.approx(4.5 / 5)


# --- trading rule ------------------------------------------------------------

def test_signal_path_opens_reverts_and_resets():
    params = VineArbParams(entry=0.6, exit=0.0, stop=None)
    h = pd.Series([0.9, 0.9, 0.3, 0.2, 0.5, 0.1, 0.2, 0.2])
    path = signal_path(h, params)
    # cmi: .4 .8 -> OPEN short; .6 .3 .3 ; -.1 -> REVERT and reset
    assert path["signal"].tolist() == [0, -1, -1, -1, -1, 0, 0, 1]
    assert path["event"].tolist()[:6] == ["", "OPEN", "", "", "", "REVERT"]
    # after reset: -.3 -> -.6 opens long
    assert path["event"].iloc[7] == "OPEN"


def test_signal_path_stop_and_forced_flat():
    params = VineArbParams(entry=0.6, stop=1.0)
    # cmi -0.4, -0.8 (open long), -1.2 (stop, reset), -0.4
    path = signal_path(pd.Series([0.1, 0.1, 0.1, 0.1]), params)
    assert path["signal"].tolist() == [0, 1, 0, 0]
    assert path["event"].tolist() == ["", "OPEN", "STOP", ""]
    path = signal_path(pd.Series([0.1, 0.1, 0.4, 0.4]), params, force_flat_from=2)
    assert path["signal"].tolist() == [0, 1, 0, 0]
    assert path["event"].iloc[2] == "END"


def test_held_position_lags_the_signal():
    signal = [1, 1, 0, -1, 0]
    assert held_position(signal, 0).tolist() == [0, 1, 1, 0, -1]
    assert held_position(signal, 1).tolist() == [0, 0, 1, 1, 0]


def test_quadruple_pnl_accounts_and_ends_flat():
    prices = synthetic_prices(n_days=400)
    returns = to_returns(prices)
    params = VineArbParams(formation_days=250, trading_days=60, families=FAST,
                           cost_bps=10, borrow_bps=0)
    model = QuadrupleModel("TGT", ["P1", "P2", "P3"], returns.iloc[:250], params)
    pnl = quadruple_pnl(model, returns.iloc[250:310], params)
    assert pnl["held"].iloc[-1] == 0
    turnover = np.abs(np.diff(pnl["held"], prepend=0)).sum()
    assert pnl["cost"].sum() == pytest.approx(turnover * 2 * 10 / 1e4)
    assert np.allclose(pnl["gross"], pnl["held"] * pnl["spread_return"])


# --- selection and backtest --------------------------------------------------

def test_select_quadruples_finds_the_factor_cluster():
    returns = to_returns(synthetic_prices())
    params = VineArbParams(n_quadruples=1, prefilter=5)
    quads = select_quadruples(returns, params)
    group = {quads.at[0, "target"], *quads.at[0, "partners"]}
    assert group == {"TGT", "P1", "P2", "P3"}


def test_signals_do_not_use_future_prices():
    prices = synthetic_prices()
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=2,
                           prefilter=4, families=FAST)
    as_of = prices.index[280]
    live = live_signals(prices, params, as_of=as_of)
    shocked = prices.copy()
    shocked.loc[shocked.index > as_of] *= 3.0
    pd.testing.assert_frame_equal(live, live_signals(shocked, params, as_of=as_of))
    assert (live["as_of"] == as_of).all()


def test_backtest_earns_the_planted_reversion():
    # All four cluster members get traded as targets; only TGT carries the
    # gap, so its quadruple must make money and the others roughly nothing.
    prices = synthetic_prices(n_days=900, kappa=0.5)
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=4,
                           prefilter=4, families=FAST, entry=0.3,
                           execution_delay=0, cost_bps=0, borrow_bps=0)
    result = backtest(prices, params)
    groups = result["models"].apply(
        lambda r: {r["target"], *r["partners"].split(", ")}, axis=1)
    assert groups.eq({"TGT", "P1", "P2", "P3"}).mean() > 0.9
    by_target = result["trades"].groupby("target")["net_return"].sum()
    assert by_target["TGT"] > 0.2
    assert by_target["TGT"] > by_target.drop("TGT").max()
    # trade P&L adds up to the daily P&L
    assert result["trades"]["net_return"].sum() == pytest.approx(
        result["daily"]["net"].sum() * params.n_quadruples
    )
    # with next-close execution the one-day reversion is mostly missed
    late = backtest(prices, VineArbParams(**{**params.__dict__,
                                             "execution_delay": 1}))
    late_tgt = late["trades"].groupby("target")["net_return"].sum()["TGT"]
    assert late_tgt < by_target["TGT"]


def test_backtest_rejects_fits_from_other_fitting_parameters():
    prices = synthetic_prices(n_days=400)
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=1,
                           prefilter=4, families=FAST)
    fits = fit_periods(prices, params)
    with pytest.raises(ValueError):
        backtest(prices, VineArbParams(formation_days=250, trading_days=60,
                                       n_quadruples=1, prefilter=4,
                                       families=FAST, vine="dvine"), fits=fits)
    # trading-rule changes are fine
    backtest(prices, VineArbParams(formation_days=250, trading_days=60,
                                   n_quadruples=1, prefilter=4, families=FAST,
                                   entry=1.0), fits=fits)


def test_sensitivity_refuses_fitting_parameters_and_runs_rules():
    from vine_arb import sensitivity

    prices = synthetic_prices(n_days=400)
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=1,
                           prefilter=4, families=FAST)
    with pytest.raises(ValueError):
        sensitivity(prices, params, grid={"vine": ["dvine"]})
    table = sensitivity(prices, params,
                        grid={"entry": [0.3, 1.0], "execution_delay": [0, 1]})
    assert len(table) == 4
    assert {"entry", "execution_delay", "sharpe", "cagr"} <= set(table.columns)


def test_report_writes_every_sheet(tmp_path):
    from openpyxl import load_workbook

    from vine_arb_run import SHEETS, run

    prices = synthetic_prices(n_days=400)
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=2,
                           prefilter=4, families=FAST)
    groups = {s: "X" if s in ("TGT", "P1", "P2", "P3") else "Y"
              for s in prices.columns}
    params = VineArbParams(**{**params.__dict__, "groups": groups})
    result = run(prices, params, output_dir=tmp_path, with_sensitivity=True,
                 per_group=True)
    assert load_workbook(result["path"]).sheetnames == list(SHEETS)


# --- industry groups ---------------------------------------------------------

def test_partners_come_from_the_target_group():
    returns = to_returns(synthetic_prices())
    # put TGT with the noise stocks: the factor cluster is no longer allowed
    groups = {"TGT": "A", "N0": "A", "N1": "A", "N2": "A", "N3": "A",
              "P1": "B", "P2": "B", "P3": "B"}
    params = VineArbParams(n_quadruples=10, prefilter=5, groups=groups)
    quads = select_quadruples(returns, params)
    for row in quads.itertuples():
        assert {groups[p] for p in row.partners} == {groups[row.target]}
        assert row.group == groups[row.target]
    # group B has only 3 stocks: no quadruple can be built there
    assert set(quads["group"]) == {"A"}


def test_trade_groups_limits_the_universe():
    from vine_arb import backtest_by_group

    prices = synthetic_prices(n_days=400)
    groups = {"TGT": "X", "P1": "X", "P2": "X", "P3": "X",
              "N0": "Y", "N1": "Y", "N2": "Y", "N3": "Y"}
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=4,
                           prefilter=4, families=FAST, groups=groups)
    by_group = backtest_by_group(prices, params)
    assert list(by_group["summary"]["group"]) == ["X", "Y"]
    x_models = by_group["results"]["X"]["models"]
    assert set(x_models["target"]) <= {"TGT", "P1", "P2", "P3"}
    assert x_models["group"].eq("X").all()
    with pytest.raises(ValueError):
        VineArbParams(trade_groups=("X",))


def test_sector_map_covers_the_universe():
    from price_data import SECTOR_INDUSTRY, SET100_2026H2, SET_SECTOR, industry_groups

    assert set(SET_SECTOR) == set(SET100_2026H2)
    assert set(SET_SECTOR.values()) <= set(SECTOR_INDUSTRY)
    assert industry_groups(["PTT", "XYZ"], level="industry") == {"PTT": "RESOURC"}


def test_small_universe_spreads_capital_over_fitted_quadruples():
    prices = synthetic_prices(n_days=400)[["TGT", "P1", "P2", "P3"]]
    params = VineArbParams(formation_days=250, trading_days=60, n_quadruples=10,
                           families=FAST, entry=0.3, cost_bps=0, borrow_bps=0)
    result = backtest(prices, params)
    assert result["models"].groupby("period").size().eq(4).all()
    assert result["trades"]["net_return"].sum() == pytest.approx(
        result["daily"]["net"].sum() * 4)
