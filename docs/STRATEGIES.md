# Strategy Specifications

_Generated from the `StrategySpec` of each class in `auction_alpha/strategies/`._

Every strategy has two modes: a **diagnostic** study (mode A, may use forward labels, labelled NOT EXECUTABLE) and a **causal backtest** (mode B, orders generated only after their inputs were available, filled by the execution simulator).

### S01: Closing Gap Reversal

- **Hypothesis:** Auction-induced deviations of the closing price from the last continuous price are partly temporary liquidity pressure and revert, so a large positive (negative) gap predicts a lower (higher) subsequent return.
- **Required data:** `daily_bars`, `daily_bars.last_continuous_price`, `daily_bars.close_auction_price`, `borrow`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** gap = ln(P_close_auction / P_last_continuous); trade if |gap| >= threshold
- **Entry rule:** side = -sign(gap); enter at the NEXT session's opening auction (the closing price is not known early enough to trade at it - that would be look-ahead)
- **Exit rule:** exit in the closing auction after hold_sessions sessions (0 = same day close)
- **Order timing:** signal available at EOD receipt (or real-time auction receipt); order entered in next pre-open
- **Executable order type:** opening-auction market/limit order; closing-auction ATC / at-auction order for exit
- **Cost assumptions:** registry fees + assumed commission per side; square-root impact on auction price; participation cap on realised auction volume (scenario dependent)
- **Hedging assumptions:** optional index-futures beta hedge entered/exited with the stock legs
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked
- **Failure modes:** most of the reversal happens overnight (before the executable entry); gaps on news are permanent; small-cap gaps coincide with wide spreads and low opening-auction volume; shorts may be unborrowable exactly when gaps are large
- **Default parameters:** `{'threshold_bps': 100.0, 'hold_sessions': 0, 'entry_session': 'open_auction', 'signal_source': 'eod', 'max_positions': 10, 'pos_frac': 0.05, 'max_adv_frac': 0.01, 'min_adv': 0.0, 'hedge': False, 'long_only': False}`
- **Parameter grid (discovery period only):** `{'threshold_bps': [50, 100, 200], 'hold_sessions': [0, 1, 4]}`

### S02: Closing Gap Continuation

- **Hypothesis:** Closing gaps reflect informed order flow and continue (alternative to S01).
- **Required data:** `daily_bars`, `daily_bars.last_continuous_price`, `daily_bars.close_auction_price`, `borrow`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** gap = ln(P_close_auction / P_last_continuous); trade if |gap| >= threshold
- **Entry rule:** side = +sign(gap); enter at the next session's opening auction
- **Exit rule:** exit in the closing auction after hold_sessions sessions
- **Order timing:** as S01
- **Executable order type:** as S01
- **Cost assumptions:** as S01
- **Hedging assumptions:** as S01
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked; S01 and S02 are mutually exclusive hypotheses and are counted as separate tests in the FDR family
- **Failure modes:** if gaps are mostly temporary pressure, continuation loses the reversal plus costs
- **Default parameters:** `{'threshold_bps': 100.0, 'hold_sessions': 0, 'entry_session': 'open_auction', 'signal_source': 'eod', 'max_positions': 10, 'pos_frac': 0.05, 'max_adv_frac': 0.01, 'min_adv': 0.0, 'hedge': False, 'long_only': False}`
- **Parameter grid (discovery period only):** `{'threshold_bps': [50, 100, 200], 'hold_sessions': [0, 4]}`

### S03: Pre-close Momentum

- **Hypothesis:** Stocks with large intraday moves into the close keep moving (late-day momentum / closing-auction continuation); entering in the closing auction on the move earns the continuation over the next session.
- **Required data:** `daily_bars`, `daily_bars.last_continuous_price`, `daily_bars.close_auction_price`, `borrow`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** z = ln(P_last_continuous / P_open) / vol_20(previous day); trade if |z| >= z_threshold
- **Entry rule:** side = direction x sign(z); closing-auction order submitted after the last continuous price is received and BEFORE the random-close window opens; filled at the auction price
- **Exit rule:** exit at the next session's opening auction (exit_session=open_auction) or next close
- **Order timing:** last_continuous_ts + feed latency + decision latency; must precede random_start
- **Executable order type:** SET ATC / SGX pre-close market / HKEX at-auction (limit) order
- **Cost assumptions:** auction impact (square root) + fees; fill capped by realised auction volume
- **Hedging assumptions:** optional futures hedge entered after the cash close
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked
- **Failure modes:** the auction itself may already absorb the continuation; late signal misses the order deadline; short-in-auction rules
- **Default parameters:** `{'z_threshold': 1.5, 'direction': 1, 'hold_sessions': 1, 'exit_session': 'open_auction', 'max_positions': 10, 'pos_frac': 0.05, 'max_adv_frac': 0.01, 'min_adv': 0.0, 'hedge': False, 'extra_delay_s': 0.0}`
- **Parameter grid (discovery period only):** `{'z_threshold': [1.0, 1.5, 2.5], 'exit_session': ['open_auction', 'close_auction']}`

