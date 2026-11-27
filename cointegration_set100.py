"""Pairwise cointegration screen over the SET100 constituents.

Every 2-stock combination of SET100 (100C2 = 4,950 pairs) is tested on log
adjusted close prices:

* Formation window (in-sample, 2 years): Engle-Granger in both orderings,
  Johansen trace test, hedge ratio, half-life of the spread.
* Validation window (out-of-sample, the following year): the spread built
  with the *formation* hedge ratio must still be stationary (ADF).  The ratio
  is fixed, not re-estimated, so plain ADF critical values apply.
* Recent window (latest 2 years): Engle-Granger again, to show whether the
  pair is cointegrated right now.

With 4,950 tests about 250 pairs pass at 5% by chance alone, so p-values are
also adjusted with Benjamini-Hochberg (FDR) and the final ``robust`` flag
needs the out-of-sample check as well.

Prices come from Yahoo Finance (``<SYMBOL>.BK``, adjusted for dividends and
splits so XD drops do not look like breaks).

Run:  python cointegration_set100.py            (downloads, then tests)
      python cointegration_set100.py --offline  (reuse the cached prices)
"""

import argparse
import itertools
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen


# SET100 / SET100FF constituents for January 1 - June 30, 2026
# (SET, "SET50 / SET100 H1 2026", updated December 15, 2025).
SET100 = """
AAV ADVANC AEONTS AMATA AOT AP AURA AWC BA BAM BANPU BBL BCH BCP BCPG BDMS
BEM BGRIM BH BJC BLA BTG BTS CBG CCET CENTEL CHG CK COM7 CPALL CPF CPN CRC
DELTA DOHOME EA EGCO ERW GFPT GLOBAL GPSC GULF GUNKUL HANA HMPRO ICHI IRPC
IVL JAS JMART JMT JTS KBANK KCE KKP KTB KTC LH M MEGA MINT MOSHI MTC OR OSP
PLANB PR9 PRM PTG PTT PTTEP PTTGC QH RATCH RCL SAWAD SCB SCC SCGP SIRI SISB
SJWD SPALI SPRC STA STECON STGT TASCO TCAP TFG TIDLOR TISCO TLI TOA TOP TRUE
TTB TU VGI WHA
""".split()

SET50 = set("""
ADVANC AOT AWC BANPU BBL BDMS BEM BH BJC BTS CBG CCET CENTEL COM7 CPALL CPF
CPN CRC DELTA EGCO GPSC GULF HMPRO IVL KBANK KKP KTB KTC LH MINT MTC OR OSP
PTT PTTEP PTTGC RATCH SAWAD SCB SCC SCGP TCAP TIDLOR TISCO TLI TOP TRUE TTB
TU WHA
""".split())

# SET sector of each constituent (same SET document).
SECTORS = {
    "Agribusiness": "GFPT STA",
    "Banking": "BBL KBANK KKP KTB SCB TCAP TISCO TTB",
    "Commerce": "BJC COM7 CPALL CRC DOHOME GLOBAL HMPRO MEGA MOSHI",
    "Construction Materials": "SCC TASCO TOA",
    "Construction Services": "CK STECON",
    "Electronic Components": "CCET DELTA HANA KCE",
    "Energy & Utilities": "BANPU BCP BCPG BGRIM EA EGCO GPSC GULF GUNKUL IRPC "
                          "OR PTG PTT PTTEP RATCH SPRC TOP",
    "Fashion": "AURA",
    "Finance & Securities": "AEONTS BAM JMT KTC MTC SAWAD TIDLOR",
    "Food & Beverage": "BTG CBG CPF ICHI M OSP TFG TU",
    "Health Care Services": "BCH BDMS BH CHG PR9",
    "Information & Communication Technology": "ADVANC JAS JMART JTS TRUE",
    "Insurance": "BLA TLI",
    "Media & Publishing": "PLANB VGI",
    "Packaging": "SCGP",
    "Personal Products & Pharmaceuticals": "STGT",
    "Petrochemicals & Chemicals": "IVL PTTGC",
    "Professional Services": "SISB",
    "Property Development": "AMATA AP AWC CPN LH QH SIRI SPALI WHA",
    "Tourism & Leisure": "CENTEL ERW MINT",
    "Transportation & Logistics": "AAV AOT BA BEM BTS PRM RCL SJWD",
}
SECTOR_OF = {s: sec for sec, names in SECTORS.items() for s in names.split()}

