"""When should the pair strategy stop trading?  Test candidate stop signals.

Strategy: beta 120-day, z 20-day, no filter (the triangle rule), with the
engine and real costs in ``basket_grid``.

Candidate signals, each computed with data up to the signal day only:

* ``corr``    - 120-day correlation of the two stocks' market-adjusted
                daily returns (market = equal-weight SET100; beta to it on
                a rolling 250-day window).  Low = no common driver left.
* ``diverge`` - |60-day market-adjusted return of Y - that of X|.  High =
                one stock is running on its own story.
* ``equity``  - the strategy's own net P&L on this pair over the last 120
                trading days.  Negative = it has stopped working here.

Test 1 (does the signal separate winners from losers?): every trade of the
ungated rule on every SET100 pair with 5 years of data, bucketed by the
signal's value on the trade's signal day.  Thresholds are judged on this
whole universe, not on the triangle, to avoid tuning to it.

Test 2 (does gating help?): rerun with entries allowed only while the
signal says "on", for all pairs and for the triangle portfolio.

Writes cointegration_output/stop_rules_<date>.xlsx and stop_rules.png.
"""

import itertools
import warnings
from concurrent.futures import ProcessPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import basket_deepdive as dd
import basket_grid as bg
import cointegration_set100 as base
import triangle as tri

warnings.simplefilter("ignore")

RULE = (120, "20d", "none")
CORR_WIN, DIV_WIN, EQ_WIN = 120, 60, 120
GATES = {
    "none": None,
    "corr >= 0.05": ("corr", 0.05),
    "diverge <= 15%": ("diverge", 0.15),
    "equity120 >= 0": ("equity", 0.0),
    "corr >= 0.05 & diverge <= 15%": ("corr+diverge", (0.05, 0.15)),
}
SPLIT = pd.Timestamp("2024-07-01")
OUT_DIR = base.OUT_DIR

_RESID = None


def market_resid(close):
    rets = np.log(close).diff()
    universe = [c for c in rets.columns
                if close[c].loc["2021-01-01":].notna().mean() >= 0.95]
    market = rets[universe].mean(axis=1)
    beta = rets.rolling(250).cov(market).div(market.rolling(250).var(), axis=0)
    return rets - beta.mul(market, axis=0), universe


def indicators(resid, a, b, index):
    ra, rb = resid[a].reindex(index), resid[b].reindex(index)
    corr = ra.rolling(CORR_WIN).corr(rb)
    div = (ra.rolling(DIV_WIN).sum() - rb.rolling(DIV_WIN).sum()).abs()
    return corr, div


def make_gate(kind, corr, div, shadow_daily):
    if kind is None:
        return None
    name, thr = kind
    if name == "corr":
        g = corr >= thr
    elif name == "diverge":
        g = div <= thr
    elif name == "equity":
        eq = shadow_daily.reindex(corr.index).fillna(0).rolling(EQ_WIN).sum()
        g = eq >= thr
    else:
        g = (corr >= thr[0]) & (div <= thr[1])
    return g.fillna(False).values


def run_pair(args):
    """Ungated trades with indicator values, plus gated results."""
    warnings.simplefilter("ignore")
    a, b, prices, resid = args
    regs = {w: bg.rolling_ols(np.log(prices), w) for w in (120, 250)}
    daily, trades = bg.run(prices, regs, *RULE)
    corr, div = indicators(resid, a, b, prices.index)
    eq = daily.reindex(prices.index).fillna(0).rolling(EQ_WIN).sum()
    if len(trades):
        sig = pd.to_datetime(trades.signal)
        trades = trades.assign(pair=f"{a}/{b}", corr=corr.reindex(sig).values,
                               diverge=div.reindex(sig).values,
                               # equity known at the signal day (excludes the
                               # trade itself, which starts the next day)
                               equity=eq.reindex(sig).values)
    gated = {}
    for gname, kind in GATES.items():
        if kind is None:
            d = daily
        else:
            d, _ = bg.run(prices, regs, *RULE,
                          gate=make_gate(kind, corr, div, daily))
        gated[gname] = d
    return a, b, trades, gated


