"""Deep dive on chosen cointegrated baskets: stability and charts.

For each basket (first stock = the long leg, normalised to weight 1):

* weights: Johansen formation / recent, OLS formation / recent
* rolling (``ROLL`` days, every ``STEP`` days): OLS hedge ratios, Johansen
  hedge ratios, Johansen trace vs 95% critical value, spread half-life
* spread with *fixed formation* weights over the whole sample, as z-score
  (formation mean / sd), with out-of-sample |z|>2 episodes and how long
  they took to get back to 0
* each stock's own ADF and every pair inside the basket (Engle-Granger)

Writes ``cointegration_output/deepdive_<name>.png`` and a stats CSV.
Run after ``cointegration_set100.py`` has cached prices.
"""

import itertools
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from statsmodels.tsa.coint_tables import c_sjt
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen

import cointegration_set100 as base

warnings.simplefilter("ignore")

BASKETS = {
    "GPSC_vs_RATCH_SCC": ["GPSC", "RATCH", "SCC"],
    "AWC_vs_CRC_GLOBAL": ["AWC", "CRC", "GLOBAL"],
}
ROLL = 250      # trading days (~1 year)
STEP = 5
OUT_DIR = base.OUT_DIR

# Reference palette (dataviz skill), light mode.
SERIES = ["#257de0", "#f47230", "#00b894"]
TEXT, TEXT_2, GRID = "#191919", "#666666", "#e6e6e6"
SURFACE, OOS_FILL = "#ffffff", "#f3f3f3"


def johansen_weights(logp):
    res = coint_johansen(logp, 0, 1)
    v = np.real(res.evec[:, 0])
    return v / v[0], float(np.real(res.lr1[0])), res.cvt[0, 1]


def ols_weights(logp):
    y, x = logp[:, 0], logp[:, 1:]
    design = np.column_stack([np.ones(len(y)), x])
    coef = np.linalg.lstsq(design, y, rcond=None)[0]
    return np.r_[1.0, -coef[1:]], coef[0]


def half_life(spread):
    lag = spread[:-1] - spread[:-1].mean()
    lam = np.dot(lag, np.diff(spread)) / np.dot(lag, lag)
    return -np.log(2) / lam if lam < 0 else np.inf


def reversion_episodes(z, dates, threshold=2.0):
    """|z| crosses threshold -> days until z crosses 0 (None = not yet)."""
    out, i = [], 0
    while i < len(z):
        if abs(z[i]) >= threshold:
            sign = np.sign(z[i])
            j = i
            while j < len(z) and np.sign(z[j]) == sign:
                j += 1
            out.append({"start": dates[i].date(), "z_start": round(z[i], 2),
                        "days_to_zero": j - i if j < len(z) else None})
            i = j
        else:
            i += 1
    return out


