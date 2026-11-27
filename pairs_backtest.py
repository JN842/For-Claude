"""Z-score mean-reversion backtest for chosen pairs / baskets.

Strategies (first stock = Y, the leg bought when the spread is bought):
spread = log(Y) - sum(beta_i * log(X_i)).

Two ways to get the hedge ratio and the z-score scale:

* ``fixed``   - Johansen weights, mean and sd from the formation window
                (Nov 2023 - Nov 2025); traded only out-of-sample
                (Dec 2025 - Nov 2026).  Nothing is fitted on traded data.
* ``rolling`` - OLS hedge ratio, mean and sd from the last ``ROLL`` trading
                days, using data up to that day only; trades from the first
                day a full window exists.

Rules: signal on the close of day t, trade on the close of t+1.
Enter long spread at z <= -ENTRY, short spread at z >= +ENTRY.  The hedge
ratio, mean and sd are frozen at entry; exit when z crosses EXIT (0), at
|z| >= STOP, or after MAX_HOLD trading days.  Open trades are closed (with
costs) on the last day.

Sizing: 1 unit of gross notional per trade, split across legs in
proportion to |weight|; shares stay fixed while the trade is open.
Costs: ``COST`` per side on traded notional (commission + VAT + fees +
slippage) and ``BORROW`` a year on the short notional (SBL).  Prices are
Yahoo adjusted closes, so dividends are in the returns of both legs (a
short leg pays them).

Writes ``cointegration_output/backtest_<date>.xlsx`` and charts.
"""

import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import basket_deepdive as dd
import cointegration_set100 as base

warnings.simplefilter("ignore")

STRATEGIES = {
    "GPSC vs RATCH+SCC": ["GPSC", "RATCH", "SCC"],
    "GPSC / SCC": ["GPSC", "SCC"],
    "AWC vs CRC+GLOBAL": ["AWC", "CRC", "GLOBAL"],
    "AWC / CRC": ["AWC", "CRC"],
    "ERW / AWC": ["ERW", "AWC"],
}
ENTRY, EXIT, STOP = 2.0, 0.0, 4.0
MAX_HOLD = 60                 # trading days
ROLL = 250                    # rolling window, trading days
COST = 0.0015                 # per side, on traded notional
BORROW = 0.02                 # per year, on short notional
DAYS_PER_YEAR = 245
SENS_ENTRY = [1.5, 2.0, 2.5]
OUT_DIR = base.OUT_DIR

FORM = base.WINDOWS["formation"]
OOS = base.WINDOWS["validation"]


# ---------------------------------------------------------------------------
# Hedge parameters
# ---------------------------------------------------------------------------

def fixed_params(logp):
    form = logp.loc[FORM[0]:FORM[1]].values
    w, _, _ = dd.johansen_weights(form)
    s = form @ w
    params = pd.DataFrame(index=logp.index, dtype=object)
    params["w"] = [w] * len(logp)
    params["mu"], params["sd"] = s.mean(), s.std()
    return params