### S04: Auction Imbalance Prediction

- **Hypothesis:** The indicative equilibrium price (IEP) during the closing auction reveals order imbalance; providing liquidity against a large indicated move earns the subsequent reversal (mode='provide'), or following the imbalance predicts the final print (mode='follow').
- **Required data:** `daily_bars`, `auction_indicative`, `daily_bars.close_auction_price`, `borrow`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** iep_gap = ln(IEP_t / P_last_continuous) from the last snapshot received at least latency seconds before the random-close window; trade if |iep_gap| >= threshold
- **Entry rule:** mode=provide: side = -sign(iep_gap); mode=follow: side = +sign(iep_gap); closing-auction order submitted before random_start
- **Exit rule:** exit at the next session's opening auction (default)
- **Order timing:** indicative receipt + decision latency <= random_start - lead_s
- **Executable order type:** closing-auction limit / at-auction order
- **Cost assumptions:** auction impact + fees; our own order would move the IEP (not modelled beyond impact)
- **Hedging assumptions:** none by default
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked; predictability (R^2) by time-to-close and by quote latency
- **Failure modes:** indicative data not archived historically (UNSUPPORTED on real data unless purchased); IEP is noisy early in the auction; latency pushes the usable snapshot earlier
- **Default parameters:** `{'threshold_bps': 50.0, 'mode': 'provide', 'hold_sessions': 1, 'exit_session': 'open_auction', 'max_positions': 10, 'pos_frac': 0.05, 'max_adv_frac': 0.01, 'min_adv': 0.0, 'hedge': False, 'lead_s': 0.0, 'latency_s': 1.0}`
- **Parameter grid (discovery period only):** `{'threshold_bps': [25, 50, 100], 'mode': ['provide', 'follow']}`

### S05: Index Rebalance Price Pressure

- **Hypothesis:** Announced index additions (deletions) attract predictable buying (selling) by index funds at the close before the effective date; prices rise (fall) between announcement and that close and partly revert afterwards.
- **Required data:** `daily_bars`, `index_events`, `index_membership`, `borrow`, `futures_daily`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** event_type in {add, delete} from point-in-time announcements (receipt_ts known)
- **Entry rule:** mode=pre_event: side=+1 add / -1 delete at the opening auction of the first session after the announcement was received; mode=post_event_reversal: opposite side in the closing auction of the index trade day
- **Exit rule:** pre_event: sell/cover into the closing auction of the trade day (effective_date - 1 session); post_event_reversal: exit after hold_sessions at the close
- **Order timing:** announcement receipt + decision latency; never before the announcement
- **Executable order type:** opening-auction order to enter; closing-auction order on the index trade day
- **Cost assumptions:** fees + impact; closing-auction participation cap applies on the trade day when the auction volume spikes
- **Hedging assumptions:** index-futures beta hedge by default (hedge=True)
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers; skip events whose trade day is before the entry date
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked; few events per year -> wide CIs; announcement vs effective date separated
- **Failure modes:** real indices (SET50, STI, HSI, FTSE, MSCI) need licensed point-in-time announcements - unavailable here; pre-announcement anticipation; crowding of the trade
- **Default parameters:** `{'mode': 'pre_event', 'hold_sessions': 5, 'max_positions': 20, 'pos_frac': 0.05, 'max_adv_frac': 0.02, 'min_adv': 0.0, 'hedge': True}`
- **Parameter grid (discovery period only):** `{'mode': ['pre_event', 'post_event_reversal'], 'hedge': [True, False]}`

### S06: Cross-sectional Closing Reversal (market-neutral)

- **Hypothesis:** Ranking stocks by closing gap each day, a long-short portfolio long the most negative and short the most positive gaps earns the reversal while neutralising market exposure.
- **Required data:** `daily_bars`, `daily_bars.last_continuous_price`, `daily_bars.close_auction_price`, `borrow`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** z = gap (or gap minus sector median gap when neutral='sector'); long bottom quantile, short top quantile
- **Entry rule:** enter both legs at the next session's opening auction; short legs only if borrow is located (rejected otherwise - the intended neutrality is then NOT achieved and this is reported)
- **Exit rule:** exit both legs in the closing auction after hold_sessions sessions
- **Order timing:** EOD signal, next pre-open order entry
- **Executable order type:** opening-auction orders; closing-auction orders for exit
- **Cost assumptions:** as S01 plus borrow fees on the short leg (accrued daily)
- **Hedging assumptions:** dollar-neutral, sector-neutral or beta-neutral leg sizing; optional futures hedge of residual beta
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers; min_names per day; equal names per leg
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked
- **Failure modes:** short leg concentrated in unborrowable names; turnover costs; beta neutrality from noisy 60-day betas
- **Default parameters:** `{'quantile': 0.1, 'min_names': 10, 'neutral': 'dollar', 'hold_sessions': 0, 'entry_session': 'open_auction', 'max_positions': 10, 'pos_frac': 0.05, 'max_adv_frac': 0.01, 'min_adv': 0.0, 'hedge': False, 'min_abs_gap_bps': 20.0}`
- **Parameter grid (discovery period only):** `{'neutral': ['dollar', 'sector', 'beta'], 'quantile': [0.1, 0.2]}`

