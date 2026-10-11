"""Daily snapshot of DW implied vols (every issuer) and matching CBOE option IVs.

Run once a day after the US close and before the Thai open (e.g. 07:15
Bangkok).  Appends to ``data/history/`` so the series survive after DWs
expire:

  dw_iv_daily.csv     one row per DW per day: issuer's published IV, price,
                      strike, ratio, last trade, underlying level
  cboe_iv_daily.csv   SPX / NDX / DJX options expiring on the US-index DWs'
                      last trading dates, strikes within ±35% of spot
  grids/<date>_<issuer>.csv.gz   issuers' bid grids (for fitting σ later)
  errors/<date>.csv   anything that failed

Usage:  python daily_snapshot.py [--skip-grids] [--delay 2]
"""

import argparse
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import issuer_sites as isx
import thaiwarrant

HISTORY = Path(__file__).resolve().parent / "data" / "history"
FOREIGN = {"HSI", "NIKKEI", "KS200", "BABA", "HSTECH", "NDX", "TENCEN", "BIDU", "KUAISH",
           "SPX", "KQ150", "HSCEI", "POPMAR", "XIAOMI", "VN30", "ZIJIN", "MEITUA", "CATL",
           "DJI", "GEELY", "LENOVO", "MCA50", "ALIHEA", "BILIBI", "BYDCOM", "CATHAY",
           "HUAHON", "ITC", "JD", "SUNNY", "WUXI"}
CBOE_ROOTS = {"SPX": "_SPX", "NDX": "_NDX", "DJI": "_DJX"}


def underlying_of(symbol):
    match = re.match(r"^(.*?)(\d\d)[CP]\d{4}[A-Z]$", symbol)
    return match.group(1) if match else None


