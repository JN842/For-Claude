"""5-year diagnostics and backtest grid for chosen pairs / baskets.

Same steps as the earlier GPSC-EGCO / AWC-ERW work, on daily closes
(Yahoo adjusted, Jan 2022 - Nov 2026):

1. Diagnostics: prices, rolling hedge ratio (beta 250 / 120), half-life of
   the 250-day regression residual (HL250), z-score (beta 250, model).
2. Grid: beta window x z method x filter.
   * beta window W: rolling OLS  y = a + X b  on the last W days.
   * z ``model``: today's residual / residual sd of that regression.
   * z ``60d`` / ``20d``: spread  y - X b  versus its own N-day mean and sd.
   * filter ``HL250<10``: half-life of the 250-day residual below 10 days.
   * filter ``HL<10 + beta agree``: also |beta120 - beta250| <= 0.5 for
     every leg.
   Entry |z| >= 2 (only while every hedge ratio is positive), exit when
   z crosses 0, stop |z| >= 4, max 60 days.
   Signal at close t, trade at close t+1; hedge and z scale frozen at
   entry; 1 gross notional per trade; cost 0.6% per round trip.
3. Current status: z, HL250, beta by window, Johansen trace on 250 days.

Writes cointegration_output/grid_<date>.xlsx and grid_diag_<name>.png.
"""

import itertools
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from statsmodels.tsa.vector_ar.vecm import coint_johansen

import basket_deepdive as dd
import cointegration_set100 as base

warnings.simplefilter("ignore")

STRATEGIES = {
    "GPSC vs RATCH+SCC": ["GPSC", "RATCH", "SCC"],
    "GPSC / SCC": ["GPSC", "SCC"],
    "AWC vs CRC+GLOBAL": ["AWC", "CRC", "GLOBAL"],
    "AWC / CRC": ["AWC", "CRC"],
    "AWC / ERW (reference)": ["AWC", "ERW"],
}
DIAG = ["GPSC vs RATCH+SCC", "AWC vs CRC+GLOBAL"]
BETA_WINDOWS = [60, 120, 250]
Z_METHODS = ["model", "60d", "20d"]
FILTERS = ["none", "HL250<10", "HL<10 + beta agree"]
START = pd.Timestamp("2022-01-01")
ENTRY, STOP, MAX_HOLD = 2.0, 4.0, 60
COST = 0.006                  # per round trip, on gross notional
HL_MAX, BETA_GAP = 10, 0.5
REQUIRE_POSITIVE_BETA = True  # no entry while any hedge ratio is <= 0
OUT_DIR = base.OUT_DIR


# ---------------------------------------------------------------------------
# Rolling regressions
# ---------------------------------------------------------------------------

def rolling_ols(logp, window):
    """Per day: intercept, beta vector, residual sd, residual half-life."""
    y = logp.values[:, 0]
    x = logp.values[:, 1:]
    n, k = x.shape
    alpha = np.full(n, np.nan)
    beta = np.full((n, k), np.nan)
    sd = np.full(n, np.nan)
    hl = np.full(n, np.nan)
    for t in range(window - 1, n):
        yy = y[t - window + 1:t + 1]
        xx = x[t - window + 1:t + 1]
        design = np.column_stack([np.ones(window), xx])
        coef = np.linalg.lstsq(design, yy, rcond=None)[0]
        resid = yy - design @ coef
        alpha[t], beta[t], sd[t] = coef[0], coef[1:], resid.std()
        hl[t] = dd.half_life(resid)
    return dict(alpha=alpha, beta=beta, sd=sd, hl=hl)


