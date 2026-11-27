"""ERW - AWC - CRC triangle: the three pairs, their portfolio, consistency.

Uses the engine in ``basket_grid`` (daily closes Jan 2022 - Nov 2026,
signal close t / trade close t+1, 0.6% per round trip):

1. Diagnostics for the three pairs: prices, beta 250, HL250, z (beta 250
   model), and triangle consistency: the direct AWC~CRC beta against the
   beta implied through ERW (beta AWC~ERW x beta ERW~CRC).
2. Grid (beta window x z method x filter) for each pair and for the
   3-stock basket AWC vs ERW+CRC.
3. Portfolio: the same rule on all three pairs, 1/3 of capital each;
   rules ranked by their weakest pair so one lucky pair does not carry it.
4. Current status and which stock is out of line.

Writes cointegration_output/triangle_<date>.xlsx and triangle_*.png.
"""

import itertools
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import basket_deepdive as dd
import basket_grid as bg
import cointegration_set100 as base

warnings.simplefilter("ignore")

PAIRS = {
    "AWC / ERW": ["AWC", "ERW"],
    "AWC / CRC": ["AWC", "CRC"],
    "ERW / CRC": ["ERW", "CRC"],
}
BASKET = {"AWC vs ERW+CRC": ["AWC", "ERW", "CRC"]}
TOP_RULES = 5
OUT_DIR = base.OUT_DIR
PAIR_COLORS = dict(zip(PAIRS, dd.SERIES))


def load():
    return pd.read_csv(OUT_DIR / "prices_5y.csv", index_col=0,
                       parse_dates=True).ffill(limit=5)


def grid_for(name, prices):
    regs = {w: bg.rolling_ols(np.log(prices), w) for w in bg.BETA_WINDOWS}
    rows, dailies, trades = [], {}, []
    for w, m, f in itertools.product(bg.BETA_WINDOWS, bg.Z_METHODS,
                                     bg.FILTERS):
        daily, tr = bg.run(prices, regs, w, m, f)
        rows.append({"strategy": name, "beta": w, "z": m, "filter": f,
                     **bg.summarise(daily, tr)})
        dailies[(w, m, f)] = daily
        if len(tr):
            trades.append(tr.assign(strategy=name, beta=w, z=m, filter=f))
    return rows, dailies, trades, regs


def portfolio(dailies_by_pair):
    """Same rule on every pair, 1/3 capital each; one row per rule."""
    rows, series = [], {}
    rules = next(iter(dailies_by_pair.values())).keys()
    for rule in rules:
        legs = pd.concat({p: d[rule] for p, d in dailies_by_pair.items()},
                         axis=1).fillna(0)
        combo = legs.mean(axis=1)
        eq = combo.cumsum()
        per_pair = legs.sum()
        row = {"beta": rule[0], "z": rule[1], "filter": rule[2],
               "net_total": combo.sum(),
               "max_dd": (eq - eq.cummax()).min(),
               "ann_vol": combo.std() * np.sqrt(245),
               "sharpe": combo.mean() / combo.std() * np.sqrt(245)
               if combo.std() > 0 else np.nan,
               "weakest_pair": per_pair.min(),
               "pairs_positive": int((per_pair > 0).sum()),
               "pnl_corr_avg": legs.corr().values[np.triu_indices(3, 1)].mean()}
        for p, v in per_pair.items():
            row[f"net_{p}"] = v
        for year, v in combo.groupby(combo.index.year).sum().items():
            row[str(year)] = v
        rows.append(row)
        series[rule] = (combo, legs)
    out = pd.DataFrame(rows).sort_values(["pairs_positive", "weakest_pair"],
                                         ascending=False)
    return out, series


def implied_beta(regs_by_pair):
    b_ae = regs_by_pair["AWC / ERW"][250]["beta"][:, 0]
    b_ec = regs_by_pair["ERW / CRC"][250]["beta"][:, 0]
    b_ac = regs_by_pair["AWC / CRC"][250]["beta"][:, 0]
    return b_ac, b_ae * b_ec


