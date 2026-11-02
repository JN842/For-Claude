# Market Rules Registry

_Generated from `auction_alpha/rules/registry_data/*.yaml` - edit the YAML, then run `python -m auction_alpha docs`._

Registry content hash: `b0eb2557b35f`

Status legend: **verified_official** = read on an official exchange page in this project (URL and access date recorded); **secondary_unverified** = from secondary knowledge or a page read only via summary - confirm before use; **assumption** = modelling choice (e.g. negotiated commission); **unknown** = value not established.

`effective_from` blank means the start date is unknown. A lookup for a date before `verified_as_of` on such a rule is flagged **historically_assumed** and counted in every backtest's rule-usage report - current rules are never silently assumed to have applied historically.

## Verification summary

| market | assumption | secondary_unverified | unknown | verified_official |
|---|---|---|---|---|
| HKEX | 1 | 14 | 1 | 11 |
| SET | 1 | 5 | 1 | 13 |
| SGX | 1 | 21 | 2 | 0 |


## Hong Kong Exchanges and Clearing - Securities Market (HKEX)

Timezone `Asia/Hong_Kong`, currency HKD, registry version `2026.11.02-1`.

### Sources

- `hkex_cas_faq`: [HKEX - Closing Auction Session FAQ](https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading/CAS?sc_lang=en) - accessed 2026-11-02 (loaded). Verbatim page text read. No last-updated date on the page.
- `hkex_trading_hours`: [HKEX - Trading Hours (Securities Market)](https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market?sc_lang=en) - accessed 2026-11-02 (loaded). Securities table labelled 'Updated 16 Sep 2017'.
- `hkex_vcm_faq`: [HKEX - Volatility Control Mechanism FAQ](https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading/VCM?sc_lang=en) - accessed 2026-11-02 (loaded). Page loaded but content was obtained through a summary, not verbatim.
- `hkex_fees`: [HKEX - Securities Transaction Fees](https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Fees/Securities-(Hong-Kong)/Trading/Transaction?sc_lang=en) - accessed 2026-11-02 (loaded). Page loaded but content was obtained through a summary, not verbatim.
- `hkex_severe_weather`: [HKEX - Severe Weather Arrangements](https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Severe-Weather-Arrangements?sc_lang=en) - accessed 2026-11-02 (loaded). Summary only.
- `secondary_knowledge`: [Secondary knowledge (not read from an official page in this project)](n/a) - accessed n/a (NOT loaded). 

### Rules

