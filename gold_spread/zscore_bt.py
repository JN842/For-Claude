"""Z-score of log(GF10/GO): enter at |z|>=2, exit when z crosses 0. Signal at close of bar t, fill at open of t+1."""
import sys, numpy as np, pandas as pd

TICK_GF, TICK_GO = 10.0, 0.1          # 1 tick per leg per side as trading cost

def run(m, lookback, flat_eod, entry=2.0, fill='open'):
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
    held = np.r_[0.0, tgt[:-1]]                                       # position from the fill at t+1 onward
    sc = s.values
    so = (np.log(m.o_gf) - np.log(m.o_go)).values if fill == 'open' else sc
    prev = np.r_[0.0, held[:-1]]                                      # position held before the fill
    gross = prev * (so - np.r_[sc[0], sc[:-1]]) + held * (sc - so)    # gap to fill on old pos, fill to close on new
    px_gf = m.o_gf.values if fill == 'open' else m.c_gf.values
    px_go = m.o_go.values if fill == 'open' else m.c_go.values
    cost = np.abs(held - prev) * (TICK_GF / px_gf + TICK_GO / px_go)
    trade_id = np.cumsum((held != 0) & (prev != held))
    df = pd.DataFrame({'g': gross, 'c': cost, 'id': np.where((held != 0) | (prev != 0), trade_id, 0)}, index=m.index)
    tr = df[df.id > 0].groupby('id').agg(g=('g', 'sum'), c=('c', 'sum'), bars=('g', 'size'))
    tr['n'] = tr.g - tr.c
    return z, pd.Series(gross - cost, index=m.index), tr

if __name__ == '__main__':
    m = pd.read_pickle(sys.argv[1]); rows = {}
    for lb in (30, 150, 750):                       # ~1.4h, ~1 day, ~1 week of both-traded bars
        for eod in (True, False):
          for fill in ('open', 'close'):
            z, net, tr = run(m, lb, eod, fill=fill)
            d = net.groupby(net.index.date).sum()
            rows[(lb, 'flat 16:50' if eod else 'hold overnight', fill)] = dict(
                  trades=len(tr), win=(tr.n > 0).mean(), gross_bp=tr.g.mean() * 1e4, net_bp=tr.n.mean() * 1e4,
                  hold_bars=tr.bars.median(), total_net_pct=net.sum() * 100, sharpe=d.mean() / d.std() * np.sqrt(250))
    print(pd.DataFrame(rows).T.round(2).to_string())
