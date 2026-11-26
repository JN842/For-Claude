"""Cointegration tests GF10 vs GO on cleaned day-session bars."""
import sys, numpy as np, pandas as pd, statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen

OZ_PER_BAHT = 15.244 * 0.965 / 31.1035     # fine oz in 1 baht-weight of 96.5% gold

def half_life(e):
    e = pd.Series(e).dropna(); phi = sm.OLS(e.diff().iloc[1:], sm.add_constant(e.shift().iloc[1:])).fit().params.iloc[1]
    return -np.log(2) / np.log1p(phi) if phi < 0 else np.inf

def eg(y, x, trend='c'):
    X = sm.add_constant(x) if trend == 'c' else sm.add_constant(np.column_stack([x, np.arange(len(x))]))
    f = sm.OLS(y, X).fit(); t, p, _ = coint(y, x, trend=trend, maxlag=10, autolag='AIC')
    return f.params[1], t, p, f.resid

def grid(m, rule):  # last close per bucket, only buckets where both legs traded
    g = m.groupby(pd.Grouper(freq=rule)).last().dropna()
    return g[(g.index.strftime('%H:%M') >= '09:45') & (g.index.strftime('%H:%M') <= '16:50')]

m = pd.read_pickle(sys.argv[1])
daily = m.groupby(m.index.date).last(); daily.index = pd.to_datetime(daily.index)
sets = {'1min (both traded)': m, '5min grid': grid(m, '5min'), 'daily (last day-session close)': daily}
rows = []
for name, d in sets.items():
    y, x = np.log(d.c_gf.values), np.log(d.c_go.values)
    for tr in ('c', 'ct'):
        b, t, p, e = eg(y, x, tr)
        rows.append([name, tr, len(d), b, t, p, np.std(e) * 1e4, half_life(e)])
res = pd.DataFrame(rows, columns=['sample', 'trend', 'n', 'beta', 'EG_t', 'EG_p', 'resid_sd_bp', 'HL_bars'])
print(res.round(4).to_string(index=False))

d = sets['5min grid']; L = np.log(d[['c_gf', 'c_go']].values)
j = coint_johansen(L, det_order=0, k_ar_diff=2)
print('\nJohansen 5min trace:', j.lr1.round(2), 'crit95:', j.cvt[:, 1].round(2))
print('Johansen daily trace:', coint_johansen(np.log(daily[['c_gf', 'c_go']].values), 0, 1).lr1.round(2),
      'crit95:', coint_johansen(np.log(daily[['c_gf', 'c_go']].values), 0, 1).cvt[:, 1].round(2))

for c in ('c_gf', 'c_go'):
    print(f'ADF level {c}: p={adfuller(np.log(daily[c]), autolag="AIC")[1]:.3f}  diff p={adfuller(np.log(daily[c]).diff().dropna())[1]:.3g}')

fx = daily.c_gf / (daily.c_go * OZ_PER_BAHT)
print(f'\nImplied USDTHB (GF10/GO/{OZ_PER_BAHT:.5f}): min {fx.min():.2f} max {fx.max():.2f}  ADF p={adfuller(np.log(fx))[1]:.3f}  HL_days={half_life(np.log(fx)):.1f}')

# rolling windows: beta + EG fitted inside each window on the 5min grid
out = []
for key, w in d.groupby([d.index.to_period('W')]):
    if len(w) < 150: continue
    b, t, p, e = eg(np.log(w.c_gf.values), np.log(w.c_go.values))
    out.append([str(key[0]), len(w), b, p, half_life(e) * 5, np.std(e) * 1e4])
r = pd.DataFrame(out, columns=['week', 'n', 'beta', 'EG_p', 'HL_min', 'resid_sd_bp'])
print(f'\nWeekly windows (5min grid): {len(r)} weeks, EG p<0.05 in {(r.EG_p < .05).mean():.0%}, p<0.10 in {(r.EG_p < .10).mean():.0%}')
print(r[['beta', 'EG_p', 'HL_min', 'resid_sd_bp']].describe(percentiles=[.1, .25, .5, .75, .9]).round(3).to_string())
r.to_csv(sys.argv[2], index=False)
