"""Vine-copula statistical arbitrage on SET50/SET100 stocks.

Each rolling period has a formation window, where the model is fitted, and a
trading window, where it trades out of sample:

1. **Partner selection.** For every target stock, pick the ``n_partners``
   stocks whose formation returns move most closely with it.  The candidates
   are the ``prefilter`` names with the highest Spearman rho to the target,
   and the chosen combination maximises the sum of all pairwise rhos inside
   the group of four.  The ``n_quadruples`` best groups are traded.
2. **Marginals.** Each stock's daily returns are mapped to uniforms with an
   empirical CDF (or a Student-t fit) estimated on the formation window only.
3. **Vine copula.** A C-vine (default), D-vine or free R-vine is fitted to the
   uniforms with the target first in the order, so the Rosenblatt transform
   gives ``h_t = P(U_target <= u_target,t | U_partners,t)``.
4. **Mispricing index.** ``MI_t = h_t - 0.5``.  ``h`` near 1 means the target
   rose more than its partners imply.  ``CMI`` sums MI and is reset to zero
   every time a position closes.
5. **Trading.** ``CMI >= entry``: short the target, long the partners.
   ``CMI <= -entry``: long the target, short the partners.  Close when CMI
   returns through ``exit`` (default 0), when ``|CMI| >= stop`` or at the end
   of the trading window.  Both legs have notional 1 (dollar neutral).

Signals use the close of day t.  ``execution_delay=0`` trades at that same
close (ATC); the default 1 trades at the next close, so a signal from day t
earns returns from day t+2.
"""

from dataclasses import asdict, dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd
import pyvinecopulib as pv
from scipy import stats


DEFAULT_FAMILIES = (
    "indep", "gaussian", "student", "clayton", "gumbel", "frank", "joe",
    "bb1", "bb7",
)


@dataclass
class VineArbParams:
    formation_days: int = 250
    trading_days: int = 60
    n_partners: int = 3
    prefilter: int = 10
    n_quadruples: int = 10
    min_coverage: float = 0.98      # share of formation days with a price
    max_abs_return: float = 0.40    # bigger formation move -> ineligible
    marginal: str = "ecdf"          # "ecdf" or "t"
    vine: str = "cvine"             # "cvine", "dvine" or "rvine"
    families: tuple = DEFAULT_FAMILIES
    selection_criterion: str = "aic"
    entry: float = 0.6
    exit: float = 0.0
    stop: float | None = 2.0
    hedge: str = "equal"            # "equal" or "ols"
    execution_delay: int = 1
    cost_bps: float = 15.0          # per side, per unit of notional traded
    borrow_bps: float = 150.0       # per year, on the short notional
    universe: tuple | None = field(default=None)
    groups: dict | None = field(default=None)       # {symbol: sector/industry}
    trade_groups: tuple | None = field(default=None)  # only these groups

    def __post_init__(self):
        if self.marginal not in ("ecdf", "t"):
            raise ValueError(f"marginal must be ecdf or t, got {self.marginal}")
        if self.vine not in ("cvine", "dvine", "rvine"):
            raise ValueError(f"vine must be cvine, dvine or rvine, got {self.vine}")
        if self.hedge not in ("equal", "ols"):
            raise ValueError(f"hedge must be equal or ols, got {self.hedge}")
        if self.prefilter < self.n_partners:
            raise ValueError("prefilter must be at least n_partners")
        if self.execution_delay < 0:
            raise ValueError("execution_delay must be >= 0")
        if self.exit >= self.entry:
            raise ValueError("exit must be below entry")
        if self.trade_groups is not None and self.groups is None:
            raise ValueError("trade_groups needs groups")


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def to_returns(prices):
    """Simple daily returns; a missing price gives a missing return."""
    prices = prices.sort_index()
    return prices.pct_change(fill_method=None).iloc[1:]


