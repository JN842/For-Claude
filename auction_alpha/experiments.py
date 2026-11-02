"""Experiment orchestration: run-study / run-all.

Protocol per (strategy, market)
-------------------------------
1. Data support check (availability matrix).  Unsupported -> recorded, skipped.
2. Diagnostic study (mode A, labelled NOT EXECUTABLE) on non-holdout dates.
3. Parameter grid under the *base* scenario.  Selection uses **discovery-period
   net Sharpe only**; the chosen parameters are frozen in ``selection.json``.
4. Selected parameters evaluated under all execution scenarios, with period
   metrics (discovery / validation / OOS).  The holdout is never touched
   unless ``--unlock-holdout`` is given together with a frozen selection file.
5. Robustness: cost, fill-rate and delay sensitivity, capacity sweep, placebo
   (random signs), hedged vs unhedged, regimes.
6. Multiple testing: BH-FDR and Holm across every grid point x strategy x
   market tested in discovery.
7. Verdict per study (see ``verdict``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import multiprocessing as mp
import subprocess
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .backtest.engine import DEFAULT_PERIODS, MarketBundle, prepare_market, run_strategy
from .backtest.portfolio import account
from .data.availability import availability_matrix, strategy_support
from .data.store import DataStore
from .data.synthetic import SyntheticConfig, WorldParams, generate, planted_truth
from .data.validation import validate_dataset
from .execution.scenarios import load_scenarios
from .execution.simulator import ExecutionSimulator
from .rules.adapters import get_adapter
from .rules.registry import default_registry
from .stats.inference import bh_fdr, holm, newey_west_tstat
from .stats.metrics import performance
from .strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------- utilities
def jsonable(o):
    if isinstance(o, pd.DataFrame):
        return {"__frame__": True, "records": jsonable(o.to_dict("records"))}
    if isinstance(o, pd.Series):
        return jsonable(o.to_dict())
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if not math.isfinite(v) else v
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
        return str(o)
    if o is pd.NaT:
        return None
    return o


def frame(o) -> pd.DataFrame:
    if isinstance(o, dict) and o.get("__frame__"):
        return pd.DataFrame(o["records"])
    return pd.DataFrame(o or [])


def git_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def load_config(path: Path | str) -> dict:
    cfg = yaml.safe_load(Path(path).read_text())
    cfg.setdefault("periods", DEFAULT_PERIODS)
    cfg["periods"] = {k: tuple(v) for k, v in cfg["periods"].items()}
    cfg.setdefault("markets", ["SET", "SGX", "HKEX"])
    cfg.setdefault("scenarios", ["optimistic", "base", "conservative", "stress"])
    cfg.setdefault("robustness", {})
    cfg["_path"] = str(path)
    return cfg


def _rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def config_hash(cfg: dict) -> str:
    c = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(c, sort_keys=True, default=str).encode()).hexdigest()[:10]


# ------------------------------------------------------------------- dataset
def synthetic_config_for(name: str, scale: str = "full") -> SyntheticConfig:
    world = WorldParams.null() if "null" in name else WorldParams()
    cfg = SyntheticConfig(world=world)
    if scale == "small":
        cfg = cfg.small(n_stocks=20, start="2018-01-02", end="2026-10-30")
    elif scale == "tiny":
        cfg = cfg.small(n_stocks=12, start="2020-01-02", end="2024-06-28")
    return cfg


def ensure_dataset(store: DataStore, name: str, scale: str = "full", force: bool = False) -> str:
    if store.has(name) and not force:
        return store.manifest(name)["data_version"]
    if not name.startswith("synthetic"):
        raise FileNotFoundError(f"dataset '{name}' not in store {store.root}; ingest it first (see docs/REPRODUCIBILITY.md)")
    scfg = synthetic_config_for(name, scale)
    tables = generate(scfg)
    rep = validate_dataset(name, tables)
    if not rep.ok():
        raise RuntimeError("synthetic data failed validation: " + str([(i.table, i.check) for i in rep.errors]))
    prov = {"generator": "auction_alpha.data.synthetic", "config": dataclasses.asdict(scfg),
            "planted_truth": planted_truth(scfg), "label": "SYNTHETIC - NOT REAL MARKET DATA", "scale": scale}
    return store.write_dataset(name, tables, is_synthetic=True, provenance=prov)


# ---------------------------------------------------------------- one study
_G: dict = {}


def _bundle(market: str) -> MarketBundle:
    if market not in _G["bundles"]:
        _G["bundles"][market] = prepare_market(_G["tables"], market)
    return _G["bundles"][market]


def _grid(spec, cfg_entry: dict) -> list[dict]:
    if not cfg_entry.get("grid", True):
        return [dict(cfg_entry.get("params", {}))]
    base = dict(cfg_entry.get("params", {}))
    grid = dict(spec.param_grid)
    grid.update(cfg_entry.get("grid_override", {}) or {})
    combos = [dict(base)]
    for k, vals in grid.items():
        combos = [dict(c, **{k: v}) for c in combos for v in vals]
    seen, out = set(), []
    for c in combos:
        key = json.dumps(c, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _regimes(daily: pd.DataFrame, bundle: MarketBundle) -> pd.DataFrame:
    lv = bundle.tables.get("index_levels")
    if lv is None or lv.empty or daily.empty:
        return pd.DataFrame()
    lv = lv[lv["market"] == bundle.market].assign(date=lambda x: pd.to_datetime(x["date"])).sort_values("date")
    r = np.log(lv["close"] / lv["close"].shift(1))
    lv = lv.assign(mret=r, rv=r.rolling(20).std())
    lv["vol_regime"] = pd.qcut(lv["rv"], 3, labels=["low_vol", "mid_vol", "high_vol"])
    lv["direction"] = np.where(lv["mret"] >= 0, "index_up_day", "index_down_day")
    d = daily.merge(lv[["date", "vol_regime", "direction"]], on="date", how="left")
    rows = []
    for col in ("vol_regime", "direction"):
        for k, g in d.groupby(col, observed=True):
            x = g["ret"].to_numpy()
            sd = x.std(ddof=1)
            rows.append(dict(dimension=col, regime=str(k), n_days=len(x), mean_daily_bps=x.mean() * 1e4,
                             sharpe=x.mean() / sd * math.sqrt(bundle.periods_per_year) if sd > 0 else np.nan))
    return pd.DataFrame(rows)


def _resim(strategy, bundle, intents, scenario, capital, cfg, fee_mult=1.0):
    adapter = get_adapter(bundle.market)
    if getattr(strategy, "instrument", "stock") == "future":
        sim = strategy.simulate(intents, bundle.md, scenario, adapter, capital)
    else:
        sim = ExecutionSimulator(bundle.md, scenario, adapter).run(intents)
    fills = sim.fills.copy()
    if fee_mult != 1.0 and not fills.empty:
        fills["fees"] = fills["fees"] * fee_mult
    acc = account(fills, bundle.md.bars, capital=capital, corporate_actions=bundle.tables.get("corporate_actions"),
                  futures=bundle.tables.get("futures_daily"), market=bundle.market,
                  funding_spread_bps=cfg.get("funding_spread_bps", 100.0), calendar=bundle.calendar)
    from .backtest.engine import _full_calendar, signal_dates
    sd = signal_dates(bundle.calendar, cfg["periods"])
    daily = _full_calendar(acc.daily, bundle.calendar, sd, capital)
    m = performance(daily, acc.trades, capital=capital, periods_per_year=bundle.periods_per_year, bootstrap=False)
    t = sim.trades
    m["fill_rate"] = float(t["entry_qty"].sum() / t["intended_qty"].sum()) if t is not None and len(t) and t["intended_qty"].sum() > 0 else np.nan
    return m, daily


def run_study(sid: str, market: str, cfg: dict, entry: dict, out_dir: Path, support: str) -> dict:
    cls = STRATEGIES[sid]
    spec = cls.spec
    res: dict = dict(strategy=sid, name=spec.name, market=market, support=support, spec=dataclasses.asdict(spec))
    sdir = out_dir / "studies" / f"{sid}_{market}"
    sdir.mkdir(parents=True, exist_ok=True)
    if support in ("unsupported", "not_applicable"):
        res["status"] = support
        (sdir / "study.json").write_text(json.dumps(jsonable(res), indent=1))
        return res
    bundle = _bundle(market)
    capital = float(cfg["capital"][market])
    periods = cfg["periods"]
    scen = load_scenarios(cfg.get("scenarios_file"))
    rob = cfg.get("robustness", {})
    lat = float(cfg.get("decision_latency_s", 2.0))
    unlocked = bool(cfg.get("_holdout_unlocked", False))
    common = dict(capital=capital, periods=periods, decision_latency_s=lat,
                  funding_spread_bps=cfg.get("funding_spread_bps", 100.0), holdout_unlocked=unlocked)
    # -------------------------------------------------- diagnostic (mode A)
    from .features.engineering import FeatureView
    from .strategies.base import StrategyContext
    from .backtest.engine import signal_dates
    sd = signal_dates(bundle.calendar, periods, holdout_unlocked=unlocked)
    ctx = StrategyContext(market, FeatureView(bundle.features, diagnostic=True), bundle.tables, get_adapter(market),
                          bundle.md, capital, lat, sd)
    base_params = dict(entry.get("params", {}))
    try:
        res["diagnostic"] = cls(**base_params).diagnostic(ctx)
    except Exception as e:  # diagnostics must never kill the study
        res["diagnostic"] = {"error": repr(e)}
    # -------------------------------------------------- grid on discovery
    frozen = (cfg.get("_frozen_selection") or {}).get(f"{sid}_{market}")
    grid_rows = []
    if frozen is None:
        combos = _grid(spec, entry)
        for params in combos:
            s = cls(**params)
            r = run_strategy(s, bundle, scenarios=[scen["base"]], bootstrap=False, **common)
            run = r.runs["base"]
            disc = run.period_metrics.get("discovery", {})
            disc_daily = run.acc.daily[(run.acc.daily["date"] >= pd.Timestamp(periods["discovery"][0])) &
                                       (run.acc.daily["date"] <= pd.Timestamp(periods["discovery"][1]))]
            _, _, p = newey_west_tstat(disc_daily["ret"].to_numpy()) if len(disc_daily) > 3 else (0, 0, np.nan)
            yearly = run.yearly if run.yearly is not None else pd.DataFrame()
            grid_rows.append(dict(params=params, label=s.label(), disc_sharpe=disc.get("sharpe", np.nan),
                                  disc_ann_return=disc.get("ann_return", np.nan), disc_trades=disc.get("trade_count", 0),
                                  disc_nw_p=p, disc_mean=float(disc_daily["ret"].mean()) if len(disc_daily) else np.nan,
                                  yearly=yearly[["year", "ret_net", "ret_gross"]].to_dict("records") if len(yearly) else []))
        ok = [g for g in grid_rows if g["disc_trades"] >= int(cfg.get("selection", {}).get("min_trades", 20))
              and np.isfinite(g["disc_sharpe"])]
        best = max(ok, key=lambda g: g["disc_sharpe"]) if ok else (grid_rows[0] if grid_rows else None)
        sel_params = best["params"] if best else base_params
    else:
        sel_params = frozen
    res["grid"] = [{k: v for k, v in g.items()} for g in grid_rows]
    res["selected_params"] = sel_params
    res["selection_rule"] = "max discovery-period net Sharpe under the base scenario (min trades enforced)"
    # -------------------------------------------------- full evaluation
    strat = cls(**sel_params)
    res["selected_label"] = strat.label()
    sc_list = [scen[s] for s in cfg["scenarios"]]
    run = run_strategy(strat, bundle, scenarios=sc_list, n_boot=int(rob.get("bootstrap_n", 300)), **common)
    res["n_intents"] = run.n_intents
    res["scenarios"] = {}
    for name, r in run.runs.items():
        res["scenarios"][name] = dict(metrics=r.metrics, periods=r.period_metrics,
                                      yearly=r.yearly if r.yearly is not None else pd.DataFrame(),
                                      rejection_counts=r.sim.rejections["reason"].value_counts().to_dict() if len(r.sim.rejections) else {})
        keep = ["date", "ret", "pnl", "pnl_gross", "pnl_long", "pnl_short", "pnl_hedge", "nav", "gross_exposure",
                "fees", "slippage", "borrow", "n_positions"]
        r.acc.daily[keep].to_parquet(sdir / f"daily_{name}.parquet", index=False)
    base = run.runs.get("base") or next(iter(run.runs.values()))
    if not base.acc.trades.empty:
        tr = base.acc.trades.merge(base.sim.trades[["trade_id", "signal_date", "signal", "intended_qty", "entry_qty",
                                                    "status", "entry_session", "exit_session"]], on="trade_id", how="left")
        tr.to_parquet(sdir / "trades_base.parquet", index=False)
    if len(base.sim.rejections):
        base.sim.rejections.to_parquet(sdir / "rejections_base.parquet", index=False)
    run.intents.head(20000).to_parquet(sdir / "intents.parquet", index=False) if len(run.intents) else None
    # -------------------------------------------------- robustness (base)
    robust: dict = {}
    intents = run.intents
    bsc = scen["base"]
    if len(intents):
        rows = []
        for mlt in rob.get("cost_multipliers", [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]):
            sc2 = dataclasses.replace(bsc, impact_coef=bsc.impact_coef * mlt, spread_mult=bsc.spread_mult * mlt,
                                      extra_slippage_bps=bsc.extra_slippage_bps * mlt, futures_cost_mult=bsc.futures_cost_mult * mlt)
            m, _ = _resim(strat, bundle, intents, sc2, capital, cfg, fee_mult=mlt)
            rows.append(dict(cost_multiplier=mlt, sharpe=m.get("sharpe"), ann_return=m.get("ann_return"),
                             bps_per_trade=m.get("bps_per_trade")))
        robust["cost_sensitivity"] = pd.DataFrame(rows)
        rows = []
        for fr in rob.get("fill_ratios", [0.25, 0.5, 0.75, 1.0]):
            m, _ = _resim(strat, bundle, intents, dataclasses.replace(bsc, fill_ratio=fr), capital, cfg)
            rows.append(dict(fill_ratio=fr, realised_fill_rate=m.get("fill_rate"), sharpe=m.get("sharpe"),
                             ann_return=m.get("ann_return"), trades=m.get("trade_count")))
        for pc in rob.get("participation_caps", [0.025, 0.05, 0.1, 0.2]):
            m, _ = _resim(strat, bundle, intents, dataclasses.replace(bsc, auction_participation=pc, open_participation=pc,
                                                                      tac_participation=pc), capital, cfg)
            rows.append(dict(participation_cap=pc, realised_fill_rate=m.get("fill_rate"), sharpe=m.get("sharpe"),
                             ann_return=m.get("ann_return"), trades=m.get("trade_count")))
        robust["fill_sensitivity"] = pd.DataFrame(rows)
        rows = []
        for L in rob.get("latencies_s", [0.0, 2.0, 30.0, 120.0, 300.0]):
            r2 = run_strategy(strat, bundle, scenarios=[bsc], bootstrap=False, **dict(common, decision_latency_s=L))
            m = r2.runs["base"].metrics
            rows.append(dict(variant=f"decision_latency={L:g}s", sharpe=m.get("sharpe"), ann_return=m.get("ann_return"),
                             fill_rate=m.get("fill_rate"), trades=m.get("trade_count")))
        if "entry_session" in sel_params or "entry_session" in spec.default_params:
            for es in ("open_auction", "open_continuous"):
                r2 = run_strategy(cls(**dict(sel_params, entry_session=es)), bundle, scenarios=[bsc], bootstrap=False, **common)
                m = r2.runs["base"].metrics
                rows.append(dict(variant=f"entry_session={es}", sharpe=m.get("sharpe"), ann_return=m.get("ann_return"),
                                 fill_rate=m.get("fill_rate"), trades=m.get("trade_count")))
        robust["delay_sensitivity"] = pd.DataFrame(rows)
        rows = []
        for mult in rob.get("capital_multiples", [0.1, 0.5, 1.0, 4.0, 16.0]):
            r2 = run_strategy(strat, bundle, scenarios=[bsc], bootstrap=False, **dict(common, capital=capital * mult))
            m = r2.runs["base"].metrics
            rows.append(dict(capital=capital * mult, ann_pnl=m.get("ann_return", 0) * capital * mult,
                             sharpe=m.get("sharpe"), fill_rate=m.get("fill_rate"), avg_entry_notional=m.get("avg_entry_notional"),
                             bps_per_trade=m.get("bps_per_trade")))
        cap = pd.DataFrame(rows)
        robust["capacity"] = cap
        pos = cap[(cap["ann_pnl"] > 0) & (cap["sharpe"] > 0)]
        robust["capacity_estimate"] = float(pos.loc[pos["ann_pnl"].idxmax(), "capital"]) if len(pos) else 0.0
        # placebo: random signs, same timing and sizes
        rng = np.random.default_rng(int(rob.get("seed", 7)))
        pl = []
        for k in range(int(rob.get("placebo_n", 10))):
            it2 = intents.copy()
            it2["side"] = rng.choice([-1, 1], len(it2))
            it2["trade_id"] = it2["trade_id"] + f"|pl{k}"
            m, _ = _resim(strat, bundle, it2, bsc, capital, cfg)
            pl.append(m.get("sharpe", np.nan))
        act = base.metrics.get("sharpe", np.nan)
        plv = np.array([x for x in pl if x is not None and np.isfinite(x)])
        robust["placebo"] = dict(sharpes=pl, actual=act,
                                 p_value=float((1 + (plv >= act).sum()) / (1 + len(plv))) if len(plv) and np.isfinite(act) else None)
        if "hedge" in spec.default_params:
            rows = []
            for h in (False, True):
                r2 = run_strategy(cls(**dict(sel_params, hedge=h)), bundle, scenarios=[bsc], bootstrap=False, **common)
                m = r2.runs["base"].metrics
                rows.append(dict(hedge=h, sharpe=m.get("sharpe"), ann_return=m.get("ann_return"),
                                 pnl_hedge=m.get("pnl_hedge"), pnl_unhedged=m.get("pnl_unhedged"), ann_vol=m.get("ann_vol")))
            robust["hedge_compare"] = pd.DataFrame(rows)
        robust["regimes"] = _regimes(base.acc.daily, bundle)
    res["robustness"] = robust
    res["shortfall"] = _shortfall(res)
    res["status"] = "ran"
    (sdir / "study.json").write_text(json.dumps(jsonable(res), indent=1, default=str))
    return res


def _shortfall(res: dict) -> dict:
    """Theoretical (diagnostic) vs executable vs realised returns per trade, in bps."""
    d = res.get("diagnostic") or {}
    m = (res.get("scenarios", {}).get("base") or {}).get("metrics", {})
    out = dict(realised_gross_bps=m.get("gross_bps_per_trade"), realised_net_bps=m.get("bps_per_trade"))
    summ = d.get("summary")
    if isinstance(summ, pd.DataFrame) and "horizon" in summ:
        s = summ[summ.get("kind", "market_adjusted") == "market_adjusted"] if "kind" in summ else summ
        for lab in ("C0->O1", "C0->C1", "O1->C1"):
            row = s[s["horizon"] == lab]
            if len(row):
                out[f"diagnostic_{lab}_bps"] = float(row["mean_bps"].iloc[0])
    return out


def _worker(args):
    sid, market, entry, support = args
    try:
        return run_study(sid, market, _G["cfg"], entry, _G["out_dir"], support)
    except Exception as e:
        return dict(strategy=sid, market=market, status="error", error=repr(e), traceback=traceback.format_exc())


# -------------------------------------------------------------- run-all
def verdict(row: dict) -> str:
    if row.get("status") in ("unsupported", "not_applicable", "error"):
        return {"unsupported": "untestable_missing_data", "not_applicable": "not_applicable", "error": "error"}[row["status"]]
    q = row.get("disc_q_bh")
    val, oos = row.get("validation_sharpe"), row.get("oos_sharpe")
    cons = row.get("conservative_sharpe")
    diag_sig = row.get("diagnostic_significant")
    exec_ok = (q is not None and q < 0.05 and (val or -1) > 0 and (oos or -1) > 0 and (cons or -1) > 0
               and (row.get("placebo_p") is None or row.get("placebo_p") <= 0.1))
    if exec_ok:
        return "executable_edge_after_costs"
    if (row.get("gross_sharpe") or 0) > 0.5 or diag_sig:
        return "statistical_anomaly_not_executable"
    return "no_robust_effect"


def run_all(cfg: dict, *, only: list[tuple[str, str]] | None = None, processes: int | None = None,
            unlock_holdout: bool = False, selection_file: str | None = None) -> Path:
    store = DataStore(ROOT / cfg.get("data_root", "data/store"))
    ds = cfg["dataset"]
    version = ensure_dataset(store, ds, cfg.get("synthetic_scale", "full"))
    man = store.manifest(ds)
    tables = store.load(ds, markets=cfg["markets"])
    if unlock_holdout:
        if not selection_file or not Path(selection_file).exists():
            raise SystemExit("--unlock-holdout requires --selection <frozen selection.json> from an earlier run")
        cfg["_frozen_selection"] = json.loads(Path(selection_file).read_text())["selected"]
        cfg["_holdout_unlocked"] = True
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{config_hash(cfg)}"
    out_dir = ROOT / cfg.get("results_root", "results") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.yaml").write_text(yaml.safe_dump({k: (list(v) if isinstance(v, tuple) else v)
                                                         for k, v in cfg.items() if not k.startswith("_")}, sort_keys=False))
    av = availability_matrix(store)
    av_ds = availability_for_dataset(store, ds)
    support = strategy_support(av_ds, STRATEGIES)
    sup = {(r.strategy, r.market): r.support for r in support.itertuples()}
    rep = validate_dataset(ds, tables)
    manifest = dict(run_id=run_id, created_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    dataset=ds, data_version=version, is_synthetic=man["is_synthetic"],
                    provenance=man.get("provenance", {}), registry_version=default_registry().version_hash(),
                    code_version=git_hash(), config_hash=config_hash(cfg), config_file=_rel(cfg["_path"]),
                    holdout_unlocked=unlock_holdout, periods=cfg["periods"], capital=cfg["capital"])
    (out_dir / "manifest.json").write_text(json.dumps(jsonable(manifest), indent=1))
    (out_dir / "data_quality.json").write_text(json.dumps(jsonable(dict(issues=rep.to_frame(), stats=rep.stats)), indent=1))
    av.to_csv(out_dir / "availability_matrix.csv", index=False)
    support.to_csv(out_dir / "strategy_support.csv", index=False)
    jobs = []
    for entry in cfg["strategies"]:
        sid = entry["id"]
        for m in entry.get("markets", cfg["markets"]):
            if only and (sid, m) not in only:
                continue
            jobs.append((sid, m, entry, sup.get((sid, m), "unsupported")))
    _G.update(cfg=cfg, tables=tables, out_dir=out_dir, bundles={})
    procs = processes or min(4, mp.cpu_count())
    if procs > 1 and len(jobs) > 1:
        # group by market so each worker builds a market's features once
        jobs.sort(key=lambda j: (j[1], j[0]))
        with mp.get_context("fork").Pool(procs) as pool:
            results = pool.map(_worker, jobs, chunksize=1)
    else:
        results = [_worker(j) for j in jobs]
    summary = summarise(results, cfg, manifest)
    (out_dir / "summary.json").write_text(json.dumps(jsonable(summary), indent=1))
    sel = {f"{r['strategy']}_{r['market']}": r.get("selected_params") for r in results if r.get("status") == "ran"}
    (out_dir / "selection.json").write_text(json.dumps(jsonable(dict(
        frozen_utc=manifest["created_utc"], rule="max discovery net Sharpe (base scenario)",
        config_hash=manifest["config_hash"], data_version=version, selected=sel)), indent=1))
    pd.DataFrame(summary["leaderboard"]).to_csv(out_dir / "leaderboard.csv", index=False)
    pd.DataFrame(summary["tests"]).to_csv(out_dir / "multiple_tests.csv", index=False)
    latest = ROOT / cfg.get("results_root", "results") / "LATEST"
    latest.write_text(run_id)
    return out_dir


def availability_for_dataset(store: DataStore, name: str) -> pd.DataFrame:
    """Availability restricted to one dataset (so synthetic runs report synthetic_only)."""
    return availability_matrix(store, datasets=[name])


def summarise(results: list[dict], cfg: dict, manifest: dict) -> dict:
    # One-sided test of H1: mean daily net return > 0.  Two-sided p-values would count reliably
    # *losing* strategies as discoveries and loosen the BH threshold for everything else.
    tests = []
    for r in results:
        for g in r.get("grid", []) or []:
            p2, mu = g.get("disc_nw_p"), g.get("disc_mean")
            if p2 is None or mu is None or not np.isfinite(p2) or not np.isfinite(mu):
                p1 = np.nan
            else:
                p1 = p2 / 2 if mu > 0 else 1 - p2 / 2
            tests.append(dict(strategy=r["strategy"], market=r["market"], label=g["label"], period="discovery",
                              sharpe=g["disc_sharpe"], mean_daily=mu, p_two_sided=p2, p=p1, trades=g["disc_trades"]))
    tdf = pd.DataFrame(tests)
    if len(tdf):
        tdf["q_bh"] = bh_fdr(tdf["p"].to_numpy())
        tdf["p_holm"] = holm(tdf["p"].to_numpy())
    board = []
    for r in results:
        row = dict(strategy=r["strategy"], name=STRATEGIES[r["strategy"]].spec.name, market=r["market"],
                   support=r.get("support"), status=r.get("status"), error=r.get("error"))
        if r.get("status") == "ran":
            sc = r["scenarios"]
            bm = sc.get("base", {}).get("metrics", {})
            per = sc.get("base", {}).get("periods", {})
            row.update(selected=r.get("selected_label"), net_sharpe=bm.get("sharpe"), gross_sharpe=bm.get("sharpe_gross"),
                       ann_return=bm.get("ann_return"), ann_return_gross=bm.get("ann_return_gross"),
                       sharpe_ci=bm.get("sharpe_ci"), max_dd=bm.get("max_drawdown"), trades=bm.get("trade_count"),
                       bps_per_trade=bm.get("bps_per_trade"), gross_bps_per_trade=bm.get("gross_bps_per_trade"),
                       win_rate=bm.get("win_rate"), fill_rate=bm.get("fill_rate"),
                       discovery_sharpe=per.get("discovery", {}).get("sharpe"),
                       validation_sharpe=per.get("validation", {}).get("sharpe"),
                       oos_sharpe=per.get("oos", {}).get("sharpe"),
                       holdout_sharpe=per.get("holdout", {}).get("sharpe") if manifest.get("holdout_unlocked") else None,
                       optimistic_sharpe=sc.get("optimistic", {}).get("metrics", {}).get("sharpe"),
                       conservative_sharpe=sc.get("conservative", {}).get("metrics", {}).get("sharpe"),
                       stress_sharpe=sc.get("stress", {}).get("metrics", {}).get("sharpe"),
                       capacity=r.get("robustness", {}).get("capacity_estimate"),
                       placebo_p=(r.get("robustness", {}).get("placebo") or {}).get("p_value"))
            if len(tdf):
                sel = tdf[(tdf["strategy"] == r["strategy"]) & (tdf["market"] == r["market"]) & (tdf["label"] == r.get("selected_label"))]
                row["disc_p"] = float(sel["p"].iloc[0]) if len(sel) else None
                row["disc_q_bh"] = float(sel["q_bh"].iloc[0]) if len(sel) else None
            diag = r.get("diagnostic") or {}
            row["diagnostic_significant"] = _diag_sig(diag)
        row["verdict"] = verdict(row)
        board.append(row)
    return dict(manifest=manifest, leaderboard=board, tests=tdf, pipeline_validation=_pipeline_validation(cfg, manifest))


def _diag_sig(diag: dict) -> bool:
    """Diagnostic effect significant (p < 0.01) IN THE HYPOTHESISED DIRECTION.

    Event-study returns are signed by the hypothesis, so only positive means count.  The
    'LCP->C0' window (the auction move itself) is excluded - it is not a return anyone can earn.
    """
    s = diag.get("summary")
    if isinstance(s, dict) and s.get("__frame__"):
        s = frame(s)
    if isinstance(s, pd.DataFrame) and "p" in s and len(s):
        s2 = s[s["kind"] == "market_adjusted"] if "kind" in s else s
        s2 = s2[s2["horizon"] != "LCP->C0"] if "horizon" in s2 else s2
        return bool(((s2["p"] < 0.01) & (s2["mean_bps"] > 0)).any())
    st = diag.get("stats")
    if isinstance(st, dict):
        return any((v or {}).get("p") is not None and v["p"] < 0.01 and (v.get("mean_bps") or 0) > 0 for v in st.values())
    res = diag.get("results")
    if isinstance(res, dict):
        return any((v or {}).get("p") is not None and v["p"] < 0.01 and (v.get("signed_mean_bps") or 0) > 0 for v in res.values())
    return False


def _pipeline_validation(cfg: dict, manifest: dict) -> dict:
    """Recovery of planted effects (synthetic datasets only)."""
    if not manifest.get("is_synthetic"):
        return {"applicable": False}
    truth = manifest.get("provenance", {}).get("planted_truth", {})
    out = {"applicable": True, "world": truth.get("world"), "planted": truth, "estimated": {}}
    for m, b in _G.get("bundles", {}).items():
        f = b.features
        f = f[(f["close_method"] == "auction") & f["gap"].notna()]
        est = {}
        for lab, col in (("frac_gap_reverting_by_next_open", "fwd_o1"),
                         ("frac_gap_reverting_next_open_to_next_close", "fwd_o1c1"),
                         ("frac_gap_reverting_by_next_close", "fwd_c1")):
            g = f[["gap", col]].dropna()
            est[lab] = float(-np.polyfit(g["gap"], g[col], 1)[0]) if len(g) > 100 else None
        out["estimated"][m] = est
    if not out["estimated"]:
        tables = _G.get("tables")
        for m in cfg["markets"]:
            try:
                b = _bundle(m)
            except Exception:
                continue
            f = b.features
            f = f[(f["close_method"] == "auction") & f["gap"].notna()]
            est = {}
            for lab, col in (("frac_gap_reverting_by_next_open", "fwd_o1"),
                             ("frac_gap_reverting_next_open_to_next_close", "fwd_o1c1"),
                             ("frac_gap_reverting_by_next_close", "fwd_c1")):
                g = f[["gap", col]].dropna()
                est[lab] = float(-np.polyfit(g["gap"], g[col], 1)[0]) if len(g) > 100 else None
            out["estimated"][m] = est
    return out