def analyse(name, stocks, close):
    logp = np.log(close[stocks]).dropna()
    f0, f1 = base.WINDOWS["formation"]
    v0, v1 = base.WINDOWS["validation"]
    r0, r1 = base.WINDOWS["recent"]
    form, valid, recent = (logp.loc[a:b] for a, b in
                           [(f0, f1), (v0, v1), (r0, r1)])

    w_j_form, tr_form, cv = johansen_weights(form.values)
    w_j_rec, tr_rec, _ = johansen_weights(recent.values)
    w_o_form, _ = ols_weights(form.values)
    w_o_rec, _ = ols_weights(recent.values)

    # Spread with fixed formation Johansen weights, whole sample.
    spread = logp.values @ w_j_form
    s_form = form.values @ w_j_form
    z = (spread - s_form.mean()) / s_form.std()
    z = pd.Series(z, index=logp.index)
    s_valid = valid.values @ w_j_form
    oos_adf = adfuller(s_valid, autolag="AIC", result_object=False)[1]
    episodes = reversion_episodes(z.loc[v0:v1].values,
                                  z.loc[v0:v1].index)

    # Rolling.
    rows = []
    for end in range(ROLL, len(logp) + 1, STEP):
        win = logp.values[end - ROLL:end]
        wj, tr, crit = johansen_weights(win)
        wo, _ = ols_weights(win)
        rows.append({
            "date": logp.index[end - 1],
            **{f"ols_{s}": -wo[i] for i, s in enumerate(stocks) if i},
            **{f"joh_{s}": -wj[i] for i, s in enumerate(stocks) if i},
            "trace": tr, "crit95": crit,
            "hl_ols": half_life(win @ wo),
            "hl_fixed": half_life(win @ w_j_form),
        })
    roll = pd.DataFrame(rows).set_index("date")

    # Single-stock ADF and pairs inside the basket.
    own = {s: adfuller(form[s], autolag="AIC", result_object=False)[1]
           for s in stocks}
    own_valid = {s: adfuller(valid[s], autolag="AIC", result_object=False)[1]
                 for s in stocks}
    pairs = {}
    for a, b in itertools.combinations(stocks, 2):
        pairs[f"{a}/{b}"] = (
            max(coint(form[a], form[b])[1], coint(form[b], form[a])[1]),
            max(coint(recent[a], recent[b])[1], coint(recent[b], recent[a])[1]),
        )

    hedge_cols = [c for c in roll if c.startswith("ols_")]
    stats = {
        "basket": name,
        "long": stocks[0], "short": " + ".join(stocks[1:]),
        "trace_formation": tr_form, "trace_recent": tr_rec, "crit95": cv,
        **{f"w_johansen_form_{s}": -w_j_form[i]
           for i, s in enumerate(stocks) if i},
        **{f"w_johansen_recent_{s}": -w_j_rec[i]
           for i, s in enumerate(stocks) if i},
        **{f"w_ols_form_{s}": -w_o_form[i] for i, s in enumerate(stocks) if i},
        **{f"w_ols_recent_{s}": -w_o_rec[i] for i, s in enumerate(stocks) if i},
        **{f"rolling_ols_range_{c[4:]}":
           f"{roll[c].min():.2f} to {roll[c].max():.2f}" for c in hedge_cols},
        **{f"rolling_ols_sd_{c[4:]}": roll[c].std() for c in hedge_cols},
        "rolling_share_trace_pass": (roll.trace > roll.crit95).mean(),
        "half_life_formation": half_life(s_form),
        "half_life_oos_fixed": half_life(s_valid),
        "rolling_hl_median": roll.hl_ols.median(),
        "oos_adf_p": oos_adf,
        "z_now": z.iloc[-1],
        "oos_max_abs_z": z.loc[v0:v1].abs().max(),
        "oos_episodes_abs_z_ge_2": len(episodes),
        "oos_episode_days_to_zero": str([e["days_to_zero"] for e in episodes]),
        **{f"own_adf_form_{s}": p for s, p in own.items()},
        **{f"own_adf_oos_{s}": p for s, p in own_valid.items()},
        **{f"pair_eg_form_{k}": v[0] for k, v in pairs.items()},
        **{f"pair_eg_recent_{k}": v[1] for k, v in pairs.items()},
    }
    return dict(logp=logp, z=z, roll=roll, stats=stats, episodes=episodes,
                w_form=w_j_form, w_rec=w_j_rec, stocks=stocks)


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

def _style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=TEXT, pad=6,
                 fontweight="bold")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#bdbdbd")
    ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))


def _windows(ax, ymax=None):
    v0, v1 = base.WINDOWS["validation"]
    ax.axvspan(v0, v1, color=OOS_FILL, zorder=0, linewidth=0)


def _end_label(ax, x, y, text, color_text=TEXT):
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=9, color=color_text)


