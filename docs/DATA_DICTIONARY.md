# Data Dictionary

_Generated from `auction_alpha/data/schemas.py` - do not edit by hand._

All timestamps are tz-aware UTC. `time_role` distinguishes event time, receipt time and trading date. Signal **availability** timestamps are derived in `auction_alpha/features` and are never earlier than the receipt time of every input.

## `security_master`

Point-in-time security master: one row per security with listing life.

- Granularity: daily
- Primary key: `market, symbol`
- Required by strategies: -

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  | SET | SGX | HKEX |
| `symbol` | str | no |  |  | Exchange ticker |
| `name` | str | yes |  |  | Security name |
| `sector` | str | no |  |  | Sector classification (point-in-time if available) |
| `listing_date` | date | no | date |  | First trading date |
| `delisting_date` | date | yes | date |  | Last trading date (null if still listed) |
| `board_lot` | int | no |  | shares | Board lot in shares |
| `shares_outstanding` | float | yes |  | shares | Shares outstanding (latest known) |
| `cas_eligible` | bool | yes |  |  | HKEX: CAS eligible security (null elsewhere) |
| `is_synthetic` | bool | no |  |  | True for generated test data |

## `daily_bars`

Daily bars with the closing-auction decomposition (last continuous price vs auction close).

- Granularity: daily
- Primary key: `market, symbol, date`
- Required by strategies: S01, S02, S03, S04, S05, S06, S07, S08

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `date` | date | no | date |  | Trading date (local) |
| `open` | float | yes |  | local ccy | Official open (opening auction price) |
| `high` | float | yes |  | local ccy |  |
| `low` | float | yes |  | local ccy |  |
| `close` | float | yes |  | local ccy | Official closing price |
| `volume` | float | no |  | shares | Total traded volume incl. auctions and TAC |
| `turnover` | float | no |  | local ccy | Total traded value |
| `open_auction_volume` | float | yes |  | shares | Volume matched in the opening auction |
| `last_continuous_price` | float | yes |  | local ccy | Last trade before the closing auction phase began |
| `last_continuous_ts` | ts | yes | event |  | Event time of that trade |
| `preclose_bid` | float | yes |  | local ccy | Best bid at end of continuous trading |
| `preclose_ask` | float | yes |  | local ccy | Best ask at end of continuous trading |
| `close_auction_price` | float | yes |  | local ccy | Closing auction uncrossing price (null if no match) |
| `close_auction_volume` | float | yes |  | shares | Closing auction matched volume |
| `close_auction_ts` | ts | yes | event |  | Actual auction matching time (random close) |
| `close_auction_receipt_ts` | ts | yes | receipt |  | When the auction result was received in real time |
| `close_method` | str | no |  |  | auction | last_trade | reference_price | none |
| `tac_volume` | float | yes |  | shares | SGX Trade-at-Close volume (null elsewhere) |
| `tac_turnover` | float | yes |  | local ccy | SGX Trade-at-Close value |
| `suspended` | bool | no |  |  | Trading suspended for the whole day |
| `is_halfday` | bool | no |  |  | Half-day session |
| `receipt_ts` | ts | no | receipt |  | When the end-of-day record was received |

## `auction_indicative`

Indicative equilibrium price / volume / imbalance snapshots during the closing auction.

- Granularity: intraday
- Primary key: `market, symbol, date, snapshot_ts`
- Required by strategies: S04

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `snapshot_ts` | ts | no | event |  | Exchange time of the indicative message |
| `receipt_ts` | ts | no | receipt |  | Receipt time (feed latency) |
| `iep` | float | yes |  | local ccy | Indicative equilibrium price |
| `iev` | float | yes |  | shares | Indicative equilibrium (matchable) volume |
| `imbalance_qty` | float | yes |  | shares | Unmatched quantity at IEP (signed: + buy surplus) |

## `intraday_trades`

Tick-level trades.

- Granularity: tick
- Primary key: `market, symbol, ts, price, size`
- Required by strategies: -
- Notes: Primary key is not guaranteed unique in vendor data; dedupe on a trade id when present.

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `ts` | ts | no | event |  |  |
| `receipt_ts` | ts | no | receipt |  |  |
| `price` | float | no |  | local ccy |  |
| `size` | float | no |  | shares |  |
| `phase` | str | yes |  |  | continuous | open_auction | close_auction | tac |