def rolling_params(logp, from_date=None):
    rows = {}
    values = logp.values
    first = ROLL - 1
    if from_date is not None:
        first = max(first, int(logp.index.searchsorted(from_date)) - 1)
    for t in range(first, len(logp)):
        win = values[t - ROLL + 1:t + 1]
        w, _ = dd.ols_weights(win)
        s = win @ w
        rows[logp.index[t]] = (w, s.mean(), s.std())
    return pd.DataFrame.from_dict(rows, orient="index",
                              columns=["w", "mu", "sd"])


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def backtest(prices, params, start, end, entry=ENTRY):
    """Run one strategy; returns (daily DataFrame, trades DataFrame)."""
    prices = prices.loc[start:end]
    params = params.reindex(prices.index).dropna()
    prices = prices.loc[params.index]
    dates = prices.index
    p = prices.values
    logp = np.log(p)

    daily = pd.DataFrame(0.0, index=dates,
                         columns=["gross", "cost", "borrow", "net",
                                  "position", "z_signal", "z_held"])
    trades = []
    pos = None              # open trade
    pending = None          # ("enter", dir) or ("exit", reason)

    for t in range(len(dates)):
        # 1) mark to market (from yesterday's close)
        if pos is not None and t > pos["entry_i"]:
            gross = (pos["shares"] * (p[t] - p[t - 1])).sum()
            short = -(pos["shares"] * p[t])[pos["shares"] < 0].sum()
            borrow = BORROW / DAYS_PER_YEAR * short
            daily.iat[t, 0] += gross
            daily.iat[t, 2] += borrow
            pos["pnl"] += gross
            pos["costs"] += borrow

        # 2) execute order decided yesterday, at today's close
        if pending is not None:
            if pending[0] == "enter":
                direction = pending[1]
                w, mu, sd = params.iloc[t - 1][["w", "mu", "sd"]]
                notional = direction * w / np.abs(w).sum()
                pos = dict(direction=direction, w=w, mu=mu, sd=sd,
                           shares=notional / p[t], entry_i=t,
                           entry_z=pending[2], pnl=0.0, costs=0.0)
                c = COST * np.abs(notional).sum()
                daily.iat[t, 1] += c
                pos["costs"] += c
            else:
                exit_value = np.abs(pos["shares"] * p[t]).sum()
                c = COST * exit_value
                daily.iat[t, 1] += c
                pos["costs"] += c
                trades.append(_close(pos, t, dates, pending[1]))
                pos = None
            pending = None

        # 3) signals on today's close
        w, mu, sd = params.iloc[t][["w", "mu", "sd"]]
        z_sig = (logp[t] @ w - mu) / sd
        daily.iat[t, 5] = z_sig
        last_day = t == len(dates) - 1
        if pos is not None:
            daily.iat[t, 4] = pos["direction"]
            z_held = (logp[t] @ pos["w"] - pos["mu"]) / pos["sd"]
            daily.iat[t, 6] = z_held
            held = t - pos["entry_i"]
            d = pos["direction"]
            if last_day:
                c = COST * np.abs(pos["shares"] * p[t]).sum()
                daily.iat[t, 1] += c
                pos["costs"] += c
                trades.append(_close(pos, t, dates, "end of data"))
                pos = None
            elif (d > 0 and z_held >= -EXIT) or (d < 0 and z_held <= EXIT):
                pending = ("exit", "target")
            elif abs(z_held) >= STOP:
                pending = ("exit", "stop")
            elif held >= MAX_HOLD:
                pending = ("exit", "time")
        elif not last_day and t < len(dates) - 1:
            if z_sig <= -entry:
                pending = ("enter", +1, z_sig)
            elif z_sig >= entry:
                pending = ("enter", -1, z_sig)
        if pos is None:
            daily.iat[t, 6] = np.nan

    daily["net"] = daily.gross - daily.cost - daily.borrow
    daily["equity"] = daily.net.cumsum()
    return daily, pd.DataFrame(trades)


def _close(pos, t, dates, reason):
    return {
        "entry_date": dates[pos["entry_i"]].date(),
        "exit_date": dates[t].date(),
        "side": "long spread" if pos["direction"] > 0 else "short spread",
        "entry_z": pos["entry_z"],
        "days": t - pos["entry_i"],
        "gross_ret": pos["pnl"],
        "costs": pos["costs"],
        "net_ret": pos["pnl"] - pos["costs"],
        "exit_reason": reason,
        "hedge": " ".join(f"{x:+.2f}" for x in pos["w"]),
    }


