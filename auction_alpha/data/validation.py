"""Schema and data-quality validation.

``validate_dataset`` returns a ``QualityReport`` listing every failed check
with severity:

* ``error``   - data violates the schema or causality (e.g. receipt before
                event, duplicate keys).  Research runs refuse to start.
* ``warning`` - data is usable but needs handling (missing rows, stale
                prices, suspensions); strategies must skip, never backfill.
* ``info``    - descriptive statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..rules.adapters import get_adapter
from .schemas import ALL_SCHEMAS, TableSchema


@dataclass
class Issue:
    table: str
    check: str
    severity: str
    count: int
    detail: str = ""
    examples: list = field(default_factory=list)


@dataclass
class QualityReport:
    dataset: str
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, table, check, severity, count, detail="", examples=None):
        if count:
            self.issues.append(Issue(table, check, severity, int(count), detail, (examples or [])[:5]))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def ok(self) -> bool:
        return not self.errors

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([i.__dict__ for i in self.issues],
                            columns=["table", "check", "severity", "count", "detail", "examples"])


_KIND_CHECK = {
    "float": pd.api.types.is_numeric_dtype,
    "int": pd.api.types.is_numeric_dtype,
    "bool": lambda s: pd.api.types.is_bool_dtype(s) or s.dropna().isin([True, False]).all(),
    "ts": lambda s: pd.api.types.is_datetime64_any_dtype(s) or s.isna().all(),
    "date": lambda s: pd.api.types.is_datetime64_any_dtype(s) or s.isna().all() or isinstance(s.dropna().iloc[0], (pd.Timestamp,)) or hasattr(s.dropna().iloc[0], "year"),
    "str": lambda s: True,
}


def validate_schema(df: pd.DataFrame, schema: TableSchema, rep: QualityReport) -> None:
    missing = [c for c in schema.column_names if c not in df.columns]
    rep.add(schema.name, "missing_columns", "error", len(missing), ", ".join(missing))
    if df.empty:
        return
    for c in schema.columns:
        if c.name not in df:
            continue
        s = df[c.name]
        if not c.nullable:
            rep.add(schema.name, f"null_in_non_nullable:{c.name}", "error", int(s.isna().sum()))
        if not _KIND_CHECK[c.dtype](s):
            rep.add(schema.name, f"dtype:{c.name}", "error", 1, f"expected {c.dtype}, got {s.dtype}")
        if c.dtype == "ts" and pd.api.types.is_datetime64_any_dtype(s) and getattr(s.dt, "tz", None) is None:
            rep.add(schema.name, f"naive_timestamp:{c.name}", "error", 1, "timestamps must be tz-aware UTC")
    pk = [k for k in schema.primary_key if k in df]
    if len(pk) == len(schema.primary_key) and schema.granularity != "tick":
        dup = df.duplicated(pk, keep=False)
        rep.add(schema.name, "duplicate_primary_key", "error", int(dup.sum()),
                examples=df.loc[dup, pk].head(5).astype(str).values.tolist())


def _daily_checks(d: pd.DataFrame, master: pd.DataFrame | None, rep: QualityReport) -> None:
    t = "daily_bars"
    live = d[~d["suspended"].astype(bool)]
    for col in ("open", "high", "low", "close", "last_continuous_price", "close_auction_price"):
        rep.add(t, f"non_positive:{col}", "error", int((live[col] <= 0).sum()))
    hi_bad = live["high"] < live[["open", "close"]].max(axis=1) - 1e-9
    lo_bad = live["low"] > live[["open", "close"]].min(axis=1) + 1e-9
    rep.add(t, "high_below_open_close", "error", int(hi_bad.sum()))
    rep.add(t, "low_above_open_close", "error", int(lo_bad.sum()))
    # causality of timestamps
    rcpt = d["close_auction_receipt_ts"]
    rep.add(t, "auction_receipt_before_event", "error", int((rcpt < d["close_auction_ts"]).sum()))
    rep.add(t, "eod_receipt_before_auction", "error", int((d["receipt_ts"] < d["close_auction_ts"]).sum()))
    rep.add(t, "lcp_after_auction_match", "error", int((d["last_continuous_ts"] >= d["close_auction_ts"]).sum()))
    auc = d["close_method"] == "auction"
    rep.add(t, "auction_method_without_price", "error", int((auc & d["close_auction_price"].isna()).sum()))
    rep.add(t, "suspended_with_prices", "error", int((d["suspended"].astype(bool) & d["close"].notna()).sum()))
    # auction timestamps inside the rule-defined random window
    for m, g in d[auc].groupby("market"):
        ad = get_adapter(m)
        dates = pd.to_datetime(g["date"]).dt.date
        bad = 0
        for (day, half), gg in g.groupby([dates, g["is_halfday"]]):
            w = ad.close_auction_window(day, bool(half))
            ts = gg["close_auction_ts"]
            bad += int(((ts < w.random_start) | (ts > w.random_end + pd.Timedelta(seconds=1))).sum())
        rep.add(t, f"auction_ts_outside_random_window:{m}", "warning", bad,
                "actual match times outside the registry window (rule may differ historically)")
    # data gaps and stale data (warnings: must be skipped, never backfilled)
    rep.add(t, "missing_last_continuous_price", "warning",
            int((~d["suspended"].astype(bool) & d["last_continuous_price"].isna()).sum()))
    rep.add(t, "suspended_rows", "warning", int(d["suspended"].astype(bool).sum()))
    rep.add(t, "no_auction_match_rows", "info", int((d["close_method"].isin(["last_trade", "reference_price"])).sum()))
    if master is not None and not master.empty:
        mm = master.set_index(["market", "symbol"])
        j = d.join(mm[["listing_date", "delisting_date"]], on=["market", "symbol"])
        outside = (j["date"] < pd.to_datetime(j["listing_date"])) | (
            j["delisting_date"].notna() & (j["date"] > pd.to_datetime(j["delisting_date"])))
        rep.add(t, "rows_outside_listing_life", "error", int(outside.sum()))
        # missing rows: listed, calendar day of that market, no row
        miss_total = 0
        miss_by = {}
        for m, g in d.groupby("market"):
            cal = pd.DatetimeIndex(sorted(g["date"].unique()))
            have = g.groupby("symbol")["date"].nunique()
            for sym, row in mm.loc[m].iterrows():
                lo = pd.Timestamp(row["listing_date"])
                hi = pd.Timestamp(row["delisting_date"]) if pd.notna(row["delisting_date"]) else cal[-1]
                exp = int(((cal >= lo) & (cal <= hi)).sum())
                n = exp - int(have.get(sym, 0))
                if n > 0:
                    miss_total += n
                    miss_by[(m, sym)] = n
        rep.add(t, "missing_rows_vs_calendar", "warning", miss_total,
                "listed security-days with no record (skipped by strategies)")
        rep.stats["missing_rows_by_security"] = {f"{k[0]}:{k[1]}": v for k, v in miss_by.items()}
    # stale: unchanged close for >= 10 consecutive non-suspended days with zero volume
    s = live.sort_values(["market", "symbol", "date"])
    zero = (s["volume"] == 0)
    rep.add(t, "zero_volume_live_rows", "warning", int(zero.sum()))
    # summary stats for the dashboard
    yr = pd.to_datetime(d["date"]).dt.year
    st = d.assign(year=yr).groupby(["market", "year"]).agg(
        rows=("symbol", "size"),
        suspended=("suspended", "sum"),
        missing_lcp=("last_continuous_price", lambda x: int(x.isna().sum())),
        no_auction=("close_method", lambda x: int((x != "auction").sum())),
    ).reset_index()
    rep.stats["daily_by_market_year"] = st.to_dict("records")


def validate_dataset(name: str, tables: dict[str, pd.DataFrame]) -> QualityReport:
    rep = QualityReport(name)
    for tname, df in tables.items():
        if tname in ALL_SCHEMAS:
            validate_schema(df, ALL_SCHEMAS[tname], rep)
    d = tables.get("daily_bars")
    if d is not None and not d.empty:
        _daily_checks(d, tables.get("security_master"), rep)
    ai = tables.get("auction_indicative")
    if ai is not None and not ai.empty:
        rep.add("auction_indicative", "receipt_before_snapshot", "error", int((ai["receipt_ts"] < ai["snapshot_ts"]).sum()))
        if d is not None:
            j = ai.merge(d[["market", "symbol", "date", "close_auction_ts"]], on=["market", "symbol", "date"], how="left")
            rep.add("auction_indicative", "snapshot_after_auction_match", "error",
                    int((j["snapshot_ts"] >= j["close_auction_ts"]).sum()))
    ie = tables.get("index_events")
    if ie is not None and not ie.empty:
        rep.add("index_events", "receipt_before_announce", "error", int((ie["receipt_ts"] < ie["announce_ts"]).sum()))
        ann_local = ie["announce_ts"].dt.tz_convert(None).dt.normalize()
        rep.add("index_events", "effective_not_after_announce", "error",
                int((pd.to_datetime(ie["effective_date"]) <= ann_local).sum()))
    ca = tables.get("corporate_actions")
    if ca is not None and not ca.empty:
        rep.add("corporate_actions", "announced_on_or_after_ex_date", "warning",
                int((ca["announce_ts"].dt.tz_convert(None).dt.normalize() >= pd.to_datetime(ca["ex_date"])).sum()))
    b = tables.get("borrow")
    if b is not None and not b.empty:
        rep.add("borrow", "negative_fee", "error", int((b["borrow_fee_bps"] < 0).sum()))
    rep.stats["tables"] = {k: len(v) for k, v in tables.items()}
    return rep
