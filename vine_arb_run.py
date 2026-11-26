"""Backtest + today's signals for the vine-copula strategy -> Excel.

    python vine_arb_run.py                      # SET100, Yahoo prices
    python vine_arb_run.py --universe set50 --entry 1.0 --delay 0
    python vine_arb_run.py --prices my_prices.csv --sensitivity
    python vine_arb_run.py --by sector                   # partners from the same sector
    python vine_arb_run.py --by sector --groups ENERG BANK  # only these sectors
    python vine_arb_run.py --by sector --per-group       # one portfolio per sector

``--prices`` takes a CSV of daily closes (date index, one column per
symbol), for example from an internal price API.
"""

import argparse
from pathlib import Path

import pandas as pd

from price_data import SET50_2026H2, SET100_2026H2, fetch_prices, industry_groups
from vine_arb import (
    VineArbParams, backtest, backtest_by_group, fit_periods, params_table,
    sensitivity,
)

SHEETS = ("Summary", "Groups", "Live", "Trades", "Daily", "Models",
          "Sensitivity", "Params")


def run(prices, params, start=None, output_dir="vine_arb_output",
        with_sensitivity=False, progress=False, per_group=False):
    """Backtest -> Excel.  ``per_group=True`` also backtests every group of
    ``params.groups`` (or ``params.trade_groups``) as its own portfolio and
    writes the comparison to the ``Groups`` sheet."""
    fits = fit_periods(prices, params, start=start, progress=progress)
    result = backtest(prices, params, fits=fits)
    if per_group:
        result["groups"] = backtest_by_group(
            prices, params, groups=params.trade_groups,
            n_quadruples=min(params.n_quadruples, 3), start=start,
            progress=progress,
        )["summary"]
    if with_sensitivity:
        result["sensitivity"] = sensitivity(
            prices, params, start=start, fits=fits,
            grid={"entry": [0.3, 0.6, 1.0, 1.5], "stop": [2.0, None],
                  "execution_delay": [0, 1], "cost_bps": [0, 5, 10, 15]},
        )
    as_of = result["daily"].index[-1]
    path = Path(output_dir) / f"vine_arb_{as_of:%Y%m%d}.xlsx"
    write_report(result, path)
    result["path"] = path
    return result


def write_report(result, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    yearly = result["daily"].groupby(result["daily"].index.year).agg(
        net=("net", lambda r: (1 + r).prod() - 1),
        gross=("gross", lambda r: (1 + r).prod() - 1),
        avg_active=("active", "mean"),
    ).rename_axis("year")
    with pd.ExcelWriter(path) as writer:
        result["summary"].rename("value").to_frame().to_excel(
            writer, sheet_name="Summary")
        yearly.to_excel(writer, sheet_name="Summary",
                        startrow=len(result["summary"]) + 3)
        if "groups" in result:
            result["groups"].to_excel(writer, sheet_name="Groups", index=False)
        result["live"].to_excel(writer, sheet_name="Live", index=False)
        result["trades"].to_excel(writer, sheet_name="Trades", index=False)
        result["daily"].rename_axis("date").to_excel(writer, sheet_name="Daily")
        result["models"].to_excel(writer, sheet_name="Models", index=False)
        if "sensitivity" in result:
            result["sensitivity"].to_excel(writer, sheet_name="Sensitivity",
                                           index=False)
        params_table(result["params"]).to_excel(writer, sheet_name="Params",
                                                index=False)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--universe", choices=["set50", "set100"], default="set100")
    parser.add_argument("--prices", help="CSV of daily closes instead of Yahoo")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--entry", type=float, default=0.6)
    parser.add_argument("--stop", type=float, default=2.0,
                        help="CMI stop-loss; 0 disables it")
    parser.add_argument("--delay", type=int, default=1,
                        help="0 = trade at the signal close (ATC), 1 = next close")
    parser.add_argument("--cost-bps", type=float, default=15.0)
    parser.add_argument("--borrow-bps", type=float, default=150.0)
    parser.add_argument("--vine", choices=["cvine", "dvine", "rvine"], default="cvine")
    parser.add_argument("--marginal", choices=["ecdf", "t"], default="ecdf")
    parser.add_argument("--quadruples", type=int, default=10)
    parser.add_argument("--by", choices=["none", "sector", "industry"],
                        default="none",
                        help="pick partners only from the target's SET sector/industry")
    parser.add_argument("--groups", nargs="+",
                        help="trade only these sectors/industries, e.g. ENERG BANK")
    parser.add_argument("--per-group", action="store_true",
                        help="also backtest each group as its own portfolio")
    parser.add_argument("--sensitivity", action="store_true")
    parser.add_argument("--output-dir", default="vine_arb_output")
    args = parser.parse_args()

    symbols = SET50_2026H2 if args.universe == "set50" else SET100_2026H2
    if args.prices:
        prices = pd.read_csv(args.prices, index_col=0, parse_dates=True)
    else:
        prices, log = fetch_prices(symbols, start="2014-01-01",
                                   cache=f"price_cache/{args.universe}.csv")
        missing = log.loc[log["status"].ne("OK"), "symbol"].tolist()
        if missing:
            print("no prices for:", ", ".join(missing))
    groups = None if args.by == "none" else industry_groups(symbols, level=args.by)
    if args.groups and groups is None:
        parser.error("--groups needs --by sector or --by industry")
    params = VineArbParams(
        groups=groups, trade_groups=tuple(args.groups) if args.groups else None,
        entry=args.entry, stop=args.stop or None, execution_delay=args.delay,
        cost_bps=args.cost_bps, borrow_bps=args.borrow_bps, vine=args.vine,
        marginal=args.marginal, n_quadruples=args.quadruples,
        universe=tuple(symbols),
    )
    result = run(prices, params, start=args.start, output_dir=args.output_dir,
                 with_sensitivity=args.sensitivity, progress=True,
                 per_group=args.per_group)
    pd.set_option("display.width", 200)
    print(result["summary"].to_string())
    if "groups" in result:
        print(result["groups"][["group", "stocks", "quadruples", "cagr",
                                "sharpe", "max_drawdown", "gross_ann_return",
                                "trades"]].to_string(index=False))
    print(result["live"][["group", "target", "partners", "h", "cmi", "signal", "held",
                          "trading_days_to_refit"]].to_string(index=False))
    print("wrote", result["path"])


if __name__ == "__main__":
    main()
