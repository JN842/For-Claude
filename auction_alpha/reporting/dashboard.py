"""Interactive HTML research dashboard built from a results directory.

Everything shown is read from the saved experiment artefacts (summary.json,
studies/*/study.json, daily/trade/rejection parquet files), so the dashboard
always reflects actual experiment results and links back to the run's config
and data version.  Synthetic runs carry a prominent SYNTHETIC banner.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..experiments import frame, jsonable

PLOTLY_CDN = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/3.1.1/plotly.min.js"


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def _thin(n: int, max_points: int) -> np.ndarray:
    """Indices for at most max_points evenly spaced samples, always keeping the first and last."""
    if n <= max_points:
        return np.arange(n)
    idx = np.unique(np.linspace(0, n - 1, max_points).round().astype(int))
    return idx


def collect(run_dir: Path, max_trades: int = 100, max_points: int = 400) -> dict:
    run_dir = Path(run_dir)
    summary = _read_json(run_dir / "summary.json")
    manifest = _read_json(run_dir / "manifest.json")
    dq = _read_json(run_dir / "data_quality.json")
    studies = {}
    for sj in sorted((run_dir / "studies").glob("*/study.json")):
        s = _read_json(sj)
        key = sj.parent.name
        diag = s.get("diagnostic") or {}
        ev = diag.get("events") if isinstance(diag, dict) else None
        if isinstance(ev, dict) and ev.get("__frame__"):
            e = pd.DataFrame(ev["records"])
            if len(e):
                e = e.reindex(e["gap"].abs().sort_values(ascending=False).index).head(max_trades)
            diag = dict(diag, events=jsonable(e), n_events_total=len(ev["records"]))
        d = {"strategy": s.get("strategy"), "market": s.get("market"), "status": s.get("status"),
             "support": s.get("support"), "selected": s.get("selected_label"), "name": s.get("name"),
             "spec": s.get("spec"), "diagnostic": diag, "grid": s.get("grid"),
             "robustness": s.get("robustness"), "shortfall": s.get("shortfall"),
             "selected_params": s.get("selected_params")}
        sc = {}
        for name, v in (s.get("scenarios") or {}).items():
            sc[name] = {"metrics": v.get("metrics"), "periods": v.get("periods"), "yearly": v.get("yearly"),
                        "rejection_counts": v.get("rejection_counts")}
            dp = sj.parent / f"daily_{name}.parquet"
            if dp.exists():
                df = pd.read_parquet(dp)
                cum = df["ret"].cumsum().to_numpy()
                capital = float(df["nav"].iloc[0] - df["pnl"].iloc[0])
                gross = (df["pnl_gross"] / capital).cumsum().to_numpy()
                dd = cum - np.maximum.accumulate(np.r_[0.0, cum])[1:]
                # equity curves are thinned for display; the drawdown keeps each bucket's minimum
                ix = _thin(len(df), max_points)
                dd_b = [float(dd[a:b].min()) for a, b in zip(ix, list(ix[1:]) + [len(dd)])] if len(dd) else []
                sc[name]["daily"] = {"date": df["date"].dt.strftime("%Y-%m-%d").to_numpy()[ix].tolist(),
                                     "cum_net": np.round(cum[ix] * 100, 3).tolist(),
                                     "cum_gross": np.round(gross[ix] * 100, 3).tolist(),
                                     "dd": np.round(np.array(dd_b) * 100, 3).tolist(),
                                     "n_days": int(len(df))}
        d["scenarios"] = sc
        tp = sj.parent / "trades_base.parquet"
        if tp.exists():
            t = pd.read_parquet(tp)
            t = t.reindex(t["pnl_net"].abs().sort_values(ascending=False).index).head(max_trades)
            cols = [c for c in ("trade_id", "symbol", "side", "signal_date", "entry_date", "exit_date", "signal",
                                "intended_qty", "entry_qty", "entry_notional", "pnl_gross", "pnl_net", "ret_bps",
                                "fees", "slippage", "borrow", "entry_session", "exit_session", "status") if c in t]
            tt = t[cols].copy()
            num = tt.select_dtypes("number").columns
            tt[num] = tt[num].round(4)
            d["trades"] = jsonable(tt.astype(object).where(tt.notna(), None).to_dict("records"))
            d["n_trades_total"] = int(len(pd.read_parquet(tp, columns=["trade_id"])))
        rp = sj.parent / "rejections_base.parquet"
        if rp.exists():
            r = pd.read_parquet(rp)
            d["rejections"] = jsonable(r.groupby(["leg", "reason"]).agg(n=("trade_id", "size"),
                                                                       qty=("requested_qty", "sum")).reset_index().to_dict("records"))
        studies[key] = d
    return {"summary": summary, "manifest": manifest, "data_quality": dq, "studies": studies,
            "availability": _csv(run_dir / "availability_matrix.csv"), "support": _csv(run_dir / "strategy_support.csv")}


def _csv(p: Path):
    return pd.read_csv(p).to_dict("records") if p.exists() else []


def build_dashboard(run_dir: Path | str, out: Path | str | None = None, *, embed_js: bool = False) -> Path:
    run_dir = Path(run_dir)
    payload = collect(run_dir)
    out = Path(out) if out else run_dir / "dashboard.html"
    if embed_js:
        import plotly
        js = (Path(plotly.__file__).parent / "package_data" / "plotly.min.js").read_text()
        script = f"<script>{js}</script>"
    else:
        script = f'<script src="{PLOTLY_CDN}"></script>'
    try:
        cfg_rel = Path("config.yaml") if out.parent == run_dir else (run_dir / "config.yaml").relative_to(out.parent.resolve(), walk_up=True)
    except Exception:
        cfg_rel = run_dir / "config.yaml"
    html = TEMPLATE.replace("__PLOTLY__", script).replace("__DATA__", json.dumps(jsonable(payload), default=str)
                                                           .replace("</", "<\\/")).replace("__CONFIG_LINK__", str(cfg_rel))
    out.write_text(html, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Closing Auction Research</title>
__PLOTLY__
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb; --surface-2: #f3f2ee;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --good: #006300; --critical: #d03b3b; --warning-bg: #fff4d6; --warning-ink: #6b4a00;
  --accent: #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19; --surface-2: #242423;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --good: #0ca30c; --critical: #e66767; --warning-bg: #3a2e10; --warning-ink: #fab219; --accent: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19; --surface-2: #242423;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --good: #0ca30c; --critical: #e66767; --warning-bg: #3a2e10; --warning-ink: #fab219; --accent: #3987e5;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text-primary);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main { max-width: 1200px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 24px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 0 0 4px; }
.sub { color: var(--text-secondary); margin: 0; }
.banner { margin: 16px 0; padding: 12px 14px; border-radius: 8px; background: var(--warning-bg);
  color: var(--warning-ink); border: 1px solid var(--border); font-weight: 600; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 20px; color: var(--text-secondary); font-size: 12.5px; }
.meta code { color: var(--text-primary); }
section { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; margin: 16px 0; }
.note { color: var(--text-muted); font-size: 12.5px; margin: 4px 0 10px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 8px 0 10px; }
.controls label { display: flex; gap: 6px; align-items: center; min-width: 0; max-width: 100%; }
.controls select { min-width: 0; width: auto; max-width: min(100%, 520px); }
select, input { font: inherit; color: var(--text-primary); background: var(--surface-2);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; max-width: 100%; }
.chart { width: 100%; height: 360px; }
.chart.tall { height: 460px; }
.tablewrap { overflow-x: auto; max-height: 460px; overflow-y: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; font-variant-numeric: tabular-nums; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; }
th { position: sticky; top: 0; background: var(--surface-2); color: var(--text-secondary); cursor: pointer; font-weight: 600; }
td.l, th.l { text-align: left; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--border); }
.v-exec { color: var(--good); font-weight: 600; }
.v-anom { color: var(--warning-ink); }
.v-none { color: var(--text-secondary); }
.v-unt { color: var(--text-muted); font-style: italic; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 800px) { .grid2 { grid-template-columns: 1fr; } .chart { height: 300px; } }
a { color: var(--accent); }
footer { color: var(--text-muted); font-size: 12px; padding: 24px 16px 40px; max-width: 1200px; margin: 0 auto; }
</style>
</head>
<body>
<header>
  <h1>Closing Auction Alpha Research</h1>
  <p class="sub">SET · SGX · HKEX — diagnostic event studies and causally valid, cost-aware backtests</p>
  <div id="banner"></div>
  <div class="meta" id="meta"></div>
</header>
<main>
<section><h2>1. Strategy leaderboard</h2>
  <p class="note">Base scenario, all non-holdout dates. Parameters were selected on the discovery period only; validation and OOS columns are out-of-sample for that choice. q = Benjamini–Hochberg across every grid point tested. Click a header to sort.</p>
  <div class="tablewrap"><table id="leader"></table></div></section>

<section><h2>2. Gross versus net performance</h2>
  <p class="note">Annualised Sharpe from daily portfolio returns (fixed capital), before and after all transaction, borrow and funding costs.</p>
  <div id="grossnet" class="chart tall"></div></section>

<section><h2>3. Equity curves and drawdowns</h2>
  <div class="controls"><label>Study <select id="selStudy"></select></label></div>
  <p class="note" id="studyNote"></p>
  <p class="note">Curves are sampled to at most 400 points for display (drawdown keeps each interval's worst value); full daily series are in <code>studies/&lt;S&gt;_&lt;MKT&gt;/daily_&lt;scenario&gt;.parquet</code>.</p>
  <div id="equity" class="chart"></div>
  <div id="drawdown" class="chart" style="height:220px"></div></section>

<section><h2>4. Closing-gap distributions</h2>
  <div class="controls"><label>Market <select id="selMarket"></select></label></div>
  <p class="note">gap = ln(closing-auction price / last continuous price), clipped to ±500 bps for display.</p>
  <div id="gapdist" class="chart"></div></section>

<section><h2>5. Event study: cumulative abnormal returns after closing gaps</h2>
  <p class="note"><b>Diagnostic — not executable.</b> Signed for reversal (S01 threshold); market-adjusted; date-clustered. Windows starting at C0 assume a trade at the closing price, which is only possible via SGX TAC.</p>
  <div class="grid2"><div id="car" class="chart"></div><div class="tablewrap"><table id="cartable"></table></div></div></section>

<section><h2>6. Heatmaps by market, year and signal threshold</h2>
  <p class="note">Left: net yearly return (% of capital, base) of each selected study. Right: grid of the chosen study's primary parameter × year (all values tested; only discovery years drove the selection).</p>
  <div class="grid2"><div id="heat1" class="chart tall"></div><div id="heat2" class="chart tall"></div></div></section>

<section><h2>7. Index rebalance event analysis</h2>
  <div class="controls"><label>Market <select id="selMarket2"></select></label></div>
  <p class="note">Mean cumulative abnormal return around the index trade day (day 0 = close before the effective date). Diagnostic.</p>
  <div class="grid2"><div id="rebal" class="chart"></div><div class="tablewrap"><table id="rebaltable"></table></div></div></section>

<section><h2>8. Long versus short attribution</h2>
  <p class="note">Total P&amp;L as % of capital by book (base scenario). Short trades that could not be borrowed were rejected and contribute nothing.</p>
  <div id="attrib" class="chart tall"></div></section>

<section><h2>9. Transaction-cost sensitivity</h2>
  <p class="note">Fees, spread and impact scaled together; 1.0 = base assumptions, 0 = frictionless.</p>
  <div id="costs" class="chart"></div></section>

<section><h2>10. Fill probability and capacity scenarios</h2>
  <div class="grid2"><div id="capacity" class="chart"></div><div class="tablewrap"><table id="filltable"></table></div></div>
  <div class="tablewrap" style="margin-top:12px"><table id="scentable"></table></div></section>

<section><h2>11. Data quality and missing data</h2>
  <div class="grid2"><div id="dqchart" class="chart"></div><div class="tablewrap"><table id="dqtable"></table></div></div>
  <h2 style="margin-top:14px">Data availability and strategy support</h2>
  <div class="grid2"><div class="tablewrap"><table id="avtable"></table></div><div class="tablewrap"><table id="suptable"></table></div></div></section>

<section><h2>12. Trade and event drill-down</h2>
  <div class="controls"><label>Filter <input id="tradeFilter" placeholder="symbol, date, session…"></label><span class="note" id="tradeCount"></span></div>
  <div class="tablewrap"><table id="trades"></table></div>
  <h2 style="margin-top:14px">Largest closing-gap events (diagnostic, this study)</h2>
  <p class="note" id="eventNote"></p>
  <div class="tablewrap"><table id="events"></table></div>
  <h2 style="margin-top:14px">Rejected and reduced orders (base scenario)</h2>
  <div class="tablewrap"><table id="rejects"></table></div>
  <h2 style="margin-top:14px">Robustness checks for this study</h2>
  <div class="tablewrap"><table id="robust"></table></div></section>

<section><h2>Pipeline validation (synthetic only)</h2>
  <p class="note">Fractions of the closing gap the generator planted to revert, and what the pipeline estimated from the generated prices. Agreement shows the plumbing works; it says nothing about real markets.</p>
  <div class="tablewrap"><table id="pv"></table></div></section>
</main>
<footer id="foot"></footer>
<script>
const D = __DATA__;
const CONFIG_LINK = "__CONFIG_LINK__";
const PAL = {
  light: ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#6250d6","#e34948"],
  dark:  ["#3987e5","#d95926","#199e70","#c98500","#d55181","#008300","#9085e9","#e66767"],
  div: {light: ["#184f95","#6da7ec","#f0efec","#ec8a87","#b52f2f"], dark: ["#5598e7","#256abf","#383835","#b74241","#e66767"]},
};
const isDark = () => { const t = document.documentElement.getAttribute("data-theme");
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; };
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const S = i => PAL[isDark() ? "dark" : "light"][i];
const fmt = (x, d=2) => (x === null || x === undefined || Number.isNaN(x)) ? "–" : (typeof x === "number" ? x.toFixed(d) : String(x));
const pct = (x, d=2) => (x === null || x === undefined) ? "–" : (100*x).toFixed(d) + "%";
const studies = D.studies, keys = Object.keys(studies).filter(k => studies[k].status === "ran");
const frameRecs = o => (o && o.__frame__) ? o.records : (Array.isArray(o) ? o : []);

function layout(extra) {
  const ax = () => ({gridcolor: css("--grid"), linecolor: css("--axis"), zerolinecolor: css("--axis"),
              tickfont: {color: css("--text-muted")}, title: {font: {color: css("--text-secondary")}}});
  return Object.assign({paper_bgcolor: css("--surface-1"), plot_bgcolor: css("--surface-1"),
    font: {family: "system-ui, -apple-system, Segoe UI, sans-serif", color: css("--text-primary"), size: 12},
    margin: {l: 60, r: 20, t: 30, b: 50}, hovermode: "closest",
    hoverlabel: {bgcolor: css("--surface-2"), bordercolor: css("--border"), font: {color: css("--text-primary")}},
    legend: {orientation: "h", y: 1.12, font: {color: css("--text-secondary")}},
    xaxis: ax(), yaxis: ax()}, extra || {});
}
const deep = (a, b) => { for (const k in b) { if (b[k] && typeof b[k] === "object" && !Array.isArray(b[k])) { a[k] = deep(a[k] || {}, b[k]); } else a[k] = b[k]; } return a; };
const plot = (id, data, extra) => Plotly.react(id, data, deep(layout(), extra || {}), {displayModeBar: false, responsive: true});

function table(id, rows, cols, opts) {
  opts = opts || {};
  const el = document.getElementById(id);
  if (!rows || !rows.length) { el.innerHTML = "<tr><td class='l'>No data</td></tr>"; return; }
  cols = cols || Object.keys(rows[0]).map(k => ({k, label: k}));
  let sortK = opts.sortK, dir = -1;
  const render = () => {
    let r = rows.slice();
    if (sortK) r.sort((a, b) => { const x = a[sortK], y = b[sortK]; if (x === y) return 0; if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1; return (x > y ? 1 : -1) * dir; });
    el.innerHTML = "<thead><tr>" + cols.map(c => `<th class="${c.l ? "l" : ""}" data-k="${c.k}">${c.label}</th>`).join("") + "</tr></thead><tbody>" +
      r.map(row => "<tr>" + cols.map(c => `<td class="${c.l ? "l" : ""}">${c.f ? c.f(row[c.k], row) : fmt(row[c.k])}</td>`).join("") + "</tr>").join("") + "</tbody>";
    el.querySelectorAll("th").forEach(th => th.onclick = () => { const k = th.dataset.k; dir = (sortK === k) ? -dir : -1; sortK = k; render(); });
  };
  render();
}
const verdictChip = v => { const cls = v && v.startsWith("executable") ? "v-exec" : v && v.startsWith("statistical") ? "v-anom" : v && v.startsWith("untestable") || v === "not_applicable" ? "v-unt" : "v-none";
  const txt = {executable_edge_after_costs: "✓ executable edge", statistical_anomaly_not_executable: "△ anomaly, not executable", no_robust_effect: "✕ no robust effect", untestable_missing_data: "untestable: missing data", not_applicable: "n/a", error: "error"}[v] || v;
  return `<span class="chip ${cls}">${txt}</span>`; };

function header() {
  const m = D.manifest || {};
  if (m.is_synthetic) document.getElementById("banner").innerHTML = `<div class="banner">⚠ SYNTHETIC DATA — every number on this page comes from generated test data (${(m.provenance||{}).label || "synthetic"}, world: ${((m.provenance||{}).planted_truth||{}).world || "?"}). These are engineering results, not empirical findings. Empirical strategy profitability has NOT been established.</div>`;
  document.getElementById("meta").innerHTML = [
    ["run", m.run_id], ["dataset", m.dataset], ["data version", m.data_version], ["rules registry", m.registry_version],
    ["code", m.code_version], ["config hash", m.config_hash]].map(([k, v]) => `<span>${k}: <code>${v}</code></span>`).join("") +
    ` <span><a href="${CONFIG_LINK}">experiment config</a></span>` + (m.holdout_unlocked ? " <span>holdout: <b>unlocked</b></span>" : " <span>holdout: locked</span>");
  document.getElementById("foot").textContent = `Generated from results/${m.run_id}. Every chart reads the saved artefacts of that run; data version ${m.data_version}; config ${m.config_file}.`;
}

function leaderboard() {
  const rows = (D.summary.leaderboard || []).map(r => Object.assign({}, r, {study: r.strategy + " " + r.market}));
  table("leader", rows, [
    {k: "study", label: "Study", l: true}, {k: "name", label: "Strategy", l: true}, {k: "selected", label: "Selected", l: true},
    {k: "verdict", label: "Verdict", l: true, f: verdictChip}, {k: "net_sharpe", label: "Net SR"}, {k: "gross_sharpe", label: "Gross SR"},
    {k: "validation_sharpe", label: "Valid SR"}, {k: "oos_sharpe", label: "OOS SR"}, {k: "conservative_sharpe", label: "Cons SR"},
    {k: "stress_sharpe", label: "Stress SR"}, {k: "ann_return", label: "Ann net", f: x => pct(x)},
    {k: "bps_per_trade", label: "Net bps/trade", f: x => fmt(x, 1)}, {k: "gross_bps_per_trade", label: "Gross bps/trade", f: x => fmt(x, 1)},
    {k: "trades", label: "Trades", f: x => fmt(x, 0)}, {k: "fill_rate", label: "Fill", f: x => pct(x, 0)},
    {k: "disc_q_bh", label: "q (BH)", f: x => fmt(x, 3)}, {k: "placebo_p", label: "Placebo p", f: x => fmt(x, 2)},
    {k: "capacity", label: "Capacity", f: x => x ? (x/1e6).toFixed(1) + "m" : "–"}, {k: "support", label: "Data", l: true, f: x => x || "–"}], {sortK: "net_sharpe"});
}

function grossNet() {
  const rows = (D.summary.leaderboard || []).filter(r => r.status === "ran");
  const y = rows.map(r => r.strategy + " " + r.market);
  plot("grossnet", [
    {type: "bar", orientation: "h", name: "Net (after costs)", y, x: rows.map(r => r.net_sharpe), marker: {color: S(0)}, hovertemplate: "%{y}<br>net Sharpe %{x:.2f}<extra></extra>"},
    {type: "bar", orientation: "h", name: "Gross (before costs)", y, x: rows.map(r => r.gross_sharpe), marker: {color: S(1)}, hovertemplate: "%{y}<br>gross Sharpe %{x:.2f}<extra></extra>"}],
    {barmode: "group", bargap: 0.25, bargroupgap: 0.08, margin: {l: 90}, xaxis: {title: {text: "Annualised Sharpe ratio"}}, yaxis: {autorange: "reversed"}});
}

function studyCharts(key) {
  const s = studies[key]; if (!s) return;
  const b = (s.scenarios.base || {}).metrics || {};
  document.getElementById("studyNote").innerHTML = `${s.name} on ${s.market}; selected <code>${s.selected}</code>. Net Sharpe ${fmt(b.sharpe)} (95% CI ${b.sharpe_ci ? b.sharpe_ci.map(x => fmt(x)).join(" to ") : "–"}), trades ${fmt(b.trade_count, 0)}, max drawdown ${pct(b.max_drawdown)}.`;
  const order = ["optimistic", "base", "conservative", "stress"];
  const tr = [];
  order.forEach((sc, i) => { const d = (s.scenarios[sc] || {}).daily; if (!d) return;
    tr.push({type: "scatter", mode: "lines", name: sc + " (net)", x: d.date, y: d.cum_net, line: {width: 2, color: S(i)}, hovertemplate: sc + " %{x}: %{y:.2f}%<extra></extra>"}); });
  const bd = (s.scenarios.base || {}).daily;
  if (bd) tr.push({type: "scatter", mode: "lines", name: "base (gross)", x: bd.date, y: bd.cum_gross, line: {width: 2, dash: "dot", color: css("--text-muted")}, hovertemplate: "gross %{x}: %{y:.2f}%<extra></extra>"});
  plot("equity", tr, {hovermode: "x unified", yaxis: {title: {text: "Cumulative return, % of capital"}}});
  if (bd) plot("drawdown", [{type: "scatter", mode: "lines", fill: "tozeroy", name: "Drawdown (base, net)", x: bd.date, y: bd.dd, line: {width: 1.5, color: S(7)}, hovertemplate: "%{x}: %{y:.2f}%<extra></extra>"}],
    {showlegend: false, yaxis: {title: {text: "Drawdown, %"}}, margin: {t: 10}});
  const g = s.grid || [], pp = (s.spec || {}).primary_param;
  if (g.length && pp) {
    const vals = [...new Set(g.map(x => String(x.params[pp])))];
    const years = [...new Set(g.flatMap(x => (x.yearly || []).map(y => y.year)))].sort();
    const z = vals.map(v => years.map(yr => { const cand = g.filter(x => String(x.params[pp]) === v);
      const ys = cand.map(c => ((c.yearly || []).find(y => y.year === yr) || {}).ret_net).filter(x => x !== undefined && x !== null);
      return ys.length ? 100 * Math.max(...ys) : null; }));
    plot("heat2", [heat(years.map(String), vals.map(v => pp + "=" + v), z, "best net return in grid, %")], {margin: {l: 130}, title: {text: s.strategy + " " + s.market + ": " + pp + " × year", font: {size: 13}}});
  } else Plotly.purge("heat2");
  const rob = s.robustness || {};
  const cs = frameRecs(rob.cost_sensitivity);
  plot("costs", [{type: "scatter", mode: "lines+markers", name: "Net Sharpe", x: cs.map(r => r.cost_multiplier), y: cs.map(r => r.sharpe), line: {width: 2, color: S(0)}, marker: {size: 8}, hovertemplate: "cost × %{x}: Sharpe %{y:.2f}<extra></extra>"}],
    {showlegend: false, xaxis: {title: {text: "Cost multiplier (fees, spread, impact)"}}, yaxis: {title: {text: "Net Sharpe"}}});
  const cap = frameRecs(rob.capacity);
  plot("capacity", [{type: "scatter", mode: "lines+markers", name: "Annual net P&L", x: cap.map(r => r.capital), y: cap.map(r => r.ann_pnl), line: {width: 2, color: S(2)}, marker: {size: 8},
    customdata: cap.map(r => [r.sharpe, r.fill_rate]), hovertemplate: "capital %{x:,.0f}<br>annual net P&L %{y:,.0f}<br>Sharpe %{customdata[0]:.2f}, fill %{customdata[1]:.0%}<extra></extra>"}],
    {showlegend: false, xaxis: {type: "log", title: {text: "Capital (local currency, log)"}}, yaxis: {title: {text: "Annual net P&L"}}});
  table("filltable", frameRecs(rob.fill_sensitivity).map(r => Object.assign({setting: r.fill_ratio !== undefined && r.fill_ratio !== null ? "fill ratio " + r.fill_ratio : "participation cap " + r.participation_cap}, r)),
    [{k: "setting", label: "Assumption", l: true}, {k: "realised_fill_rate", label: "Fill rate", f: x => pct(x, 0)}, {k: "sharpe", label: "Net SR"}, {k: "ann_return", label: "Ann net", f: x => pct(x)}, {k: "trades", label: "Trades", f: x => fmt(x, 0)}]);
  table("scentable", order.filter(o => s.scenarios[o]).map(o => { const m = s.scenarios[o].metrics || {}; return {scenario: o, ...m}; }),
    [{k: "scenario", label: "Scenario", l: true}, {k: "sharpe", label: "Net SR"}, {k: "sharpe_gross", label: "Gross SR"}, {k: "ann_return", label: "Ann net", f: x => pct(x)},
     {k: "sortino", label: "Sortino"}, {k: "max_drawdown", label: "Max DD", f: x => pct(x)}, {k: "win_rate", label: "Win", f: x => pct(x, 0)}, {k: "profit_factor", label: "PF"},
     {k: "bps_per_trade", label: "bps/trade", f: x => fmt(x, 1)}, {k: "fill_rate", label: "Fill", f: x => pct(x, 0)}, {k: "trade_count", label: "Trades", f: x => fmt(x, 0)},
     {k: "avg_holding_sessions", label: "Hold (sess)", f: x => fmt(x, 1)}, {k: "capital_utilization", label: "Util", f: x => pct(x, 1)}, {k: "turnover_ann", label: "Turnover/yr", f: x => fmt(x, 1)},
     {k: "pnl_long", label: "Long P&L", f: x => fmt(x, 0)}, {k: "pnl_short", label: "Short P&L", f: x => fmt(x, 0)}, {k: "pnl_hedge", label: "Hedge P&L", f: x => fmt(x, 0)}]);
  const trades = s.trades || [];
  const drawTrades = () => { const q = document.getElementById("tradeFilter").value.toLowerCase();
    const rows = trades.filter(t => !q || JSON.stringify(t).toLowerCase().includes(q));
    document.getElementById("tradeCount").textContent = `${rows.length} shown of ${trades.length} largest-|P&L| trades (${s.n_trades_total || 0} total in trades_base.parquet)`;
    table("trades", rows, [{k: "symbol", label: "Symbol", l: true}, {k: "side", label: "Side", f: x => x > 0 ? "long" : "short"}, {k: "signal_date", label: "Signal", l: true, f: x => (x || "").slice(0, 10)},
      {k: "entry_date", label: "Entry", l: true, f: x => (x || "").slice(0, 10)}, {k: "exit_date", label: "Exit", l: true, f: x => (x || "").slice(0, 10)}, {k: "signal", label: "Signal", f: x => fmt(x, 4)},
      {k: "intended_qty", label: "Intended", f: x => fmt(x, 0)}, {k: "entry_qty", label: "Filled", f: x => fmt(x, 0)}, {k: "entry_notional", label: "Notional", f: x => fmt(x, 0)},
      {k: "pnl_gross", label: "Gross P&L", f: x => fmt(x, 0)}, {k: "pnl_net", label: "Net P&L", f: x => fmt(x, 0)}, {k: "ret_bps", label: "Net bps", f: x => fmt(x, 1)},
      {k: "entry_session", label: "Entry via", l: true, f: x => x || "–"}, {k: "exit_session", label: "Exit via", l: true, f: x => x || "–"}]); };
  document.getElementById("tradeFilter").oninput = drawTrades; drawTrades();
  const evs = frameRecs((s.diagnostic || {}).events);
  document.getElementById("eventNote").textContent = evs.length ? `${evs.length} largest |gap| of ${(s.diagnostic || {}).n_events_total} events; returns signed by the hypothesis, market-adjusted. Diagnostic - not executable.` : "No event-level diagnostic for this strategy.";
  table("events", evs, [{k: "date", label: "Date", l: true, f: x => (x || "").slice(0, 10)}, {k: "symbol", label: "Symbol", l: true}, {k: "sector", label: "Sector", l: true},
    {k: "gap", label: "Gap bps", f: x => fmt(1e4 * x, 0)}, {k: "ar_C0->O1", label: "C0→O1 bps", f: x => x === null ? "–" : fmt(1e4 * x, 1)},
    {k: "ar_C0->C1", label: "C0→C1 bps", f: x => x === null ? "–" : fmt(1e4 * x, 1)}, {k: "ar_O1->C1", label: "O1→C1 bps (exec.)", f: x => x === null ? "–" : fmt(1e4 * x, 1)}]);
  table("rejects", s.rejections || [], [{k: "leg", label: "Leg", l: true}, {k: "reason", label: "Reason", l: true}, {k: "n", label: "Orders", f: x => fmt(x, 0)}, {k: "qty", label: "Requested qty", f: x => fmt(x, 0)}], {sortK: "n"});
  const rr = [];
  const pl = rob.placebo || {}; rr.push({check: "placebo (random signs)", value: `actual SR ${fmt(pl.actual)} vs placebo median ${fmt(median(pl.sharpes || []))}; p = ${fmt(pl.p_value)}`});
  frameRecs(rob.delay_sensitivity).forEach(r => rr.push({check: "delay: " + r.variant, value: `SR ${fmt(r.sharpe)}, fill ${pct(r.fill_rate, 0)}, trades ${fmt(r.trades, 0)}`}));
  frameRecs(rob.hedge_compare).forEach(r => rr.push({check: "hedge=" + r.hedge, value: `SR ${fmt(r.sharpe)}, ann vol ${pct(r.ann_vol)}, hedge P&L ${fmt(r.pnl_hedge, 0)}`}));
  frameRecs(rob.regimes).forEach(r => rr.push({check: "regime " + r.dimension + ": " + r.regime, value: `mean ${fmt(r.mean_daily_bps, 2)} bps/day, SR ${fmt(r.sharpe)} (${r.n_days} days)`}));
  const sf = s.shortfall || {}; Object.keys(sf).forEach(k => rr.push({check: "shortfall: " + k, value: fmt(sf[k], 1) + " bps"}));
  Object.entries((s.scenarios.base || {}).metrics?.rule_usage || {}).forEach(([k, v]) => rr.push({check: "rule lookups: " + k, value: String(v)}));
  table("robust", rr, [{k: "check", label: "Check", l: true}, {k: "value", label: "Result", l: true, f: x => x}]);
}
const median = a => { const b = a.filter(x => x !== null).slice().sort((x, y) => x - y); return b.length ? b[Math.floor(b.length / 2)] : null; };
function heat(x, y, z, title) {
  const p = PAL.div[isDark() ? "dark" : "light"];
  const flat = z.flat().filter(v => v !== null); const m = Math.max(1e-9, ...flat.map(Math.abs));
  return {type: "heatmap", x, y, z, zmin: -m, zmax: m, zmid: 0, xgap: 2, ygap: 2,
    colorscale: [[0, p[0]], [0.25, p[1]], [0.5, p[2]], [0.75, p[3]], [1, p[4]]], colorbar: {title: {text: title, side: "right"}, outlinewidth: 0, tickfont: {color: css("--text-muted")}},
    hovertemplate: "%{y}, %{x}: %{z:.2f}%<extra></extra>"};
}

function marketCharts(mkt) {
  const s1 = studies["S01_" + mkt]; const dg = s1 && s1.diagnostic;
  const dist = dg && dg.distribution;
  if (dist && dist.hist_counts) { const e = dist.hist_edges_bps; const mids = dist.hist_counts.map((_, i) => (e[i] + e[i + 1]) / 2);
    plot("gapdist", [{type: "bar", name: "Security-days", x: mids, y: dist.hist_counts, marker: {color: S(0)}, hovertemplate: "%{x:.0f} bps: %{y} days<extra></extra>"}],
      {showlegend: false, bargap: 0.05, xaxis: {title: {text: "Closing gap, bps"}}, yaxis: {title: {text: "Security-days"}}, title: {text: `${mkt}: n=${dist.n}, mean |gap| ${fmt(dist.abs_mean_bps, 1)} bps, ${pct(dist.frac_zero, 0)} zero`, font: {size: 13}}});
  } else Plotly.purge("gapdist");
}
function carChart() {
  const tr = []; const tabRows = [];
  ["SET", "SGX", "HKEX"].forEach((m, i) => { const dg = (studies["S01_" + m] || {}).diagnostic; if (!dg) return;
    const p = frameRecs(dg.car_path); tr.push({type: "scatter", mode: "lines+markers", name: m, x: p.map(r => r.step), y: p.map(r => r.mean_bps), line: {width: 2, color: S(i)}, marker: {size: 8}, hovertemplate: m + " %{x}: %{y:.1f} bps<extra></extra>"});
    frameRecs(dg.summary).filter(r => r.kind === "market_adjusted").forEach(r => tabRows.push(Object.assign({market: m}, r))); });
  plot("car", tr, {xaxis: {title: {text: "Window (C0 = closing auction, O1 = next open)"}}, yaxis: {title: {text: "Mean signed abnormal return, bps"}}});
  table("cartable", tabRows, [{k: "market", label: "Mkt", l: true}, {k: "horizon", label: "Window", l: true}, {k: "executable_window", label: "Exec?", l: true, f: x => x ? "yes" : "no"},
    {k: "mean_bps", label: "Mean bps", f: x => fmt(x, 1)}, {k: "ci_lo_bps", label: "CI lo", f: x => fmt(x, 1)}, {k: "ci_hi_bps", label: "CI hi", f: x => fmt(x, 1)}, {k: "t", label: "t"}, {k: "n_events", label: "Events", f: x => fmt(x, 0)}]);
}
function rebalChart(mkt) {
  const dg = (studies["S05_" + mkt] || {}).diagnostic; if (!dg) { Plotly.purge("rebal"); table("rebaltable", []); return; }
  const c = frameRecs(dg.car); const tr = [];
  ["add", "delete"].forEach((t, i) => { const r = c.filter(x => x.event_type === t); tr.push({type: "scatter", mode: "lines+markers", name: t === "add" ? "Additions" : "Deletions", x: r.map(x => x.rel_day), y: r.map(x => x.car_bps), line: {width: 2, color: S(i)}, marker: {size: 8}, hovertemplate: t + " day %{x}: %{y:.0f} bps<extra></extra>"}); });
  plot("rebal", tr, {xaxis: {title: {text: "Sessions relative to index trade day"}}, yaxis: {title: {text: "CAR, bps"}}});
  const st = dg.stats || {}; table("rebaltable", Object.entries(st).map(([k, v]) => Object.assign({window: k}, v)).concat([{window: "events", n: dg.n_events}]),
    [{k: "window", label: "Window", l: true}, {k: "mean_bps", label: "Mean signed bps", f: x => fmt(x, 1)}, {k: "t", label: "t"}, {k: "p", label: "p", f: x => fmt(x, 3)}, {k: "n", label: "n", f: x => fmt(x, 0)}]);
}
function heat1() {
  const rows = (D.summary.leaderboard || []).filter(r => r.status === "ran");
  const y = [], z = []; let years = new Set();
  rows.forEach(r => { const s = studies[r.strategy + "_" + r.market]; ((s.scenarios.base || {}).yearly ? frameRecs(s.scenarios.base.yearly) : []).forEach(q => years.add(q.year)); });
  years = [...years].sort();
  rows.forEach(r => { const s = studies[r.strategy + "_" + r.market]; const yr = frameRecs((s.scenarios.base || {}).yearly);
    y.push(r.strategy + " " + r.market); z.push(years.map(Y => { const q = yr.find(a => a.year === Y); return q ? 100 * q.ret_net : null; })); });
  plot("heat1", [heat(years.map(String), y, z, "net return, %")], {margin: {l: 90}, yaxis: {autorange: "reversed"}});
}
function attribution() {
  const rows = (D.summary.leaderboard || []).filter(r => r.status === "ran");
  const y = rows.map(r => r.strategy + " " + r.market);
  const capital = (D.manifest || {}).capital || {};
  const tr = [["pnl_long", "Long book"], ["pnl_short", "Short book"], ["pnl_hedge", "Futures hedge"]].map(([k, n], i) => ({type: "bar", orientation: "h", name: n, y,
    x: rows.map(r => { const m = ((studies[r.strategy + "_" + r.market].scenarios.base || {}).metrics) || {}; const c = capital[r.market]; return c && m[k] !== null && m[k] !== undefined ? 100 * m[k] / c : null; }),
    marker: {color: S(i)}, hovertemplate: n + " %{y}: %{x:.2f}% of capital<extra></extra>"}));
  plot("attrib", tr, {barmode: "group", bargap: 0.25, bargroupgap: 0.08, margin: {l: 90}, yaxis: {autorange: "reversed"}, xaxis: {title: {text: "Total P&L, % of capital (whole sample)"}}});
}
function dataQuality() {
  const st = (D.data_quality.stats || {}).daily_by_market_year || [];
  const tr = ["SET", "SGX", "HKEX"].map((m, i) => { const r = st.filter(x => x.market === m); return {type: "scatter", mode: "lines+markers", name: m, x: r.map(x => x.year),
    y: r.map(x => 100 * (x.missing_lcp + x.no_auction) / x.rows), line: {width: 2, color: S(i)}, marker: {size: 8}, hovertemplate: m + " %{x}: %{y:.2f}%<extra></extra>"}; });
  plot("dqchart", tr, {xaxis: {title: {text: "Year"}, dtick: 1}, yaxis: {title: {text: "% rows without usable auction gap"}}});
  table("dqtable", frameRecs(D.data_quality.issues), [{k: "table", label: "Table", l: true}, {k: "check", label: "Check", l: true}, {k: "severity", label: "Severity", l: true}, {k: "count", label: "Count", f: x => fmt(x, 0)}], {sortK: "count"});
  const av = D.availability || []; const byData = {}; av.forEach(r => { (byData[r.data] = byData[r.data] || {data: r.data})[r.market] = r.status; });
  table("avtable", Object.values(byData), [{k: "data", label: "Dataset", l: true}, {k: "SET", label: "SET", l: true}, {k: "SGX", label: "SGX", l: true}, {k: "HKEX", label: "HKEX", l: true}]);
  const sp = D.support || []; const bySt = {}; sp.forEach(r => { (bySt[r.strategy] = bySt[r.strategy] || {strategy: r.strategy})[r.market] = r.support; });
  table("suptable", Object.values(bySt), [{k: "strategy", label: "Strategy", l: true}, {k: "SET", label: "SET", l: true}, {k: "SGX", label: "SGX", l: true}, {k: "HKEX", label: "HKEX", l: true}]);
}
function pipeline() {
  const pv = D.summary.pipeline_validation || {}; if (!pv.applicable) { table("pv", [{note: "Not a synthetic run"}]); return; }
  const rows = []; Object.entries(pv.estimated || {}).forEach(([m, est]) => Object.entries(est).forEach(([k, v]) => rows.push({market: m, quantity: k, planted: (pv.planted || {})[k], estimated: v})));
  table("pv", rows, [{k: "market", label: "Market", l: true}, {k: "quantity", label: "Quantity", l: true}, {k: "planted", label: "Planted", f: x => fmt(x, 3)}, {k: "estimated", label: "Estimated", f: x => fmt(x, 3)}]);
}
function init() {
  header(); leaderboard();
  const sel = document.getElementById("selStudy"); sel.innerHTML = keys.map(k => `<option value="${k}">${k.replace("_", " ")} — ${studies[k].name}</option>`).join("");
  const mk = [...new Set(keys.map(k => studies[k].market))];
  ["selMarket", "selMarket2"].forEach(id => document.getElementById(id).innerHTML = mk.map(m => `<option>${m}</option>`).join(""));
  const all = () => { grossNet(); studyCharts(sel.value); marketCharts(document.getElementById("selMarket").value); carChart(); heat1(); rebalChart(document.getElementById("selMarket2").value); attribution(); dataQuality(); pipeline(); };
  sel.onchange = () => studyCharts(sel.value);
  document.getElementById("selMarket").onchange = e => marketCharts(e.target.value);
  document.getElementById("selMarket2").onchange = e => rebalChart(e.target.value);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", all);
  new MutationObserver(all).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  all();
}
if (window.Plotly) init(); else document.addEventListener("DOMContentLoaded", () => window.Plotly ? init() : document.body.insertAdjacentHTML("afterbegin", "<p style='padding:16px'>plotly.js failed to load; run <code>python -m auction_alpha report --latest --offline</code> for a self-contained file.</p>"));
</script>
</body>
</html>
"""