| key | status | effective from | effective to | verified as of | value | notes |
|---|---|---|---|---|---|---|
| `auction.close.eligibility` | verified_official |  |  | 2026-11-02 | `"equities_funds_li_products"` | All equities (incl. DRs, investment companies, preference shares, stapled securities), funds (ETFs, REITs) and L&I products.  Structured products (DWs, CBBCs) excluded.  The 2016 CAS universe was narrower per secondary knowledge - per-security eligibility must come from data. |
| `auction.close.exists` | secondary_unverified | 2016-07-25 |  |  | `true` | CAS (re)introduced 25 July 2016 per secondary knowledge; the CAS FAQ gives no start date. |
| `auction.close.exists` | secondary_unverified |  | 2016-07-24 |  | `false` | Before CAS the close was the median of five nominal prices in the last minute (secondary knowledge). |
| `auction.close.no_match_fallback` | verified_official |  |  | 2026-11-02 | `"reference_price"` | If no final IEP forms the Reference Price becomes the closing price. |
| `auction.close.non_cas_close` | verified_official |  |  | 2026-11-02 | `"reference_price"` | For non-CAS securities the Reference Price becomes the closing price. |
| `auction.close.order_entry` | verified_official |  |  | 2026-11-02 | `{"phase_start": "16:01", "accepts": ["AT_AUCTION", "AT_AUCTION_LIMIT"], "cancel_allowed_until": "16:06", "stage1_band_pct": 5.0, "stage2_band": "within_lowes...` | Only at-auction and at-auction limit orders from 16:01. No cancellation 16:06 to close. Short sells must be at-auction limit priced >= reference price. |
| `auction.close.price_rule` | secondary_unverified |  |  |  | `["max_executable_volume", "min_imbalance", "nearest_reference_price"]` | The FAQ states matching at the final IEP and priority 'by order type, price and then time' but does NOT describe the IEP tie-break criteria. Tie-breaks here are secondary knowledge. |
| `auction.close.random_window` | verified_official |  |  | 2026-11-02 | `{"start": "16:08", "end": "16:10"}` | "the market closes randomly within two minutes". Whether the 2016 CAS already had this structure is not verified. |
| `auction.close.reference_price` | verified_official |  |  | 2026-11-02 | `"median_of_5_nominal_prices_last_minute_15s"` | Five snapshots at 15-second intervals from 15:59:00. |
| `auction.close.vcm_applies` | secondary_unverified |  |  |  | `false` | VCM applies to board-lot orders in continuous trading only, not POS or CAS (from page summary). |
| `auction.open.random_window` | secondary_unverified |  |  |  | `{"start": "09:20", "end": "09:22"}` | Pre-opening session random matching window - not read from the official page. |
| `board_lot.default` | secondary_unverified |  |  |  | `–` | Board lots are set per security by the issuer (commonly 100-2000). Must come from the security master. |
| `calendar.halfday` | verified_official |  |  | 2026-11-02 | `"eves_of_christmas_new_year_lunar_new_year"` |  |
| `calendar.holidays` | unknown |  |  |  | `–` | Official HKEX holiday calendars not loaded. Severe-weather trading (from Sep 2024, summary only) means typhoon days may still be trading days. |
| `fees.commission_bps` | assumption |  |  |  | `5.0` | Negotiated institutional commission - modelling assumption. |
| `fees.exchange` | secondary_unverified | 2023-11-17 |  |  | `{"trading_fee_bps": 0.565, "sfc_levy_bps": 0.27, "afrc_levy_bps": 0.015, "stamp_duty_bps": 10.0, "tax_on_fees_pct": 0.0, "per_trade_tariff": 0.0}` | Trading fee 0.00565% from 1 Jan 2023 (tariff removed); SFC levy 0.0027% since 1 Nov 2014; AFRC levy 0.00015% from 1 Jan 2022 (fees page, read via summary).  Stamp duty 0.1% per side; its effective date (17 Nov 2023) is secondary knowledge. |
| `fees.exchange` | secondary_unverified | 2023-01-01 | 2023-11-16 |  | `{"trading_fee_bps": 0.565, "sfc_levy_bps": 0.27, "afrc_levy_bps": 0.015, "stamp_duty_bps": 13.0, "tax_on_fees_pct": 0.0, "per_trade_tariff": 0.0}` | Stamp duty 0.13% from 1 Aug 2021 to 16 Nov 2023 (secondary knowledge). |
| `fees.exchange` | secondary_unverified | 2022-01-01 | 2022-12-31 |  | `{"trading_fee_bps": 0.5, "sfc_levy_bps": 0.27, "afrc_levy_bps": 0.015, "stamp_duty_bps": 13.0, "tax_on_fees_pct": 0.0, "per_trade_tariff": 0.5}` |  |
| `fees.exchange` | secondary_unverified | 2021-08-01 | 2021-12-31 |  | `{"trading_fee_bps": 0.5, "sfc_levy_bps": 0.27, "afrc_levy_bps": 0.0, "stamp_duty_bps": 13.0, "tax_on_fees_pct": 0.0, "per_trade_tariff": 0.5}` |  |
| `fees.exchange` | secondary_unverified |  | 2021-07-31 |  | `{"trading_fee_bps": 0.5, "sfc_levy_bps": 0.27, "afrc_levy_bps": 0.0, "stamp_duty_bps": 10.0, "tax_on_fees_pct": 0.0, "per_trade_tariff": 0.5}` |  |
| `futures.index_hedge` | secondary_unverified |  |  |  | `{"contract": "Hang Seng Index Futures", "multiplier": 50, "day_close": "16:30", "cost_bps": 0.8}` | HSI futures day session closes after the CAS; after-hours session exists. Contract specs not verified here. |
| `price_limits.daily_pct` | verified_official |  |  | 2026-11-02 | `–` | HKEX has no daily ceiling/floor for equities; CAS has a +/-5% band around the reference price instead. |
| `session.halfday_phases` | verified_official |  |  | 2026-11-02 | `[{"phase": "pre_open", "start": "09:00", "end": "09:30"}, {"phase": "continuous_1", "start": "09:30", "end": "12:00"}, {"phase": "cas_reference_price_fixing"...` | Eves of Christmas, New Year and Lunar New Year. No afternoon session. |
| `session.phases` | verified_official |  |  | 2026-11-02 | `[{"phase": "pre_open", "start": "09:00", "end": "09:30"}, {"phase": "continuous_1", "start": "09:30", "end": "12:00"}, {"phase": "lunch", "start": "12:00", "...` | Page table 'Updated 16 Sep 2017'. Earlier history not verified. |
| `short_selling.regime` | secondary_unverified |  |  |  | `{"covered_only": true, "eligible_list": "designated_securities", "price_rule": "tick_rule_not_below_best_ask", "allowed_in_closing_auction": "at_auction_limi...` | Short selling only in designated securities; CAS short sells must be at-auction limit orders priced >= reference price (CAS FAQ). |
| `tac.available` | verified_official |  |  | 2026-11-02 | `false` | No Trade-at-Close phase is described for HKEX. |
| `tick_size.table` | secondary_unverified |  |  |  | `[[0.01, 0.001], [0.25, 0.005], [0.5, 0.01], [10, 0.02], [20, 0.05], [100, 0.1], [200, 0.2], [500, 0.5], [1000, 1.0], [2000, 2.0], [5000, 5.0]]` | Long-standing HKEX spread table (secondary knowledge).  HKEX announced tick-size reductions for some price bands around 2025 that are NOT captured here - verify before use. |

