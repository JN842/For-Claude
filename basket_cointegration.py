"""Basket cointegration screen over SET100: 1-vs-2 and 2-vs-2 stocks.

Every 3-stock and 4-stock combination of the screened SET100 universe (same
stocks, windows and exclusions as ``cointegration_set100``) is tested with
the Johansen trace test.  The first cointegrating vector gives the basket
weights on log prices; its signs say which stocks are long and which short:

* 3 stocks: one sign against two  -> 1 vs 2
* 4 stocks: two against two        -> 2 vs 2 (1 vs 3 is dropped)

Filters, in order (same spirit as the pair screen):

1. Formation window Johansen trace rejects r=0 at 5%.
2. Sign pattern is 1v2 or 2v2, and every leg carries at least
   ``MIN_LEG_SHARE`` of the gross weight (otherwise it is really a smaller
   basket).
3. Spread half-life within ``HALF_LIFE_RANGE``.
4. Out-of-sample: the spread built with the *formation* weights is
   stationary in the validation window (ADF p<0.05).
5. Recent 2-year window: Johansen trace again rejects r=0 at 5%.

No multiple-testing adjustment is applied (as requested).

Johansen is computed in batches with numpy: with one lag (k_ar_diff=1) and a
constant (det_order=0), every moment matrix a basket needs is a sub-block of
one cross-product matrix of all stocks, so millions of baskets need only
small batched solves.  ``check_against_statsmodels`` verifies the result
matches ``statsmodels.tsa.vector_ar.vecm.coint_johansen``.

Run after ``cointegration_set100.py`` has cached prices:
    python basket_cointegration.py
"""

import itertools
import time
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from statsmodels.tsa.adfvalues import mackinnonp
from statsmodels.tsa.coint_tables import c_sjt
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen

import cointegration_set100 as base


ALPHA = base.ALPHA
HALF_LIFE_RANGE = base.HALF_LIFE_RANGE
MIN_LEG_SHARE = 0.10          # each stock >= 10% of gross |weight|
CHUNK = 100_000
FAST_ADF_PREFILTER = 0.20     # exact ADF only where the quick ADF p < this
OUT_DIR = base.OUT_DIR


# ---------------------------------------------------------------------------
# Batched Johansen (k_ar_diff=1, det_order=0)
# ---------------------------------------------------------------------------

class Moments:
    """Cross-products of [dY_t, Y_t-1, dY_t-1] for all stocks in a window."""

    def __init__(self, logp):
        x = np.asarray(logp, dtype=float)
        d = np.diff(x, axis=0)
        z0, z1, z2 = d[1:], x[1:-1], d[:-1]
        g = np.hstack([z0, z1, z2])
        g = g - g.mean(axis=0)           # projects out the constant
        self.t = g.shape[0]
        self.n = x.shape[1]
        self.m = g.T @ g / self.t
        self.mean = x.mean(axis=0)
        self.cov = np.cov(x, rowvar=False, bias=True)

    def blocks(self, idx):
        """idx (B, k) -> M00, M01, M02, M11, M12, M22 each (B, k, k)."""
        n = self.n
        parts = [idx, idx + n, idx + 2 * n]
        def blk(i, j):
            return self.m[parts[i][:, :, None], parts[j][:, None, :]]
        return blk(0, 0), blk(0, 1), blk(0, 2), blk(1, 1), blk(1, 2), blk(2, 2)


def johansen_batch(mom, idx):
    """Trace statistic for r=0 and first cointegrating vector, per basket."""
    m00, m01, m02, m11, m12, m22 = mom.blocks(idx)
    m22_inv_m20 = np.linalg.solve(m22, np.swapaxes(m02, 1, 2))
    m22_inv_m21 = np.linalg.solve(m22, np.swapaxes(m12, 1, 2))
    s00 = m00 - m02 @ m22_inv_m20
    s01 = m01 - m02 @ m22_inv_m21
    s11 = m11 - m12 @ m22_inv_m21
    s10 = np.swapaxes(s01, 1, 2)

    chol = np.linalg.cholesky(s11)
    chol_inv = np.linalg.inv(chol)
    inner = s10 @ np.linalg.solve(s00, s01)
    sym = chol_inv @ inner @ np.swapaxes(chol_inv, 1, 2)
    sym = (sym + np.swapaxes(sym, 1, 2)) / 2
    eigval, eigvec = np.linalg.eigh(sym)                 # ascending
    eigval = np.clip(eigval, 0, 1 - 1e-12)
    trace = -mom.t * np.log(1 - eigval).sum(axis=1)
    vec = np.swapaxes(chol_inv, 1, 2) @ eigvec[:, :, -1:]  # largest eigval
    return trace, vec[:, :, 0]