def status(prices_all, regs_by_pair):
    rows = []
    for name, stocks in PAIRS.items():
        pr = prices_all[stocks].dropna()
        lv = np.log(pr.values)
        t = len(pr) - 1
        regs = regs_by_pair[name]
        row = {"pair": name}
        for w in bg.BETA_WINDOWS:
            r = regs[w]
            row[f"beta{w}"] = r["beta"][t][0]
            for m in bg.Z_METHODS:
                row[f"z_{m}_b{w}"] = bg.z_value(lv, t, r["alpha"][t],
                                                r["beta"][t], r["sd"][t], m)
        row["HL250"] = regs[250]["hl"][t]
        row["beta_gap_120_250"] = abs(regs[120]["beta"][t][0]
                                      - regs[250]["beta"][t][0])
        row["HL_open"] = row["HL250"] < bg.HL_MAX
        row["beta_agree_open"] = row["beta_gap_120_250"] <= bg.BETA_GAP
        res = bg.coint_johansen(lv[-250:], 0, 1)
        row["johansen250"] = float(np.real(res.lr1[0]))
        row["johansen250_crit"] = res.cvt[0, 1]
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _style(ax, title):
    dd._style(ax, title)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))


def plot_diag(prices_all, regs_by_pair, path):
    stocks = ["AWC", "ERW", "CRC"]
    logp = np.log(prices_all[stocks].dropna())
    idx = logp.index
    show = idx >= bg.START - pd.Timedelta(days=1)
    fig, axes = plt.subplots(5, 1, figsize=(11, 15), sharex=True,
                             gridspec_kw={"hspace": 0.45})
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.92)
    fig.suptitle("ERW – AWC – CRC triangle: 5-year diagnostics", x=0.06,
                 y=0.985, va="top", ha="left", fontsize=15, color=dd.TEXT,
                 fontweight="bold")
    fig.text(0.06, 0.962, va="top", fontsize=10, color=dd.TEXT_2,
             s="Daily adjusted closes, Jan 2022 – Nov 2026. "
               "Pairs: AWC on ERW, AWC on CRC, ERW on CRC (rolling OLS).")

    ax = axes[0]
    _style(ax, "Log price, rebased to 0 in Jan 2022")
    first = logp.loc[show].iloc[0]
    for i, s in enumerate(stocks):
        series = (logp[s] - first[s]).loc[show]
        ax.plot(series.index, series, color=dd.SERIES[i], lw=1.5, label=s)
        dd._end_label(ax, series.index[-1], series.iloc[-1], s)
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="lower left", frameon=False, fontsize=9, ncol=3,
              labelcolor=dd.TEXT)

    def pair_series(name, values):
        pr_idx = prices_all[PAIRS[name]].dropna().index
        return pd.Series(values, pr_idx).reindex(idx).loc[show]

    ax = axes[1]
    _style(ax, "Hedge ratio β, 250-day")
    for name in PAIRS:
        b = pair_series(name, regs_by_pair[name][250]["beta"][:, 0])
        ax.plot(b.index, b, color=PAIR_COLORS[name], lw=1.6, label=name)
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
              labelcolor=dd.TEXT)

    ax = axes[2]
    _style(ax, "Triangle check: AWC~CRC β direct vs implied through ERW "
               "(β AWC~ERW × β ERW~CRC)")
    direct, implied = implied_beta(regs_by_pair)
    d = pair_series("AWC / CRC", direct)
    im = pair_series("AWC / CRC", implied)
    ax.plot(d.index, d, color=PAIR_COLORS["AWC / CRC"], lw=1.6,
            label="direct AWC~CRC")
    ax.plot(im.index, im, color="#4d4d4d", lw=1.2, ls="--",
            label="implied via ERW")
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2,
              labelcolor=dd.TEXT)

    ax = axes[3]
    _style(ax, "Half-life of the 250-day residual (HL250, days, capped at 60)")
    for name in PAIRS:
        hl = pair_series(name, regs_by_pair[name][250]["hl"]).clip(upper=60)
        ax.plot(hl.index, hl, color=PAIR_COLORS[name], lw=1.4, label=name)
    ax.axhline(bg.HL_MAX, color="#dc3c3d", lw=1, ls="--")
    ax.set_ylim(0, 62)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
              labelcolor=dd.TEXT)
    ax.text(0.995, 0.95, "dashed red = 10-day filter", transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color=dd.TEXT_2,
            bbox=dict(facecolor=dd.SURFACE, edgecolor="none", pad=1.5))

    ax = axes[4]
    _style(ax, "z-score (β 250-day, model)")
    for name in PAIRS:
        pr = prices_all[PAIRS[name]].dropna()
        lv = np.log(pr.values)
        r = regs_by_pair[name][250]
        z = [bg.z_value(lv, i, r["alpha"][i], r["beta"][i], r["sd"][i],
                        "model") if not np.isnan(r["sd"][i]) else np.nan
             for i in range(len(pr))]
        z = pair_series(name, z).clip(-5, 5)
        ax.plot(z.index, z, color=PAIR_COLORS[name], lw=1.0, label=name)
        dd._end_label(ax, z.index[-1], z.iloc[-1], f"{z.iloc[-1]:+.2f}")
    for lv_ in (2, -2):
        ax.axhline(lv_, color="#9c9c9c", lw=0.9, ls="--")
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
              labelcolor=dd.TEXT)
    axes[-1].set_xlim(bg.START - pd.Timedelta(days=5),
                      idx[-1] + pd.Timedelta(days=60))
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


