"""Clean GO / GF10 1-min continuous bars -> aligned day-session closes."""
import sys, numpy as np, pandas as pd

ROLL_MONTHS = {3, 6, 9, 12}          # GO quarterly contracts
ROLL_MIN_JUMP = 0.009                # GO roll gap ~1.2-1.7% vs GF10
SKIP_ROLLS = ['2026-03-23 18:50']    # flip to next and back again before the next day session (net 0)

def load(path, sheet):
    d = pd.read_excel(path, sheet, header=None, names=['t', 'o', 'h', 'l', 'c', 'v'])
    d = d.drop_duplicates('t').sort_values('t').set_index('t')
    ok = (d.v > 0) & (d.l > 0) & (d.l <= d.h) & d.c.between(d.l, d.h)
    return d[ok]

def go_rolls(go, gf):
    """Session-gap jumps of GO that GF10 does not share = contract switches."""
    lgo, lgf = np.log(go.c), np.log(gf.c)
    gap = go.index.to_series().diff() > pd.Timedelta('30min')
    t, t0 = go.index[gap], go.index[np.flatnonzero(gap) - 1]
    gf_after = gf.index[gf.index.searchsorted(t)]          # first GF10 trade after the gap
    rel = (lgo[t].values - lgo[t0].values) - (lgf.asof(t).values - lgf.asof(t0).values)
    rel_gf = (lgf[gf_after].values - lgf.asof(t0).values) - (lgo.asof(gf_after).values - lgo[t0].values)
    r = pd.Series(rel, index=t)
    keep = ((r.abs() > ROLL_MIN_JUMP) & (np.abs(rel_gf) > 0.6 * ROLL_MIN_JUMP)
            & t.month.isin(ROLL_MONTHS) & (t.day >= 13) & ~t.isin(pd.to_datetime(SKIP_ROLLS)))
    return r[keep]

def back_adjust(px, rolls):
    """Multiplicative back-adjustment: price levels before each roll are scaled by exp(jump)."""
    f = pd.Series(0.0, index=px.index)
    for t, j in rolls.items():
        f[f.index < t] += j
    return px * np.exp(f.values)[:, None] if px.ndim == 2 else px * np.exp(f)

def clean(path):
    go, gf = load(path, 'GO'), load(path, 'GF10')
    rolls = go_rolls(go, gf)
    go[['o', 'h', 'l', 'c']] = back_adjust(go[['o', 'h', 'l', 'c']], rolls)
    m = go[['o', 'c', 'v']].join(gf[['o', 'c', 'v']], how='inner', lsuffix='_go', rsuffix='_gf')  # both traded that minute
    hm = m.index.strftime('%H:%M')
    m = m[(hm >= '09:45') & (hm <= '16:50')]                    # bar-open time, day session only
    m = m[m.index.normalize() < m.index.normalize().max()]      # drop today's unfinished session
    s = np.log(m.c_gf) - np.log(m.c_go)                         # spike filter on log ratio
    dev = (s - s.rolling(31, center=True, min_periods=5).median()).abs()
    m = m[dev <= 10 * 1.4826 * s.diff().abs().median()]
    return m, rolls

if __name__ == '__main__':
    m, rolls = clean(sys.argv[1])
    print('GO rolls adjusted:\n', rolls.round(4).to_string())
    print('rows', len(m), m.index.min(), '->', m.index.max())
    m.to_pickle(sys.argv[2])