def metrics(daily, trades):
    net = daily.net
    eq = net.cumsum()
    dd_ = (eq - eq.cummax()).min()
    n = len(net)
    vol = net.std() * np.sqrt(DAYS_PER_YEAR)
    ann = net.mean() * DAYS_PER_YEAR
    closed = trades
    return {
        "start": daily.index[0].date(), "end": daily.index[-1].date(),
        "days": n,
        "trades": len(closed),
        "win_rate": (closed.net_ret > 0).mean() if len(closed) else np.nan,
        "avg_trade_net": closed.net_ret.mean() if len(closed) else np.nan,
        "avg_days_held": closed.days.mean() if len(closed) else np.nan,
        "exits_target": int((closed.exit_reason == "target").sum())
        if len(closed) else 0,
        "exits_stop": int((closed.exit_reason == "stop").sum())
        if len(closed) else 0,
        "exits_time": int((closed.exit_reason == "time").sum())
        if len(closed) else 0,
        "open_at_end": int((closed.exit_reason == "end of data").sum())
        if len(closed) else 0,
        "time_in_market": (daily.position != 0).mean(),
        "total_gross": daily.gross.sum(),
        "total_costs": (daily.cost + daily.borrow).sum(),
        "total_net": net.sum(),
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe": ann / vol if vol > 0 else np.nan,
        "max_drawdown": dd_,
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

LONG_C, SHORT_C = dd.SERIES[0], dd.SERIES[1]


def _z_panel(ax, daily, trades, title, entry=ENTRY):
    """z the rule acts on: signal z when flat, frozen-hedge z when in a trade.

    Markers sit on the signal day (trades execute at the next close).
    """
    dd._style(ax, title)
    z = daily.z_held.where(daily.position != 0, daily.z_signal).clip(-5, 5)
    in_pos = daily.position != 0
    ax.fill_between(daily.index, -5, 5, where=in_pos, color="#e3edf9",
                    step="mid", linewidth=0, zorder=0.5)
    for lv, ls in [(entry, "--"), (-entry, "--"), (STOP, ":"), (-STOP, ":")]:
        ax.axhline(lv, color="#9c9c9c", lw=0.9, ls=ls, zorder=1)
    ax.axhline(0, color="#bdbdbd", lw=0.8, zorder=1)
    ax.plot(daily.index, z, color="#4d4d4d", lw=1.1, zorder=2)
    dates = daily.index
    if len(trades):
        for side, color, marker in [("long spread", LONG_C, "^"),
                                    ("short spread", SHORT_C, "v")]:
            tr = trades[trades.side == side]
            x = [dates[dates.get_loc(pd.Timestamp(d)) - 1]
                 for d in tr.entry_date]
            ax.scatter(x, tr.entry_z.clip(-5, 5), marker=marker, s=80,
                       color=color, edgecolor="white", linewidth=1.5,
                       zorder=4, label=f"enter {side}")
        closed = trades[trades.exit_reason != "end of data"]
        x = [dates[dates.get_loc(pd.Timestamp(d)) - 1]
             for d in closed.exit_date]
        ax.scatter(x, z.reindex(x), marker="o", s=45, facecolor="white",
                   edgecolor="#4d4d4d", linewidth=1.4, zorder=4,
                   label="exit signal")
    ax.set_ylim(-5, 5)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=3,
              labelcolor=dd.TEXT)
    ax.text(0.995, 0.03, f"dashed = ±{entry:g} entry · dotted = ±{STOP:g} "
            "stop · blue band = in a trade", transform=ax.transAxes,
            ha="right", fontsize=8, color=dd.TEXT_2,
            bbox=dict(facecolor=dd.SURFACE, edgecolor="none", pad=1.5))


def _n(trades):
    return f"{trades} trade" + ("" if trades == 1 else "s")


def plot_strategy(name, res, path):
    fig, axes = plt.subplots(3, 1, figsize=(11, 10.5), sharex=True,
                             gridspec_kw={"hspace": 0.45,
                                          "height_ratios": [1.1, 1, 1]})
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.89)
    m_f = res["fixed"]["metrics"]
    m_r = res["rolling_oos"]["metrics"]
    fig.suptitle(name, x=0.06, y=0.985, va="top", ha="left", fontsize=15,
                 color=dd.TEXT, fontweight="bold")
    fig.text(0.06, 0.952, va="top", fontsize=10, color=dd.TEXT_2, s=(
        f"Out-of-sample Dec 2025 – Nov 2026, net of costs.  "
        f"Rolling hedge {m_r['total_net']:+.1%} ({_n(m_r['trades'])}, "
        f"Sharpe {m_r['sharpe']:.2f})  ·  fixed hedge "
        f"{m_f['total_net']:+.1%} ({_n(m_f['trades'])}, "
        f"Sharpe {m_f['sharpe']:.2f})"))

    ax = axes[0]
    dd._style(ax, "Cumulative net P&L, % of gross notional")
    for key, color, label in [("rolling_oos", dd.SERIES[0],
                               "rolling 250-day OLS hedge"),
                              ("fixed", dd.SERIES[1],
                               "fixed formation hedge")]:
        eq = res[key]["daily"].equity * 100
        ax.plot(eq.index, eq, color=color, lw=1.8, label=label)
        dd._end_label(ax, eq.index[-1], eq.iloc[-1], f"{eq.iloc[-1]:+.1f}%")
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2,
              labelcolor=dd.TEXT)

    _z_panel(axes[1], res["rolling_oos"]["daily"],
             res["rolling_oos"]["trades"], "Rolling hedge: z-score and trades")
    _z_panel(axes[2], res["fixed"]["daily"], res["fixed"]["trades"],
             "Fixed hedge: z-score and trades")
    axes[-1].set_xlim(OOS[0], OOS[1] + pd.Timedelta(days=28))
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