def plot_portfolio(port, series, path):
    top = port.head(3)
    fig, axes = plt.subplots(len(top), 1, figsize=(11, 3.6 * len(top) + 1),
                             sharex=True, gridspec_kw={"hspace": 0.55})
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.86 if len(top) > 1 else 0.8)
    fig.suptitle("Triangle portfolio: same rule on all three pairs",
                 x=0.06, y=0.985, va="top", ha="left", fontsize=15,
                 color=dd.TEXT, fontweight="bold")
    fig.text(0.06, 0.945, va="top", fontsize=10, color=dd.TEXT_2,
             s="Cumulative net P&L, % of capital (1/3 per pair). Thin lines "
               "= each pair's contribution. Top 3 rules by weakest pair.")
    for ax, (_, r) in zip(np.atleast_1d(axes), top.iterrows()):
        rule = (r.beta, r.z, r["filter"])
        combo, legs = series[rule]
        _style(ax, f"β {r.beta} · z {r.z} · {r['filter']}:  "
                   f"{r.net_total * 100:+.1f}%, max DD {r.max_dd * 100:.1f}%, "
                   f"Sharpe {r.sharpe:.2f}")
        for name in PAIRS:
            c = legs[name].cumsum() / 3 * 100
            ax.plot(c.index, c, color=PAIR_COLORS[name], lw=1.0, alpha=0.9,
                    label=name)
        eq = combo.cumsum() * 100
        ax.plot(eq.index, eq, color=dd.TEXT, lw=2.0, label="portfolio")
        dd._end_label(ax, eq.index[-1], eq.iloc[-1], f"{eq.iloc[-1]:+.1f}%")
        ax.axhline(0, color="#bdbdbd", lw=0.8)
        ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=4,
                  labelcolor=dd.TEXT)
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------

def main():
    prices_all = load()
    grid, trades, dailies, regs_by_pair = [], [], {}, {}
    for name, stocks in {**PAIRS, **BASKET}.items():
        rows, d, tr, regs = grid_for(name, prices_all[stocks].dropna())
        grid += rows
        trades += tr
        if name in PAIRS:
            dailies[name] = d
            regs_by_pair[name] = regs
    grid = pd.DataFrame(grid).sort_values(["strategy", "net_total"],
                                          ascending=[True, False])
    port, series = portfolio(dailies)
    st = status(prices_all, regs_by_pair)
    direct, implied = implied_beta(regs_by_pair)
    gap = pd.Series(np.abs(direct - implied),
                    prices_all[["AWC", "CRC"]].dropna().index)

    plot_diag(prices_all, regs_by_pair, OUT_DIR / "triangle_diag.png")
    plot_portfolio(port, series, OUT_DIR / "triangle_portfolio.png")

    path = OUT_DIR / f"triangle_{base.AS_OF:%Y%m%d}.xlsx"
    with pd.ExcelWriter(path) as xl:
        port.round(4).to_excel(xl, sheet_name="Portfolio", index=False)
        grid.round(4).to_excel(xl, sheet_name="Grid_by_pair", index=False)
        st.round(3).set_index("pair").T.to_excel(xl, sheet_name="Status_Now")
        pd.concat(trades).round(4).to_excel(xl, sheet_name="Trades",
                                            index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    pct = lambda df, cols: df.assign(**{c: (df[c] * 100).round(1)
                                        for c in cols if c in df})
    years = [c for c in port.columns if c.isdigit()]
    print(pct(port, ["net_total", "max_dd", "ann_vol", "weakest_pair",
                     *[c for c in port if c.startswith("net_")], *years])
          .round(2).head(12).to_string(index=False))
    for name in [*PAIRS, *BASKET]:
        g = grid[grid.strategy == name].head(6)
        print(pct(g, ["net_per_trade", "win_rate", "worst_mae", "net_total",
                      "max_dd", *years]).round(2).to_string(index=False))
    print(st.round(2).set_index("pair").T.to_string())
    print("\nimplied vs direct beta gap (median, last 250d):",
          round(gap.dropna().median(), 3), round(gap.iloc[-250:].median(), 3),
          "now", round(gap.iloc[-1], 3), "direct", round(direct[-1], 3),
          "implied", round(implied[-1], 3))
    print(f"Written: {path}")


if __name__ == "__main__":
    main()