def z_value(logp_vals, t, alpha, beta, sd, method):
    """z at day t for a given (frozen or current) hedge."""
    y, x = logp_vals[:, 0], logp_vals[:, 1:]
    if method == "model":
        return (y[t] - alpha - x[t] @ beta) / sd
    n = int(method[:-1])
    if t - n + 1 < 0:
        return np.nan
    s = y[t - n + 1:t + 1] - x[t - n + 1:t + 1] @ beta
    s_sd = s.std(ddof=1)
    return (s[-1] - s.mean()) / s_sd if s_sd > 0 else np.nan


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def run(prices, regs, window, method, filt):
    logp_vals = np.log(prices.values)
    p = prices.values
    dates = prices.index
    reg = regs[window]
    hl250 = regs[250]["hl"]
    b120, b250 = regs[120]["beta"], regs[250]["beta"]
    first = max(int(dates.searchsorted(START)), 250)

    pnl = np.zeros(len(dates))
    trades, pos, pending = [], None, None
    for t in range(first, len(dates)):
        if pos is not None and t > pos["i"]:
            d = (pos["shares"] * (p[t] - p[t - 1])).sum()
            pnl[t] += d
            pos["pnl"] += d
            pos["mae"] = min(pos["mae"], pos["pnl"])
        if pending is not None:
            if pending[0] == "enter":
                _, direction, frozen = pending
                w = np.r_[1.0, -frozen["beta"]]
                notional = direction * w / np.abs(w).sum()
                pos = dict(direction=direction, i=t, pnl=0.0, mae=0.0,
                           shares=notional / p[t], **frozen)
                pnl[t] -= COST / 2
            else:
                pnl[t] -= COST / 2
                trades.append(_close(pos, t, dates, pending[1]))
                pos = None
            pending = None

        last = t == len(dates) - 1
        if pos is not None:
            z = z_value(logp_vals, t, pos["alpha"], pos["beta"], pos["sd"],
                        method)
            held = t - pos["i"]
            if last:
                pnl[t] -= COST / 2
                trades.append(_close(pos, t, dates, "end of data"))
                pos = None
            elif pos["direction"] * z >= 0:
                pending = ("exit", "target")
            elif abs(z) >= STOP:
                pending = ("exit", "stop")
            elif held >= MAX_HOLD:
                pending = ("exit", "time")
        elif not last and not np.isnan(reg["sd"][t]):
            ok = True
            if filt != "none":
                ok = hl250[t] < HL_MAX
            if ok and REQUIRE_POSITIVE_BETA:
                # a negative hedge ratio means buying both legs: not a spread
                ok = np.all(reg["beta"][t] > 0)
            if ok and filt == "HL<10 + beta agree":
                ok = np.all(np.abs(b120[t] - b250[t]) <= BETA_GAP)
            if ok:
                frozen = dict(alpha=reg["alpha"][t], beta=reg["beta"][t].copy(),
                              sd=reg["sd"][t])
                z = z_value(logp_vals, t, frozen["alpha"], frozen["beta"],
                            frozen["sd"], method)
                if z <= -ENTRY:
                    pending = ("enter", +1, frozen)
                elif z >= ENTRY:
                    pending = ("enter", -1, frozen)
    daily = pd.Series(pnl, index=dates).loc[dates[first]:]
    return daily, pd.DataFrame(trades)


def _close(pos, t, dates, reason):
    return {"entry": dates[pos["i"]].date(), "exit": dates[t].date(),
            "side": "long" if pos["direction"] > 0 else "short",
            "days": t - pos["i"], "gross": pos["pnl"],
            "net": pos["pnl"] - COST, "mae": pos["mae"], "reason": reason,
            "hedge_beta": " ".join(f"{b:.2f}" for b in pos["beta"])}


def summarise(daily, trades):
    eq = daily.cumsum()
    row = {
        "trades": len(trades),
        "net_per_trade": trades.net.mean() if len(trades) else np.nan,
        "win_rate": (trades.net > 0).mean() if len(trades) else np.nan,
        "worst_mae": trades.mae.min() if len(trades) else np.nan,
        "net_total": daily.sum(),
        "max_dd": (eq - eq.cummax()).min(),
    }
    for year, v in daily.groupby(daily.index.year).sum().items():
        row[str(year)] = v
    return row


# ---------------------------------------------------------------------------
# Diagnostics and current status
# ---------------------------------------------------------------------------