def normalise(w):
    """Scale so the largest |weight| is +1 (sign of that stock positive)."""
    pick = np.abs(w).argmax(axis=1)
    lead = w[np.arange(len(w)), pick]
    return w / lead[:, None]


def half_life_batch(mom, idx, w):
    """OU half-life from  d s = lam * s(t-1),  s = Y w  (demeaned)."""
    _, m01, _, m11, _, _ = mom.blocks(idx)
    num = np.einsum("bi,bij,bj->b", w, np.swapaxes(m01, 1, 2), w)
    den = np.einsum("bi,bij,bj->b", w, m11, w)
    lam = num / den
    with np.errstate(divide="ignore"):
        return np.where(lam < 0, -np.log(2) / lam, np.inf)


def spread_stats(mom, idx, w):
    mu = (mom.mean[idx] * w).sum(axis=1)
    cov = mom.cov[idx[:, :, None], idx[:, None, :]]
    sd = np.sqrt(np.einsum("bi,bij,bj->b", w, cov, w))
    return mu, sd


def fast_adf_p(spreads):
    """ADF with one lagged difference and a constant, batched over rows."""
    s = spreads
    ds = np.diff(s, axis=1)
    y = ds[:, 1:]
    x1 = s[:, 1:-1]
    x2 = ds[:, :-1]
    n = y.shape[1]
    design = np.stack([np.ones_like(x1), x1, x2], axis=2)   # (B, n, 3)
    xtx = np.einsum("bni,bnj->bij", design, design)
    xty = np.einsum("bni,bn->bi", design, y)
    coef = np.linalg.solve(xtx, xty[:, :, None])[:, :, 0]
    resid = y - np.einsum("bni,bi->bn", design, coef)
    sigma2 = (resid ** 2).sum(axis=1) / (n - 3)
    se = np.sqrt(sigma2 * np.linalg.inv(xtx)[:, 1, 1])
    tstat = coef[:, 1] / se
    return np.array([mackinnonp(t, regression="c", N=1) for t in tstat])


def exact_adf(series):
    warnings.simplefilter("ignore")
    return adfuller(series, autolag="AIC", result_object=False)[1]


def check_against_statsmodels(logp, k=4, n_checks=20, seed=0):
    rng = np.random.default_rng(seed)
    mom = Moments(logp.values)
    idx = np.array([rng.choice(logp.shape[1], k, replace=False)
                    for _ in range(n_checks)])
    trace, vec = johansen_batch(mom, idx)
    worst_stat, worst_vec = 0.0, 0.0
    for b in range(n_checks):
        ref = coint_johansen(logp.values[:, idx[b]], 0, 1)
        worst_stat = max(worst_stat, abs(ref.lr1[0] - trace[b]) / ref.lr1[0])
        v_ref = ref.evec[:, 0] / np.abs(ref.evec[:, 0]).max()
        v_me = vec[b] / np.abs(vec[b]).max()
        if np.sign(v_ref[np.abs(v_ref).argmax()]) != np.sign(
                v_me[np.abs(v_me).argmax()]):
            v_me = -v_me
        worst_vec = max(worst_vec, np.abs(v_ref - v_me).max())
    return worst_stat, worst_vec


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def sign_pattern_ok(w):
    k = w.shape[1]
    neg = (w < 0).sum(axis=1)
    if k == 3:
        return (neg == 1) | (neg == 2)
    return neg == 2


