"""Ingestion interfaces for real vendor data.

No real market data ships with this repository.  To ingest vendor files,
write a mapping YAML (see ``config/ingest_template.yaml``)::

    dataset: real_set_2018_2026
    is_synthetic: false
    tables:
      daily_bars:
        path: /data/vendor/set_daily_*.csv
        format: csv
        timezone: Asia/Bangkok          # for naive timestamps in the file
        columns: {canonical_name: vendor_column, ...}
        constants: {market: SET}
        receipt_ts_rule: "eod+18:00"    # if the vendor file has no receipt time

and run ``python -m auction_alpha ingest --mapping my_mapping.yaml``.
The data are validated against the canonical schemas before being written.
Receipt times must never be fabricated as earlier than the event: when a
vendor provides no receipt time the configured rule assigns a conservative
(late) receipt time.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd
import yaml

from .schemas import ALL_SCHEMAS
from .store import DataStore
from .validation import validate_dataset


def _read_any(path_glob: str, fmt: str) -> pd.DataFrame:
    files = sorted(glob.glob(path_glob))
    if not files:
        raise FileNotFoundError(path_glob)
    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    return pd.concat([reader(f) for f in files], ignore_index=True)


def map_table(raw: pd.DataFrame, table: str, spec: dict) -> pd.DataFrame:
    schema = ALL_SCHEMAS[table]
    cols = spec.get("columns", {})
    out = pd.DataFrame(index=raw.index)
    for c in schema.columns:
        if c.name in spec.get("constants", {}):
            out[c.name] = spec["constants"][c.name]
        elif c.name in cols:
            out[c.name] = raw[cols[c.name]]
        else:
            out[c.name] = pd.NA
    tz = spec.get("timezone", "UTC")
    for c in schema.columns:
        if c.dtype == "ts":
            s = pd.to_datetime(out[c.name], errors="coerce")
            if getattr(s.dt, "tz", None) is None:
                s = s.dt.tz_localize(tz)
            out[c.name] = s.dt.tz_convert("UTC")
        elif c.dtype == "date":
            out[c.name] = pd.to_datetime(out[c.name], errors="coerce").dt.normalize()
        elif c.dtype in ("float", "int"):
            out[c.name] = pd.to_numeric(out[c.name], errors="coerce")
        elif c.dtype == "bool":
            out[c.name] = out[c.name].fillna(False).astype(bool)
    rule = spec.get("receipt_ts_rule")
    if rule and "receipt_ts" in out and out["receipt_ts"].isna().all():
        if rule.startswith("eod+"):
            hhmm = rule[4:]
            local = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d") + " " + hhmm
            out["receipt_ts"] = pd.to_datetime(local).dt.tz_localize(tz).dt.tz_convert("UTC")
        elif rule == "next_day_open":
            local = (pd.to_datetime(out["date"]) + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d") + " 08:00"
            out["receipt_ts"] = pd.to_datetime(local).dt.tz_localize(tz).dt.tz_convert("UTC")
    if table == "daily_bars":
        out["suspended"] = out["suspended"].fillna(False).astype(bool)
        out["is_halfday"] = out["is_halfday"].fillna(False).astype(bool)
        if out["close_method"].isna().all():
            out["close_method"] = out["close_auction_price"].notna().map({True: "auction", False: "none"})
    return out


def ingest_from_mapping(mapping_path: Path | str, store: DataStore) -> tuple[str, object]:
    spec = yaml.safe_load(Path(mapping_path).read_text())
    tables = {}
    for tname, tspec in spec["tables"].items():
        raw = _read_any(tspec["path"], tspec.get("format", "csv"))
        tables[tname] = map_table(raw, tname, tspec)
    rep = validate_dataset(spec["dataset"], tables)
    if not rep.ok():
        raise ValueError("validation errors: " + "; ".join(f"{i.table}:{i.check}={i.count}" for i in rep.errors))
    version = store.write_dataset(spec["dataset"], tables, is_synthetic=bool(spec.get("is_synthetic", False)),
                                  provenance={"mapping": str(mapping_path), "sources": spec.get("sources", [])})
    return version, rep


def derive_last_continuous(trades: pd.DataFrame, auction_start: pd.Series) -> pd.DataFrame:
    """Last continuous-session trade strictly before the closing-auction start.

    ``auction_start`` is indexed by (market, symbol, date).  Uses only trades
    with ``phase == 'continuous'`` and ``ts < auction_start`` so that no
    auction or post-close information can leak into the feature.
    """
    t = trades[trades["phase"].fillna("continuous") == "continuous"].copy()
    t["date"] = pd.to_datetime(t["date"])
    a = auction_start.rename("auction_start").reset_index()
    a["date"] = pd.to_datetime(a["date"])
    t = t.merge(a, on=["market", "symbol", "date"], how="inner")
    t = t[t["ts"] < t["auction_start"]]
    t = t.sort_values("ts").groupby(["market", "symbol", "date"]).tail(1)
    return t.rename(columns={"price": "last_continuous_price", "ts": "last_continuous_ts"})[
        ["market", "symbol", "date", "last_continuous_price", "last_continuous_ts", "receipt_ts"]]
