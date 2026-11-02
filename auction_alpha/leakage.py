"""Automated temporal-leakage tests.

``truncation_invariance``  intents for dates before a cutoff must be identical
                           whether or not data after the cutoff exists.
``perturbation_invariance`` scrambling every price after the cutoff must not
                           change any intent submitted before it.
``label_guard``            strategies cannot read forward-looking columns.
``survivorship``           securities delisted later are present (and can be
                           traded) before their delisting date.
``index_pit``              rebalance trades are never submitted before the
                           announcement was received.
``corporate_action_pit``   returns across an ex-date use the adjustment only
                           if the action was announced in time.
``auction_deadline``       every filled closing-auction order was submitted
                           before the random-close window opened.

The trading calendar itself is treated as known in advance (exchange holiday
schedules are published ahead of time), so the full calendar is passed to the
truncated run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest.engine import prepare_market
from .execution.scenarios import SCENARIOS
from .execution.simulator import ExecutionSimulator
from .features.engineering import FeatureView, LeakageError
from .rules.adapters import get_adapter
from .strategies.base import StrategyContext

COMPARE = ["trade_id", "symbol", "side", "submit_ts", "entry_date", "entry_session", "hold_sessions", "target_notional", "ref_price"]


def truncate_tables(tables: dict, cutoff: pd.Timestamp) -> dict:
    """Keep only information strictly before ``cutoff`` (a date)."""
    cutoff = pd.Timestamp(cutoff)
    end_ts = cutoff.tz_localize("UTC")
    out = {}
    for name, df in tables.items():
        if df is None or df.empty:
            out[name] = df
            continue
        d = df.copy()
        if name == "security_master":
            d = d[pd.to_datetime(d["listing_date"]) < cutoff]
            d.loc[pd.to_datetime(d["delisting_date"]) >= cutoff, "delisting_date"] = None
        elif name == "index_events":
            d = d[d["receipt_ts"] < end_ts]
        elif name == "corporate_actions":
            d = d[d["announce_ts"] < end_ts]
        elif name == "index_membership":
            d = d[pd.to_datetime(d["effective_from"]) < cutoff]
            d.loc[pd.to_datetime(d["effective_to"]) >= cutoff, "effective_to"] = None
        elif "date" in d.columns:
            d = d[pd.to_datetime(d["date"]) < cutoff]
        out[name] = d
    return out


def _ctx(bundle, capital=10e6, calendar=None):
    ad = get_adapter(bundle.market)
    sd = bundle.calendar if calendar is None else calendar[calendar <= bundle.calendar[-1]]
    return StrategyContext(bundle.market, FeatureView(bundle.features), bundle.tables, ad, bundle.md, capital, 2.0, sd)


def _intents(strategy, tables, market, calendar=None):
    b = prepare_market(tables, market)
    if calendar is not None:
        b.md.dates = calendar
        b.calendar = calendar
    ctx = _ctx(b)
    if calendar is not None:
        last_data = pd.to_datetime(tables["daily_bars"]["date"]).max()
        ctx.signal_dates = calendar[calendar <= last_data]
    return strategy.generate_intents(ctx)


def _norm(df: pd.DataFrame, before: pd.Timestamp) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COMPARE)
    df = df[pd.to_datetime(df["signal_date"]) < before]
    cols = [c for c in COMPARE if c in df.columns]
    out = df[cols].copy()
    if "target_notional" in out:
        out["target_notional"] = out["target_notional"].astype(float).round(4)
    if "ref_price" in out:
        out["ref_price"] = out["ref_price"].astype(float).round(6)
    key = [c for c in ("symbol", "submit_ts", "side") if c in out]
    out = out.drop(columns=["trade_id"], errors="ignore").sort_values(key).reset_index(drop=True)
    return out


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if len(a) == 0 and len(b) == 0:
        return True
    if a.shape != b.shape:
        return False
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    for c in a.columns:
        x, y = a[c], b[c]
        if pd.api.types.is_float_dtype(x) and pd.api.types.is_float_dtype(y):
            if not np.allclose(x.to_numpy(float), y.to_numpy(float), equal_nan=True, rtol=1e-9, atol=1e-9):
                return False
        elif not (x.astype(str).to_numpy() == y.astype(str).to_numpy()).all():
            return False
    return True


def truncation_invariance(strategy, tables: dict, market: str, cutoff) -> dict:
    cutoff = pd.Timestamp(cutoff)
    full_cal = prepare_market(tables, market).calendar
    full = _intents(strategy, tables, market)
    trunc = _intents(strategy, truncate_tables(tables, cutoff), market, calendar=full_cal)
    a, b = _norm(full, cutoff), _norm(trunc, cutoff)
    same = _same(a, b)
    return dict(test="truncation_invariance", strategy=strategy.label(), market=market, passed=bool(same),
                n_before=len(a), detail="" if same else f"full={len(a)} truncated={len(b)}")


def perturbation_invariance(strategy, tables: dict, market: str, cutoff, seed: int = 0) -> dict:
    cutoff = pd.Timestamp(cutoff)
    rng = np.random.default_rng(seed)
    pert = {k: (v.copy() if v is not None else v) for k, v in tables.items()}
    d = pert["daily_bars"]
    fut = pd.to_datetime(d["date"]) >= cutoff
    k = rng.uniform(0.5, 1.5, int(fut.sum()))
    for c in ("open", "high", "low", "close", "last_continuous_price", "close_auction_price", "preclose_bid", "preclose_ask"):
        d.loc[fut, c] = d.loc[fut, c].to_numpy() * k
    d.loc[fut, "volume"] = d.loc[fut, "volume"].to_numpy() * rng.uniform(0.1, 3, int(fut.sum()))
    ai = pert.get("auction_indicative")
    if ai is not None and not ai.empty:
        f2 = pd.to_datetime(ai["date"]) >= cutoff
        ai.loc[f2, "iep"] = ai.loc[f2, "iep"].to_numpy() * rng.uniform(0.9, 1.1, int(f2.sum()))
    for name in ("index_levels", "futures_daily"):
        t = pert.get(name)
        if t is not None and not t.empty:
            f3 = pd.to_datetime(t["date"]) >= cutoff
            for c in ("close", "open", "price_post_auction", "level_at_lcp"):
                if c in t:
                    t.loc[f3, c] = t.loc[f3, c].to_numpy() * rng.uniform(0.95, 1.05, int(f3.sum()))
    a = _norm(_intents(strategy, tables, market), cutoff)
    b = _norm(_intents(strategy, pert, market), cutoff)
    same = _same(a, b)
    return dict(test="perturbation_invariance", strategy=strategy.label(), market=market, passed=bool(same),
                n_before=len(a), detail="" if same else f"orig={len(a)} perturbed={len(b)}")


def label_guard(features: pd.DataFrame) -> dict:
    fv = FeatureView(features)
    try:
        fv["fwd_c1"]
        ok = False
    except LeakageError:
        ok = True
    return dict(test="label_guard", strategy="*", market="*", passed=ok, n_before=0,
                detail="" if ok else "FeatureView exposed a forward column")


def survivorship(tables: dict, market: str) -> dict:
    m = tables["security_master"]
    m = m[(m["market"] == market) & m["delisting_date"].notna()]
    d = tables["daily_bars"]
    d = d[d["market"] == market]
    present = [s for s in m["symbol"] if (d["symbol"] == s).any()]
    ok = len(present) == len(m)
    return dict(test="survivorship_delisted_present", strategy="*", market=market, passed=ok, n_before=len(m),
                detail=f"{len(present)}/{len(m)} delisted securities present before delisting")


def index_pit(intents: pd.DataFrame, events: pd.DataFrame, market: str) -> dict:
    if intents is None or intents.empty:
        return dict(test="index_pit", strategy="S05", market=market, passed=True, n_before=0, detail="no intents")
    ev = events[events["market"] == market]
    first = ev.groupby("symbol")["receipt_ts"].min()
    bad = 0
    for it in intents.itertuples(index=False):
        rec = ev[(ev["symbol"] == it.symbol) & (ev["receipt_ts"] <= it.submit_ts)]
        if rec.empty:
            bad += 1
    return dict(test="index_pit", strategy="S05", market=market, passed=bad == 0, n_before=len(intents),
                detail=f"{bad} intents without a previously received announcement")


def corporate_action_pit(features: pd.DataFrame) -> dict:
    bad = features["ca_unannounced"] & features["ret_cc"].notna() if "ca_unannounced" in features else pd.Series([], dtype=bool)
    return dict(test="corporate_action_pit", strategy="*", market=str(features["market"].iloc[0]) if len(features) else "*",
                passed=int(bad.sum()) == 0, n_before=int(features.get("ca_unannounced", pd.Series(dtype=bool)).sum()),
                detail=f"{int(bad.sum())} returns used an unannounced corporate action")


def auction_deadline(strategy, bundle, scenario="base") -> dict:
    ctx = _ctx(bundle)
    intents = strategy.generate_intents(ctx)
    if intents is None or intents.empty or getattr(strategy, "instrument", "stock") != "stock":
        return dict(test="auction_deadline", strategy=strategy.label(), market=bundle.market, passed=True, n_before=0, detail="n/a")
    sim = ExecutionSimulator(bundle.md, SCENARIOS[scenario], ctx.adapter).run(intents)
    f = sim.fills
    if f.empty:
        return dict(test="auction_deadline", strategy=strategy.label(), market=bundle.market, passed=True, n_before=0, detail="no fills")
    ent = f[(f["leg"] == "entry") & (f["session"] == "close_auction")].merge(
        intents[["trade_id", "submit_ts"]], on="trade_id")
    bad = 0
    for r in ent.itertuples(index=False):
        hd = bool(bundle.md.cols["is_halfday"][bundle.md.row(r.symbol, r.date)])
        w = ctx.adapter.close_auction_window(pd.Timestamp(r.date).date(), hd)
        if max(r.submit_ts, w.auction_start) >= w.random_start:
            bad += 1
    return dict(test="auction_deadline", strategy=strategy.label(), market=bundle.market, passed=bad == 0,
                n_before=len(ent), detail=f"{bad} auction fills submitted inside/after the random window")


def run_suite(tables: dict, markets, strategies: dict, cutoff=None) -> pd.DataFrame:
    rows = []
    for m in markets:
        bundle = prepare_market(tables, m)
        cal = bundle.calendar
        c = pd.Timestamp(cutoff) if cutoff is not None else cal[int(len(cal) * 0.6)]
        rows.append(label_guard(bundle.features))
        rows.append(survivorship(tables, m))
        rows.append(corporate_action_pit(bundle.features))
        for sid, cls in strategies.items():
            if cls.spec.markets and m not in cls.spec.markets:
                continue
            kw = {"min_names": 5} if sid == "S06" else {}
            s = cls(**kw)
            rows.append(truncation_invariance(s, tables, m, c))
            rows.append(perturbation_invariance(s, tables, m, c))
            rows.append(auction_deadline(s, bundle))
            if sid == "S05":
                rows.append(index_pit(s.generate_intents(_ctx(bundle)), tables["index_events"], m))
    return pd.DataFrame(rows)