def status(name, prices, regs):
    logp = np.log(prices)
    vals = logp.values
    t = len(prices) - 1
    out = {"strategy": name}
    for w in BETA_WINDOWS:
        r = regs[w]
        out[f"z_model_beta{w}"] = z_value(vals, t, r["alpha"][t],
                                          r["beta"][t], r["sd"][t], "model")
        for j, s in enumerate(prices.columns[1:]):
            out[f"beta{w}_{s}"] = r["beta"][t][j]
    out["HL250"] = regs[250]["hl"][t]
    res = coint_johansen(vals[-250:], 0, 1)
    out["johansen250_trace"] = float(np.real(res.lr1[0]))
    out["johansen250_crit95"] = res.cvt[0, 1]
    out["johansen250_pass"] = out["johansen250_trace"] > res.cvt[0, 1]
    gap = np.abs(regs[120]["beta"][t] - regs[250]["beta"][t]).max()
    out["max_gap_beta120_vs_250"] = gap
    out["filter_HL_open"] = out["HL250"] < HL_MAX
    out["filter_beta_agree_open"] = gap <= BETA_GAP
    # z behaviour since Oct 2025 (beta 250, model; current hedge per day)
    since = prices.index.searchsorted(pd.Timestamp("2025-10-01"))
    zs = [z_value(vals, i, regs[250]["alpha"][i], regs[250]["beta"][i],
                  regs[250]["sd"][i], "model") for i in range(since, t + 1)]
    zs = np.array(zs)
    out["z_since_oct25_min"] = zs.min()
    out["z_since_oct25_max"] = zs.max()
    out["days_abs_z_ge_2_since_oct25"] = f"{(np.abs(zs) >= 2).sum()} / {len(zs)}"
    return out


def plot_diag(name, prices, regs, path):
    logp = np.log(prices)
    stocks = list(prices.columns)
    idx = prices.index
    vals = logp.values
    z = np.array([z_value(vals, i, regs[250]["alpha"][i], regs[250]["beta"][i],
                          regs[250]["sd"][i], "model")
                  if not np.isnan(regs[250]["sd"][i]) else np.nan
                  for i in range(len(idx))])
    show = idx >= START - pd.Timedelta(days=1)

    fig, axes = plt.subplots(4, 1, figsize=(11, 12.5), sharex=True,
                             gridspec_kw={"hspace": 0.42})
    fig.patch.set_facecolor(dd.SURFACE)
    fig.subplots_adjust(top=0.9)
    fig.suptitle(f"{name}: 5-year diagnostics", x=0.06, y=0.985, va="top",
                 ha="left", fontsize=15, color=dd.TEXT, fontweight="bold")
    fig.text(0.06, 0.957, va="top", fontsize=10, color=dd.TEXT_2,
             s="Daily adjusted closes, Jan 2022 – Nov 2026. Rolling OLS "
               f"y = {stocks[0]}, X = {', '.join(stocks[1:])}.")

    def style(ax, title):
        dd._style(ax, title)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))

    ax = axes[0]
    style(ax, "Log price, rebased to 0 in Jan 2022")
    base_row = logp.loc[show].iloc[0]
    for i, s in enumerate(stocks):
        series = (logp[s] - base_row[s]).loc[show]
        ax.plot(series.index, series, color=dd.SERIES[i], lw=1.5, label=s)
        dd._end_label(ax, series.index[-1], series.iloc[-1], s)
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
              labelcolor=dd.TEXT)

    ax = axes[1]
    style(ax, "Hedge ratio β (solid = 250-day, dashed = 120-day)")
    for j, s in enumerate(stocks[1:], start=1):
        b250 = pd.Series(regs[250]["beta"][:, j - 1], idx).loc[show]
        b120 = pd.Series(regs[120]["beta"][:, j - 1], idx).loc[show]
        ax.plot(b250.index, b250, color=dd.SERIES[j], lw=1.6, label=f"β {s} 250d")
        ax.plot(b120.index, b120, color=dd.SERIES[j], lw=1.0, ls="--",
                label=f"β {s} 120d")
        dd._end_label(ax, b250.index[-1], b250.iloc[-1],
                      f"{s} {b250.iloc[-1]:.2f}")
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=4,
              labelcolor=dd.TEXT)

    ax = axes[2]
    style(ax, "Half-life of the 250-day residual (HL250, days, capped at 80)")
    hl = pd.Series(regs[250]["hl"], idx).loc[show].clip(upper=80)
    ax.plot(hl.index, hl, color=dd.SERIES[0], lw=1.6)
    ax.axhline(HL_MAX, color="#dc3c3d", lw=1, ls="--")
    ax.fill_between(hl.index, 0, 80, where=hl < HL_MAX, color="#e3edf9",
                    step="mid", lw=0, zorder=0.5)
    ax.set_ylim(0, 82)
    ax.text(0.995, 0.95, "blue band = HL250 < 10 (filter open)",
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            color=dd.TEXT_2,
            bbox=dict(facecolor=dd.SURFACE, edgecolor="none", pad=1.5))
    dd._end_label(ax, hl.index[-1], hl.iloc[-1], f"{hl.iloc[-1]:.1f}d")

    ax = axes[3]
    style(ax, "z-score (β 250-day, model)")
    zz = pd.Series(z, idx).loc[show].clip(-5, 5)
    for lv, ls in [(2, "--"), (-2, "--")]:
        ax.axhline(lv, color="#9c9c9c", lw=0.9, ls=ls)
    ax.axhline(0, color="#bdbdbd", lw=0.8)
    ax.plot(zz.index, zz, color="#4d4d4d", lw=1.1)
    ax.scatter([zz.index[-1]], [zz.iloc[-1]], s=40, color=dd.SERIES[0],
               edgecolor="white", linewidth=1.5, zorder=3)
    dd._end_label(ax, zz.index[-1], zz.iloc[-1], f"now {zz.iloc[-1]:+.2f}")
    axes[-1].set_xlim(START - pd.Timedelta(days=5),
                      idx[-1] + pd.Timedelta(days=60))
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=dd.SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------