## Stock Exchange of Thailand (SET)

Timezone `Asia/Bangkok`, currency THB, registry version `2026.11.02-1`.

### Sources

- `set_open_close_calc`: [SET - Opening / Closing Price Calculation](https://www.set.or.th/en/market/information/trading-procedure/opening-closing-price-calculation) - accessed 2026-11-02 (loaded). 
- `set_trading_hours`: [SET - Trading Hours](https://www.set.or.th/en/market/information/trading-procedure/trading-hours) - accessed 2026-11-02 (loaded). 
- `set_order_types`: [SET - Order Types](https://www.set.or.th/en/market/information/trading-procedure/order-types) - accessed 2026-11-02 (loaded). 
- `set_price_bands_ticks`: [SET - Price Bands and Tick Sizes](https://www.set.or.th/en/market/information/trading-procedure/price-bands-and-tick-sizes) - accessed 2026-11-02 (loaded). 
- `set_trading_units`: [SET - Trading Units](https://www.set.or.th/en/market/information/trading-procedure/trading-units) - accessed 2026-11-02 (loaded). 
- `set_trading_methods`: [SET - Trading Methods](https://www.set.or.th/en/market/information/trading-procedure/trading-methods) - accessed 2026-11-02 (loaded). 
- `secondary_knowledge`: [Secondary knowledge (not read from an official page in this project)](n/a) - accessed n/a (NOT loaded). 

### Rules

| key | status | effective from | effective to | verified as of | value | notes |
|---|---|---|---|---|---|---|
| `auction.close.atc_price_outside_limits_ticks` | verified_official |  |  | 2026-11-02 | `1` | Open/close determined with ATO/ATC can fall outside ceiling/floor by at most +/-1 tick. |
| `auction.close.eligibility` | verified_official |  |  | 2026-11-02 | `"all_board_lot"` | Pre-close is a board-lot auction for all regular securities; odd lots are not listed for pre-close. |
| `auction.close.exists` | verified_official |  |  | 2026-11-02 | `true` | Closing price by auction (pre-close session). Start date of the closing auction not verified. |
| `auction.close.no_match_fallback` | secondary_unverified |  |  |  | `"last_trade_price"` | If no closing auction match occurs the official close is assumed to be the last traded price. Not stated on the pages read. |
| `auction.close.order_entry` | verified_official |  |  | 2026-11-02 | `{"phase_start": "16:30", "accepts": ["ATC", "LIMIT"], "cancel_allowed_until": "random_close", "min_resting_ms_before_cancel": 250}` | ATC (market, fill-and-kill) is explicitly allowed in pre-close. Limit orders participate ('all valid orders in the system') but the page does not explicitly list limit entry in pre-close.  No pre-close specific cancellation restriction is published; the general 250 ms minimum resting time applies.  cancel_allowed_until is therefore an inference. |
| `auction.close.price_rule` | verified_official |  |  | 2026-11-02 | `["max_executable_volume", "min_imbalance", "market_pressure", "nearest_last_price"]` | Tie-break fallback (3.3) is 'closest to Last Sale, IPO price or lowest price'. |
| `auction.close.random_window` | verified_official |  |  | 2026-11-02 | `{"start": "16:35", "end": "16:40"}` | "randomly select a closing time between 16:35 - 16:40 hours". Introduction date of the random close not verified. |
| `auction.open.random_window` | verified_official |  |  | 2026-11-02 | `{"start": "09:55", "end": "10:00"}` |  |
| `board_lot.default` | verified_official |  |  | 2026-11-02 | `100` | 50 units for securities priced >= 500 THB for 6 consecutive months. |
| `calendar.halfday` | secondary_unverified |  |  |  | `"none"` | SET is not known to have regular half-day sessions. |
| `calendar.holidays` | unknown |  |  |  | `–` | Official SET holiday calendars for 2018-2026 were not loaded. Real-data runs infer the trading calendar from the price files. |
| `fees.commission_bps` | assumption |  |  |  | `8.0` | Negotiated institutional commission - modelling assumption, configurable per experiment. |
| `fees.exchange` | secondary_unverified |  |  |  | `{"trading_fee_bps": 0.5, "clearing_fee_bps": 0.1, "regulatory_fee_bps": 0.1, "stamp_duty_bps": 0.0, "tax_on_fees_pct": 7.0}` | SET trading fee 0.005%, TCH clearing 0.001%, SEC fee 0.001%, VAT 7% on commission and fees.  From secondary knowledge; fee holidays/reductions in some years are NOT modelled.  Verify against current SET fee schedule. |
| `futures.index_hedge` | secondary_unverified |  |  |  | `{"contract": "SET50 Index Futures (TFEX)", "multiplier": 200, "day_close": "16:55", "cost_bps": 1.0}` | TFEX SET50 futures continue trading after the cash closing auction. Contract specs not verified here. |
| `price_band.dynamic_pct` | verified_official |  |  | 2026-11-02 | `{"pct": 10.0, "applies_in": "continuous", "min_price": 1.0, "action": "partial match then 2-minute pre-open"}` | Effective date of the dynamic price band not published on the page. |
| `price_limits.daily_pct` | verified_official |  |  | 2026-11-02 | `30.0` | Shares - not exceeding 30% of preceding trading day's close (min 0.01 THB). First trading day 3x IPO price. |
| `session.phases` | verified_official |  |  | 2026-11-02 | `[{"phase": "pre_open_1", "start": "09:30", "end": "10:00", "random_end": ["09:55", "10:00"]}, {"phase": "continuous_1", "start": "10:00", "end": "12:30"}, {"...` | Regular securities. Continuous sessions start at the random opening time T1/T2; the 'start' above is the latest possible time.  Intermission times are implied by adjacent rows on the page.  No effective date is published, so earlier dates are historically assumed. |
| `short_selling.regime` | secondary_unverified |  |  |  | `{"covered_only": true, "eligible_list": "exchange_published", "price_rule": "uptick_or_zero_plus_tick", "allowed_in_closing_auction": "unknown"}` | Short sales only in SET-designated eligible securities with borrowing arranged.  Price-rule details changed over time (tightened in 2024 per secondary sources) - exact history not verified.  The simulator treats shortability as data (borrow table) AND requires this regime. |
| `tac.available` | verified_official |  |  | 2026-11-02 | `false` | SET has no Trade-at-Close phase. Off-hour session (to 17:00) is for trade reports only. |
| `tick_size.table` | verified_official | 2009-03-30 |  | 2026-11-02 | `[[0, 0.01], [2, 0.02], [5, 0.05], [10, 0.1], [25, 0.25], [100, 0.5], [200, 1.0], [400, 2.0]]` | "effective since March 30, 2009". [lower_bound, tick]. ETFs/unit trusts use 0.01. |

## Singapore Exchange Securities Trading (SGX)

Timezone `Asia/Singapore`, currency SGD, registry version `2026.11.02-1`.

### Sources

- `sgx_trading`: [SGX - Securities Trading](https://www.sgx.com/securities/trading) - accessed 2026-11-02 (NOT loaded). Returned an 'unsupported browser' interstitial only.
- `sgx_rulebook`: [SGX-ST Trading Rules](https://rulebook.sgx.com/rulebook/securities-trading-rules) - accessed 2026-11-02 (NOT loaded). HTTP 404.
- `secondary_knowledge`: [Secondary knowledge (not read from an official page in this project)](n/a) - accessed n/a (NOT loaded). 

### Rules

| key | status | effective from | effective to | verified as of | value | notes |
|---|---|---|---|---|---|---|
| `auction.close.eligibility` | secondary_unverified |  |  |  | `"all_board_lot"` |  |
| `auction.close.exists` | secondary_unverified |  |  |  | `true` |  |
| `auction.close.no_match_fallback` | secondary_unverified |  |  |  | `"last_trade_price"` |  |
| `auction.close.order_entry` | secondary_unverified |  |  |  | `{"phase_start": "17:00", "accepts": ["MARKET", "LIMIT"], "cancel_allowed_until": "17:04"}` | Non-cancel phase from 17:04 - orders may be entered but not amended or withdrawn. |
| `auction.close.price_rule` | secondary_unverified |  |  |  | `["max_executable_volume", "min_imbalance", "market_pressure", "nearest_reference_price"]` |  |
| `auction.close.random_window` | secondary_unverified |  |  |  | `{"start": "17:05", "end": "17:06"}` |  |
| `auction.open.random_window` | secondary_unverified |  |  |  | `{"start": "08:59", "end": "09:00"}` |  |
| `board_lot.default` | secondary_unverified | 2015-01-19 |  |  | `100` |  |
| `board_lot.default` | secondary_unverified |  | 2015-01-18 |  | `1000` |  |
| `calendar.halfday` | secondary_unverified |  |  |  | `"eves_of_christmas_new_year_lunar_new_year"` |  |
| `calendar.holidays` | unknown |  |  |  | `–` |  |
| `fees.commission_bps` | assumption |  |  |  | `8.0` | Negotiated institutional commission - modelling assumption. |
| `fees.exchange` | secondary_unverified | 2024-01-01 |  |  | `{"clearing_fee_bps": 3.25, "trading_fee_bps": 0.75, "stamp_duty_bps": 0.0, "tax_on_fees_pct": 9.0}` | Clearing fee 0.0325% (capped), SGX access fee 0.0075%, GST 9% from 2024. |
| `fees.exchange` | secondary_unverified | 2023-01-01 | 2023-12-31 |  | `{"clearing_fee_bps": 3.25, "trading_fee_bps": 0.75, "stamp_duty_bps": 0.0, "tax_on_fees_pct": 8.0}` |  |
| `fees.exchange` | secondary_unverified |  | 2022-12-31 |  | `{"clearing_fee_bps": 3.25, "trading_fee_bps": 0.75, "stamp_duty_bps": 0.0, "tax_on_fees_pct": 7.0}` |  |
| `futures.index_hedge` | secondary_unverified |  |  |  | `{"contract": "FTSE Straits Times Index Futures", "multiplier": 10, "day_close": "17:35", "cost_bps": 1.5}` | STI futures are comparatively illiquid; hedge cost assumption is conservative. Specs not verified. |
| `price_band.dynamic_pct` | unknown |  |  |  | `–` | SGX dynamic price limit parameters not verified. |
| `price_limits.daily_pct` | secondary_unverified |  |  |  | `–` | No fixed daily ceiling/floor; SGX uses dynamic price limits / circuit breakers in continuous trading (details unverified). |
| `session.halfday_phases` | secondary_unverified |  |  |  | `[{"phase": "pre_open", "start": "08:30", "end": "08:58"}, {"phase": "pre_open_non_cancel", "start": "08:58", "end": "09:00", "random_end": ["08:59", "09:00"]...` | Eves of Christmas, New Year and Lunar New Year (secondary knowledge). |
| `session.phases` | secondary_unverified |  |  |  | `[{"phase": "pre_open", "start": "08:30", "end": "08:58"}, {"phase": "pre_open_non_cancel", "start": "08:58", "end": "09:00", "random_end": ["08:59", "09:00"]...` | No lunch break (removed 2011 per secondary knowledge). Not verified. |
| `short_selling.regime` | secondary_unverified |  |  |  | `{"covered_only": true, "eligible_list": "borrow_availability", "price_rule": "none", "allowed_in_closing_auction": "unknown"}` | Naked short selling prohibited; short sales must be marked and borrow arranged (secondary knowledge). |
| `tac.available` | secondary_unverified |  |  |  | `true` | Trade-at-Close phase after the closing routine where orders trade only at the closing price.  Introduction date not verified (believed ~2017). Before the effective date is known, every TAC result is historically assumed. |
| `tac.rules` | secondary_unverified |  |  |  | `{"price": "closing_price_only", "order_types": ["LIMIT_AT_CLOSE_PRICE"], "requires_closing_price": true, "matching": "price_time_priority_at_close"}` | TAC only runs for securities with a closing price; liquidity is whatever residual interest exists. |
| `tick_size.table` | secondary_unverified |  |  |  | `[[0, 0.001], [0.2, 0.005], [1.0, 0.01]]` |  |
