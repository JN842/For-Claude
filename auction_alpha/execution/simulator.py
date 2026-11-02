"""Execution simulator.

Takes order *intents* produced by strategies and returns fills, rejections and
trade records under one :class:`ExecutionScenario`.

Causality is enforced, not assumed:

* an intent whose ``submit_ts`` precedes its ``signal_ts`` is a code bug and
  raises :class:`LeakageError`;
* closing-auction orders must be in the book before the random-close window
  opens (scenario ``order_deadline``) - the realised close time is unknown;
* TAC orders require the closing price to be known (signal after the auction
  result was received) and TAC to be in force on that date;
* sizing uses only ex-ante prices; realised volume only caps the fill.

Every refused or reduced order is logged with a reason code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..features.engineering import LeakageError
from ..rules.adapters import ExchangeAdapter, get_adapter
from .auction import AuctionStateMachine
from .scenarios import ExecutionScenario

INTENT_COLUMNS = [
    "trade_id", "strategy", "market", "symbol", "signal_date", "signal_ts", "submit_ts", "side",
    "target_notional", "ref_price", "entry_session", "entry_date", "hold_sessions", "exit_session",
    "hedge", "beta", "vol_20", "vol_shares_20", "spread_rel", "signal",
]
SESSIONS = ("close_auction", "open_auction", "open_continuous", "tac")


@dataclass
class MarketData:
    market: str
    bars: pd.DataFrame
    borrow: pd.DataFrame | None = None
    futures: pd.DataFrame | None = None
    corporate_actions: pd.DataFrame | None = None

    def __post_init__(self):
        b = self.bars[self.bars["market"] == self.market].copy()
        b["date"] = pd.to_datetime(b["date"]).astype("datetime64[ns]")
        b = b.sort_values(["symbol", "date"]).reset_index(drop=True)
        self.bars = b
        self.idx = {k: i for i, k in enumerate(zip(b["symbol"].to_numpy(), b["date"].to_numpy()))}
        self.sym_dates: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for sym, g in b.groupby("symbol", sort=False):
            self.sym_dates[sym] = (g["date"].to_numpy().astype("datetime64[ns]"), g.index.to_numpy())
        self.cols = {c: (b[c].astype(object).to_numpy() if b[c].dtype.kind not in "fiubmM" else b[c].to_numpy())
                     for c in b.columns}
        # previous raw close (for price limits), past information only
        self.cols["prev_close"] = b.groupby("symbol")["close"].transform(lambda s: s.ffill().shift(1)).to_numpy()
        self.last_close = b.groupby("symbol")["close"].transform(lambda s: s.ffill()).to_numpy()
        self.dates = pd.DatetimeIndex(sorted(b["date"].unique()))
        if self.borrow is not None and not self.borrow.empty:
            br = self.borrow[self.borrow["market"] == self.market].copy()
            br["date"] = pd.to_datetime(br["date"]).astype("datetime64[ns]")
            self.borrow_idx = {k: r for k, r in zip(zip(br["symbol"].to_numpy(), br["date"].to_numpy()),
                                                    br[["shortable", "borrow_fee_bps", "available_qty", "receipt_ts"]].itertuples(index=False))}
        else:
            self.borrow_idx = {}
        if self.futures is not None and not self.futures.empty:
            f = self.futures[self.futures["market"] == self.market].copy()
            f["date"] = pd.to_datetime(f["date"])
            self.fut = f.set_index("date").sort_index()
        else:
            self.fut = None

    def row(self, sym, date):
        return self.idx.get((sym, np.datetime64(pd.Timestamp(date), "ns")))

    def rows_from(self, sym, date):
        """Row indices for this symbol on/after date (chronological)."""
        if sym not in self.sym_dates:
            return np.array([], dtype=int)
        ds, ix = self.sym_dates[sym]
        k = np.searchsorted(ds, np.datetime64(pd.Timestamp(date), "ns"), side="left")
        return ix[k:]

    def session_date_offset(self, date, n: int):
        """Market-calendar date n sessions after date."""
        k = self.dates.searchsorted(pd.Timestamp(date))
        k2 = k + n
        return self.dates[k2] if 0 <= k2 < len(self.dates) else None

    def next_session_date(self, date):
        k = self.dates.searchsorted(pd.Timestamp(date), side="right")
        return self.dates[k] if k < len(self.dates) else None


@dataclass
class SimResult:
    scenario: str
    fills: pd.DataFrame
    rejections: pd.DataFrame
    trades: pd.DataFrame
    rule_usage: dict = field(default_factory=dict)


class ExecutionSimulator:
    def __init__(self, md: MarketData, scenario: ExecutionScenario, adapter: ExchangeAdapter | None = None,
                 *, allow_fractional_hedge: bool = False):
        self.md, self.sc = md, scenario
        self.ad = adapter or get_adapter(md.market)
        self.allow_fractional_hedge = allow_fractional_hedge
        self._sm_cache: dict = {}

    # ----------------------------------------------------------- helpers
    def _sm(self, date, i):
        c = self.md.cols
        key = (pd.Timestamp(date), c["symbol"][i])
        rc = c["close_auction_ts"][i]
        rc = None if pd.isna(rc) else pd.Timestamp(rc)
        return AuctionStateMachine(self.ad, pd.Timestamp(date).date(), bool(c["is_halfday"][i]), realized_close=rc)

    def _session_px_vol(self, i, session, side, half_spread):
        c = self.md.cols
        if c["suspended"][i]:
            return None, 0.0, "suspended"
        if session == "close_auction":
            if c["close_method"][i] != "auction" or not np.isfinite(c["close_auction_price"][i]):
                reason = "not_cas_eligible" if c["close_method"][i] == "reference_price" else "no_auction_match"
                return None, 0.0, reason
            return float(c["close_auction_price"][i]), float(c["close_auction_volume"][i]) * self.sc.auction_participation, ""
        if session == "open_auction":
            px, v = c["open"][i], c["open_auction_volume"][i]
            if not np.isfinite(px):
                return None, 0.0, "no_open_price"
            return float(px), float(v) * self.sc.open_participation, ""
        if session == "open_continuous":
            px = c["open"][i]
            if not np.isfinite(px):
                return None, 0.0, "no_open_price"
            return float(px) * (1 + side * half_spread), float(c["volume"][i]) * self.sc.continuous_participation, ""
        if session == "tac":
            if not self.ad.tac_available(pd.Timestamp(c["date"][i]).date()):
                return None, 0.0, "tac_not_available"
            if c["close_method"][i] != "auction" or not np.isfinite(c["close"][i]):
                return None, 0.0, "tac_requires_closing_price"
            v = c.get("tac_volume", np.full(len(c["symbol"]), np.nan))[i]
            if not np.isfinite(v) or v <= 0:
                return None, 0.0, "no_tac_liquidity"
            return float(c["close"][i]), float(v) * self.sc.tac_participation, ""
        raise ValueError(session)

    def _impact_px(self, base, side, qty, vol20, adv_sh, session):
        if session == "tac":
            return base  # TAC trades at the closing price by construction
        sig = vol20 if np.isfinite(vol20) and vol20 > 0 else 0.03
        adv = adv_sh if np.isfinite(adv_sh) and adv_sh > 0 else max(qty * 20, 1.0)
        imp = self.sc.impact_coef * sig * math.sqrt(qty / adv) + self.sc.extra_slippage_bps / 1e4
        return base * (1 + side * imp)

    def _limit_locked(self, i, side, px):
        c = self.md.cols
        lim = self.ad.price_limits(c["prev_close"][i], pd.Timestamp(c["date"][i]).date())
        if lim is None:
            return False
        lo, hi = lim
        tick = self.ad.tick_size(px, pd.Timestamp(c["date"][i]).date())
        return (side > 0 and px >= hi - tick / 2) or (side < 0 and px <= lo + tick / 2)

    def _deadline_ok(self, i, session, submit_ts, date):
        if session == "close_auction":
            sm = self._sm(date, i)
            eff = max(submit_ts, sm.window.auction_start)
            return sm.can_submit_auction_order(eff, self.sc.order_deadline)
        if session == "tac":
            sm = self._sm(date, i)
            ok, why = sm.can_submit_tac(submit_ts)
            if ok and not pd.isna(self.md.cols["close_auction_ts"][i]) and submit_ts < pd.Timestamp(self.md.cols["close_auction_ts"][i]):
                return False, "tac_order_before_close_known"
            return ok, why
        if session in ("open_auction",):
            return (submit_ts <= self.ad.open_auction_cutoff(pd.Timestamp(date).date())), "submitted_after_open_auction_cutoff"
        if session == "open_continuous":
            return (submit_ts <= self.ad.open_time(pd.Timestamp(date).date()) + pd.Timedelta(minutes=5)), "submitted_too_late_for_open"
        return True, ""

    def _fill_ts(self, i, session, date):
        c = self.md.cols
        d = pd.Timestamp(date).date()
        if session == "close_auction":
            return pd.Timestamp(c["close_auction_ts"][i])
        if session == "tac":
            return self.ad.close_auction_window(d, bool(c["is_halfday"][i])).tac_start
        return self.ad.open_time(d)

    def _fees(self, date, notional):
        return self.ad.fee_schedule(pd.Timestamp(date).date()).cost(notional)

    # -------------------------------------------------------------- main
    def run(self, intents: pd.DataFrame) -> SimResult:
        fills, rejects, trades = [], [], []
        md, sc, c = self.md, self.sc, self.md.cols
        if intents is None or intents.empty:
            empty = pd.DataFrame()
            return SimResult(sc.name, empty, empty, empty, {})
        bad = intents["submit_ts"] < intents["signal_ts"]
        if bad.any():
            raise LeakageError(f"{int(bad.sum())} intents submitted before their signal was available")
        it_df = intents.copy()
        for col in it_df.columns:
            if it_df[col].dtype.kind not in "fiubM":
                it_df[col] = it_df[col].astype(object)
        for it in it_df.itertuples(index=False):
            rec = self._one(it, fills, rejects)
            trades.append(rec)
        f = pd.DataFrame(fills)
        r = pd.DataFrame(rejects, columns=["trade_id", "strategy", "market", "symbol", "date", "leg", "reason",
                                           "requested_qty", "filled_qty", "scenario"])
        t = pd.DataFrame(trades)
        return SimResult(sc.name, f, r, t, self.ad.usage_summary())

    def _reject(self, rejects, it, date, leg, reason, req, filled=0.0):
        rejects.append(dict(trade_id=it.trade_id, strategy=it.strategy, market=it.market, symbol=it.symbol,
                            date=pd.Timestamp(date) if date is not None else None, leg=leg, reason=reason,
                            requested_qty=float(req), filled_qty=float(filled), scenario=self.sc.name))

    def _one(self, it, fills, rejects) -> dict:
        md, sc, c = self.md, self.sc, self.md.cols
        rec = dict(trade_id=it.trade_id, strategy=it.strategy, market=it.market, symbol=it.symbol, side=int(it.side),
                   signal_date=pd.Timestamp(it.signal_date), signal=float(it.signal), status="rejected",
                   intended_qty=0.0, entry_qty=0.0, entry_date=pd.NaT, exit_date=pd.NaT, scenario=sc.name,
                   entry_session=it.entry_session, exit_session=it.exit_session)
        date = pd.Timestamp(it.entry_date) if pd.notna(it.entry_date) else None
        i = md.row(it.symbol, date) if date is not None else None
        if i is None:
            self._reject(rejects, it, date, "entry", "entry_row_missing", 0)
            return rec
        lot = self.ad.board_lot(date.date(), c["board_lot"][i] if "board_lot" in c else None)
        ref = float(it.ref_price) if np.isfinite(it.ref_price) and it.ref_price > 0 else np.nan
        if not np.isfinite(ref):
            self._reject(rejects, it, date, "entry", "no_ex_ante_reference_price", 0)
            return rec
        qty = math.floor(it.target_notional / ref / lot) * lot
        rec["intended_qty"] = float(qty)
        if qty <= 0:
            self._reject(rejects, it, date, "entry", "below_board_lot", 0)
            return rec
        ok, why = self._deadline_ok(i, it.entry_session, pd.Timestamp(it.submit_ts), date)
        if not ok:
            self._reject(rejects, it, date, "entry", why, qty)
            return rec
        side = int(it.side)
        borrow_fee = 0.0
        if side < 0:
            b = md.borrow_idx.get((it.symbol, np.datetime64(date, "ns")))
            if b is None:
                self._reject(rejects, it, date, "entry", "no_borrow_data", qty)
                return rec
            if pd.Timestamp(b.receipt_ts) > pd.Timestamp(it.submit_ts):
                self._reject(rejects, it, date, "entry", "borrow_info_not_yet_available", qty)
                return rec
            if not bool(b.shortable):
                self._reject(rejects, it, date, "entry", "not_shortable", qty)
                return rec
            if it.entry_session in ("close_auction", "open_auction"):
                regime = self.ad.short_regime(date.date())
                allowed = regime.get("allowed_in_closing_auction", "unknown")
                if allowed == "unknown" and sc.short_in_auction_unknown == "reject":
                    self._reject(rejects, it, date, "entry", "short_in_auction_rule_unverified", qty)
                    return rec
                if allowed == "at_auction_limit_ge_reference_price" and it.entry_session == "close_auction":
                    if c["close_auction_price"][i] < c["last_continuous_price"][i]:
                        self._reject(rejects, it, date, "entry", "hk_cas_short_below_reference_price", qty)
                        return rec
            avail = float(b.available_qty) if np.isfinite(b.available_qty) else 0.0
            if avail < qty:
                new = math.floor(avail / lot) * lot
                self._reject(rejects, it, date, "entry", "borrow_quantity_limited", qty, new)
                qty = new
                if qty <= 0:
                    return rec
            borrow_fee = float(b.borrow_fee_bps) * sc.borrow_fee_mult
        half = max(float(it.spread_rel) / 2 if np.isfinite(it.spread_rel) else 0.002, 0.0) * sc.spread_mult
        px, vol_av, why = self._session_px_vol(i, it.entry_session, side, half)
        if px is None:
            self._reject(rejects, it, date, "entry", why, qty)
            return rec
        fq = min(qty * sc.fill_ratio, vol_av)
        if it.entry_session == "close_auction" and self._limit_locked(i, side, px):
            fq *= sc.limit_lock_fill
            if fq < qty:
                self._reject(rejects, it, date, "entry", "price_limit_locked", qty, math.floor(fq / lot) * lot)
        fq = math.floor(fq / lot) * lot
        if fq <= 0:
            self._reject(rejects, it, date, "entry", "unfilled_liquidity", qty)
            return rec
        if fq < qty:
            self._reject(rejects, it, date, "entry", "partial_fill", qty, fq)
        fpx = self._impact_px(px, side, fq, it.vol_20, it.vol_shares_20, it.entry_session)
        base_px = px if it.entry_session != "open_continuous" else float(c["open"][i])
        notional = fq * fpx
        ets = self._fill_ts(i, it.entry_session, date)
        fills.append(dict(trade_id=it.trade_id, leg="entry", instrument="stock", market=it.market, symbol=it.symbol,
                          date=date, ts=ets, side_qty=side * fq, price=fpx, base_price=base_px,
                          fees=self._fees(date, notional), slip_cost=side * fq * (fpx - base_px),
                          session=it.entry_session, trade_side=side, borrow_fee_bps=borrow_fee))
        rec.update(status="filled" if fq == qty else "partial", entry_qty=float(fq), entry_date=date)
        # ------------------------------------------------------- exit legs
        exit_target = md.session_date_offset(date, int(it.hold_sessions)) if int(it.hold_sessions) > 0 else date
        if exit_target is None:
            exit_target = md.dates[-1]
        last_exit_date, last_exit_session = self._exit(it, fq, side, lot, exit_target, fills, rejects)
        rec["exit_date"] = last_exit_date
        # ------------------------------------------------------- hedge legs
        if bool(it.hedge):
            self._hedge(it, side, notional, date, it.entry_session, last_exit_date, last_exit_session, fills, rejects)
        return rec

    def _exit(self, it, qty, side, lot, target, fills, rejects):
        md, sc, c = self.md, self.sc, self.md.cols
        remaining = qty
        xside = -side
        session = it.exit_session
        rows = md.rows_from(it.symbol, target)
        delays = 0
        last_i = None
        hit_delay_limit = False
        last_date, last_session = target, session
        for i in rows:
            d = pd.Timestamp(c["date"][i])
            if delays > sc.max_exit_delay_days:
                hit_delay_limit = True
                break
            last_i = i
            px, vol_av, why = self._session_px_vol(i, session, xside, 0.002 * sc.spread_mult)
            if px is None:
                self._reject(rejects, it, d, "exit", f"exit_delayed:{why}", remaining)
                delays += 1
                continue
            fq = math.floor(min(remaining, vol_av) / lot) * lot
            if fq <= 0:
                self._reject(rejects, it, d, "exit", "exit_delayed:liquidity", remaining)
                delays += 1
                continue
            fpx = self._impact_px(px, xside, fq, it.vol_20, it.vol_shares_20, session)
            fills.append(dict(trade_id=it.trade_id, leg="exit", instrument="stock", market=it.market, symbol=it.symbol,
                              date=d, ts=self._fill_ts(i, session, d), side_qty=xside * fq, price=fpx, base_price=px,
                              fees=self._fees(d, fq * fpx), slip_cost=xside * fq * (fpx - px), session=session,
                              trade_side=side, borrow_fee_bps=0.0))
            remaining -= fq
            last_date, last_session = d, session
            if remaining <= 0:
                return last_date, last_session
            self._reject(rejects, it, d, "exit", "exit_partial_carry_forward", remaining + fq, fq)
            delays += 1
        # Forced exit at the last known close with an adverse haircut (delisting, or no liquidity
        # within the allowed delay).  Uses the last observed row, never a future price.
        if last_i is None:
            before = [j for j in md.rows_from(it.symbol, pd.Timestamp("1900-01-01")) if pd.Timestamp(c["date"][j]) < target]
            last_i = before[-1] if before else None
        lc = float(md.last_close[last_i]) if last_i is not None and np.isfinite(md.last_close[last_i]) else 0.0
        d = pd.Timestamp(c["date"][last_i]) if last_i is not None else target
        hair = sc.delist_haircut
        fpx = lc * (1 + xside * hair)
        fills.append(dict(trade_id=it.trade_id, leg="exit", instrument="stock", market=it.market, symbol=it.symbol,
                          date=d, ts=pd.NaT, side_qty=xside * remaining, price=fpx, base_price=lc,
                          fees=self._fees(d, remaining * fpx), slip_cost=xside * remaining * (fpx - lc),
                          session="forced", trade_side=side, borrow_fee_bps=0.0))
        reason = "forced_exit_max_delay" if hit_delay_limit else "forced_exit_delisted_or_illiquid"
        self._reject(rejects, it, d, "exit", reason, remaining)
        return d, "forced"

    def _hedge(self, it, side, notional, d_in, s_in, d_out, s_out, fills, rejects):
        md, sc = self.md, self.sc
        if md.fut is None:
            self._reject(rejects, it, d_in, "hedge", "no_futures_data", 0)
            return
        spec = self.ad.futures_spec(d_in.date())
        mult = float(spec.get("multiplier", 1.0))
        cost_bps = float(spec.get("cost_bps", 1.0)) * sc.futures_cost_mult

        def px(d, s):
            if d not in md.fut.index:
                return None
            r = md.fut.loc[d]
            col = "price_post_auction" if s in ("close_auction", "tac") else "open" if s.startswith("open") else "close"
            v = r[col]
            return float(v) if np.isfinite(v) else None

        p_in, p_out = px(d_in, s_in), px(d_out, s_out if s_out != "forced" else "close")
        if p_in is None or p_out is None:
            self._reject(rejects, it, d_in, "hedge", "futures_price_missing", 0)
            return
        beta = it.beta if np.isfinite(it.beta) else 1.0
        n = -side * beta * notional / (p_in * mult)
        n = n if self.allow_fractional_hedge else float(round(n))
        if n == 0:
            self._reject(rejects, it, d_in, "hedge", "hedge_below_one_contract", 0)
            return
        for leg, d, p, q in (("hedge_entry", d_in, p_in, n), ("hedge_exit", d_out, p_out, -n)):
            fills.append(dict(trade_id=it.trade_id, leg=leg, instrument="future", market=it.market,
                              symbol=f"{it.market}_FUT", date=d, ts=pd.NaT, side_qty=q, price=p, base_price=p,
                              fees=abs(q) * p * mult * cost_bps / 1e4, slip_cost=0.0, session=s_in if leg == "hedge_entry" else s_out,
                              trade_side=side, borrow_fee_bps=0.0, multiplier=mult))
