"""Command-line interface: ``python -m auction_alpha <command>``.

Research and simulation only - no command connects to a broker or submits
orders.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _store(cfg):
    from .data.store import DataStore
    return DataStore(ROOT / cfg.get("data_root", "data/store"))


def _cfg(path):
    from .experiments import load_config
    return load_config(ROOT / path if not Path(path).is_absolute() else path)


def cmd_data_audit(a):
    from .data.availability import availability_matrix, matrix_wide, strategy_support
    from .data.validation import validate_dataset
    from .experiments import ensure_dataset
    from .reporting.reports import data_feasibility_markdown
    from .strategies import STRATEGIES

    cfg = _cfg(a.config)
    store = _store(cfg)
    ds = a.dataset or cfg["dataset"]
    if ds.startswith("synthetic"):
        ensure_dataset(store, ds, a.scale or cfg.get("synthetic_scale", "full"))
    datasets = []
    for name in store.datasets():
        man = store.manifest(name)
        datasets.append(dict(dataset=name, is_synthetic=man["is_synthetic"], data_version=man["data_version"],
                             rows_daily_bars=man["tables"].get("daily_bars", {}).get("rows"),
                             created_utc=man["created_utc"]))
    tables = store.load(ds)
    rep = validate_dataset(ds, tables)
    av = availability_matrix(store)
    out = ROOT / "reports" / "data_audit"
    out.mkdir(parents=True, exist_ok=True)
    rep.to_frame().to_csv(out / f"quality_{ds}.csv", index=False)
    av.to_csv(out / "availability_matrix.csv", index=False)
    (ROOT / "docs" / "DATA_FEASIBILITY.md").write_text(data_feasibility_markdown(av, datasets), encoding="utf-8")
    print(f"dataset {ds}: {'OK' if rep.ok() else 'ERRORS'}; {len(rep.issues)} findings "
          f"({len(rep.errors)} errors)")
    print(rep.to_frame()[["table", "check", "severity", "count"]].to_string(index=False))
    print()
    print(matrix_wide(av).to_string())
    print()
    print(strategy_support(av, STRATEGIES).pivot(index="strategy", columns="market", values="support").to_string())
    print(f"\nwrote {out}/ and docs/DATA_FEASIBILITY.md")
    return 0 if rep.ok() else 1


def cmd_generate(a):
    from .experiments import ensure_dataset
    cfg = _cfg(a.config)
    v = ensure_dataset(_store(cfg), a.dataset, a.scale, force=True)
    print(f"generated {a.dataset} (data version {v}) - SYNTHETIC")
    return 0


def cmd_ingest(a):
    from .data.ingest import ingest_from_mapping
    cfg = _cfg(a.config)
    v, rep = ingest_from_mapping(a.mapping, _store(cfg))
    print(f"ingested data version {v}; {len(rep.issues)} quality findings")
    return 0


def _finish(out_dir, offline=False):
    from .reporting.dashboard import build_dashboard
    from .reporting.reports import research_summary_markdown
    p = build_dashboard(out_dir, embed_js=offline)
    (Path(out_dir) / "RESEARCH_SUMMARY.md").write_text(research_summary_markdown(out_dir), encoding="utf-8")
    lb = pd.read_csv(Path(out_dir) / "leaderboard.csv")
    cols = [c for c in ("strategy", "market", "selected", "net_sharpe", "gross_sharpe", "validation_sharpe", "oos_sharpe", "trades", "verdict") if c in lb]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(lb[cols].to_string(index=False))
    print(f"\nresults: {out_dir}\ndashboard: {p}\nsummary: {Path(out_dir) / 'RESEARCH_SUMMARY.md'}")


def cmd_run_study(a):
    from .experiments import run_all
    cfg = _cfg(a.config)
    if a.dataset:
        cfg["dataset"] = a.dataset
    entry = next((e for e in cfg["strategies"] if e["id"] == a.strategy), {"id": a.strategy, "grid": True})
    cfg["strategies"] = [dict(entry, markets=[a.market])]
    out = run_all(cfg, processes=1)
    _finish(out, a.offline)
    return 0


def cmd_run_all(a):
    from .experiments import run_all
    cfg = _cfg(a.config)
    if a.dataset:
        cfg["dataset"] = a.dataset
    out = run_all(cfg, processes=a.processes, unlock_holdout=a.unlock_holdout, selection_file=a.selection)
    _finish(out, a.offline)
    return 0


def _resolve_run(a) -> Path:
    res = ROOT / "results"
    if a.run:
        return res / a.run if not Path(a.run).is_absolute() else Path(a.run)
    latest = res / "LATEST"
    if not latest.exists():
        raise SystemExit("no runs yet - run `python -m auction_alpha run-all` first")
    return res / latest.read_text().strip()


def cmd_report(a):
    from .reporting.dashboard import build_dashboard
    from .reporting.reports import research_summary_markdown
    run_dir = _resolve_run(a)
    p = build_dashboard(run_dir, embed_js=a.offline)
    s = run_dir / "RESEARCH_SUMMARY.md"
    s.write_text(research_summary_markdown(run_dir), encoding="utf-8")
    print(f"dashboard: {p}\nsummary: {s}")
    if a.publish:
        dest = ROOT / "reports" / a.publish
        dest.mkdir(parents=True, exist_ok=True)
        for f in ("RESEARCH_SUMMARY.md", "leaderboard.csv", "multiple_tests.csv", "selection.json", "manifest.json",
                  "config.yaml", "strategy_support.csv", "availability_matrix.csv"):
            if (run_dir / f).exists():
                shutil.copy(run_dir / f, dest / f)
        build_dashboard(run_dir, dest / "dashboard.html", embed_js=a.offline)
        print(f"published to {dest}")
    return 0


def cmd_leakage(a):
    from .experiments import ensure_dataset
    from .leakage import run_suite
    from .strategies import STRATEGIES
    cfg = _cfg(a.config)
    store = _store(cfg)
    ds = a.dataset or "synthetic_tiny_planted"
    if ds.startswith("synthetic"):
        ensure_dataset(store, ds, "tiny" if "tiny" in ds else cfg.get("synthetic_scale", "full"))
    tables = store.load(ds)
    r = run_suite(tables, cfg["markets"], STRATEGIES)
    out = ROOT / "reports" / "leakage_checks.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    r.to_csv(out, index=False)
    print(r[["test", "strategy", "market", "passed", "n_before", "detail"]].to_string(index=False))
    print(f"\n{int(r['passed'].sum())}/{len(r)} checks passed; wrote {out}")
    return 0 if r["passed"].all() else 1


def cmd_rules(a):
    from .rules.registry import default_registry
    rows = pd.DataFrame(default_registry().audit_rows())
    if a.market:
        rows = rows[rows["market"] == a.market]
    with pd.option_context("display.width", 220, "display.max_colwidth", 60):
        print(rows[["market", "key", "status", "effective_from", "effective_to", "verified_as_of", "source"]].to_string(index=False))
    print("\n" + rows.groupby(["market", "status"]).size().unstack(fill_value=0).to_string())
    return 0


def cmd_docs(a):
    from .reporting.reports import write_generated_docs
    for p in write_generated_docs(ROOT / "docs"):
        print(f"wrote {p}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m auction_alpha",
                                description="Closing-auction alpha research platform (SET / SGX / HKEX). Research only - never trades.")
    p.add_argument("--config", default="config/experiments.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("data-audit", help="validate data, build availability matrix and feasibility report")
    s.add_argument("--dataset")
    s.add_argument("--scale", choices=["full", "small", "tiny"])
    s.set_defaults(fn=cmd_data_audit)
    s = sub.add_parser("generate-data", help="(re)generate a SYNTHETIC dataset")
    s.add_argument("--dataset", default="synthetic_planted", help="name containing 'null' generates the null world")
    s.add_argument("--scale", default="full", choices=["full", "small", "tiny"])
    s.set_defaults(fn=cmd_generate)
    s = sub.add_parser("ingest", help="ingest vendor files via a mapping YAML")
    s.add_argument("--mapping", required=True)
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("run-study", help="run one strategy on one market")
    s.add_argument("--strategy", required=True, choices=[f"S0{i}" for i in range(1, 9)])
    s.add_argument("--market", required=True, choices=["SET", "SGX", "HKEX"])
    s.add_argument("--dataset")
    s.add_argument("--offline", action="store_true", help="embed plotly.js in the dashboard")
    s.set_defaults(fn=cmd_run_study)
    s = sub.add_parser("run-all", help="run every configured study")
    s.add_argument("--dataset")
    s.add_argument("--processes", type=int, default=None)
    s.add_argument("--unlock-holdout", action="store_true")
    s.add_argument("--selection", help="frozen selection.json (required with --unlock-holdout)")
    s.add_argument("--offline", action="store_true")
    s.set_defaults(fn=cmd_run_all)
    s = sub.add_parser("report", help="rebuild dashboard + summary for a run")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--latest", action="store_true")
    g.add_argument("--run")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--publish", help="copy summary artefacts to reports/<name>/")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("leakage-check", help="run the temporal-leakage test suite")
    s.add_argument("--dataset")
    s.set_defaults(fn=cmd_leakage)
    s = sub.add_parser("rules", help="print the market-rules registry")
    s.add_argument("--market", choices=["SET", "SGX", "HKEX"])
    s.set_defaults(fn=cmd_rules)
    s = sub.add_parser("docs", help="regenerate MARKET_RULES / DATA_DICTIONARY / STRATEGIES docs")
    s.set_defaults(fn=cmd_docs)
    a = p.parse_args(argv)
    return int(a.fn(a) or 0)


if __name__ == "__main__":
    sys.exit(main())
