"""Data-availability matrix: market x dataset, and strategy x market support.

Statuses
--------
real            present in a non-synthetic dataset in the store
synthetic_only  present only in synthetic datasets (engineering use only)
missing         not present anywhere
not_applicable  dataset/strategy does not apply to that market (by rule)
"""

from __future__ import annotations

import pandas as pd

from ..rules.adapters import MARKETS
from .store import DataStore

DATASETS = {
    "daily_bars": "Historical daily prices and volumes",
    "intraday_trades": "Intraday trades",
    "intraday_quotes": "Intraday quotes",
    "daily_bars.last_continuous_price": "Last continuous-session price",
    "daily_bars.close_auction_price": "Final closing-auction price and matched volume",
    "auction_indicative": "Indicative equilibrium prices / imbalance",
    "daily_bars.close_auction_ts": "Actual auction matching timestamps",
    "daily_bars.close_auction_receipt_ts": "Real-time receipt of the auction result",
    "daily_bars.tac_volume": "SGX Trade-at-Close volume",
    "order_book_depth": "Historical order-book depth",
    "index_membership": "Point-in-time index constituents",
    "index_events": "Index review announcements",
    "corporate_actions": "Corporate actions",
    "borrow": "Stock-borrow eligibility and costs",
    "futures_daily": "Index futures (incl. post-auction price)",
    "index_levels": "Index levels",
}

# Known potential real sources (NOT claimed to be accessible; documentation only).
POTENTIAL_SOURCES = {
    "SET": "SETSMART / SET Market Data (licensed); TFEX for SET50 futures; SET short-sale eligible list",
    "SGX": "SGX Market Data / SGX Titan (licensed); SGX-DT for STI futures; SBL data from brokers",
    "HKEX": "HKEX Data Marketplace / OMD-C historical (licensed); HKEX designated short-sell list",
}


def _present(df: pd.DataFrame | None, market: str, column: str | None) -> bool:
    if df is None or df.empty:
        return False
    if "market" in df.columns:
        df = df[df["market"] == market]
    if df.empty:
        return False
    if column is None:
        return True
    return column in df.columns and df[column].notna().any()


def availability_matrix(store: DataStore, datasets: list[str] | None = None) -> pd.DataFrame:
    rows = []
    for ds in (datasets if datasets is not None else store.datasets()):
        man = store.manifest(ds)
        syn = man["is_synthetic"]
        tables = {t: store.read(ds, t) for t in man["tables"]}
        for key in DATASETS:
            tname, _, col = key.partition(".")
            for m in MARKETS:
                if key == "daily_bars.tac_volume" and m != "SGX":
                    st = "not_applicable"
                else:
                    st = ("real" if not syn else "synthetic_only") if _present(tables.get(tname), m, col or None) else "missing"
                rows.append({"dataset_name": ds, "is_synthetic": syn, "data": key, "market": m, "status": st})
    df = pd.DataFrame(rows, columns=["dataset_name", "is_synthetic", "data", "market", "status"])
    if df.empty:
        return pd.DataFrame([{"data": k, "market": m, "status": "missing"} for k in DATASETS for m in MARKETS])
    rank = {"real": 3, "synthetic_only": 2, "not_applicable": 1, "missing": 0}
    df["r"] = df["status"].map(rank)
    best = df.sort_values("r").groupby(["data", "market"]).tail(1)
    return best[["data", "market", "status"]].reset_index(drop=True)


def matrix_wide(av: pd.DataFrame) -> pd.DataFrame:
    w = av.pivot(index="data", columns="market", values="status").reindex(list(DATASETS))
    w.insert(0, "description", [DATASETS[k] for k in w.index])
    return w


def strategy_support(av: pd.DataFrame, strategies: dict) -> pd.DataFrame:
    """For each strategy and market: supported_real / synthetic_only / unsupported / not_applicable."""
    status = {(r.data, r.market): r.status for r in av.itertuples()}
    rows = []
    for sid, cls in strategies.items():
        spec = cls.spec
        for m in MARKETS:
            if spec.markets and m not in spec.markets:
                rows.append({"strategy": sid, "market": m, "support": "not_applicable", "missing": "rule: market not applicable"})
                continue
            sts = [status.get((d, m), "missing") for d in spec.required_data]
            missing = [d for d, s in zip(spec.required_data, sts) if s == "missing"]
            if missing:
                sup = "unsupported"
            elif all(s in ("real", "not_applicable") for s in sts):
                sup = "supported_real"
            else:
                sup = "synthetic_only"
            rows.append({"strategy": sid, "market": m, "support": sup, "missing": ", ".join(missing)})
    return pd.DataFrame(rows)