### S07: SGX Trade-at-Close Dislocation

- **Hypothesis:** On SGX the closing price is known before the Trade-at-Close (TAC) phase, so a contrarian order at the closing price can capture the overnight reversal of auction pressure - the only market here where trading at a known close is possible.  Capacity is bounded by TAC liquidity.
- **Required data:** `daily_bars`, `daily_bars.close_auction_price`, `daily_bars.close_auction_receipt_ts`, `daily_bars.tac_volume`, `borrow`
- **Markets:** SGX
- **Signal formula:** gap = ln(P_close / P_last_continuous) from the real-time auction result
- **Entry rule:** side = -sign(gap); TAC order at the closing price, submitted after the auction result is received; fill <= tac_participation x realised TAC volume (zero when no TAC trades occurred)
- **Exit rule:** exit at next session's opening auction (hold_sessions=1, exit_session=open_auction) or at the next close
- **Order timing:** auction result receipt + decision latency, must be before TAC end; TAC rules must be in force
- **Executable order type:** TAC limit order at the closing price
- **Cost assumptions:** no price impact inside TAC (price fixed) but fill capped by realised TAC volume; exit costs as S01
- **Hedging assumptions:** none by default
- **Risk controls:** max positions per day, per-position notional = min(capital x pos_frac, max_adv_frac x ADV20), ex-ante liquidity filter (ADV20 >= min_adv), skip suspended / missing data, no averaging into losers
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked; TAC auction matching is separated from post-auction TAC transactions
- **Failure modes:** TAC volume is thin or zero; counterparties in TAC are not random (adverse selection); TAC introduction date unverified - pre-introduction dates are historically assumed
- **Default parameters:** `{'threshold_bps': 100.0, 'hold_sessions': 1, 'exit_session': 'open_auction', 'max_positions': 10, 'pos_frac': 0.05, 'max_adv_frac': 0.01, 'min_adv': 0.0, 'hedge': False}`
- **Parameter grid (discovery period only):** `{'threshold_bps': [50, 100, 200], 'exit_session': ['open_auction', 'close_auction']}`

### S08: Closing Auction vs Futures Basis

- **Hypothesis:** The cash index's closing-auction move contains temporary pressure.  Index futures that keep trading after the cash close price only part of the expected reversal, so a futures position against the index auction gap, entered after the cash close, earns the residual.
- **Required data:** `futures_daily`, `index_levels`, `daily_bars.close_auction_price`
- **Markets:** SET, SGX, HKEX
- **Signal formula:** G = ln(index_close / index_at_last_continuous_prices); trade if |G| >= threshold
- **Entry rule:** side = -sign(G) in front-month index futures at the first futures price observed after the cash auction result (price_post_auction)
- **Exit rule:** exit at next session's futures close (exit='close') or open (exit='open')
- **Order timing:** post_auction_ts (cash close + futures quote) + decision latency
- **Executable order type:** futures limit/market order in the post-close futures session
- **Cost assumptions:** futures cost_bps per side from registry (spread + fees, assumption) x scenario multiplier; contract rounding; max_contracts cap
- **Hedging assumptions:** the position IS the hedge instrument; no stock legs
- **Risk controls:** contracts = round(capital x pos_frac / (F x multiplier)) <= max_contracts
- **Statistical validation:** event study (diagnostic) + causal backtest; stationary block bootstrap CI on daily returns; Newey-West t-stat; BH-FDR across all strategy x market x parameter tests; placebo (shuffled signal dates / signs); cost, fill and delay sensitivity; discovery/validation/OOS split, holdout locked
- **Failure modes:** requires intraday futures quotes time-aligned to the cash auction (rarely archived); futures may close before the cash auction in some markets/eras; basis noise
- **Default parameters:** `{'threshold_bps': 10.0, 'exit': 'close', 'pos_frac': 0.5, 'max_contracts': 200}`
- **Parameter grid (discovery period only):** `{'threshold_bps': [5, 10, 20], 'exit': ['open', 'close']}`