def main():
    prices_all = pd.read_csv(OUT_DIR / "prices_5y.csv", index_col=0,
                             parse_dates=True).ffill(limit=5)
    grid, all_trades, states = [], [], []
    for name, stocks in STRATEGIES.items():
        prices = prices_all[stocks].dropna()
        regs = {w: rolling_ols(np.log(prices), w) for w in BETA_WINDOWS}
        for w, m, f in itertools.product(BETA_WINDOWS, Z_METHODS, FILTERS):
            daily, trades = run(prices, regs, w, m, f)
            grid.append({"strategy": name, "beta": w, "z": m, "filter": f,
                         **summarise(daily, trades)})
            if len(trades):
                all_trades.append(trades.assign(strategy=name, beta=w, z=m,
                                                filter=f))
        states.append(status(name, prices, regs))
        if name in DIAG:
            slug = name.replace(" vs ", "_vs_").replace("+", "_")
            plot_diag(name, prices, regs, OUT_DIR / f"grid_diag_{slug}.png")

    grid = pd.DataFrame(grid)
    trades = pd.concat(all_trades, ignore_index=True)
    states = pd.DataFrame(states)
    years = [c for c in grid.columns if c.isdigit()]
    grid = grid.sort_values(["strategy", "net_total"], ascending=[True, False])
    path = OUT_DIR / f"grid_{base.AS_OF:%Y%m%d}.xlsx"
    with pd.ExcelWriter(path) as xl:
        grid.round(4).to_excel(xl, sheet_name="Grid", index=False)
        states.T.to_excel(xl, sheet_name="Status_Now", header=False)
        trades.round(4).to_excel(xl, sheet_name="Trades", index=False)
        pd.DataFrame([
            ("period", f"{START.date()} to {base.AS_OF.date()} (daily)"),
            ("entry / exit / stop", f"|z|>={ENTRY} / z crosses 0 / |z|>={STOP}"),
            ("max hold", MAX_HOLD), ("cost per round trip", COST),
            ("execution", "signal close t, trade close t+1"),
            ("HL filter", f"HL250 < {HL_MAX}"),
            ("beta agree", f"|beta120 - beta250| <= {BETA_GAP} every leg"),
        ], columns=["item", "value"]).to_excel(xl, sheet_name="Assumptions",
                                               index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    show = grid.copy()
    for c in ["net_per_trade", "win_rate", "worst_mae", "net_total",
              "max_dd", *years]:
        show[c] = (show[c] * 100).round(1)
    print(show.to_string(index=False))
    print(states.T.to_string())
    print(f"\nWritten: {path}")


if __name__ == "__main__":
    main()