def plot(res, path):
    stocks, logp, z, roll = res["stocks"], res["logp"], res["z"], res["roll"]
    st = res["stats"]
    v0, _ = base.WINDOWS["validation"]
    fig, axes = plt.subplots(5, 1, figsize=(11, 15.5), sharex=True,
                             gridspec_kw={"hspace": 0.42})
    fig.subplots_adjust(top=0.915)
    fig.patch.set_facecolor(SURFACE)
    short = " + ".join(f"{-res['w_form'][i]:.2f}×{s}"
                       for i, s in enumerate(stocks) if i)
    fig.suptitle(f"Long {stocks[0]}  vs  short {short}",
                 x=0.06, y=0.985, va="top", ha="left", fontsize=15, color=TEXT,
                 fontweight="bold")
    fig.text(0.06, 0.958, va="top", s=
             "Johansen weights on log prices, formation Nov 2023 – Nov 2025. "
             "Grey band = out-of-sample (Dec 2025 – Nov 2026).",
             fontsize=10, color=TEXT_2)

    # 1. Prices indexed to 100.
    ax = axes[0]
    _style(ax, "Price, indexed to 100 at start")
    _windows(ax)
    idx = 100 * np.exp(logp - logp.iloc[0])
    for i, s in enumerate(stocks):
        ax.plot(idx.index, idx[s], color=SERIES[i], lw=1.6, label=s)
        _end_label(ax, idx.index[-1], idx[s].iloc[-1], s)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
              labelcolor=TEXT)

    # 2. Z-score of fixed-weight spread.
    ax = axes[1]
    _style(ax, "Spread z-score with fixed formation weights "
               f"(out-of-sample ADF p = {st['oos_adf_p']:.3f})")
    _windows(ax)
    for level, ls in [(2, "--"), (1, ":"), (-1, ":"), (-2, "--")]:
        ax.axhline(level, color="#9c9c9c", lw=0.9, ls=ls, zorder=1)
    ax.axhline(0, color="#9c9c9c", lw=0.9, zorder=1)
    ax.plot(z.index, z, color=SERIES[0], lw=1.4, zorder=2)
    ax.scatter([z.index[-1]], [z.iloc[-1]], s=40, color=SERIES[0],
               edgecolor=SURFACE, linewidth=2, zorder=3)
    _end_label(ax, z.index[-1], z.iloc[-1], f"now {z.iloc[-1]:+.2f}")
    ax.text(0.995, 0.03, "dashed = ±2σ, dotted = ±1σ (formation mean/sd)",
            transform=ax.transAxes, ha="right", fontsize=8, color=TEXT_2,
            bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.5))

    # 3. Rolling hedge ratios.
    ax = axes[2]
    _style(ax, f"Rolling hedge ratio, {ROLL}-day window "
               "(solid = OLS, dashed = Johansen)")
    _windows(ax)
    for i, s in enumerate(stocks[1:], start=1):
        ax.plot(roll.index, roll[f"ols_{s}"], color=SERIES[i], lw=1.6,
                label=f"{s} (OLS)")
        ax.plot(roll.index, roll[f"joh_{s}"], color=SERIES[i], lw=1.0,
                ls="--", label=f"{s} (Johansen)")
        ax.axhline(-res["w_form"][i], color=SERIES[i], lw=0.8, ls=":",
                   alpha=0.9)
        _end_label(ax, roll.index[-1], roll[f"ols_{s}"].iloc[-1], s)
    lo = min(roll[[c for c in roll if c[:4] in ("ols_", "joh_")]].min().min(), 0)
    hi = roll[[c for c in roll if c[:4] in ("ols_", "joh_")]].max().max()
    pad = 0.1 * (hi - lo)
    ax.set_ylim(max(lo - pad, -1.5), min(hi + pad, 3))
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=4,
              labelcolor=TEXT)
    ax.text(0.995, 0.97, "dotted = formation Johansen weight", va="top",
            transform=ax.transAxes, ha="right", fontsize=8, color=TEXT_2,
            bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.5))

    # 4. Rolling trace.
    ax = axes[3]
    share = st["rolling_share_trace_pass"]
    _style(ax, f"Rolling Johansen trace statistic (passes 95% in "
               f"{share:.0%} of windows)")
    _windows(ax)
    ax.plot(roll.index, roll.trace, color=SERIES[0], lw=1.6)
    ax.axhline(roll.crit95.iloc[0], color="#dc3c3d", lw=1.0, ls="--")
    ax.text(0.995, 0.97, f"dashed red = 95% critical value "
            f"({roll.crit95.iloc[0]:.1f})", transform=ax.transAxes,
            ha="right", va="top", fontsize=8.5, color=TEXT_2,
            bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.5))

    # 5. Rolling half-life.
    ax = axes[4]
    _style(ax, "Rolling half-life of the spread (days)")
    _windows(ax)
    hl = roll.hl_ols.clip(upper=120)
    hlf = roll.hl_fixed.clip(upper=120)
    ax.plot(roll.index, hl, color=SERIES[0], lw=1.6,
            label="rolling OLS weights")
    ax.plot(roll.index, hlf, color=SERIES[1], lw=1.6,
            label="fixed formation weights")
    _end_label(ax, roll.index[-1], hl.iloc[-1], f"{hl.iloc[-1]:.0f}d")
    _end_label(ax, roll.index[-1], hlf.iloc[-1], f"{hlf.iloc[-1]:.0f}d")
    ax.set_ylim(0, min(max(hl.max(), hlf.max()) * 1.15, 125))
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2,
              labelcolor=TEXT)
    ax.text(0.995, 0.92, "capped at 120", transform=ax.transAxes,
            ha="right", fontsize=8, color=TEXT_2,
            bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.5))

    axes[-1].set_xlim(logp.index[0], logp.index[-1] + pd.Timedelta(days=45))
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def main():
    close = base.clean_prices(pd.read_csv(OUT_DIR / "set100_close.csv",
                                          index_col=0, parse_dates=True))
    stats = []
    for name, stocks in BASKETS.items():
        res = analyse(name, stocks, close)
        plot(res, OUT_DIR / f"deepdive_{name}.png")
        stats.append(res["stats"])
        print(f"\n== {name} ==")
        for k, v in res["stats"].items():
            print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")
        print("  episodes:", res["episodes"])
    pd.DataFrame(stats).T.to_csv(OUT_DIR / "deepdive_stats.csv")


if __name__ == "__main__":
    main()
