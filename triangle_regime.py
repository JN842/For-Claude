"""Why did the ERW-AWC-CRC triangle start working in mid-2024, and is it overfit?

Four checks for the triangle rule (beta 120-day, z 20-day, no filter), all
with the engine and costs in ``basket_grid``:

1. Parameter plateau: portfolio net for beta window x z lookback, before
   and after 1 Jul 2024.  A real effect shows a smooth area, not a spike.
2. Walk-forward: pick the best rule on data before the split only, then
   look at what it earned after.
3. Regime: rolling 120-day correlation of daily returns between the three
   stocks, raw and after removing the market (equal-weight SET100 return,
   since Yahoo has no SET index history).  If the tourism names started
   to move together for their own reasons, the residual correlation rises.
4. All-pairs placebo: the same rule on every SET100 pair with 5 years of
   data.  Are the triangle pairs unusual, and did every pair do better
   after mid-2024 (a market-wide regime) or only these?

Writes cointegration_output/regime_<date>.xlsx and regime_*.png.
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

SPLIT = pd.Timestamp("2024-07-01")
RULE = (120, "20d", "none")
BETAS = [60, 90, 120, 180, 250]
ZS = ["10d", "15d", "20d", "30d", "40d", "60d", "model"]
CORR_WIN = 120
OUT_DIR = base.OUT_DIR


def regs_for(prices, windows):
    need = sorted(set(windows) | {120, 250})
    return {w: bg.rolling_ols(np.log(prices), w) for w in need}


def pair_daily(prices, regs, rule):
    daily, trades = bg.run(prices, regs, *rule)
    return daily, trades


# ---------------------------------------------------------------------------
# 1-2. Plateau and walk-forward on the triangle
# ---------------------------------------------------------------------------

def triangle_grid(prices_all):
    regs = {n: regs_for(prices_all[s].dropna(), BETAS)
            for n, s in tri.PAIRS.items()}
    rows, dailies = [], {}
    for b, z in itertools.product(BETAS, ZS):
        legs = {}
        for n, s in tri.PAIRS.items():
            legs[n], _ = bg.run(prices_all[s].dropna(), regs[n], b, z, "none")
        legs = pd.concat(legs, axis=1).fillna(0)
        combo = legs.mean(axis=1)
        dailies[(b, z)] = (combo, legs)
        pre, post = combo[combo.index < SPLIT], combo[combo.index >= SPLIT]
        rows.append({"beta": b, "z": z, "net_all": combo.sum(),
                     "net_pre": pre.sum(), "net_post": post.sum(),
                     "sharpe_pre": _sharpe(pre), "sharpe_post": _sharpe(post),
                     "pairs_positive_post": int((legs[legs.index >= SPLIT]
                                                 .sum() > 0).sum())})
    return pd.DataFrame(rows), dailies, regs


def _sharpe(x):
    return x.mean() / x.std() * np.sqrt(bg.DAYS_PER_YEAR) if x.std() > 0 else np.nan


# ---------------------------------------------------------------------------
# 3. Regime: correlations
# ---------------------------------------------------------------------------

def correlations(close_all):
    rets = np.log(close_all).diff()
    universe = [c for c in rets.columns
                if close_all[c].loc["2021-01-01":].notna().mean() >= 0.95]
    market = rets[universe].mean(axis=1)
    stocks = ["AWC", "ERW", "CRC"]
    r = rets[stocks]
    # market beta on a rolling 250-day window, using data up to each day
    resid = pd.DataFrame(index=r.index, columns=stocks, dtype=float)
    for s in stocks:
        cov = r[s].rolling(250).cov(market)
        var = market.rolling(250).var()
        resid[s] = r[s] - (cov / var) * market
    out = {}
    for name, df in [("raw", r), ("ex-market", resid)]:
        pairs = [df[a].rolling(CORR_WIN).corr(df[b])
                 for a, b in itertools.combinations(stocks, 2)]
        out[name] = pd.concat(pairs, axis=1).mean(axis=1)
    # the same ex-market correlation for every SET100 pair, as a baseline
    return pd.DataFrame(out), market, universe


def baseline_resid_corr(close_all, universe, market, n_pairs=400, seed=0):
    rets = np.log(close_all[universe]).diff()
    beta = rets.rolling(250).cov(market).div(market.rolling(250).var(), axis=0)
    resid = rets - beta.mul(market, axis=0)
    rng = np.random.default_rng(seed)
    pairs = [tuple(rng.choice(universe, 2, replace=False))
             for _ in range(n_pairs)]
    cs = [resid[a].rolling(CORR_WIN).corr(resid[b]) for a, b in pairs]
    return pd.concat(cs, axis=1).median(axis=1)


# ---------------------------------------------------------------------------
# 4. All-pairs placebo
# ---------------------------------------------------------------------------

def _placebo(args):
    warnings.simplefilter("ignore")
    a, b, prices = args
    regs = regs_for(prices, [RULE[0]])
    daily, trades = bg.run(prices, regs, *RULE)
    return {"stock_a": a, "stock_b": b, "trades": len(trades),
            "net_all": daily.sum(),
            "net_pre": daily[daily.index < SPLIT].sum(),
            "net_post": daily[daily.index >= SPLIT].sum()}


def placebo(close_all, universe):
    stocks = [s for s in universe if s not in base.EXCLUDE]
    jobs = [(a, b, close_all[[a, b]].dropna())
            for a, b in itertools.combinations(stocks, 2)]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(_placebo, jobs, chunksize=20))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def plot_heatmap(grid, path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.74, wspace=0.25)
    fig.suptitle("Is β120 / z20 a plateau or a spike?", x=0.06, y=0.97,
                 va="top", ha="left", fontsize=15, color=dd.TEXT,
                 fontweight="bold")
    fig.text(0.06, 0.885, va="top", fontsize=10, color=dd.TEXT_2,
             s="Triangle portfolio net return (% of capital, 1/3 per pair, "
               "no filter, real costs). Box = β120 / z20.")
    lim = np.nanmax(np.abs(grid[["net_pre", "net_post"]].values)) * 100
    for ax, col, title in [(axes[0], "net_pre", "Jan 2022 – Jun 2024"),
                           (axes[1], "net_post", "Jul 2024 – Nov 2026")]:
        m = grid.pivot(index="beta", columns="z", values=col).reindex(
            index=BETAS, columns=ZS) * 100
        ax.imshow(m.values, cmap="RdBu", vmin=-lim, vmax=lim, aspect="auto")
        ax.set_xticks(range(len(ZS)), ZS)
        ax.set_yticks(range(len(BETAS)), [f"β{b}" for b in BETAS])
        for i in range(len(BETAS)):
            for j in range(len(ZS)):
                v = m.values[i, j]
                ax.text(j, i, f"{v:+.0f}", ha="center", va="center",
                        fontsize=9,
                        color="white" if abs(v) > lim * 0.55 else dd.TEXT)
        i, j = BETAS.index(RULE[0]), ZS.index(RULE[1])
        ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False,
                                   edgecolor=dd.TEXT, lw=2))
        ax.set_title(title, loc="left", fontsize=11, color=dd.TEXT,
                     fontweight="bold")
        ax.tick_params(colors=dd.TEXT_2, labelsize=9, length=0)
        for side in ax.spines.values():
            side.set_visible(False)
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


def plot_regime(corr, base_corr, combo, legs, plac_half, path):
    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True,
                             gridspec_kw={"hspace": 0.45})
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.89)
    fig.suptitle("What changed in mid-2024?", x=0.06, y=0.985, va="top",
                 ha="left", fontsize=15, color=dd.TEXT, fontweight="bold")
    fig.text(0.06, 0.955, va="top", fontsize=10, color=dd.TEXT_2,
             s="Grey line = 1 Jul 2024. Market = equal-weight SET100 daily "
               "return (Yahoo has no SET index history).")

    def style(ax, title):
        dd._style(ax, title)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
        ax.axvline(SPLIT, color="#9c9c9c", lw=1)

    show = corr.index >= bg.START
    ax = axes[0]
    style(ax, f"Average pairwise correlation of daily returns, "
              f"{CORR_WIN}-day rolling")
    for (name, s), color in zip(corr.items(), dd.SERIES):
        s = s[show]
        ax.plot(s.index, s, color=color, lw=1.6,
                label=f"AWC/ERW/CRC {name}")
        dd._end_label(ax, s.index[-1], s.iloc[-1], f"{s.iloc[-1]:.2f}")
    b = base_corr[show]
    ax.plot(b.index, b, color="#9c9c9c", lw=1.2, ls="--",
            label="random SET100 pairs, ex-market (median)")
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=3,
              labelcolor=dd.TEXT)

    ax = axes[1]
    style(ax, "Triangle portfolio (β120 / z20), cumulative net %")
    for n in tri.PAIRS:
        c = legs[n].cumsum() / 3 * 100
        ax.plot(c.index, c, color=tri.PAIR_COLORS[n], lw=1.0, label=n)
    eq = combo.cumsum() * 100
    ax.plot(eq.index, eq, color=dd.TEXT, lw=2, label="portfolio")
    dd._end_label(ax, eq.index[-1], eq.iloc[-1], f"{eq.iloc[-1]:+.1f}%")
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=4,
              labelcolor=dd.TEXT)

    ax = axes[2]
    style(ax, "Same rule on every SET100 pair: median net per half-year "
              "(%) vs triangle pairs")
    x = plac_half.index
    ax.bar(x, plac_half["all_pairs_median"] * 100, width=120,
           color="#b6d5f8", label="all SET100 pairs (median)", zorder=2)
    ax.plot(x, plac_half["triangle_mean"] * 100, color=dd.TEXT, lw=1.8,
            marker="o", ms=5, label="triangle pairs (mean)", zorder=3)
    ax.axhline(0, color="#9c9c9c", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2,
              labelcolor=dd.TEXT)
    axes[-1].set_xlim(bg.START - pd.Timedelta(days=5),
                      corr.index[-1] + pd.Timedelta(days=60))
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------

def main():
    prices_all = tri.load()
    close_all = pd.read_csv(OUT_DIR / "set100_close_6y.csv", index_col=0,
                            parse_dates=True).drop(columns=["^SET"],
                                                   errors="ignore")
    close_all = close_all.ffill(limit=5)

    grid, dailies, _ = triangle_grid(prices_all)
    combo, legs = dailies[(RULE[0], RULE[1])]

    # walk-forward: choose on pre-split only
    best_pre = grid.sort_values("net_pre", ascending=False).iloc[0]
    rank_rule_pre = int(grid.net_pre.rank(ascending=False)[
        (grid.beta == RULE[0]) & (grid.z == RULE[1])].iloc[0])

    corr, market, universe = correlations(close_all)
    base_corr = baseline_resid_corr(close_all, universe, market)

    plac = placebo(close_all, universe)
    tri_names = {tuple(sorted(s)) for s in tri.PAIRS.values()}
    plac["triangle"] = [tuple(sorted((a, b))) in tri_names
                        for a, b in zip(plac.stock_a, plac.stock_b)]
    tri_rows = plac[plac.triangle]
    pct = lambda v, col: (plac[col] < v).mean()
    tri_rows = tri_rows.assign(pct_all=[pct(v, "net_all") for v in tri_rows.net_all],
                               pct_post=[pct(v, "net_post") for v in tri_rows.net_post])

    # half-year P&L: all pairs median vs triangle pairs
    stocks = [s for s in universe if s not in base.EXCLUDE]
    # recompute daily for a sample of pairs to get half-year medians
    rng = np.random.default_rng(1)
    sample = [tuple(rng.choice(stocks, 2, replace=False)) for _ in range(300)]
    with ProcessPoolExecutor() as pool:
        dailies_s = list(pool.map(_daily_only, [(a, b, close_all[[a, b]].dropna())
                                                for a, b in sample], chunksize=10))
    sample_df = pd.concat(dailies_s, axis=1).fillna(0)
    half = lambda s: s.groupby([s.index.year, (s.index.month > 6) + 1]).sum()
    all_half = sample_df.apply(half).median(axis=1)
    tri_half = half(legs.mean(axis=1) * 1)  # mean of the three pair P&Ls
    plac_half = pd.DataFrame({"all_pairs_median": all_half,
                              "triangle_mean": tri_half})
    plac_half.index = [pd.Timestamp(y, 3 if h == 1 else 9, 15)
                       for y, h in plac_half.index]

    plot_heatmap(grid, OUT_DIR / "regime_heatmap.png")
    plot_regime(corr, base_corr, combo, legs, plac_half,
                OUT_DIR / "regime_story.png")

    corr_half = corr.groupby([corr.index.year, (corr.index.month > 6) + 1]).mean()
    trades = pd.concat({n: bg.run(prices_all[s].dropna(),
                                  regs_for(prices_all[s].dropna(), [120]),
                                  *RULE)[1] for n, s in tri.PAIRS.items()})
    path = OUT_DIR / f"regime_{base.AS_OF:%Y%m%d}.xlsx"
    with pd.ExcelWriter(path) as xl:
        grid.round(4).to_excel(xl, sheet_name="Plateau_grid", index=False)
        tri_rows.round(4).to_excel(xl, sheet_name="Triangle_vs_all", index=False)
        plac.round(4).to_excel(xl, sheet_name="All_pairs", index=False)
        plac_half.round(4).to_excel(xl, sheet_name="Half_year")
        corr_half.round(3).to_excel(xl, sheet_name="Corr_half_year")
        trades.round(4).to_excel(xl, sheet_name="Rule_trades")

    pd.set_option("display.width", 250)
    g = grid.assign(**{c: (grid[c] * 100).round(1)
                       for c in ["net_all", "net_pre", "net_post"]})
    print(g.pivot(index="beta", columns="z", values="net_pre").reindex(columns=ZS))
    print(g.pivot(index="beta", columns="z", values="net_post").reindex(columns=ZS))
    print("best rule on pre-split data:", best_pre[["beta", "z"]].to_dict(),
          "pre", round(best_pre.net_pre * 100, 1), "post",
          round(best_pre.net_post * 100, 1))
    print("rank of beta120/z20 on pre-split data:", rank_rule_pre, "of", len(grid))
    print("share of grid cells positive pre / post:",
          round((grid.net_pre > 0).mean(), 2), round((grid.net_post > 0).mean(), 2))
    print("\nAll pairs (rule beta120/z20):", len(plac), "pairs")
    for col in ["net_all", "net_pre", "net_post"]:
        print(f"  {col}: median {plac[col].median()*100:.1f}%  "
              f"share>0 {(plac[col] > 0).mean():.0%}")
    print(tri_rows[["stock_a", "stock_b", "trades", "net_all", "net_pre",
                    "net_post", "pct_all", "pct_post"]].round(3).to_string(index=False))
    print("\nhalf-year:\n", (plac_half * 100).round(1).to_string())
    print("\ncorrelation by half-year:\n", corr_half.round(2).to_string())
    print(f"Written: {path}")


def _daily_only(args):
    warnings.simplefilter("ignore")
    a, b, prices = args
    regs = regs_for(prices, [RULE[0]])
    daily, _ = bg.run(prices, regs, *RULE)
    return daily.rename(f"{a}/{b}")


if __name__ == "__main__":
    main()