def bucket_table(trades, col, edges, labels):
    t = trades.dropna(subset=[col]).copy()
    t["bucket"] = pd.cut(t[col], edges, labels=labels)
    g = t.groupby("bucket", observed=True).net
    return pd.DataFrame({"trades": g.size(), "avg_net_%": g.mean() * 100,
                         "win_rate": g.apply(lambda x: (x > 0).mean())})


def main():
    close = pd.read_csv(OUT_DIR / "set100_close_6y.csv", index_col=0,
                        parse_dates=True).drop(columns=["^SET"],
                                               errors="ignore").ffill(limit=5)
    resid, universe = market_resid(close)
    stocks = [s for s in universe if s not in base.EXCLUDE]

    # all SET100 pairs (Y = first alphabetically)
    jobs = [(a, b, close[[a, b]].dropna(), resid[[a, b]])
            for a, b in itertools.combinations(stocks, 2)]
    with ProcessPoolExecutor() as pool:
        res = list(pool.map(run_pair, jobs, chunksize=10))
    all_trades = pd.concat([r[2] for r in res if len(r[2])], ignore_index=True)

    # triangle pairs with their own orientation (same as triangle.py)
    tri_res = {}
    for name, (y, x) in tri.PAIRS.items():
        pr = tri.load()[[y, x]].dropna()
        tri_res[name] = run_pair((y, x, pr, resid[[y, x]]))
    tri_trades = pd.concat([r[2] for r in tri_res.values()], ignore_index=True)

    corr_edges = [-1, 0, 0.05, 0.1, 0.2, 1]
    corr_lab = ["< 0", "0–0.05", "0.05–0.1", "0.1–0.2", "> 0.2"]
    div_edges = [0, 0.05, 0.10, 0.15, 0.20, 0.30, 5]
    div_lab = ["0–5%", "5–10%", "10–15%", "15–20%", "20–30%", "> 30%"]
    eq_edges = [-5, -0.05, 0, 0.05, 5]
    eq_lab = ["< −5%", "−5–0%", "0–5%", "> 5%"]
    tables = {}
    for scope, tr in [("all_pairs", all_trades), ("triangle", tri_trades)]:
        tables[(scope, "corr")] = bucket_table(tr, "corr", corr_edges, corr_lab)
        tables[(scope, "diverge")] = bucket_table(tr, "diverge", div_edges, div_lab)
        tables[(scope, "equity")] = bucket_table(tr, "equity", eq_edges, eq_lab)

    # gated results
    rows = []
    for gname in GATES:
        nets = pd.Series({f"{a}/{b}": g[gname].sum() for a, b, _, g in res})
        tri_combo = pd.concat({n: r[3][gname] for n, r in tri_res.items()},
                              axis=1).fillna(0).mean(axis=1)
        eq = tri_combo.cumsum()
        rows.append({
            "gate": gname,
            "all_pairs_mean_net": nets.mean(),
            "all_pairs_median_net": nets.median(),
            "all_pairs_share_positive": (nets > 0).mean(),
            "triangle_net": tri_combo.sum(),
            "triangle_pre_split": tri_combo[tri_combo.index < SPLIT].sum(),
            "triangle_post_split": tri_combo[tri_combo.index >= SPLIT].sum(),
            "triangle_max_dd": (eq - eq.cummax()).min(),
        })
    gate_summary = pd.DataFrame(rows)

    # current indicator values for the triangle
    now = []
    for name, (y, x) in tri.PAIRS.items():
        pr = tri.load()[[y, x]].dropna()
        corr, div = indicators(resid, y, x, pr.index)
        d = tri_res[name][3]["none"]
        now.append({"pair": name, "corr120_exmkt": corr.iloc[-1],
                    "diverge60": div.iloc[-1],
                    "y_exmkt_60d": resid[y].iloc[-DIV_WIN:].sum(),
                    "x_exmkt_60d": resid[x].iloc[-DIV_WIN:].sum(),
                    "equity120": d.iloc[-EQ_WIN:].sum()})
    now = pd.DataFrame(now)

    plot(tri_res, resid, OUT_DIR / "stop_rules.png")

    path = OUT_DIR / f"stop_rules_{base.AS_OF:%Y%m%d}.xlsx"
    with pd.ExcelWriter(path) as xl:
        gate_summary.round(4).to_excel(xl, sheet_name="Gate_summary", index=False)
        r = 0
        for (scope, ind), tb in tables.items():
            pd.DataFrame([[f"{scope} · {ind}"]]).to_excel(
                xl, sheet_name="Buckets", startrow=r, index=False, header=False)
            tb.round(3).to_excel(xl, sheet_name="Buckets", startrow=r + 1)
            r += len(tb) + 4
        now.round(4).to_excel(xl, sheet_name="Now", index=False)
        tri_trades.round(4).to_excel(xl, sheet_name="Triangle_trades", index=False)

    pd.set_option("display.width", 250)
    print(f"all-pairs trades: {len(all_trades):,}  triangle trades: {len(tri_trades)}")
    for (scope, ind), tb in tables.items():
        print(f"\n{scope} · {ind}\n", tb.round(2).to_string())
    g = gate_summary.copy()
    for c in g.columns[1:]:
        if c != "all_pairs_share_positive":
            g[c] = (g[c] * 100).round(1)
    print("\n", g.round(2).to_string(index=False))
    print("\n", now.round(3).to_string(index=False))
    print(f"Written: {path}")


