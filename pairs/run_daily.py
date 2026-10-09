"""Daily MTC-SAWAD study: price-only (as before) vs total return with exact
corporate-action factors, under spot and single-stock-futures funding.

    python -I pairs/run_daily.py /path/to/FinancePair.xlsx [results_dir]
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from pairs.corporate import check_against_table, load_actions, price_panel  # noqa: E402
from pairs.daily_bt import Params, backtest, by_year, summarize  # noqa: E402
from pairs.data import load_daily  # noqa: E402
from pairs.stats import pair_diagnostics, rolling_diagnostics  # noqa: E402

A, B = "MTC", "SAWAD"
ADJUSTED_IN_FILE = {"SAWAD"}
GRID = dict(lookback=[126, 252], zwin=[20, 40], entry=[1.5, 2.0], gate_p=[0.05, 0.10])
SCENARIOS = {
    # name: (price basis, base params)
    "old_spot_15bp": ("adj", Params(funding="spot", borrow=0.03, cost=0.0015)),
    "tr_spot_15bp": ("tr", Params(funding="spot", borrow=0.03, cost=0.0015)),
    "tr_ssf_15bp": ("tr", Params(funding="ssf", rf=0.02, cost=0.0015)),
    "tr_ssf_5bp": ("tr", Params(funding="ssf", rf=0.02, cost=0.0005)),
    "tr_ssf_5bp_lag1": ("tr", Params(funding="ssf", rf=0.02, cost=0.0005, lag=1)),
}


def run_grid(panel, basis, base: Params) -> pd.DataFrame:
    sa, sb = panel[A][basis], panel[B][basis]
    ra, rb = sa.pct_change().fillna(0), sb.pct_change().fillna(0)
    rows = []
    for values in itertools.product(*GRID.values()):
        p = replace(base, **dict(zip(GRID, values)))
        daily, trades = backtest(sa, sb, ra, rb, p)
        yr = by_year(daily)
        rows.append({**dict(zip(GRID, values)), **summarize(daily, trades),
                     "years_positive": int((yr > 0).sum()), "years": len(yr),
                     **{f"y{y}": r for y, r in yr.items()}})
    return pd.DataFrame(rows)


def main(xlsx: str, out: str = "results") -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    actions = load_actions()
    close = load_daily(xlsx, [A, B])
    panel = price_panel(close, actions, ADJUSTED_IN_FILE)

    check = check_against_table(panel, actions)
    check.to_csv(out / "ca_check.csv", index=False)

    # dividend jumps in the spread
    jumps = []
    for sym in (A, B):
        d = panel[sym]
        for x in d.index[d["div"] > 0]:
            jumps.append((sym, x.date(), d.at[x, "div"] / d["adj"].shift().at[x]))
    jumps = pd.DataFrame(jumps, columns=["symbol", "x_date", "div_yield"])
    jumps.to_csv(out / "dividend_jumps.csv", index=False)

    diag, roll = {}, {}
    for basis in ("adj", "tr"):
        la, lb = np.log(panel[A][basis]), np.log(panel[B][basis])
        diag[basis] = pair_diagnostics(la, lb)
        r = rolling_diagnostics(la, lb)
        roll[basis] = r
        diag[basis].update({
            "roll_adf_lt_05": (r["adf_p"] < 0.05).mean(),
            "roll_adf_lt_10": (r["adf_p"] < 0.10).mean(),
            "roll_beta_min": r["beta"].min(), "roll_beta_max": r["beta"].max(),
            "roll_half_life_med": r["half_life_d"].median(),
        })
    diag = pd.DataFrame(diag)
    diag.to_csv(out / "diagnostics.csv")

    # ungated ratio-spread baseline from the handoff (beta = 1, 40d z, entry 2)
    base_rows = []
    for name, (basis, base) in SCENARIOS.items():
        p = replace(base, lookback=None, gate_p=None, zwin=40, entry=2.0)
        sa, sb = panel[A][basis], panel[B][basis]
        daily, trades = backtest(sa, sb, sa.pct_change().fillna(0), sb.pct_change().fillna(0), p)
        base_rows.append({"scenario": name, **summarize(daily, trades)})
    baseline = pd.DataFrame(base_rows)
    baseline.to_csv(out / "baseline_ungated.csv", index=False)

    grids = []
    for name, (basis, base) in SCENARIOS.items():
        g = run_grid(panel, basis, base)
        g.insert(0, "scenario", name)
        grids.append(g)
    grid = pd.concat(grids, ignore_index=True)
    grid.to_csv(out / "daily_grid.csv", index=False)

    agg = grid.groupby("scenario", sort=False).agg(
        cells=("total", "size"), positive=("total", lambda x: int((x > 0).sum())),
        median_total=("total", "median"), median_sharpe=("sharpe", "median"),
        best_total=("total", "max"), worst_total=("total", "min"),
        median_trades=("trades", "median"))
    agg.to_csv(out / "daily_grid_summary.csv")

    _plot(panel, roll, grid, out)
    _write_summary(out, check, jumps, diag, baseline, agg, grid)


def _plot(panel, roll, grid, out: Path) -> None:
    fig, ax = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    for basis, label in (("adj", "price only"), ("tr", "total return")):
        ratio = np.log(panel[A][basis] / panel[B][basis])
        ax[0].plot(ratio.index, ratio - ratio.iloc[0], label=label, lw=1)
        ax[1].plot(roll[basis].index, roll[basis]["adf_p"], label=label, lw=1)
        ax[2].plot(roll[basis].index, roll[basis]["beta"], label=label, lw=1)
    ax[0].set_title(f"log({A}/{B}), rebased")
    ax[1].axhline(0.05, color="grey", lw=0.8, ls="--")
    ax[1].set_title("rolling 1y ADF p on OLS spread")
    ax[2].set_title("rolling 1y beta")
    for a in ax:
        a.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "spread_diagnostics.png", dpi=120)
    plt.close(fig)

    scen = list(dict.fromkeys(grid["scenario"]))
    fig, ax = plt.subplots(figsize=(10, 4))
    data = [grid.loc[grid["scenario"].eq(s), "total"] * 100 for s in scen]
    ax.boxplot(data, showfliers=False)
    for i, d in enumerate(data, 1):
        ax.scatter(np.full(len(d), i), d, s=12)
    ax.set_xticks(range(1, len(scen) + 1), scen, rotation=15)
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_ylabel("total return, %")
    ax.set_title("Gated daily grid: every cell (16 per scenario)")
    fig.tight_layout()
    fig.savefig(out / "daily_grid.png", dpi=120)
    plt.close(fig)


def _fmt(df: pd.DataFrame, floatfmt="{:.3f}") -> str:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def _write_summary(out, check, jumps, diag, baseline, agg, grid) -> None:
    key = ["scenario", "lookback", "zwin", "entry", "gate_p", "trades", "total", "sharpe", "max_dd",
           "win_rate", "years_positive", "years"]
    best = grid.sort_values("total", ascending=False).groupby("scenario", sort=False).head(1)[key]
    handoff_best = grid.loc[grid["lookback"].eq(252) & grid["zwin"].eq(40) & grid["entry"].eq(1.5)
                            & grid["gate_p"].eq(0.10), key]
    text = f"""# MTC–SAWAD daily: exact corporate actions + total return

Generated by `pairs/run_daily.py`.

## Corporate-action check (reconstructed raw close vs table)
{_fmt(check)}

## Cash-dividend jumps entering the price-only spread
{_fmt(jumps, "{:.4f}")}

## Diagnostics: price only (adj) vs total return (tr)
{_fmt(diag.reset_index().rename(columns={"index": "stat"}), "{:.3f}")}

## Ungated ratio baseline (beta 1, z 40d, entry 2)
{_fmt(baseline)}

## Gated grid, all 16 cells per scenario
{_fmt(agg.reset_index())}

Handoff's chosen cell (L 252, z 40, entry 1.5, ADF p < 0.10):
{_fmt(handoff_best)}

Best cell per scenario (selected in-sample, do not trust alone):
{_fmt(best)}
"""
    (out / "daily_summary.md").write_text(text)


if __name__ == "__main__":
    main(*sys.argv[1:])