## `intraday_quotes`

Top-of-book quotes.

- Granularity: tick
- Primary key: `market, symbol, ts`
- Required by strategies: -

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `ts` | ts | no | event |  |  |
| `receipt_ts` | ts | no | receipt |  |  |
| `bid` | float | yes |  |  |  |
| `ask` | float | yes |  |  |  |
| `bid_size` | float | yes |  |  |  |
| `ask_size` | float | yes |  |  |  |

## `order_book_depth`

Order book depth snapshots (multi-level).

- Granularity: tick
- Primary key: `market, symbol, ts, level`
- Required by strategies: -

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `ts` | ts | no | event |  |  |
| `receipt_ts` | ts | no | receipt |  |  |
| `level` | int | no |  |  |  |
| `bid_px` | float | yes |  |  |  |
| `bid_sz` | float | yes |  |  |  |
| `ask_px` | float | yes |  |  |  |
| `ask_sz` | float | yes |  |  |  |

## `index_membership`

Point-in-time index membership intervals.

- Granularity: daily
- Primary key: `index_id, symbol, effective_from`
- Required by strategies: S05, S08

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `index_id` | str | no |  |  |  |
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `effective_from` | date | no | date |  | First date as a member |
| `effective_to` | date | yes | date |  | Last date as a member (null = current) |

## `index_events`

Index review announcements (additions, deletions, weight changes).

- Granularity: event
- Primary key: `index_id, symbol, announce_ts, event_type`
- Required by strategies: S05

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `index_id` | str | no |  |  |  |
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `event_type` | str | no |  |  | add | delete | weight_change |
| `announce_ts` | ts | no | event |  | Public announcement time |
| `receipt_ts` | ts | no | receipt |  |  |
| `effective_date` | date | no | date |  | First date of new membership; trade at prior close |
| `old_weight` | float | yes |  |  |  |
| `new_weight` | float | yes |  |  |  |
| `source` | str | yes |  |  | Announcement source / URL |

## `index_levels`

Index levels (official close and level implied by last continuous prices).

- Granularity: daily
- Primary key: `index_id, date`
- Required by strategies: S08

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `index_id` | str | no |  |  |  |
| `market` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `open` | float | yes |  |  |  |
| `close` | float | yes |  |  |  |
| `level_at_lcp` | float | yes |  |  | Index computed from constituents' last continuous prices |
| `receipt_ts` | ts | no | receipt |  |  |

## `corporate_actions`

Corporate actions with announcement and ex dates.

- Granularity: event
- Primary key: `market, symbol, action_type, ex_date`
- Required by strategies: -

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `action_type` | str | no |  |  | split | cash_dividend |
| `announce_ts` | ts | no | event |  |  |
| `ex_date` | date | no | date |  |  |
| `ratio` | float | yes |  |  | New shares per old share (split) |
| `cash_amount` | float | yes |  | local ccy | Dividend per share |

## `borrow`

Stock-borrow availability and cost (point in time).

- Granularity: daily
- Primary key: `market, symbol, date`
- Required by strategies: S05, S06

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `symbol` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `shortable` | bool | no |  |  | Regulatory short-eligible AND borrow located |
| `borrow_fee_bps` | float | yes |  | bps p.a. | Annualised borrow fee |
| `available_qty` | float | yes |  | shares | Lendable quantity |
| `receipt_ts` | ts | no | receipt |  | When the availability record was known |

## `futures_daily`

Front-month index futures with the price observed just after the cash closing auction.

- Granularity: daily
- Primary key: `market, contract, date`
- Required by strategies: S08

| column | type | nullable | time role | unit | description |
|---|---|---|---|---|---|
| `market` | str | no |  |  |  |
| `contract` | str | no |  |  |  |
| `date` | date | no | date |  |  |
| `open` | float | yes |  |  |  |
| `close` | float | yes |  |  |  |
| `price_at_cash_lcp` | float | yes |  |  | Futures price at the cash last-continuous time |
| `price_post_auction` | float | yes |  |  | Futures price shortly after cash closing auction |
| `post_auction_ts` | ts | yes | event |  | Event time of price_post_auction |
| `multiplier` | float | no |  | ccy per index point |  |
| `receipt_ts` | ts | no | receipt |  |  |
