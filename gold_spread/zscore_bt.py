"""Z-score of log(GF10/GO): enter at |z|>=2, exit when z crosses 0. Signal at bar t, fill at close t+1."""
import sys, numpy as np, pandas as pd

TICK_GF, TICK_GO = 10.0, 0.1          # 1 tick per leg per side as trading cost

def run(m, lookback, flat_eod, entry=2.0):
    s = np.log(m.c_gf) - np.log(m.c_go)
    z = ((s - s.rolling(lookback).mean()) / s.rolling(lookback).std()).values
    day = m.index.normalize()
    last = np.r_[day[1:] != day[:-1], True]                       # last bar of each day
    near_close = last | np.r_[last[1:], True] if flat_eod else np.zeros(len(m), bool)
    tgt, p = np.zeros(len(m)), 0.0
    for i, zi in enumerate(z):
        if near_close[i] or np.isnan(zi): p = 0.0 if near_close[i] else p
        elif p == 0 and abs(zi) >= entry: p = -np.sign(zi)            # z>+2 short spread, z<-2 long
        elif p != 0 and np.sign(zi) == p: p = 0.0                     # crossed 0
        tgt[i] = p
    held = np.r_[0.0, tgt[:-1]]                                       # filled at close t+1
    r = np.r_[0.0, np.diff(s.values)]
    gross = np.r_[0.0, held[:-1]] * r                                 # earns from t+1 onward
    cost = np.abs(np.diff(np.r_[0.0, held])) * (TICK_GF / m.c_gf.values + TICK_GO / m.c_go.values)
    trade_id = np.cumsum((held != 0) & (np.r_[0.0, held[:-1]] != held))
    df = pd.DataFrame({'g': gross, 'c': cost, 'id': np.where(np.r_[0.0, held[:-1]] != 0, trade_id, 0)}, index=m.index)
    tr = df[df.id > 0].groupby('id').agg(g=('g', 'sum'), bars=('g', 'size'))
    tr['n'] = tr.g - 2 * cost[cost > 0].mean()
    return z, pd.Series(gross - cost, index=m.index), tr

if __name__ == '__main__':
    m = pd.read_pickle(sys.argv[1]); rows = {}
    for lb in (30, 150, 750):                       # ~1.4h, ~1 day, ~1 week of both-traded bars
        for eod in (True, False):
            z, net, tr = run(m, lb, eod)
            d = net.groupby(net.index.date).sum()
            rows[(lb, 'flat 16:50' if eod else 'hold overnight')] = dict(
                trades=len(tr), win=(tr.n > 0).mean(), gross_bp=tr.g.mean() * 1e4, net_bp=tr.n.mean() * 1e4,
                hold_bars=tr.bars.median(), total_net_pct=net.sum() * 100, sharpe=d.mean() / d.std() * np.sqrt(250))
    print(pd.DataFrame(rows).T.round(2).to_string())