def plot_overview(summary, path):
    oos = summary[summary.period == "out-of-sample"]
    names = list(STRATEGIES)
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.8)
    fig.suptitle("Out-of-sample net return by strategy", x=0.06, y=0.97,
                 va="top", ha="left", fontsize=15, color=dd.TEXT,
                 fontweight="bold")
    fig.text(0.06, 0.895, va="top", fontsize=10, color=dd.TEXT_2,
             s="Dec 2025 – Nov 2026, % of gross notional, after costs "
               f"({COST:.2%}/side, borrow {BORROW:.0%} p.a.). "
               "Label = net return · trades · Sharpe")
    y = np.arange(len(names))
    h = 0.36
    for k, (method, color) in enumerate([("rolling", dd.SERIES[0]),
                                         ("fixed", dd.SERIES[1])]):
        sub = oos[oos.method == method].set_index("strategy").reindex(names)
        vals = sub.total_net.values * 100
        pos = y + (k - 0.5) * h
        ax.barh(pos, vals, height=h - 0.04, color=color,
                label=f"{method} hedge", zorder=2)
        for yy, v, tr, sh in zip(pos, vals, sub.trades, sub.sharpe):
            ax.annotate(f"{v:+.1f}% · {int(tr)} tr · {sh:.2f}",
                        (v, yy), xytext=(5 if v >= 0 else -5, 0),
                        textcoords="offset points", va="center",
                        ha="left" if v >= 0 else "right",
                        fontsize=8.5, color=dd.TEXT)
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.axvline(0, color="#9c9c9c", lw=0.9)
    ax.set_facecolor(dd.SURFACE)
    ax.grid(axis="x", color=dd.GRID, lw=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#bdbdbd")
    ax.tick_params(colors=dd.TEXT_2, labelsize=9.5, length=0)
    ax.tick_params(axis="y", colors=dd.TEXT)
    lim = max(abs(oos.total_net).max() * 100 * 1.6, 5)
    ax.set_xlim(-lim, lim)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    ax.legend(loc="lower right", frameon=False, fontsize=9,
              labelcolor=dd.TEXT)
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


def plot_placebo(traded, pick, path):
    fig, ax = plt.subplots(figsize=(10, 4.6))
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.78)
    fig.suptitle("Our picks vs the same rule on every SET100 pair",
                 x=0.06, y=0.97, va="top", ha="left", fontsize=15,
                 color=dd.TEXT, fontweight="bold")
    fig.text(0.06, 0.885, va="top", fontsize=10, color=dd.TEXT_2, s=(
        f"Rolling hedge, out-of-sample Dec 2025 – Nov 2026, net of costs. "
        f"{len(traded):,} pairs with at least one trade; "
        f"median {traded.total_net.median():+.1%}, "
        f"{(traded.total_net > 0).mean():.0%} positive."))
    vals = traded.total_net.clip(-0.6, 0.6) * 100
    ax.hist(vals, bins=60, color="#b6d5f8", edgecolor=dd.SURFACE,
            linewidth=0.6, zorder=2)
    ax.axvline(traded.total_net.median() * 100, color="#666666", lw=1,
               ls="--", zorder=3)
    top = ax.get_ylim()[1]
    for i, (_, r) in enumerate(pick.sort_values("total_net").iterrows()):
        x = r.total_net * 100
        ax.axvline(x, color=dd.SERIES[1], lw=1.4, zorder=3)
        ax.annotate(f"{r.strategy}  {x:+.1f}% (p{r.percentile_vs_all_pairs:.0%})",
                    (x, top * (0.95 - 0.11 * i)), xytext=(4, 0),
                    textcoords="offset points", fontsize=8.5,
                    color=dd.TEXT, va="center", zorder=6,
                    bbox=dict(facecolor=dd.SURFACE, edgecolor="none",
                              pad=1.5))
    ax.set_facecolor(dd.SURFACE)
    ax.grid(axis="y", color=dd.GRID, lw=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#bdbdbd")
    ax.tick_params(colors=dd.TEXT_2, labelsize=9, length=0)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    ax.set_xlabel("net return, % of gross notional (clipped at ±60%)",
                  color=dd.TEXT_2, fontsize=9)
    ax.set_ylabel("pairs", color=dd.TEXT_2, fontsize=9)
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Placebo: same rolling rule on every SET100 pair
# ---------------------------------------------------------------------------

def _placebo_pair(args):
    warnings.simplefilter("ignore")
    a, b, prices = args
    logp = np.log(prices)
    par = rolling_params(logp, from_date=OOS[0])
    daily, trades = backtest(prices, par, OOS[0], OOS[1])
    m = metrics(daily, trades)
    return {"stock_a": a, "stock_b": b, "trades": m["trades"],
            "total_net": m["total_net"], "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"]}