def plot(tri_res, resid, path):
    fig, axes = plt.subplots(3, 1, figsize=(11, 10.5), sharex=True,
                             gridspec_kw={"hspace": 0.45})
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.89)
    fig.suptitle("Stop signals on the triangle pairs", x=0.06, y=0.985,
                 va="top", ha="left", fontsize=15, color=dd.TEXT,
                 fontweight="bold")
    fig.text(0.06, 0.955, va="top", fontsize=10, color=dd.TEXT_2,
             s="Each line is one pair. Dashed = threshold. Grey line = "
               "1 Jul 2024.")

    def style(ax, title):
        dd._style(ax, title)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
        ax.axvline(SPLIT, color="#9c9c9c", lw=1)

    for name, (y, x) in tri.PAIRS.items():
        pr = tri.load()[[y, x]].dropna()
        corr, div = indicators(resid, y, x, pr.index)
        eq = tri_res[name][3]["none"].reindex(pr.index).fillna(0) \
            .rolling(EQ_WIN).sum()
        show = pr.index >= bg.START
        color = tri.PAIR_COLORS[name]
        axes[0].plot(pr.index[show], corr[show], color=color, lw=1.3, label=name)
        axes[1].plot(pr.index[show], div[show] * 100, color=color, lw=1.3,
                     label=name)
        axes[2].plot(pr.index[show], eq[show] * 100, color=color, lw=1.3,
                     label=name)
    style(axes[0], f"Market-adjusted return correlation, {CORR_WIN}-day "
                   "(stop below 0.05)")
    axes[0].axhline(0.05, color="#dc3c3d", lw=1, ls="--")
    style(axes[1], f"Divergence: |{DIV_WIN}-day market-adjusted return gap|, "
                   "% (stop above 15%)")
    axes[1].axhline(15, color="#dc3c3d", lw=1, ls="--")
    style(axes[2], f"Strategy's own net P&L over the last {EQ_WIN} days, % "
                   "(stop below 0)")
    axes[2].axhline(0, color="#dc3c3d", lw=1, ls="--")
    for ax in axes:
        ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=3,
                  labelcolor=dd.TEXT)
    axes[-1].set_xlim(bg.START - pd.Timedelta(days=5),
                      pd.Timestamp("2026-12-31"))
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    main()
