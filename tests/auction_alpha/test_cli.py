import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _run(args, cwd=ROOT):
    r = subprocess.run([sys.executable, "-m", "auction_alpha", *args], cwd=cwd, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-4000:]
    return r.stdout


def test_rules_command():
    out = _run(["rules", "--market", "HKEX"])
    assert "auction.close.random_window" in out and "verified_official" in out


def test_run_study_and_report_end_to_end(tmp_path):
    cfg = yaml.safe_load((ROOT / "config" / "experiments.yaml").read_text())
    cfg.update(dataset="synthetic_tiny_planted", synthetic_scale="tiny", data_root=str(tmp_path / "store"),
               results_root=str(tmp_path / "results"))
    cfg["robustness"].update(placebo_n=1, bootstrap_n=50, cost_multipliers=[0.0, 1.0], fill_ratios=[0.5, 1.0],
                             participation_caps=[0.1], latencies_s=[2.0], capital_multiples=[1.0, 4.0])
    cfg["periods"] = {"discovery": ["2020-01-01", "2021-12-31"], "validation": ["2022-01-01", "2022-12-31"],
                      "oos": ["2023-01-01", "2023-12-31"], "holdout": ["2024-01-01", "2024-12-31"]}
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg))
    out = _run(["--config", str(p), "run-study", "--strategy", "S01", "--market", "SET"])
    assert "dashboard" in out
    run = sorted((tmp_path / "results").glob("2*"))[-1]
    summary = json.loads((run / "summary.json").read_text())
    assert summary["manifest"]["is_synthetic"] is True
    row = summary["leaderboard"][0]
    assert row["strategy"] == "S01" and row["status"] == "ran"
    html = (run / "dashboard.html").read_text()
    assert "SYNTHETIC DATA" in html and summary["manifest"]["data_version"] in html
    sel = json.loads((run / "selection.json").read_text())
    assert "S01_SET" in sel["selected"]
    assert (run / "studies" / "S01_SET" / "trades_base.parquet").exists()
    md = (run / "RESEARCH_SUMMARY.md").read_text()
    assert "SYNTHETIC DATA" in md and "untestable" in md
    # holdout cannot be unlocked without a frozen selection
    r = subprocess.run([sys.executable, "-m", "auction_alpha", "--config", str(p), "run-all", "--unlock-holdout"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0