def screen_size(k, stocks, frames, moms):
    n = len(stocks)
    crit_form = c_sjt(k, 0)[1]
    combos = itertools.combinations(range(n), k)
    keep = []
    total = 0
    while True:
        chunk = np.fromiter(itertools.chain.from_iterable(
            itertools.islice(combos, CHUNK)), dtype=np.int32)
        if chunk.size == 0:
            break
        idx = chunk.reshape(-1, k)
        total += len(idx)
        trace, vec = johansen_batch(moms["formation"], idx)
        ok = trace > crit_form
        idx, trace, vec = idx[ok], trace[ok], vec[ok]
        w = normalise(vec)
        share = np.abs(w) / np.abs(w).sum(axis=1, keepdims=True)
        ok = sign_pattern_ok(w) & (share.min(axis=1) >= MIN_LEG_SHARE)
        idx, trace, w = idx[ok], trace[ok], w[ok]
        hl = half_life_batch(moms["formation"], idx, w)
        ok = (hl >= HALF_LIFE_RANGE[0]) & (hl <= HALF_LIFE_RANGE[1])
        keep.append((idx[ok], trace[ok], w[ok], hl[ok]))
    idx = np.concatenate([k_[0] for k_ in keep])
    trace = np.concatenate([k_[1] for k_ in keep])
    w = np.concatenate([k_[2] for k_ in keep])
    hl = np.concatenate([k_[3] for k_ in keep])
    return total, idx, trace, w, hl


def validate(k, idx, w, frames, moms, workers=None):
    """Out-of-sample ADF with formation weights + recent-window Johansen."""
    valid = frames["validation"].values
    mu, sd = spread_stats(moms["formation"], idx, w)
    fast_p = np.empty(len(idx))
    z_last = np.empty(len(idx))
    for start in range(0, len(idx), 20_000):
        sl = slice(start, start + 20_000)
        spreads = np.einsum("tbk,bk->bt", valid[:, idx[sl]], w[sl])
        fast_p[sl] = fast_adf_p(spreads)
        z_last[sl] = (spreads[:, -1] - mu[sl]) / sd[sl]

    oos_p = np.full(len(idx), np.nan)
    todo = np.flatnonzero(fast_p < FAST_ADF_PREFILTER)
    series = [valid[:, idx[i]] @ w[i] for i in todo]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        oos_p[todo] = list(pool.map(exact_adf, series, chunksize=200))

    trace_recent, vec_recent = johansen_batch(moms["recent"], idx)
    w_recent = normalise(vec_recent)
    # Similarity of weights (formation vs recent), sign-free cosine.
    cos = np.abs((w * w_recent).sum(axis=1)) / (
        np.linalg.norm(w, axis=1) * np.linalg.norm(w_recent, axis=1))
    hl_recent = half_life_batch(moms["recent"], idx, w_recent)
    mu_r, sd_r = spread_stats(moms["recent"], idx, w_recent)
    recent = frames["recent"].values
    s_last = (recent[-1, idx] * w_recent).sum(axis=1)
    z_recent = (s_last - mu_r) / sd_r
    return dict(fast_p=fast_p, oos_p=oos_p, oos_z=z_last,
                recent_trace=trace_recent,
                recent_pass=trace_recent > c_sjt(k, 0)[1],
                w_recent=w_recent, weight_cos=cos,
                hl_recent=hl_recent, z_recent=z_recent)


def describe(stocks, k, idx, trace, w, hl, val):
    names = np.array(stocks)
    sym = names[idx]
    rows = {
        "k": k,
        "long": [" + ".join(f"{s}({x:.2f})" for s, x in zip(r, ww) if x > 0)
                 for r, ww in zip(sym, w)],
        "short": [" + ".join(f"{s}({-x:.2f})" for s, x in zip(r, ww) if x < 0)
                  for r, ww in zip(sym, w)],
        "stocks": [" ".join(r) for r in sym],
        "johansen_trace": trace,
        "half_life_days": hl,
        "oos_adf_p": val["oos_p"],
        "oos_zscore_last": val["oos_z"],
        "recent_trace": val["recent_trace"],
        "recent_pass": val["recent_pass"],
        "weight_cos": val["weight_cos"],
        "hl_recent": val["hl_recent"],
        "z_recent": val["z_recent"],
        "weights_recent": [" ".join(f"{s}:{x:.2f}" for s, x in zip(r, ww))
                           for r, ww in zip(sym, val["w_recent"])],
        "sectors": [" | ".join(sorted({base.SECTOR_OF[s] for s in r}))
                    for r in sym],
        "n_sectors": [len({base.SECTOR_OF[s] for s in r}) for r in sym],
    }
    for j in range(k):
        rows[f"s{j + 1}"] = sym[:, j]
        rows[f"w{j + 1}"] = w[:, j]
    out = pd.DataFrame(rows)
    out["pass_oos"] = out["oos_adf_p"] < ALPHA
    out["pass_all"] = out["pass_oos"] & out["recent_pass"]
    return out