def _append(path, frame, keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        frame = pd.concat([pd.read_csv(path), frame.astype(object)], ignore_index=True)
        frame = frame.drop_duplicates(keys, keep="last")
    frame.to_csv(path, index=False)


def snapshot_dws(today, delay, with_grids, errors):
    listing = isx.yuanta_search(underlying="")
    listing["underlying"] = listing["symbol"].map(underlying_of)
    rows = []

    # SET50: Yuanta's search covers every issuer with one IV method
    set50 = listing[listing["underlying"].eq("SET50")]
    for _, r in set50.iterrows():
        rows.append({"symbol": r["symbol"], "issuer": r["issuer"], "underlying": "SET50",
                     "cp": r["type"], "strike": r["exercise_price"],
                     "ratio": r["dw_per_point"], "last_trade": r["last_trade"],
                     "price": r["price"], "published_iv": r["implied_vol"],
                     "iv_source": "yuanta_search"})

    # issuers' own pages: SET50 grids + foreign DWs' published IV
    foreign = listing[listing["underlying"].isin(FOREIGN)]
    plan = {
        "YUANTA": (isx.fetch_yuanta, set50[set50["issuer"].eq("YUANTA")]["symbol"], None),
        "JPM": (isx.fetch_jpm, pd.concat([set50[set50["issuer"].eq("JPM")]["symbol"],
                                          foreign[foreign["issuer"].eq("JPM")]["symbol"]]), None),
        "BLS": (isx.fetch_bls, set50[set50["issuer"].eq("BLS")]["symbol"], None),
        "MACQ": (isx.fetch_macquarie, foreign[foreign["issuer"].eq("MACQ")]["symbol"], None),
        "KTX": (isx.fetch_ktx, foreign[foreign["issuer"].eq("KTX")]["symbol"], "ktx"),
    }
    for issuer, (fetch, symbols, session_kind) in plan.items():
        try:
            session = isx.ktx_session() if session_kind == "ktx" else None
            terms, grid, err = isx.fetch_many(fetch, sorted(set(symbols)), delay=delay,
                                              session=session)
        except Exception as exc:
            errors.append({"source": issuer, "symbol": "*", "error": str(exc)})
            continue
        errors.extend({"source": issuer, **e} for e in err.to_dict("records"))
        info = listing.set_index("symbol")
        for _, t in terms.iterrows():
            base = info.loc[t["symbol"]] if t["symbol"] in info.index else None
            rows.append({
                "symbol": t["symbol"], "issuer": issuer,
                "underlying": underlying_of(t["symbol"]),
                "cp": t.get("cp") if pd.notna(t.get("cp", np.nan)) else (base["type"] if base is not None else None),
                "strike": t.get("strike") if pd.notna(t.get("strike", np.nan)) else (base["exercise_price"] if base is not None else None),
                "ratio": t.get("ratio") if pd.notna(t.get("ratio", np.nan)) else (base["dw_per_point"] if base is not None else None),
                "last_trade": t.get("last_trade") if pd.notna(t.get("last_trade", pd.NaT)) else (base["last_trade"] if base is not None else None),
                "price": t.get("dw_last", t.get("dw_price", base["price"] if base is not None else None)),
                "underlying_price": t.get("underlying_price", t.get("underlying_last")),
                "fx": t.get("fx"), "published_iv": t.get("published_iv"),
                "iv_source": f"{issuer.lower()}_site",
            })
        if with_grids and not grid.empty:
            path = HISTORY / "grids" / f"{today:%Y%m%d}_{issuer.lower()}.csv.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            grid.to_csv(path, index=False)

    # KGI: thaiwarrant (term sheet, daily history, indicative grid)
    try:
        errs = thaiwarrant.snapshot(delay=delay)
        errors.extend({"source": "KGI", **e} for e in errs.to_dict("records"))
        kgi = pd.read_csv(thaiwarrant.DATA_DIR / "dw_terms.csv")
        for _, t in kgi[kgi["issuer"].eq("KGI")].iterrows():
            rows.append({"symbol": t["symbol"], "issuer": "KGI", "underlying": "SET50",
                         "cp": t["cp"], "strike": t["strike"], "ratio": t["ratio"],
                         "last_trade": t["last_trade"], "published_iv": t["issuer_iv_now"],
                         "iv_source": "thaiwarrant"})
    except Exception as exc:
        errors.append({"source": "KGI", "symbol": "*", "error": str(exc)})

    out = pd.DataFrame(rows)
    out.insert(0, "snapshot_date", today.date())
    return out


def snapshot_cboe(today, dws, errors):
    us = dws[dws["underlying"].isin(CBOE_ROOTS)]
    expiries = set(pd.to_datetime(us["last_trade"]).dt.normalize())
    frames = []
    for ul, root in CBOE_ROOTS.items():
        try:
            data = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{root}.json",
                                headers={"User-Agent": isx.UA}, timeout=120).json()
        except Exception as exc:
            errors.append({"source": "CBOE", "symbol": root, "error": str(exc)})
            continue
        spot = data["data"]["current_price"]
        options = pd.DataFrame(data["data"]["options"])
        parts = options["option"].str.extract(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
        options = options.assign(root=parts[0], expiry=pd.to_datetime(parts[1], format="%y%m%d"),
                                 cp=parts[2], strike=parts[3].astype(int) / 1000)
        keep = (options["expiry"].isin(expiries)
                & options["strike"].between(0.65 * spot, 1.35 * spot)
                & options["root"].isin(["SPX", "NDX", "DJX"]))
        frames.append(options.loc[keep, ["option", "root", "expiry", "cp", "strike", "bid", "ask",
                                         "iv", "delta", "open_interest", "volume"]]
                      .assign(underlying=ul, spot=spot, cboe_timestamp=data["timestamp"]))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out.insert(0, "snapshot_date", today.date())
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-grids", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
    today = pd.Timestamp.now(tz="Asia/Bangkok").normalize().tz_localize(None)
    errors = []
    started = time.time()

    dws = snapshot_dws(today, args.delay, not args.skip_grids, errors)
    _append(HISTORY / "dw_iv_daily.csv", dws, ["snapshot_date", "symbol", "iv_source"])
    cboe = snapshot_cboe(today, dws, errors)
    if not cboe.empty:
        _append(HISTORY / "cboe_iv_daily.csv", cboe, ["snapshot_date", "option"])
    if errors:
        path = HISTORY / "errors" / f"{today:%Y%m%d}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(errors).to_csv(path, index=False)

    by_source = dws.groupby("iv_source")["symbol"].nunique().to_dict()
    print(f"snapshot {today.date()}: {len(dws)} DW rows {by_source}, "
          f"{len(cboe)} CBOE rows, {len(errors)} errors, {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