def placebo(close):
    """Rolling method, out-of-sample, on every pair of the screened universe."""
    from concurrent.futures import ProcessPoolExecutor
    import itertools

    window = close.loc[FORM[0]:OOS[1]]
    ok = window.notna().mean() >= base.MIN_COVERAGE
    stocks = [s for s in window.columns[ok] if s not in base.EXCLUDE]
    jobs = [(a, b, close[[a, b]].dropna())
            for a, b in itertools.combinations(stocks, 2)]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(_placebo_pair, jobs, chunksize=20))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------

def main():
    close = base.clean_prices(pd.read_csv(OUT_DIR / "set100_close.csv",
                                          index_col=0, parse_dates=True))
    summary, all_trades, sens, daily_out = [], [], [], {}
    first_roll = None
    for name, stocks in STRATEGIES.items():
        prices = close[stocks].dropna()
        logp = np.log(prices)
        par = {"fixed": fixed_params(logp), "rolling": rolling_params(logp)}
        first_roll = par["rolling"].index[0]
        runs = {
            "fixed": (par["fixed"], OOS[0], OOS[1], "out-of-sample"),
            "rolling_oos": (par["rolling"], OOS[0], OOS[1], "out-of-sample"),
            "rolling": (par["rolling"], first_roll, OOS[1], "full rolling"),
        }
        res = {}
        for key, (pr, a, b, period) in runs.items():
            daily, trades = backtest(prices, pr, a, b)
            m = metrics(daily, trades)
            method = "fixed" if key == "fixed" else "rolling"
            res[key] = dict(daily=daily, trades=trades, metrics=m)
            summary.append({"strategy": name, "method": method,
                            "period": period, **m})
            if len(trades):
                all_trades.append(trades.assign(strategy=name,
                                                method=method,
                                                period=period))
            daily_out[f"{name} | {method} | {period}"] = daily.equity
        for e in SENS_ENTRY:
            for key in ("fixed", "rolling_oos"):
                pr, a, b, _ = runs[key]
                daily, trades = backtest(prices, pr, a, b, entry=e)
                m = metrics(daily, trades)
                sens.append({"strategy": name,
                             "method": "fixed" if key == "fixed" else "rolling",
                             "entry_z": e, "trades": m["trades"],
                             "total_net": m["total_net"],
                             "sharpe": m["sharpe"],
                             "max_drawdown": m["max_drawdown"]})
        slug = name.replace(" / ", "_").replace(" vs ", "_vs_").replace("+", "_")
        plot_strategy(name, res, OUT_DIR / f"backtest_{slug}.png")

    summary = pd.DataFrame(summary)
    plac = placebo(close)
    traded = plac[plac.trades > 0]
    pick = summary[(summary.method == "rolling")
                   & (summary.period == "out-of-sample")]
    pick = pick.assign(percentile_vs_all_pairs=[
        (traded.total_net < v).mean() for v in pick.total_net])
    # Pairs chosen with formation data only (pair screen in-sample pass):
    # the honest out-of-sample test of the screening method itself.
    screen = pd.read_csv(OUT_DIR / f"cointegration_set100_{base.AS_OF:%Y%m%d}.csv")
    chosen = screen.loc[screen.pass_insample, ["stock_a", "stock_b"]]
    chosen = traded.merge(chosen, on=["stock_a", "stock_b"])
    placebo_summary = pd.DataFrame([
        ("in-sample-pass pairs (formation only) traded", len(chosen)),
        ("  their median net", chosen.total_net.median()),
        ("  their mean net", chosen.total_net.mean()),
        ("  their share positive", (chosen.total_net > 0).mean()),
        ("pairs tested", len(plac)),
        ("pairs with at least one trade", len(traded)),
        ("share with positive net", (traded.total_net > 0).mean()),
        ("median net", traded.total_net.median()),
        ("mean net", traded.total_net.mean()),
        ("25th percentile net", traded.total_net.quantile(0.25)),
        ("75th percentile net", traded.total_net.quantile(0.75)),
        ("median trades", traded.trades.median()),
    ], columns=["item", "value"])
    plot_placebo(traded, pick, OUT_DIR / "backtest_vs_all_pairs.png")
    trades = pd.concat(all_trades, ignore_index=True)
    sens = pd.DataFrame(sens)
    daily = pd.DataFrame(daily_out)
    plot_overview(summary, OUT_DIR / "backtest_overview.png")

    assumptions = pd.DataFrame([
        ("formation window", f"{FORM[0].date()} to {FORM[1].date()}"),
        ("out-of-sample window", f"{OOS[0].date()} to {OOS[1].date()}"),
        ("rolling window (trading days)", ROLL),
        ("fixed hedge", "Johansen weights, mean, sd from formation"),
        ("rolling hedge", "OLS on last ROLL days, data up to signal day"),
        ("entry |z|", ENTRY), ("exit z", EXIT), ("stop |z|", STOP),
        ("max hold (trading days)", MAX_HOLD),
        ("execution", "signal at close t, trade at close t+1"),
        ("sizing", "1 gross notional per trade, legs ∝ |weight|, "
                   "shares fixed while open"),
        ("cost per side", COST), ("borrow fee p.a.", BORROW),
        ("prices", "Yahoo adjusted close (dividends in returns)"),
        ("returns", "additive, % of gross notional; no compounding"),
    ], columns=["item", "value"])
    path = OUT_DIR / f"backtest_{base.AS_OF:%Y%m%d}.xlsx"
    pct = ["win_rate", "avg_trade_net", "time_in_market", "total_gross",
           "total_costs", "total_net", "ann_return", "ann_vol",
           "max_drawdown"]
    with pd.ExcelWriter(path) as xl:
        summary.round(4).to_excel(xl, sheet_name="Summary", index=False)
        trades.round(4).to_excel(xl, sheet_name="Trades", index=False)
        sens.round(4).to_excel(xl, sheet_name="Sensitivity", index=False)
        placebo_summary.to_excel(xl, sheet_name="All_Pairs_Check",
                                 index=False)
        pick[["strategy", "total_net", "percentile_vs_all_pairs"]].to_excel(
            xl, sheet_name="All_Pairs_Check", index=False, startcol=3)
        plac.round(4).to_excel(xl, sheet_name="All_Pairs_Rolling",
                               index=False)
        daily.round(5).to_excel(xl, sheet_name="Equity")
        assumptions.to_excel(xl, sheet_name="Assumptions", index=False)

    show = summary.copy()
    for c in pct:
        show[c] = (show[c] * 100).round(1)
    cols = ["strategy", "method", "period", "trades", "win_rate",
            "avg_days_held", "exits_target", "exits_stop", "exits_time",
            "open_at_end", "time_in_market", "total_gross", "total_costs",
            "total_net", "ann_vol", "sharpe", "max_drawdown"]
    pd.set_option("display.width", 250)
    print(show[cols].round(2).to_string(index=False))
    print("\nSensitivity (out-of-sample):")
    print(sens.assign(total_net=(sens.total_net * 100).round(1))
          .pivot_table(index=["strategy", "method"], columns="entry_z",
                       values="total_net").to_string())
    print("\nAll-pairs check (rolling, out-of-sample):")
    print(placebo_summary.to_string(index=False))
    print(pick[["strategy", "total_net", "percentile_vs_all_pairs"]]
          .round(3).to_string(index=False))
    print(f"\nWritten: {path}")


if __name__ == "__main__":
    main()