def main():
    t0 = time.time()
    close = base.clean_prices(pd.read_csv(OUT_DIR / "set100_close.csv",
                                          index_col=0, parse_dates=True))
    frames = {}
    for name, (s, e) in base.WINDOWS.items():
        frames[name], _ = base.window_prices(close, s, e)
    stocks = [s for s in frames["formation"].columns if s not in base.EXCLUDE]
    stocks = [s for s in stocks
              if s in frames["validation"] and s in frames["recent"]]
    for name in frames:
        frames[name] = frames[name][stocks]
    moms = {name: Moments(f.values) for name, f in frames.items()}

    err_stat, err_vec = check_against_statsmodels(frames["formation"])
    print(f"check vs statsmodels: max rel trace err {err_stat:.2e}, "
          f"max weight err {err_vec:.2e}")

    summary, results = [], []
    for k in (3, 4):
        total, idx, trace, w, hl = screen_size(k, stocks, frames, moms)
        print(f"k={k}: {total:,} baskets, {len(idx):,} pass in-sample "
              f"({time.time() - t0:.0f}s)")
        val = validate(k, idx, w, frames, moms)
        res = describe(stocks, k, idx, trace, w, hl, val)
        results.append(res)
        summary += [
            (f"{k} stocks: baskets tested", total),
            (f"{k} stocks: in-sample pass (Johansen + sign + legs + HL)",
             len(res)),
            (f"{k} stocks: + out-of-sample ADF p<0.05", int(res.pass_oos.sum())),
            (f"{k} stocks: + recent 2y Johansen (pass all)",
             int(res.pass_all.sum())),
        ]
        print(f"k={k}: oos pass {res.pass_oos.sum():,}, "
              f"pass all {res.pass_all.sum():,} ({time.time() - t0:.0f}s)")

    result = pd.concat(results, ignore_index=True)
    result = result.sort_values(["pass_all", "oos_adf_p"],
                                ascending=[False, True])
    summary.append(("Stocks in universe", len(stocks)))
    summary = pd.DataFrame(summary, columns=["item", "value"])
    passed = result[result.pass_all]
    # Shortlist: few sectors, weights stable between formation and recent
    # windows, and still fast mean reversion in the recent window.
    shortlist = passed[(passed.n_sectors <= 2) & (passed.weight_cos >= 0.9)
                       & (passed.hl_recent <= 20)]
    cols = ["k", "long", "short", "sectors", "n_sectors", "johansen_trace",
            "half_life_days", "oos_adf_p", "oos_zscore_last", "recent_trace",
            "recent_pass", "weight_cos", "hl_recent", "z_recent",
            "weights_recent", "pass_oos", "pass_all", "stocks"]
    run_info = pd.DataFrame([
        ("as_of", base.AS_OF.date()),
        *[(f"{n}_window", f"{a.date()} to {b.date()}")
          for n, (a, b) in base.WINDOWS.items()],
        ("test", "Johansen trace r=0, k_ar_diff=1, constant, 5%"),
        ("weights", "first cointegrating vector on log prices, max |w| = 1"),
        ("min_leg_share", MIN_LEG_SHARE),
        ("half_life_range_days", str(HALF_LIFE_RANGE)),
        ("excluded", ", ".join(sorted(base.EXCLUDE))),
        ("multiple_testing", "none"),
    ], columns=["item", "value"])
    path = OUT_DIR / f"basket_cointegration_{base.AS_OF:%Y%m%d}.xlsx"
    with pd.ExcelWriter(path) as xl:
        summary.to_excel(xl, sheet_name="Summary", index=False)
        shortlist[cols].to_excel(xl, sheet_name="Shortlist", index=False)
        passed[cols].to_excel(xl, sheet_name="Pass_All", index=False)
        result[cols].to_excel(xl, sheet_name="InSample_Pass", index=False)
        run_info.to_excel(xl, sheet_name="Run_Info", index=False)
    result.to_csv(OUT_DIR / f"basket_insample_{base.AS_OF:%Y%m%d}.csv.gz",
                  index=False)
    print(summary.to_string(index=False))
    print(f"shortlist: {len(shortlist)}")
    print(f"Written: {path}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