AS_OF = pd.Timestamp("2026-11-27")
WINDOWS = {
    "formation": (pd.Timestamp("2023-11-28"), pd.Timestamp("2025-11-27")),
    "validation": (pd.Timestamp("2025-11-28"), AS_OF),
    "recent": (pd.Timestamp("2024-11-28"), AS_OF),
}
ALPHA = 0.05
MIN_COVERAGE = 0.95          # share of window days a stock must have traded
HALF_LIFE_RANGE = (2, 60)    # trading days; outside this a pair is not tradable
OUT_DIR = Path(__file__).resolve().parent / "cointegration_output"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def download_prices(symbols, start, end, cache):
    import yfinance as yf

    tickers = [f"{s}.BK" for s in symbols]
    data = yf.download(tickers, start=start, end=end + pd.Timedelta(days=1),
                       auto_adjust=True, progress=False, threads=True)
    close = data["Close"].rename(columns=lambda t: t.removesuffix(".BK"))
    close = close.reindex(columns=symbols)
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    cache.parent.mkdir(parents=True, exist_ok=True)
    close.to_csv(cache)
    return close


def clean_prices(close):
    """Keep SET trading days and fill short gaps (suspensions, missing ticks)."""
    close = close.where(close > 0)
    close = close[close.notna().mean(axis=1) >= 0.5]   # drop holidays/bad rows
    return close.ffill(limit=5)


def window_prices(close, start, end):
    """Log prices of stocks with enough history in the window."""
    frame = close.loc[start:end]
    coverage = frame.notna().mean()
    keep = coverage[coverage >= MIN_COVERAGE].index
    frame = np.log(frame[keep]).dropna()
    return frame, coverage


# ---------------------------------------------------------------------------
# Statistics for one pair
# ---------------------------------------------------------------------------

def hedge_ratio(y, x):
    """OLS y = a + b x."""
    b, a = np.polyfit(x, y, 1)
    return a, b


def half_life(spread):
    """Ornstein-Uhlenbeck half-life (days) from  d s = lam * s(t-1) + e."""
    lagged = spread[:-1] - spread[:-1].mean()
    delta = np.diff(spread)
    lam = np.dot(lagged, delta) / np.dot(lagged, lagged)
    return -np.log(2) / lam if lam < 0 else np.inf


def johansen_trace(y, x):
    result = coint_johansen(np.column_stack([y, x]), det_order=0, k_ar_diff=1)
    stat, crit_95 = result.lr1[0], result.cvt[0, 1]
    return stat, crit_95


