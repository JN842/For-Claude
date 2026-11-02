"""Clearly-labelled SYNTHETIC market data for engineering tests and pipeline demos.

Nothing produced here is real market data.  Every table carries
``is_synthetic`` metadata in the store manifest and every report built from
it is stamped SYNTHETIC.

The generator simulates a "world" with *known* parameters so that the
research pipeline can be validated:

* ``planted`` world - closing-auction price pressure is mostly temporary and
  reverts overnight and next day by known fractions; index-rebalance
  pressure ramps into the effective close then reverts; index futures quoted
  after the cash close only partly price the expected reversal.
* ``null`` world - the same data-generating process but all auction pressure
  is permanent (a random walk), rebalance pressure is zero, so any
  "significant" strategy is a false positive.

Model (log prices, per stock i and day t)::

    efficient return  r_t = beta_i m_t + s_{sector,t} + e_{i,t}
        split into overnight / intraday / auction-information pieces
    auction pressure  p_t = sigma_p,i z_t + c_t           (c_t common to market)
    observed close    = eff_close_t + sum_k w_k p_{t-k}  (temporary part)
    permanent part of p_t is folded into the efficient price.

Edge cases deliberately included: suspensions, delistings, late listings,
missing rows, missing last-continuous prices, auctions with no match,
HKEX non-CAS securities, SGX days with zero TAC volume, half-days, splits,
cash dividends, borrow restrictions, daily price-limit clipping (SET).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..calendars import synthetic_calendar
from ..rules.adapters import get_adapter

GENERATOR_VERSION = "1.0.0"


@dataclass
class WorldParams:
    name: str = "planted"
    overnight_reversal: float = 0.45     # fraction of temporary pressure reverting by next open
    nextday_reversal: float = 0.25       # further fraction reverting by next close
    decay: float = 0.5                   # remaining residual decays by this factor per day
    permanent_frac: float = 0.15         # fraction of pressure that is permanent information
    common_pressure_bps: float = 8.0
    rebalance_pressure_bps: float = 150.0
    rebalance_reversal_days: int = 5
    futures_price_in_frac: float = 0.5   # fraction of expected reversal priced by post-close futures

    @classmethod
    def null(cls) -> "WorldParams":
        return cls(name="null", overnight_reversal=0.0, nextday_reversal=0.0, decay=1.0,
                   permanent_frac=1.0, rebalance_pressure_bps=0.0, futures_price_in_frac=1.0)


@dataclass
class MarketParams:
    n_stocks: int
    price_median: float
    price_lo: float
    price_hi: float
    adv_median: float          # turnover, local ccy
    pressure_bps: float        # base auction-pressure std
    close_auction_share: float
    lots: tuple = (100,)
    shortable_top_frac: float = 0.5


DEFAULT_MARKETS = {
    "SET": MarketParams(60, 15.0, 1.0, 400.0, 50e6, 35.0, 0.08, (100,), 0.4),
    "SGX": MarketParams(45, 1.5, 0.1, 40.0, 5e6, 30.0, 0.12, (100,), 0.5),
    "HKEX": MarketParams(60, 12.0, 0.3, 500.0, 60e6, 25.0, 0.10, (100, 200, 400, 500, 1000, 2000), 0.6),
}


@dataclass
class SyntheticConfig:
    start: str = "2018-01-02"
    end: str = "2026-10-30"
    seed: int = 20260101
    markets: dict = field(default_factory=lambda: dict(DEFAULT_MARKETS))
    world: WorldParams = field(default_factory=WorldParams)
    n_indicative_stocks: int = 20
    indicative_interval_s: int = 30
    index_size: int = 30
    missing_row_frac: float = 0.001
    missing_lcp_frac: float = 0.002

    def small(self, n_stocks: int = 12, start: str = "2021-01-04", end: str = "2022-12-30") -> "SyntheticConfig":
        c = dataclasses.replace(self, start=start, end=end, n_indicative_stocks=4, index_size=6)
        c.markets = {m: dataclasses.replace(p, n_stocks=n_stocks) for m, p in self.markets.items()}
        return c

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


TZ = {"SET": "Asia/Bangkok", "SGX": "Asia/Singapore", "HKEX": "Asia/Hong_Kong"}
SECTORS = ["Financials", "Energy", "Industrials", "ConsumerStaples", "ConsumerDisc", "Materials", "Property", "Technology"]


def _local_ts(date: pd.Timestamp, hhmm: str, tz: str) -> pd.Timestamp:
    return pd.Timestamp(f"{date.date()} {hhmm}", tz=tz).tz_convert("UTC")


def _tick_of(prices: np.ndarray, table) -> np.ndarray:
    out = np.full(np.shape(prices), table[0][1], dtype=float)
    for lower, t in table:
        out = np.where(prices >= lower, t, out)
    return out


def _round_ticks(prices: np.ndarray, table) -> np.ndarray:
    out = np.array(prices, dtype=float)
    ticks = np.full(out.shape, table[0][1], dtype=float)
    for lower, t in table:
        ticks = np.where(out >= lower, t, ticks)
    with np.errstate(invalid="ignore"):
        r = np.round(out / ticks) * ticks
    return np.round(np.where(np.isfinite(out), np.maximum(r, ticks), np.nan), 6)


class _MarketSim:
    def __init__(self, market: str, mp: MarketParams, cfg: SyntheticConfig, rng: np.random.Generator):
        self.m, self.mp, self.cfg, self.rng, self.w = market, mp, cfg, rng, cfg.world
        self.ad = get_adapter(market)
        self.cal = synthetic_calendar(market, cfg.start, cfg.end)
        self.days = self.cal.days
        self.T, self.N = len(self.days), mp.n_stocks
        self.tz = TZ[market]

    # ------------------------------------------------------------ securities
    def securities(self):
        rng, N, T, mp = self.rng, self.N, self.T, self.mp
        self.symbols = np.array([f"{self.m[:2]}{i:03d}" for i in range(N)])
        self.sector = rng.integers(0, len(SECTORS), N)
        p0 = np.exp(rng.normal(np.log(mp.price_median), 0.9, N)).clip(mp.price_lo, mp.price_hi)
        self.p0 = p0
        mcap = np.exp(rng.normal(np.log(mp.adv_median * 400), 1.0, N))
        self.shares = mcap / p0
        self.adv_turn = mp.adv_median * np.exp(rng.normal(0, 0.8, N)) * (mcap / np.median(mcap)) ** 0.5
        self.liq_pct = pd.Series(self.adv_turn).rank(pct=True).to_numpy()
        self.beta = rng.uniform(0.6, 1.4, N)
        self.idio = rng.uniform(0.010, 0.030, N)
        self.sigma_p = mp.pressure_bps / 1e4 * (0.6 + 1.4 * (1 - self.liq_pct))
        self.lot = rng.choice(np.array(mp.lots), N)
        # listing life
        list_idx = np.zeros(N, dtype=int)
        late = rng.random(N) < 0.12
        list_idx[late] = rng.integers(20, int(T * 0.7), late.sum())
        delist_idx = np.full(N, T - 1)
        gap = min(200, T // 3)
        dl = (rng.random(N) < 0.08) & (list_idx < T * 0.6) & (list_idx + gap < T - 20)
        delist_idx[dl] = [rng.integers(li + gap, T - 20) for li in list_idx[dl]]
        self.list_idx, self.delist_idx, self.delisted = list_idx, delist_idx, dl
        self.cas_eligible = (rng.random(N) < 0.85) if self.m == "HKEX" else np.ones(N, bool)
        self.alive = np.zeros((T, N), bool)
        for i in range(N):
            self.alive[list_idx[i]:delist_idx[i] + 1, i] = True

    # --------------------------------------------------------------- prices
    def prices(self):
        rng, T, N, w = self.rng, self.T, self.N, self.w
        # market vol regime (2-state Markov)
        hi = np.zeros(T, bool)
        for t in range(1, T):
            hi[t] = (rng.random() < 0.97) if hi[t - 1] else (rng.random() < 0.015)
        self.high_vol = hi
        sig_m = np.where(hi, 0.018, 0.008)
        m = rng.normal(0, 1, T) * sig_m
        s = rng.normal(0, 0.005, (T, len(SECTORS)))[:, self.sector]
        e = rng.standard_t(4, (T, N)) / np.sqrt(2.0) * self.idio
        r = self.beta * m[:, None] + s + e
        o_part, a_part = 0.25 * r, rng.normal(0, 0.0015, (T, N))
        # auction pressure
        z = np.clip(rng.standard_t(3, (T, N)) / np.sqrt(3.0), -8, 8)
        c = rng.normal(0, w.common_pressure_bps / 1e4, T)
        p = self.sigma_p * z + c[:, None]
        # securities without a closing auction get no auction pressure
        no_auc = rng.random((T, N)) < 0.03 * (1 - self.liq_pct) ** 2
        if self.m == "HKEX":
            no_auc |= ~self.cas_eligible[None, :]
        p = np.where(no_auc, 0.0, p)
        a_part = np.where(no_auc, 0.0, a_part)
        self.no_auction = no_auc
        perm = w.permanent_frac * p
        temp = (1 - w.permanent_frac) * p
        # dividends: price drop at the open of ex-date
        self._corporate_actions()
        div_drop = np.log1p(-self.div_yield_on_ex)  # (T,N), 0 when no dividend
        eff_close = np.log(self.p0)[None, :] + np.cumsum(r + perm + div_drop, axis=0)
        eff_prev = np.vstack([np.log(self.p0)[None, :], eff_close[:-1]])
        eff_open = eff_prev + o_part + div_drop
        eff_lcp = eff_close - a_part - perm
        # temporary-pressure residual weights
        K = 12
        wc = np.zeros(K)
        wc[0] = 1.0
        rem = 1 - w.overnight_reversal - w.nextday_reversal
        for k in range(1, K):
            wc[k] = rem * w.decay ** (k - 1)
        wo = wc.copy()
        wo[1] = 1 - w.overnight_reversal
        res_close = np.zeros((T, N))
        res_open = np.zeros((T, N))
        res_lcp = np.zeros((T, N))
        for k in range(K):
            sh = np.vstack([np.zeros((k, N)), temp[: T - k]]) if k else temp
            res_close += wc[k] * sh
            if k >= 1:
                res_open += wo[k] * sh
                res_lcp += wc[k] * sh
        self.temp_pressure = temp
        # index-rebalance pressure component (computed later; placeholder)
        self.eff = dict(close=eff_close, open=eff_open, lcp=eff_lcp)
        self.res = dict(close=res_close, open=res_open, lcp=res_lcp)
        self.r_eff = r

    def _corporate_actions(self):
        rng, T, N = self.rng, self.T, self.N
        self.div_yield_on_ex = np.zeros((T, N))
        rows = []
        payers = rng.random(N) < 0.5
        years = sorted(set(self.days.year))
        for i in np.where(payers)[0]:
            month = rng.integers(2, 11)
            y_ = rng.uniform(0.02, 0.05)
            for y in years:
                cands = np.where((self.days.year == y) & (self.days.month == month))[0]
                if not len(cands):
                    continue
                t = int(cands[min(10, len(cands) - 1)])
                if not (self.list_idx[i] + 25 < t <= self.delist_idx[i]):
                    continue
                self.div_yield_on_ex[t, i] = y_
                rows.append(dict(i=i, t=t, action_type="cash_dividend", yield_=y_, ratio=np.nan, ann_t=t - 20))
        self.split_factor = np.ones((T, N))  # cumulative shares per original share
        for i in rng.choice(N, size=min(3, N), replace=False):
            lo, hi = self.list_idx[i] + 60, self.delist_idx[i] - 30
            if hi <= lo:
                continue
            t = int(rng.integers(lo, hi))
            ratio = float(rng.choice([2.0, 5.0]))
            self.split_factor[t:, i] *= ratio
            rows.append(dict(i=i, t=t, action_type="split", yield_=np.nan, ratio=ratio, ann_t=t - 15))
        self.ca_rows = rows

    # ---------------------------------------------------------------- index
    def index_and_rebalance(self):
        cfg, w, T, N = self.cfg, self.w, self.T, self.N
        size = min(cfg.index_size, max(2, N // 2))
        self.index_id = f"SYN_{self.m}_IDX"
        days = self.days
        logp_est = self.eff["close"]  # used only for ranking at review cut-off (past data)
        mcap = np.exp(logp_est) * self.shares[None, :]
        members = np.zeros((T, N), bool)
        alive0 = self.alive[0]
        first = np.argsort(-np.where(alive0, mcap[0], -1))[:size]
        cur = set(int(i) for i in first if alive0[i])
        events = []
        eff_dates = []
        for y in sorted(set(days.year)):
            for mo in (1, 7):
                cands = np.where((days.year == y) & (days.month == mo))[0]
                if len(cands):
                    eff_dates.append(int(cands[0]))
        eff_dates = [t for t in eff_dates if t > 30]
        tprev = 0
        rb = np.zeros((T, N))
        for te in eff_dates:
            members[tprev:te, list(cur)] = True
            t_ann = te - 15
            t_cut = t_ann - 5
            alive = self.alive[t_cut] & self.alive[min(te, T - 1)]
            ranks = pd.Series(np.where(alive, mcap[t_cut], -np.inf)).rank(ascending=False).to_numpy()
            buf = max(1, size // 6)
            new = set(cur)
            for i in list(new):
                if not alive[i] or ranks[i] > size + buf:
                    new.discard(i)
            for i in np.argsort(ranks):
                if len(new) >= size:
                    break
                if alive[i] and i not in new and ranks[i] <= size - buf:
                    new.add(int(i))
            for i in np.argsort(ranks):
                if len(new) >= size:
                    break
                if alive[i] and i not in new:
                    new.add(int(i))
            adds, dels = new - cur, cur - new
            ann_ts = _local_ts(days[t_ann], "17:30", self.tz)
            for i, et in [(i, "add") for i in adds] + [(i, "delete") for i in dels]:
                events.append(dict(index_id=self.index_id, market=self.m, symbol=self.symbols[i], event_type=et,
                                   announce_ts=ann_ts, receipt_ts=ann_ts + pd.Timedelta(minutes=5),
                                   effective_date=days[te].date(), old_weight=np.nan, new_weight=np.nan,
                                   source="synthetic"))
                sgn = 1 if et == "add" else -1
                ramp_days = np.arange(t_ann + 1, te)  # pressure builds through the trade day te-1
                if len(ramp_days) and self.alive[te - 1, i]:
                    amp = sgn * w.rebalance_pressure_bps / 1e4
                    rb[ramp_days, i] += amp * np.linspace(1 / len(ramp_days), 1.0, len(ramp_days))
                    for k in range(1, w.rebalance_reversal_days + 1):
                        if te - 1 + k < T:
                            rb[te - 1 + k, i] += amp * (1 - k / w.rebalance_reversal_days)
            cur, tprev = new, te
        members[tprev:, list(cur)] = True
        members &= self.alive
        self.members, self.index_events = members, events
        self.rb = rb
        self.reb_trade_day = np.zeros((T, N), bool)
        for ev in events:
            te = int(np.where(days == pd.Timestamp(ev["effective_date"]))[0][0])
            i = int(np.where(self.symbols == ev["symbol"])[0][0])
            self.reb_trade_day[te - 1, i] = True

    # ------------------------------------------------------------- assemble
    def observed(self):
        rng, T, N = self.rng, self.T, self.N
        rb = self.rb
        lc = self.eff["close"] + self.res["close"] + rb
        lo = self.eff["open"] + self.res["open"] + np.vstack([np.zeros((1, N)), rb[:-1]])
        ll = self.eff["lcp"] + self.res["lcp"] + np.vstack([np.zeros((1, N)), rb[:-1]]) * 0.5 + rb * 0.5
        sf = self.split_factor
        close = np.exp(lc) / sf
        open_ = np.exp(lo) / sf
        lcp = np.exp(ll) / sf
        table = self.ad._get("tick_size.table", self.days[-1].date())
        limits_pct = self.ad._get("price_limits.daily_pct", self.days[-1].date())
        open_, lcp, close = (_round_ticks(x, table) for x in (open_, lcp, close))
        if limits_pct:  # SET ceiling/floor relative to previous close (split-adjusted)
            prev = np.vstack([close[:1], close[:-1]]) * np.vstack([sf[:1], sf[:-1]]) / sf
            lo_lim, hi_lim = prev * (1 - limits_pct / 100), prev * (1 + limits_pct / 100)
            hi_g = _tick_of(hi_lim, table)
            lo_g = _tick_of(lo_lim, table)
            hi_lim = np.floor(hi_lim / hi_g + 1e-9) * hi_g
            lo_lim = np.ceil(lo_lim / lo_g - 1e-9) * lo_g
            open_, lcp, close = (np.round(np.clip(x, lo_lim, hi_lim), 6) for x in (open_, lcp, close))
        # no-auction days: close is last trade (SET/SGX) or reference price (HKEX non-CAS)
        close = np.where(self.no_auction, lcp, close)
        noise = np.abs(rng.normal(0, 0.3, (T, N))) * self.idio
        high = np.maximum.reduce([open_, lcp, close]) * np.exp(noise)
        low = np.minimum.reduce([open_, lcp, close]) * np.exp(-np.abs(rng.normal(0, 0.3, (T, N))) * self.idio)
        high, low = _round_ticks(high, table), _round_ticks(low, table)
        high = np.fmax(high, np.fmax(np.fmax(open_, lcp), close))
        low = np.fmin(low, np.fmin(np.fmin(open_, lcp), close))
        # volumes
        price_ref = close
        adv_sh = self.adv_turn[None, :] / np.where(price_ref > 0, price_ref, np.nan)
        vol = adv_sh * np.exp(rng.normal(0, 0.4, (T, N))) * (1 + 2 * np.abs(self.r_eff) / self.idio[None, :] / 3)
        cash = self.mp.close_auction_share * np.exp(rng.normal(0, 0.35, (T, N)))
        cash = cash * (1 + 0.5 * np.abs(self.temp_pressure) / np.maximum(self.sigma_p, 1e-6))
        cash = np.where(self.reb_trade_day, cash * 6, cash)
        cav = np.where(self.no_auction, 0.0, vol * np.minimum(cash, 0.6))
        oav = vol * 0.04 * np.exp(rng.normal(0, 0.4, (T, N)))
        if self.m == "SGX":
            tac = vol * 0.006 * np.exp(rng.normal(0, 0.8, (T, N)))
            tac = np.where((rng.random((T, N)) < 0.4) | self.no_auction, 0.0, tac)
        else:
            tac = np.full((T, N), np.nan)
        # spreads at pre-close
        tick = _tick_of(lcp, table)
        rel = np.maximum(tick / lcp, 0.0006 + 0.004 * (1 - self.liq_pct[None, :]) ** 2)
        half = np.maximum(tick, _round_ticks(lcp * rel / 2, table))
        bid, ask = lcp - half, lcp + half
        self.obs = dict(open=open_, high=high, low=low, close=close, lcp=lcp, volume=vol, cav=cav, oav=oav,
                        tac=tac, bid=bid, ask=ask)

    def suspensions(self):
        rng, T, N = self.rng, self.T, self.N
        susp = np.zeros((T, N), bool)
        for i in rng.choice(N, size=max(1, int(0.08 * N)), replace=False):
            for _ in range(rng.integers(1, 4)):
                t0 = int(rng.integers(self.list_idx[i], max(self.list_idx[i] + 1, self.delist_idx[i] - 5)))
                susp[t0:t0 + int(rng.integers(1, 15)), i] = True
        for i in np.where(self.delisted)[0]:
            susp[max(self.list_idx[i], self.delist_idx[i] - 8):self.delist_idx[i] + 1, i] = True
        self.susp = susp & self.alive

    # ------------------------------------------------------------- frames
    def frames(self):
        rng, T, N, days, ad = self.rng, self.T, self.N, self.days, self.ad
        o = self.obs
        rows_t, rows_i = np.where(self.alive)
        win = {}
        for t, d in enumerate(days):
            hd = self.cal.is_halfday(d)
            cw = ad.close_auction_window(d.date(), hd)
            win[t] = cw
        rs = np.array([win[t].random_start.value for t in range(T)])
        re_ = np.array([win[t].random_end.value for t in range(T)])
        st = np.array([win[t].auction_start.value for t in range(T)])
        auc_ts = rs[:, None] + (rng.random((T, N)) * (re_ - rs)[:, None]).astype(np.int64)
        lcp_ts = st[:, None] - (rng.random((T, N)) * 120e9 * (1.2 - self.liq_pct[None, :])).astype(np.int64)
        rcpt = auc_ts + (rng.uniform(0.05, 1.5, (T, N)) * 1e9).astype(np.int64)
        eod = np.array([_local_ts(d, "18:00", self.tz).value for d in days])
        eod = eod[:, None] + (rng.uniform(0, 1800, (T, N)) * 1e9).astype(np.int64)
        susp = self.susp
        method = np.where(self.no_auction, "last_trade", "auction").astype(object)
        if self.m == "HKEX":
            method = np.where(~self.cas_eligible[None, :] & np.ones((T, 1), bool), "reference_price", method)
        f = lambda a: a[rows_t, rows_i]
        sp = f(susp)
        nan_if = lambda a: np.where(sp, np.nan, f(a))
        auc_px = np.where(f(self.no_auction) | sp, np.nan, f(o["close"]))
        df = pd.DataFrame({
            "market": self.m, "symbol": self.symbols[rows_i], "date": days[rows_t].date,
            "open": nan_if(o["open"]), "high": nan_if(o["high"]), "low": nan_if(o["low"]),
            "close": nan_if(o["close"]),
            "volume": np.where(sp, 0.0, np.round(f(o["volume"]))),
            "open_auction_volume": np.where(sp, 0.0, np.round(f(o["oav"]))),
            "last_continuous_price": nan_if(o["lcp"]),
            "last_continuous_ts": pd.to_datetime(np.where(sp, np.datetime64("NaT", "ns"), f(lcp_ts).astype("datetime64[ns]")), utc=True),
            "preclose_bid": nan_if(o["bid"]), "preclose_ask": nan_if(o["ask"]),
            "close_auction_price": auc_px,
            "close_auction_volume": np.where(np.isnan(auc_px), np.where(sp, np.nan, 0.0), np.round(f(o["cav"]))),
            "close_auction_ts": pd.to_datetime(np.where(np.isnan(auc_px), np.datetime64("NaT", "ns"), f(auc_ts).astype("datetime64[ns]")), utc=True),
            "close_auction_receipt_ts": pd.to_datetime(np.where(np.isnan(auc_px), np.datetime64("NaT", "ns"), f(rcpt).astype("datetime64[ns]")), utc=True),
            "close_method": np.where(sp, "none", f(method)),
            "tac_volume": np.where(sp, np.nan, np.round(f(o["tac"]))) if self.m == "SGX" else np.nan,
            "suspended": sp,
            "is_halfday": [self.cal.is_halfday(d) for d in days[rows_t]],
            "receipt_ts": pd.to_datetime(f(eod), utc=True),
        })
        df["close_method"] = df["close_method"].astype(str)
        df["turnover"] = (df["volume"] * df["close"].fillna(0)).round(2)
        df["tac_turnover"] = df["tac_volume"] * df["close"]
        # missing rows and missing LCP (data-quality edge cases)
        drop = rng.random(len(df)) < self.cfg.missing_row_frac
        df = df[~drop]
        miss = (rng.random(len(df)) < self.cfg.missing_lcp_frac) & ~df["suspended"].to_numpy()
        df.loc[miss, "last_continuous_price"] = np.nan
        df.loc[miss, "last_continuous_ts"] = pd.NaT
        self.daily = df.reset_index(drop=True)
        self._win = win

    def master(self):
        days = self.days
        return pd.DataFrame({
            "market": self.m, "symbol": self.symbols, "name": [f"Synthetic {s}" for s in self.symbols],
            "sector": [SECTORS[k] for k in self.sector],
            "listing_date": days[self.list_idx].date,
            "delisting_date": [days[d].date() if dl else None for d, dl in zip(self.delist_idx, self.delisted)],
            "board_lot": self.lot.astype(int), "shares_outstanding": self.shares.round(0),
            "cas_eligible": self.cas_eligible if self.m == "HKEX" else None,
            "is_synthetic": True,
        })

    def corp_actions(self):
        rows = []
        for r in self.ca_rows:
            t, i = r["t"], r["i"]
            prev_close = self.obs["close"][t - 1, i]
            rows.append(dict(market=self.m, symbol=self.symbols[i], action_type=r["action_type"],
                             announce_ts=_local_ts(self.days[max(0, r["ann_t"])], "18:00", self.tz),
                             ex_date=self.days[t].date(), ratio=r["ratio"],
                             cash_amount=round(prev_close * r["yield_"], 4) if r["action_type"] == "cash_dividend" else np.nan))
        return pd.DataFrame(rows, columns=["market", "symbol", "action_type", "announce_ts", "ex_date", "ratio", "cash_amount"])

    def borrow(self):
        rng, T, N, days = self.rng, self.T, self.N, self.days
        mcap_rank = pd.Series(self.shares * self.p0).rank(pct=True, ascending=False).to_numpy()
        top = mcap_rank <= self.mp.shortable_top_frac
        month_id = days.year * 12 + days.month
        months = np.unique(month_id)
        state = rng.random(N) < np.where(top, 0.9, 0.12)
        fee_base = np.where(top, rng.uniform(25, 80, N), rng.uniform(200, 800, N))
        sh = np.zeros((T, N), bool)
        fee = np.zeros((T, N))
        for mid in months:
            flip = rng.random(N) < 0.03
            state = np.where(flip, ~state, state)
            idx = month_id == mid
            sh[idx] = state
            fee[idx] = fee_base * np.exp(rng.normal(0, 0.2, N))
        rows_t, rows_i = np.where(self.alive)
        avail = 2 * self.adv_turn[rows_i] / np.maximum(self.obs["close"][rows_t, rows_i], 1e-9)
        rec = np.array([_local_ts(d, "08:00", self.tz).value for d in days])
        return pd.DataFrame({
            "market": self.m, "symbol": self.symbols[rows_i], "date": days[rows_t].date,
            "shortable": sh[rows_t, rows_i] & ~self.susp[rows_t, rows_i],
            "borrow_fee_bps": fee[rows_t, rows_i].round(1),
            "available_qty": np.where(sh[rows_t, rows_i], np.round(avail), 0.0),
            "receipt_ts": pd.to_datetime(rec[rows_t], utc=True),
        })

    def index_tables(self):
        T, days = self.T, self.days
        mem = self.members
        rows = []
        for i in range(self.N):
            col = mem[:, i]
            if not col.any():
                continue
            d = np.diff(np.r_[0, col.astype(int), 0])
            starts, ends = np.where(d == 1)[0], np.where(d == -1)[0] - 1
            for a, b in zip(starts, ends):
                rows.append(dict(index_id=self.index_id, market=self.m, symbol=self.symbols[i],
                                 effective_from=days[a].date(), effective_to=None if b == T - 1 else days[b].date()))
        membership = pd.DataFrame(rows)
        # cap-weighted index from observed (split-adjusted) prices
        o = self.obs
        sf = self.split_factor
        adj = lambda x: x * sf
        close_a, open_a, lcp_a = adj(o["close"]), adj(o["open"]), adj(o["lcp"])
        susp = self.susp
        close_f = pd.DataFrame(np.where(susp, np.nan, close_a)).ffill().to_numpy()
        prev = np.vstack([close_f[:1], close_f[:-1]])
        mcap_prev = prev * self.shares[None, :]
        lvl = np.zeros(T)
        lvl_o = np.zeros(T)
        lvl_l = np.zeros(T)
        lvl[0] = lvl_o[0] = lvl_l[0] = 1000.0
        div = np.log1p(-self.div_yield_on_ex)  # total-return neutral: ignore div drop in index
        for t in range(1, T):
            mbr = mem[t] & ~susp[t] & np.isfinite(close_a[t]) & np.isfinite(prev[t])
            if not mbr.any():
                lvl[t] = lvl_o[t] = lvl_l[t] = lvl[t - 1]
                continue
            wgt = mcap_prev[t, mbr] / mcap_prev[t, mbr].sum()
            ratio = lambda x: np.nan_to_num(x[t, mbr] / prev[t, mbr] / np.exp(div[t, mbr]), nan=1.0)
            lvl[t] = lvl[t - 1] * (wgt * ratio(close_a)).sum()
            lvl_o[t] = lvl[t - 1] * (wgt * ratio(open_a)).sum()
            lvl_l[t] = lvl[t - 1] * (wgt * ratio(lcp_a)).sum()
        rec = pd.to_datetime([_local_ts(d, "18:00", self.tz) for d in days])
        levels = pd.DataFrame({"index_id": self.index_id, "market": self.m, "date": days.date,
                               "open": lvl_o.round(4), "close": lvl.round(4), "level_at_lcp": lvl_l.round(4),
                               "receipt_ts": rec})
        # futures
        rng = self.rng
        basis = np.zeros(T)
        for t in range(1, T):
            basis[t] = 0.8 * basis[t - 1] + rng.normal(0, 0.0004)
        gap = np.log(lvl / lvl_l)
        w = self.w
        exp_rev = (w.overnight_reversal + w.nextday_reversal) * (1 - w.permanent_frac)
        # post-auction futures price prices in a fraction of the expected reversal of the auction gap
        post = lvl * np.exp(basis - (w.futures_price_in_frac * exp_rev) * gap)
        if w.name == "null":
            post = lvl * np.exp(basis)
        spec = self.ad.futures_spec(days[-1].date())
        pts = [self._win[t].random_end + pd.Timedelta(minutes=2) for t in range(T)]
        fut = pd.DataFrame({
            "market": self.m, "contract": f"{self.index_id}_F1", "date": days.date,
            "open": (lvl_o * np.exp(basis)).round(2), "close": (lvl * np.exp(basis)).round(2),
            "price_at_cash_lcp": (lvl_l * np.exp(basis)).round(2), "price_post_auction": post.round(2),
            "post_auction_ts": pd.to_datetime(pts), "multiplier": float(spec.get("multiplier", 10)),
            "receipt_ts": pd.to_datetime(pts) + pd.Timedelta(seconds=1),
        })
        ev = pd.DataFrame(self.index_events, columns=["index_id", "market", "symbol", "event_type", "announce_ts",
                                                      "receipt_ts", "effective_date", "old_weight", "new_weight", "source"])
        return membership, ev, levels, fut

    def indicative(self):
        cfg, rng = self.cfg, self.rng
        k = min(cfg.n_indicative_stocks, self.N)
        sel = set(self.symbols[np.argsort(-self.liq_pct)[:k]])
        d = self.daily[self.daily["symbol"].isin(sel) & (self.daily["close_method"] == "auction")
                       & self.daily["last_continuous_price"].notna()]
        if d.empty:
            return pd.DataFrame(columns=["market", "symbol", "date", "snapshot_ts", "receipt_ts", "iep", "iev", "imbalance_qty"])
        dates = pd.to_datetime(d["date"])
        t_index = self.days.get_indexer(dates)
        start = np.array([self._win[t].auction_start.value for t in t_index])
        end_rand = np.array([self._win[t].random_end.value for t in t_index])
        auc = d["close_auction_ts"].astype("int64").to_numpy()
        step = int(cfg.indicative_interval_s * 1e9)
        nmax = int(((end_rand - start).max()) // step) + 1
        offs = np.arange(nmax) * step
        snap = start[:, None] + offs[None, :]
        valid = snap < auc[:, None]
        frac = np.clip((snap - start[:, None]) / np.maximum(auc - start, 1)[:, None], 0, 1)
        lcp = d["last_continuous_price"].to_numpy()[:, None]
        g = np.log(d["close_auction_price"].to_numpy() / d["last_continuous_price"].to_numpy())[:, None]
        sym_idx = pd.Index(self.symbols).get_indexer(d["symbol"])
        sp = self.sigma_p[sym_idx][:, None]
        wgt = 0.35 + 0.65 * frac
        noise = rng.normal(0, 1, snap.shape) * sp * (1.05 - frac) * 0.8
        iep_gap = wgt * g + noise
        table = self.ad._get("tick_size.table", self.days[-1].date())
        iep = _round_ticks(lcp * np.exp(iep_gap), table)
        fv = d["close_auction_volume"].to_numpy()[:, None]
        iev = np.round(fv * (0.3 + 0.7 * wgt) * np.exp(rng.normal(0, 0.15, snap.shape)))
        imb = np.round((g - iep_gap) / np.maximum(sp, 1e-6) * fv * 0.2 + rng.normal(0, 0.1, snap.shape) * fv)
        lat = (np.exp(rng.normal(np.log(0.3), 0.6, snap.shape)) * 1e9).astype(np.int64)
        r, c = np.where(valid)
        out = pd.DataFrame({
            "market": self.m, "symbol": d["symbol"].to_numpy()[r], "date": d["date"].to_numpy()[r],
            "snapshot_ts": pd.to_datetime(snap[r, c], utc=True),
            "receipt_ts": pd.to_datetime(snap[r, c] + lat[r, c], utc=True),
            "iep": iep[r, c], "iev": iev[r, c], "imbalance_qty": imb[r, c],
        })
        return out

    def intraday_sample(self, n_days: int = 3, n_syms: int = 2):
        """Tiny tick-level sample (trades + quotes) to exercise those schemas."""
        rng = self.rng
        d = self.daily[(self.daily["close_method"] == "auction") & self.daily["last_continuous_price"].notna()]
        d = d[d["symbol"].isin(d["symbol"].unique()[:n_syms])].groupby("symbol").head(n_days)
        trades, quotes = [], []
        for _, row in d.iterrows():
            t_end = row["last_continuous_ts"]
            ts = t_end - pd.to_timedelta(np.sort(rng.uniform(0, 3600, 40))[::-1], unit="s")
            px = row["last_continuous_price"] * np.exp(np.cumsum(rng.normal(0, 0.0005, 40))[::-1] - 0)
            px[-1] = row["last_continuous_price"]
            for t_, p_ in zip(ts, px):
                trades.append(dict(market=self.m, symbol=row["symbol"], date=row["date"], ts=t_,
                                   receipt_ts=t_ + pd.Timedelta(milliseconds=200), price=round(float(p_), 4),
                                   size=float(rng.integers(1, 50) * 100), phase="continuous"))
                quotes.append(dict(market=self.m, symbol=row["symbol"], date=row["date"], ts=t_ - pd.Timedelta(milliseconds=5),
                                   receipt_ts=t_ + pd.Timedelta(milliseconds=150), bid=round(float(p_) * 0.999, 4),
                                   ask=round(float(p_) * 1.001, 4), bid_size=1000.0, ask_size=1000.0))
            trades.append(dict(market=self.m, symbol=row["symbol"], date=row["date"], ts=row["close_auction_ts"],
                               receipt_ts=row["close_auction_receipt_ts"], price=row["close_auction_price"],
                               size=row["close_auction_volume"], phase="close_auction"))
        return pd.DataFrame(trades), pd.DataFrame(quotes)


def generate(cfg: SyntheticConfig | None = None) -> dict[str, pd.DataFrame]:
    cfg = cfg or SyntheticConfig()
    rng = np.random.default_rng(cfg.seed)
    out: dict[str, list] = {k: [] for k in ("security_master", "daily_bars", "auction_indicative", "corporate_actions",
                                            "borrow", "index_membership", "index_events", "index_levels",
                                            "futures_daily", "intraday_trades", "intraday_quotes")}
    for m, mp in cfg.markets.items():
        sim = _MarketSim(m, mp, cfg, rng)
        sim.securities()
        sim.prices()
        sim.index_and_rebalance()
        sim.observed()
        sim.suspensions()
        sim.frames()
        out["security_master"].append(sim.master())
        out["daily_bars"].append(sim.daily)
        out["corporate_actions"].append(sim.corp_actions())
        out["borrow"].append(sim.borrow())
        mem, ev, lv, fu = sim.index_tables()
        out["index_membership"].append(mem)
        out["index_events"].append(ev)
        out["index_levels"].append(lv)
        out["futures_daily"].append(fu)
        out["auction_indicative"].append(sim.indicative())
        tr, qu = sim.intraday_sample()
        out["intraday_trades"].append(tr)
        out["intraday_quotes"].append(qu)
    from .schemas import ALL_SCHEMAS
    res = {}
    for k, v in out.items():
        df = pd.concat([x for x in v if len(x)] or v[:1], ignore_index=True)
        for c in ALL_SCHEMAS[k].columns:
            if c.name not in df:
                continue
            if c.dtype == "ts":
                df[c.name] = pd.to_datetime(df[c.name], utc=True)
            elif c.dtype == "date":
                df[c.name] = pd.to_datetime(df[c.name])
        res[k] = df
    return res


def planted_truth(cfg: SyntheticConfig) -> dict:
    """The effect sizes the generator planted - used to check recovery, never as 'findings'."""
    w = cfg.world
    temp = 1 - w.permanent_frac
    return {
        "world": w.name,
        "frac_gap_reverting_by_next_open": temp * w.overnight_reversal,
        "frac_gap_reverting_next_open_to_next_close": temp * w.nextday_reversal,
        "frac_gap_reverting_by_next_close": temp * (w.overnight_reversal + w.nextday_reversal),
        "rebalance_pressure_bps": w.rebalance_pressure_bps,
        "futures_price_in_frac": w.futures_price_in_frac,
        "preclose_momentum_effect": 0.0,
    }