def eligible_returns(returns, params):
    """Formation returns of stocks with enough history and no broken prints.

    Missing days are filled with 0 (suspension), stocks that never move are
    dropped, as are stocks with a jump above ``max_abs_return`` (mergers,
    relistings), whose dependence in the window is not meaningful.
    """
    coverage = returns.notna().mean()
    keep = coverage.index[coverage >= params.min_coverage]
    window = returns[keep].fillna(0.0)
    moves = window.abs()
    keep = moves.columns[
        (moves.max() <= params.max_abs_return) & (window.ne(0).mean() > 0.5)
    ]
    return window[keep]


# ---------------------------------------------------------------------------
# Partner selection
# ---------------------------------------------------------------------------

def spearman(returns):
    return returns.rank().corr()


def select_quadruples(returns, params):
    """Best partner group for every target, ranked by summed Spearman rho.

    With ``params.groups`` the partners must share the target's group
    (sector or industry) and stocks without a group are left out.
    """
    if params.groups is not None:
        returns = returns[[c for c in returns.columns if c in params.groups]]
    rho = spearman(returns)
    names = rho.columns.to_numpy()
    matrix = rho.to_numpy()
    labels = (np.array([params.groups[n] for n in names], dtype=object)
              if params.groups is not None else None)
    k = params.n_partners
    rows = []
    for i, target in enumerate(names):
        others = np.delete(np.arange(len(names)), i)
        if labels is not None:
            others = others[labels[others] == labels[i]]
        candidates = others[np.argsort(-matrix[i, others])[: params.prefilter]]
        if len(candidates) < k:
            continue
        combos = np.array(list(combinations(candidates, k)))
        # target-partner links plus partner-partner links
        score = matrix[i, combos].sum(axis=1)
        for a, b in combinations(range(k), 2):
            score += matrix[combos[:, a], combos[:, b]]
        best = combos[np.argmax(score)]
        best = best[np.argsort(-matrix[i, best])]
        rows.append({
            "group": labels[i] if labels is not None else None,
            "target": target,
            "partners": tuple(names[best]),
            "score": float(score.max()),
            "rho_target": float(matrix[i, best].mean()),
        })
    table = pd.DataFrame(
        rows, columns=["group", "target", "partners", "score", "rho_target"])
    return (
        table.sort_values("score", ascending=False)
        .head(params.n_quadruples)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Marginals
# ---------------------------------------------------------------------------

class EmpiricalMarginal:
    """Mid-rank empirical CDF on the ``n/(n+1)`` scale, clipped inside (0, 1).

    Ties (frequent with SET tick sizes) get their average rank, and an
    out-of-sample value between two sample points gets the midpoint.
    """

    def __init__(self, x):
        self.sorted = np.sort(np.asarray(x, dtype=float))
        self.n = len(self.sorted)

    def cdf(self, x):
        x = np.asarray(x, dtype=float)
        left = np.searchsorted(self.sorted, x, side="left")
        right = np.searchsorted(self.sorted, x, side="right")
        u = (left + right + 1) / 2 / (self.n + 1)
        return np.clip(u, 0.5 / (self.n + 1), (self.n + 0.5) / (self.n + 1))


class StudentTMarginal:
    def __init__(self, x):
        self.df, self.loc, self.scale = stats.t.fit(np.asarray(x, dtype=float))

    def cdf(self, x):
        u = stats.t.cdf(x, self.df, self.loc, self.scale)
        return np.clip(u, 1e-6, 1 - 1e-6)


def fit_marginal(x, kind):
    return EmpiricalMarginal(x) if kind == "ecdf" else StudentTMarginal(x)


# ---------------------------------------------------------------------------
# Vine copula
# ---------------------------------------------------------------------------

def _controls(params):
    families = [getattr(pv.BicopFamily, name) for name in params.families]
    return pv.FitControlsVinecop(
        family_set=families, selection_criterion=params.selection_criterion
    )


def fit_vine(u, params):
    """Vine on uniforms ``u``; column 0 is the target, then partners by rho.

    C-vine order ``[1, d, ..., 2]`` makes the most correlated partner the
    root of tree 1 and leaves the target to be conditioned on everything in
    the last tree.  D-vine order ``[1, 2, ..., d]`` puts the target at the end
    of the path, next to its most correlated partner.
    """
    d = u.shape[1]
    controls = _controls(params)
    if params.vine == "rvine":
        return pv.Vinecop.from_data(u, controls=controls)
    if params.vine == "cvine":
        structure = pv.CVineStructure(order=[1, *range(d, 1, -1)])
    else:
        structure = pv.DVineStructure(order=list(range(1, d + 1)))
    vine = pv.Vinecop.from_structure(structure=structure)
    vine.select(u, controls=controls)
    return vine


def conditional_cdf(vine, u, target=0, grid=801):
    """``P(U_target <= u_target | all other columns)`` for each row of ``u``.

    Exact (Rosenblatt) when the target comes first in the vine order, which
    is always the case for the C- and D-vines built by ``fit_vine``.  A free
    R-vine may order the variables differently; then the density is
    integrated numerically over the target coordinate on a normal-score grid
    (fine in the tails, where most of the trading signals are).
    """
    u = np.atleast_2d(np.asarray(u, dtype=float))
    if vine.order[0] == target + 1:
        return vine.rosenblatt(u)[:, target]
    z = np.linspace(-6.0, 6.0, grid)
    points = stats.norm.cdf(z)
    n = len(u)
    stacked = np.repeat(u, grid, axis=0)
    stacked[:, target] = np.tile(points, n)
    integrand = vine.pdf(stacked).reshape(n, grid) * stats.norm.pdf(z)
    steps = (integrand[:, 1:] + integrand[:, :-1]) / 2 * np.diff(z)
    cumulative = np.concatenate([np.zeros((n, 1)), np.cumsum(steps, axis=1)], axis=1)
    cumulative /= cumulative[:, -1:]
    target_z = stats.norm.ppf(np.clip(u[:, target], 1e-12, 1 - 1e-12))
    return np.array([
        np.interp(target_z[i], z, cumulative[i]) for i in range(n)
    ])


# ---------------------------------------------------------------------------
# Fitted quadruple
# ---------------------------------------------------------------------------

class QuadrupleModel:
    """Marginals, vine and hedge weights fitted on one formation window."""

    def __init__(self, target, partners, formation, params):
        self.target = target
        self.partners = list(partners)
        self.columns = [target, *self.partners]
        data = formation[self.columns].to_numpy()
        self.marginals = [fit_marginal(data[:, j], params.marginal)
                          for j in range(data.shape[1])]
        u = self.uniforms(formation)
        self.vine = fit_vine(u, params)
        self.weights = hedge_weights(data[:, 0], data[:, 1:], params.hedge)
        in_sample = conditional_cdf(self.vine, u)
        self.in_sample_mean = float(in_sample.mean())

    def uniforms(self, returns):
        data = returns[self.columns].to_numpy()
        return np.column_stack([
            m.cdf(data[:, j]) for j, m in enumerate(self.marginals)
        ])

    def conditional(self, returns):
        """``h_t`` for each day in ``returns`` (a frame with these columns)."""
        return pd.Series(
            conditional_cdf(self.vine, self.uniforms(returns)),
            index=returns.index,
        )

    def describe(self):
        families = [
            str(bicop.family).split(".")[-1]
            for tree in self.vine.pair_copulas for bicop in tree
        ]
        return {
            "target": self.target,
            "partners": ", ".join(self.partners),
            "weights": ", ".join(f"{w:.3f}" for w in self.weights),
            "vine_aic": float(self.vine.aic()),
            "vine_npars": float(self.vine.npars),
            "families": ", ".join(families),
            "in_sample_mean_h": self.in_sample_mean,
        }


def hedge_weights(target, partners, method):
    """Partner weights summing to 1 (the partner leg's notional)."""
    k = partners.shape[1]
    equal = np.full(k, 1.0 / k)
    if method == "equal":
        return equal
    beta, *_ = np.linalg.lstsq(
        np.column_stack([np.ones(len(target)), partners]), target, rcond=None
    )
    beta = np.clip(beta[1:], 0.0, None)
    return beta / beta.sum() if beta.sum() > 0 else equal


# ---------------------------------------------------------------------------
# Trading rule
# ---------------------------------------------------------------------------

def signal_path(h, params, force_flat_from=None):
    """Run the CMI state machine over ``h``.

    Returns a frame with ``mi``, ``cmi`` (after the day's update, before a
    reset), ``signal`` (position wanted after the close: +1 long target,
    -1 short target, 0 flat) and ``event`` (OPEN / REVERT / STOP / END).
    From index ``force_flat_from`` on the position must be flat.
    """
    values = np.asarray(h, dtype=float)
    n = len(values)
    mi = values - 0.5
    cmi_out = np.zeros(n)
    signal = np.zeros(n, dtype=int)
    events = np.array([""] * n, dtype=object)
    cmi, position = 0.0, 0
    for t in range(n):
        cmi += mi[t]
        cmi_out[t] = cmi
        forced = force_flat_from is not None and t >= force_flat_from
        if position == 0:
            if not forced and cmi >= params.entry:
                position, events[t] = -1, "OPEN"
            elif not forced and cmi <= -params.entry:
                position, events[t] = 1, "OPEN"
        else:
            reverted = (position == -1 and cmi <= params.exit) or (
                position == 1 and cmi >= -params.exit
            )
            stopped = params.stop is not None and abs(cmi) >= params.stop
            if reverted or stopped or forced:
                events[t] = "REVERT" if reverted else "STOP" if stopped else "END"
                position, cmi = 0, 0.0
        signal[t] = position
    index = h.index if isinstance(h, pd.Series) else None
    return pd.DataFrame(
        {"mi": mi, "cmi": cmi_out, "signal": signal, "event": events},
        index=index,
    )


def held_position(signal, delay):
    """Position earning day t's return: the signal from ``delay + 1`` days ago."""
    lag = delay + 1
    held = np.zeros(len(signal), dtype=int)
    if len(signal) > lag:
        held[lag:] = np.asarray(signal)[:-lag]
    return held


def quadruple_pnl(model, returns, params, complete=True, h=None):
    """Daily P&L of one quadruple over one trading window.

    ``returns`` covers the trading window; missing returns count as 0.
    With ``complete=True`` the position is flat by the window's last day.
    ``h`` can be passed in when already computed for these returns.
    """
    returns = returns[model.columns].fillna(0.0)
    if h is None:
        h = model.conditional(returns)
    n = len(returns)
    flat_from = n - params.execution_delay - 2 if complete else None
    path = signal_path(h, params, force_flat_from=flat_from)
    held = held_position(path["signal"], params.execution_delay)

    target = returns[model.target].to_numpy()
    hedge = returns[model.partners].to_numpy() @ model.weights
    spread = target - hedge
    gross = held * spread
    turnover = np.abs(np.diff(held, prepend=0))
    cost = turnover * 2 * params.cost_bps / 1e4        # both legs trade
    borrow = (held != 0) * params.borrow_bps / 1e4 / 252
    net = gross - cost - borrow

    daily = pd.DataFrame({
        "h": h.to_numpy(), "mi": path["mi"].to_numpy(),
        "cmi": path["cmi"].to_numpy(), "signal": path["signal"].to_numpy(),
        "event": path["event"].to_numpy(), "held": held,
        "spread_return": spread, "gross": gross, "cost": cost + borrow,
        "net": net,
    }, index=returns.index)
    return daily


def extract_trades(daily, model, period, delay):
    """One row per round trip, dated by the days the position was held.

    ``net_return`` includes the closing cost, charged on the first flat day.
    """
    held = daily["held"].to_numpy()
    events = daily["event"].to_numpy()
    lag = delay + 1
    rows, start = [], None
    for t in range(len(held) + 1):
        current = held[t] if t < len(held) else 0
        previous = held[t - 1] if t > 0 else 0
        if previous != 0 and current != previous:
            block = daily.iloc[start:t]
            closed = t < len(held)
            rows.append({
                "period": period, "target": model.target,
                "partners": ", ".join(model.partners),
                "side": "LONG_TARGET" if previous > 0 else "SHORT_TARGET",
                "first_day": block.index[0], "last_day": block.index[-1],
                "days": len(block),
                "gross_return": float(block["gross"].sum()),
                "net_return": float(daily["net"].iloc[start:min(t + 1, len(daily))].sum()),
                "exit_reason": events[t - lag] if closed else "OPEN",
            })
            start = None
        if current != 0 and current != previous:
            start = t
    return rows


# ---------------------------------------------------------------------------
# Rolling backtest
# ---------------------------------------------------------------------------

# Parameters that change the fitted models.  The others (entry, exit, stop,
# execution_delay, costs) only change the trading rule, so a sensitivity run
# can reuse the fits from ``fit_periods``.
FIT_FIELDS = (
    "formation_days", "trading_days", "n_partners", "prefilter",
    "n_quadruples", "min_coverage", "max_abs_return", "marginal", "vine",
    "families", "selection_criterion", "hedge", "universe", "groups",
    "trade_groups",
)


def _fit_key(params):
    return tuple(getattr(params, name) for name in FIT_FIELDS)


def _universe(prices, params):
    columns = list(prices.columns)
    if params.universe is not None:
        columns = [c for c in params.universe if c in prices.columns]
    if params.trade_groups is not None:
        columns = [c for c in columns
                   if params.groups.get(c) in params.trade_groups]
    return prices[columns]


def period_schedule(index, params):
    """(formation_start, trading_start, trading_end) positions in ``index``."""
    out = []
    start = params.formation_days
    while start < len(index):
        end = min(start + params.trading_days, len(index))
        out.append((start - params.formation_days, start, end))
        start = end
    return out


def fit_period(returns, f0, t0, t1, params, period):
    """Select and fit quadruples on [f0, t0); evaluate ``h`` on [t0, t1)."""
    formation = eligible_returns(returns.iloc[f0:t0], params)
    if formation.shape[1] < params.n_partners + 1:
        return None
    quads = select_quadruples(formation, params)
    trading = returns.iloc[t0:t1]
    fitted = []
    for row in quads.itertuples():
        model = QuadrupleModel(row.target, row.partners, formation, params)
        window = trading[model.columns].fillna(0.0)
        fitted.append({
            "model": model, "returns": window, "h": model.conditional(window),
            "score": row.score, "rho_target": row.rho_target,
            "group": row.group,
        })
    return {
        "period": period,
        "formation_start": returns.index[f0],
        "trading_start": returns.index[t0],
        "trading_end": returns.index[t1 - 1],
        "complete": (t1 - t0) == params.trading_days,
        "fitted": fitted,
    }


def fit_periods(prices, params=None, start=None, end=None, progress=False):
    """Fit every rolling period once (the slow part of a backtest)."""
    params = params or VineArbParams()
    returns = to_returns(_universe(prices, params).loc[start:end])
    schedule = period_schedule(returns.index, params)
    if not schedule:
        raise ValueError("Not enough history for one formation window")
    periods = []
    for period, (f0, t0, t1) in enumerate(schedule, start=1):
        if progress:
            print(f"fit {period}/{len(schedule)} "
                  f"{returns.index[t0].date()}..{returns.index[t1 - 1].date()}")
        fit = fit_period(returns, f0, t0, t1, params, period)
        if fit is not None:
            periods.append(fit)
    return {"key": _fit_key(params), "periods": periods}


def trade_period(fit, params):
    """Apply the trading rule to one fitted period."""
    daily, trades, models = {}, [], []
    for item in fit["fitted"]:
        model = item["model"]
        pnl = quadruple_pnl(model, item["returns"], params,
                            complete=fit["complete"], h=item["h"])
        daily[model.target] = pnl
        trades.extend(
            dict(row, group=item["group"]) for row in extract_trades(
                pnl, model, fit["period"], params.execution_delay))
        models.append({
            "period": fit["period"], "formation_start": fit["formation_start"],
            "trading_start": fit["trading_start"],
            "trading_end": fit["trading_end"],
            "group": item["group"],
            "score": item["score"], "rho_target": item["rho_target"],
            **model.describe(),
        })
    return {"daily": daily, "trades": trades, "models": models}


def backtest(prices, params=None, start=None, end=None, progress=False,
             fits=None):
    """Rolling out-of-sample backtest.

    Returns a dict with ``daily`` (portfolio returns: each quadruple gets
    1/``n_quadruples`` of capital, idle capital earns 0), ``positions``
    (held position per quadruple-day), ``trades``, ``models`` (fitted
    quadruples per period), ``summary`` and ``live`` (state on the last day).

    Pass ``fits`` from ``fit_periods`` to try other trading-rule parameters
    without refitting; the fitting parameters must be the same.
    """
    params = params or VineArbParams()
    if fits is None:
        fits = fit_periods(prices, params, start, end, progress)
    elif fits["key"] != _fit_key(params):
        raise ValueError("fits were made with different fitting parameters")

    portfolio, positions, trades, models, last = [], [], [], [], None
    for fit in fits["periods"]:
        result = trade_period(fit, params)
        if not result["daily"]:
            continue
        frame = {k: v for k, v in result["daily"].items()}
        net = pd.DataFrame({k: v["net"] for k, v in frame.items()})
        gross = pd.DataFrame({k: v["gross"] for k, v in frame.items()})
        held = pd.DataFrame({k: v["held"] for k, v in frame.items()})
        portfolio.append(pd.DataFrame({
            "period": fit["period"],
            "net": net.sum(axis=1) / params.n_quadruples,
            "gross": gross.sum(axis=1) / params.n_quadruples,
            "active": held.ne(0).sum(axis=1),
        }))
        positions.append(
            held.rename_axis("date").rename_axis("target", axis=1)
            .stack().rename("held").reset_index().assign(period=fit["period"])
        )
        trades.extend(result["trades"])
        models.extend(result["models"])
        last = (fit, result)

    daily = pd.concat(portfolio)
    daily["equity"] = (1 + daily["net"]).cumprod()
    trades = pd.DataFrame(trades)
    return {
        "params": params,
        "daily": daily,
        "positions": pd.concat(positions, ignore_index=True),
        "trades": trades,
        "models": pd.DataFrame(models),
        "summary": summarize(daily, trades, params),
        "live": _live_table(*last, params) if last else pd.DataFrame(),
    }


def backtest_by_group(prices, params, groups=None, n_quadruples=None,
                      start=None, end=None, progress=False):
    """Separate backtest for each group in ``params.groups``.

    Each group is its own portfolio: only its stocks, ``n_quadruples``
    quadruples per period (default: ``params.n_quadruples``, capped so a
    group never trades more quadruples than it has stocks).  Groups with
    fewer than ``n_partners + 1`` priced stocks are skipped.

    Returns ``{"summary": one row per group, "results": {group: backtest}}``.
    """
    from dataclasses import replace

    if params.groups is None:
        raise ValueError("params.groups is required")
    members = pd.Series(params.groups)
    members = members[members.index.isin(prices.columns)]
    names = groups or sorted(members.unique())
    rows, results = [], {}
    for name in names:
        size = int((members == name).sum())
        if size < params.n_partners + 1:
            continue
        quads = min(n_quadruples or params.n_quadruples, size)
        trial = replace(params, trade_groups=(name,), n_quadruples=quads)
        if progress:
            print(f"{name}: {size} stocks, {quads} quadruples")
        try:
            result = backtest(prices, trial, start=start, end=end)
        except ValueError as exc:      # e.g. no period with enough history
            if progress:
                print(f"  skipped: {exc}")
            continue
        results[name] = result
        rows.append({"group": name, "stocks": size, "quadruples": quads,
                     **result["summary"].to_dict()})
    return {"summary": pd.DataFrame(rows), "results": results}


SIDE = {1: "LONG_TARGET", -1: "SHORT_TARGET", 0: "FLAT"}


def _live_table(fit, result, params):
    """State on the last day of the last period.

    ``signal`` is the position to hold after today's close; ``held`` is the
    position that earned today's return.  With ``execution_delay=1`` a new
    signal is executed at the next close.
    """
    rows = []
    for item in fit["fitted"]:
        model = item["model"]
        pnl = result["daily"][model.target]
        last = pnl.iloc[-1]
        rows.append({
            "period": fit["period"], "as_of": pnl.index[-1],
            "group": item["group"], "target": model.target, "partners": ", ".join(model.partners),
            "weights": ", ".join(f"{w:.3f}" for w in model.weights),
            "h": last["h"], "cmi": last["cmi"],
            "signal": SIDE[int(last["signal"])],
            "held": SIDE[int(last["held"])],
            "event_today": last["event"],
            "days_in_period": len(pnl),
            "trading_days_to_refit": params.trading_days - len(pnl),
            "period_net": float(pnl["net"].sum()),
        })
    return pd.DataFrame(rows)


def live_signals(prices, params=None, as_of=None):
    """Today's signals: the last period of the rolling schedule up to ``as_of``.

    The schedule starts at the first date in ``prices``, so pass the same
    price history as the backtest to get the same refit dates.  When
    ``trading_days_to_refit`` is 0 today is the last day of the window: all
    positions close and the next session starts a new formation.
    """
    params = params or VineArbParams()
    returns = to_returns(_universe(prices, params).loc[:as_of])
    schedule = period_schedule(returns.index, params)
    if not schedule:
        raise ValueError("Not enough history for one formation window")
    f0, t0, t1 = schedule[-1]
    fit = fit_period(returns, f0, t0, t1, params, len(schedule))
    return _live_table(fit, trade_period(fit, params), params)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def max_drawdown(equity):
    return float((equity / equity.cummax() - 1).min())


def summarize(daily, trades, params, periods_per_year=252):
    net = daily["net"]
    years = len(net) / periods_per_year
    equity = (1 + net).cumprod()
    vol = net.std() * np.sqrt(periods_per_year)
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    row = {
        "start": net.index[0].date(), "end": net.index[-1].date(),
        "years": round(years, 2),
        "cagr": cagr,
        "ann_return": net.mean() * periods_per_year,
        "ann_vol": vol,
        "sharpe": net.mean() / net.std() * np.sqrt(periods_per_year) if vol > 0 else np.nan,
        "max_drawdown": max_drawdown(equity),
        "gross_ann_return": daily["gross"].mean() * periods_per_year,
        "avg_active_quadruples": daily["active"].mean(),
        "trades": len(trades),
    }
    if len(trades):
        row.update({
            "trades_per_year": len(trades) / years,
            "win_rate": float((trades["net_return"] > 0).mean()),
            "avg_trade_net": float(trades["net_return"].mean()),
            "avg_holding_days": float(trades["days"].mean()),
            "stop_share": float(trades["exit_reason"].eq("STOP").mean()),
        })
    return pd.Series(row)


def sensitivity(prices, params=None, grid=None, start=None, end=None,
                fits=None):
    """Summary for every trading-rule combination in ``grid``.

    ``grid`` maps parameter names to lists of values, e.g.
    ``{"entry": [0.3, 0.6, 1.0], "execution_delay": [0, 1]}``.  Only
    trading-rule parameters may vary (entry, exit, stop, execution_delay,
    cost_bps, borrow_bps), so the vines are fitted once.  These numbers are
    all in-sample choices: picking the best row overstates what live
    trading would earn.
    """
    from dataclasses import replace
    from itertools import product

    params = params or VineArbParams()
    grid = grid or {"entry": [0.3, 0.6, 1.0, 1.5],
                    "execution_delay": [0, 1], "cost_bps": [0, 5, 10, 15]}
    bad = set(grid) & set(FIT_FIELDS)
    if bad:
        raise ValueError(f"grid may not vary fitting parameters: {sorted(bad)}")
    fits = fits or fit_periods(prices, params, start, end)
    rows = []
    for values in product(*grid.values()):
        trial = replace(params, **dict(zip(grid, values)))
        summary = backtest(prices, trial, fits=fits)["summary"]
        rows.append({**dict(zip(grid, values)), **summary.to_dict()})
    return pd.DataFrame(rows)


def params_table(params):
    return pd.DataFrame(
        [(k, str(v)) for k, v in asdict(params).items()],
        columns=["parameter", "value"],
    )