def test_pair(args):
    warnings.simplefilter("ignore")  # Johansen ComplexWarning, adfuller API notice
    a, b, form, valid, recent = args
    ya, yb = form[a], form[b]

    p_ab = coint(ya, yb, autolag="AIC")[1]   # a on b
    p_ba = coint(yb, ya, autolag="AIC")[1]   # b on a
    # Use the ordering with the stronger result for the hedge ratio.
    dep, ind = (ya, yb) if p_ab <= p_ba else (yb, ya)
    intercept, beta = hedge_ratio(dep, ind)
    spread = dep - intercept - beta * ind
    trace, trace_crit = johansen_trace(ya, yb)
    corr = np.corrcoef(np.diff(ya), np.diff(yb))[0, 1]

    row = {
        "stock_a": a, "stock_b": b,
        "dependent": a if p_ab <= p_ba else b,
        "eg_p_a_on_b": p_ab, "eg_p_b_on_a": p_ba,
        "eg_p_min": min(p_ab, p_ba), "eg_p_max": max(p_ab, p_ba),
        "johansen_trace": trace, "johansen_crit95": trace_crit,
        "johansen_pass": trace > trace_crit,
        "beta": beta, "intercept": intercept,
        "half_life_days": half_life(spread),
        "spread_sd": spread.std(),
        "return_corr": corr,
        "oos_adf_p": np.nan, "oos_zscore_last": np.nan,
        "recent_eg_p_min": np.nan,
    }

    if valid is not None and a in valid and b in valid:
        va, vb = valid[a], valid[b]
        v_dep, v_ind = (va, vb) if row["dependent"] == a else (vb, va)
        v_spread = v_dep - intercept - beta * v_ind
        row["oos_adf_p"] = adfuller(v_spread, autolag="AIC", result_object=False)[1]
        row["oos_zscore_last"] = (
            (v_spread[-1] - spread.mean()) / spread.std()
        )

    if recent is not None and a in recent and b in recent:
        ra, rb = recent[a], recent[b]
        row["recent_eg_p_min"] = min(coint(ra, rb, autolag="AIC")[1],
                                     coint(rb, ra, autolag="AIC")[1])
    return row


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def run(close, workers=None):
    frames, coverage = {}, {}
    for name, (start, end) in WINDOWS.items():
        frames[name], coverage[name] = window_prices(close, start, end)

    form = frames["formation"]
    to_dict = lambda f: {c: f[c].to_numpy() for c in f.columns}
    form_d, valid_d, recent_d = (to_dict(frames[n]) for n in
                                 ("formation", "validation", "recent"))
    stocks = list(form.columns)
    jobs = [(a, b, form_d, valid_d, recent_d)
            for a, b in itertools.combinations(stocks, 2)]

    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(test_pair, jobs, chunksize=50))
    result = pd.DataFrame(rows)

    # Multiple-testing control across all pairs.
    result["eg_q_max"] = multipletests(result["eg_p_max"], method="fdr_bh")[1]
    lo, hi = HALF_LIFE_RANGE
    hl_ok = result["half_life_days"].between(lo, hi)
    result["pass_insample"] = (
        (result["eg_p_max"] < ALPHA) & result["johansen_pass"] & hl_ok
    )
    result["pass_fdr"] = result["pass_insample"] & (result["eg_q_max"] < ALPHA)
    result["pass_oos"] = result["oos_adf_p"] < ALPHA
    result["robust"] = result["pass_insample"] & result["pass_oos"]
    result["recent_pass"] = result["recent_eg_p_min"] < ALPHA
    result["both_set50"] = (result["stock_a"].isin(SET50)
                            & result["stock_b"].isin(SET50))
    result["sector_a"] = result["stock_a"].map(SECTOR_OF)
    result["sector_b"] = result["stock_b"].map(SECTOR_OF)
    result["same_sector"] = result["sector_a"] == result["sector_b"]
    # FDR within the smaller same-sector family (fewer tests, less penalty).
    same = result["same_sector"]
    result["eg_q_within_sector"] = np.nan
    result.loc[same, "eg_q_within_sector"] = multipletests(
        result.loc[same, "eg_p_max"], method="fdr_bh")[1]
    # Recent window: min over two orderings, so double p before BH.
    recent = result["recent_eg_p_min"].notna()
    result["recent_q"] = np.nan
    result.loc[recent, "recent_q"] = multipletests(
        (2 * result.loc[recent, "recent_eg_p_min"]).clip(upper=1),
        method="fdr_bh")[1]
    result = result.sort_values(["robust", "pass_insample", "eg_p_max"],
                                ascending=[False, False, True])

    stock_info = pd.DataFrame({
        "symbol": SET100,
        "set50": [s in SET50 for s in SET100],
        **{f"coverage_{n}": coverage[n].reindex(SET100).fillna(0).round(3)
           for n in WINDOWS},
        "sector": [SECTOR_OF[s] for s in SET100],
        "first_price": [close[s].first_valid_index() for s in SET100],
        "in_formation_test": [s in stocks for s in SET100],
    })
    return result, stock_info, frames


def summary_table(result, stock_info):
    n = len(result)
    rows = [
        ("Universe (SET100 H1 2026)", len(stock_info)),
        ("Stocks with ≥95% history in formation window",
         int(stock_info["in_formation_test"].sum())),
        ("Pairs tested", n),
        ("Expected false positives at 5%", round(n * ALPHA)),
        ("EG p<0.05 in at least one ordering", int((result.eg_p_min < ALPHA).sum())),
        ("EG p<0.05 in both orderings", int((result.eg_p_max < ALPHA).sum())),
        ("Johansen trace rejects r=0 at 5%", int(result.johansen_pass.sum())),
        (f"In-sample pass (EG both + Johansen + half-life {HALF_LIFE_RANGE})",
         int(result.pass_insample.sum())),
        ("In-sample pass and BH-FDR q<0.05", int(result.pass_fdr.sum())),
        ("Robust: in-sample pass and out-of-sample ADF p<0.05",
         int(result.robust.sum())),
        ("Recent 2y EG p<0.05 (any ordering)", int(result.recent_pass.sum())),
        ("Recent 2y BH-FDR q<0.05", int((result.recent_q < ALPHA).sum())),
        ("Same-sector pairs", int(result.same_sector.sum())),
        ("Same-sector in-sample pass",
         int((result.same_sector & result.pass_insample).sum())),
        ("Same-sector BH-FDR q<0.05 (within sector family)",
         int((result.eg_q_within_sector < ALPHA).sum())),
        ("Same-sector robust",
         int((result.same_sector & result.robust).sum())),
    ]
    return pd.DataFrame(rows, columns=["item", "value"])


def write_excel(result, stock_info, path):
    columns = ["stock_a", "stock_b", "sector_a", "sector_b", "same_sector",
               "dependent", "both_set50",
               "eg_p_a_on_b", "eg_p_b_on_a", "eg_q_max", "eg_q_within_sector",
               "johansen_trace", "johansen_crit95", "beta", "half_life_days",
               "return_corr", "oos_adf_p", "oos_zscore_last",
               "recent_eg_p_min", "recent_q",
               "pass_insample", "pass_fdr", "pass_oos", "robust",
               "recent_pass"]
    run_info = pd.DataFrame([
        ("as_of", AS_OF.date()),
        *[(f"{n}_window", f"{s.date()} to {e.date()}")
          for n, (s, e) in WINDOWS.items()],
        ("price", "Yahoo Finance adjusted close, log"),
        ("alpha", ALPHA), ("min_coverage", MIN_COVERAGE),
        ("half_life_range_days", str(HALF_LIFE_RANGE)),
        ("constituents", "SET100 Jan 1 - Jun 30 2026 (SET, Dec 15 2025)"),
    ], columns=["item", "value"])
    with pd.ExcelWriter(path) as xl:
        summary_table(result, stock_info).to_excel(xl, sheet_name="Summary", index=False)
        result.loc[result.robust, columns].to_excel(xl, sheet_name="Robust", index=False)
        result.loc[result.pass_insample, columns].to_excel(xl, sheet_name="InSample_Pass", index=False)
        result[result.same_sector].sort_values("eg_p_max")[columns].to_excel(
            xl, sheet_name="Same_Sector", index=False)
        result[columns].to_excel(xl, sheet_name="All_Pairs", index=False)
        stock_info.to_excel(xl, sheet_name="Stocks", index=False)
        run_info.to_excel(xl, sheet_name="Run_Info", index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="use cached prices instead of downloading")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    cache = OUT_DIR / "set100_close.csv"
    if args.offline:
        close = pd.read_csv(cache, index_col=0, parse_dates=True)
    else:
        start = WINDOWS["formation"][0] - pd.Timedelta(days=10)
        close = download_prices(SET100, start, AS_OF, cache)
    close = clean_prices(close)

    result, stock_info, _ = run(close, args.workers)
    path = OUT_DIR / f"cointegration_set100_{AS_OF:%Y%m%d}.xlsx"
    result.to_csv(OUT_DIR / f"cointegration_set100_{AS_OF:%Y%m%d}.csv",
                  index=False)
    write_excel(result, stock_info, path)
    print(summary_table(result, stock_info).to_string(index=False))
    print(f"\nWritten: {path}")


if __name__ == "__main__":
    main()
