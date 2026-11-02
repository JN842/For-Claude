"""Canonical dataset schemas.

Each table records three kinds of time separately where they apply:

* ``event`` time  - when the market event happened (e.g. auction match time);
* ``receipt`` time - when the research system received the record;
* ``availability`` time - when a signal derived from it may first be used
  (computed in ``features``; never earlier than receipt time).

All timestamps are tz-aware UTC.  Trading dates are naive dates in the
exchange's local calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str            # 'str','float','int','bool','date','ts'
    nullable: bool = True
    description: str = ""
    unit: str = ""
    time_role: str = ""   # '', 'event', 'receipt', 'date'


@dataclass(frozen=True)
class TableSchema:
    name: str
    description: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    required_for: tuple[str, ...] = ()
    granularity: str = "daily"
    notes: str = ""

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def col(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


C = Column

SECURITY_MASTER = TableSchema(
    "security_master",
    "Point-in-time security master: one row per security with listing life.",
    (
        C("market", "str", False, "SET | SGX | HKEX"),
        C("symbol", "str", False, "Exchange ticker"),
        C("name", "str", True, "Security name"),
        C("sector", "str", False, "Sector classification (point-in-time if available)"),
        C("listing_date", "date", False, "First trading date", time_role="date"),
        C("delisting_date", "date", True, "Last trading date (null if still listed)", time_role="date"),
        C("board_lot", "int", False, "Board lot in shares", "shares"),
        C("shares_outstanding", "float", True, "Shares outstanding (latest known)", "shares"),
        C("cas_eligible", "bool", True, "HKEX: CAS eligible security (null elsewhere)"),
        C("is_synthetic", "bool", False, "True for generated test data"),
    ),
    ("market", "symbol"),
)

DAILY_BARS = TableSchema(
    "daily_bars",
    "Daily bars with the closing-auction decomposition (last continuous price vs auction close).",
    (
        C("market", "str", False),
        C("symbol", "str", False),
        C("date", "date", False, "Trading date (local)", time_role="date"),
        C("open", "float", True, "Official open (opening auction price)", "local ccy"),
        C("high", "float", True, "", "local ccy"),
        C("low", "float", True, "", "local ccy"),
        C("close", "float", True, "Official closing price", "local ccy"),
        C("volume", "float", False, "Total traded volume incl. auctions and TAC", "shares"),
        C("turnover", "float", False, "Total traded value", "local ccy"),
        C("open_auction_volume", "float", True, "Volume matched in the opening auction", "shares"),
        C("last_continuous_price", "float", True, "Last trade before the closing auction phase began", "local ccy"),
        C("last_continuous_ts", "ts", True, "Event time of that trade", time_role="event"),
        C("preclose_bid", "float", True, "Best bid at end of continuous trading", "local ccy"),
        C("preclose_ask", "float", True, "Best ask at end of continuous trading", "local ccy"),
        C("close_auction_price", "float", True, "Closing auction uncrossing price (null if no match)", "local ccy"),
        C("close_auction_volume", "float", True, "Closing auction matched volume", "shares"),
        C("close_auction_ts", "ts", True, "Actual auction matching time (random close)", time_role="event"),
        C("close_auction_receipt_ts", "ts", True, "When the auction result was received in real time", time_role="receipt"),
        C("close_method", "str", False, "auction | last_trade | reference_price | none"),
        C("tac_volume", "float", True, "SGX Trade-at-Close volume (null elsewhere)", "shares"),
        C("tac_turnover", "float", True, "SGX Trade-at-Close value", "local ccy"),
        C("suspended", "bool", False, "Trading suspended for the whole day"),
        C("is_halfday", "bool", False, "Half-day session"),
        C("receipt_ts", "ts", False, "When the end-of-day record was received", time_role="receipt"),
    ),
    ("market", "symbol", "date"),
    required_for=("S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08"),
)

AUCTION_INDICATIVE = TableSchema(
    "auction_indicative",
    "Indicative equilibrium price / volume / imbalance snapshots during the closing auction.",
    (
        C("market", "str", False), C("symbol", "str", False),
        C("date", "date", False, time_role="date"),
        C("snapshot_ts", "ts", False, "Exchange time of the indicative message", time_role="event"),
        C("receipt_ts", "ts", False, "Receipt time (feed latency)", time_role="receipt"),
        C("iep", "float", True, "Indicative equilibrium price", "local ccy"),
        C("iev", "float", True, "Indicative equilibrium (matchable) volume", "shares"),
        C("imbalance_qty", "float", True, "Unmatched quantity at IEP (signed: + buy surplus)", "shares"),
    ),
    ("market", "symbol", "date", "snapshot_ts"),
    required_for=("S04",), granularity="intraday",
)

INTRADAY_TRADES = TableSchema(
    "intraday_trades", "Tick-level trades.",
    (
        C("market", "str", False), C("symbol", "str", False), C("date", "date", False, time_role="date"),
        C("ts", "ts", False, time_role="event"), C("receipt_ts", "ts", False, time_role="receipt"),
        C("price", "float", False, unit="local ccy"), C("size", "float", False, unit="shares"),
        C("phase", "str", True, "continuous | open_auction | close_auction | tac"),
    ),
    ("market", "symbol", "ts", "price", "size"), granularity="tick",
    notes="Primary key is not guaranteed unique in vendor data; dedupe on a trade id when present.",
)

INTRADAY_QUOTES = TableSchema(
    "intraday_quotes", "Top-of-book quotes.",
    (
        C("market", "str", False), C("symbol", "str", False), C("date", "date", False, time_role="date"),
        C("ts", "ts", False, time_role="event"), C("receipt_ts", "ts", False, time_role="receipt"),
        C("bid", "float", True), C("ask", "float", True), C("bid_size", "float", True), C("ask_size", "float", True),
    ),
    ("market", "symbol", "ts"), granularity="tick",
)

ORDER_BOOK_DEPTH = TableSchema(
    "order_book_depth", "Order book depth snapshots (multi-level).",
    (
        C("market", "str", False), C("symbol", "str", False), C("date", "date", False, time_role="date"),
        C("ts", "ts", False, time_role="event"), C("receipt_ts", "ts", False, time_role="receipt"),
        C("level", "int", False), C("bid_px", "float", True), C("bid_sz", "float", True),
        C("ask_px", "float", True), C("ask_sz", "float", True),
    ),
    ("market", "symbol", "ts", "level"), granularity="tick",
)

INDEX_MEMBERSHIP = TableSchema(
    "index_membership", "Point-in-time index membership intervals.",
    (
        C("index_id", "str", False), C("market", "str", False), C("symbol", "str", False),
        C("effective_from", "date", False, "First date as a member", time_role="date"),
        C("effective_to", "date", True, "Last date as a member (null = current)", time_role="date"),
    ),
    ("index_id", "symbol", "effective_from"), required_for=("S05", "S08"),
)

INDEX_EVENTS = TableSchema(
    "index_events", "Index review announcements (additions, deletions, weight changes).",
    (
        C("index_id", "str", False), C("market", "str", False), C("symbol", "str", False),
        C("event_type", "str", False, "add | delete | weight_change"),
        C("announce_ts", "ts", False, "Public announcement time", time_role="event"),
        C("receipt_ts", "ts", False, time_role="receipt"),
        C("effective_date", "date", False, "First date of new membership; trade at prior close", time_role="date"),
        C("old_weight", "float", True), C("new_weight", "float", True),
        C("source", "str", True, "Announcement source / URL"),
    ),
    ("index_id", "symbol", "announce_ts", "event_type"), required_for=("S05",), granularity="event",
)

CORPORATE_ACTIONS = TableSchema(
    "corporate_actions", "Corporate actions with announcement and ex dates.",
    (
        C("market", "str", False), C("symbol", "str", False),
        C("action_type", "str", False, "split | cash_dividend"),
        C("announce_ts", "ts", False, time_role="event"),
        C("ex_date", "date", False, time_role="date"),
        C("ratio", "float", True, "New shares per old share (split)"),
        C("cash_amount", "float", True, "Dividend per share", "local ccy"),
    ),
    ("market", "symbol", "action_type", "ex_date"), granularity="event",
)

BORROW = TableSchema(
    "borrow", "Stock-borrow availability and cost (point in time).",
    (
        C("market", "str", False), C("symbol", "str", False), C("date", "date", False, time_role="date"),
        C("shortable", "bool", False, "Regulatory short-eligible AND borrow located"),
        C("borrow_fee_bps", "float", True, "Annualised borrow fee", "bps p.a."),
        C("available_qty", "float", True, "Lendable quantity", "shares"),
        C("receipt_ts", "ts", False, "When the availability record was known", time_role="receipt"),
    ),
    ("market", "symbol", "date"), required_for=("S05", "S06"),
)

FUTURES_DAILY = TableSchema(
    "futures_daily", "Front-month index futures with the price observed just after the cash closing auction.",
    (
        C("market", "str", False), C("contract", "str", False), C("date", "date", False, time_role="date"),
        C("open", "float", True), C("close", "float", True),
        C("price_at_cash_lcp", "float", True, "Futures price at the cash last-continuous time"),
        C("price_post_auction", "float", True, "Futures price shortly after cash closing auction"),
        C("post_auction_ts", "ts", True, "Event time of price_post_auction", time_role="event"),
        C("multiplier", "float", False, unit="ccy per index point"),
        C("receipt_ts", "ts", False, time_role="receipt"),
    ),
    ("market", "contract", "date"), required_for=("S08",),
)

INDEX_LEVELS = TableSchema(
    "index_levels", "Index levels (official close and level implied by last continuous prices).",
    (
        C("index_id", "str", False), C("market", "str", False), C("date", "date", False, time_role="date"),
        C("open", "float", True), C("close", "float", True),
        C("level_at_lcp", "float", True, "Index computed from constituents' last continuous prices"),
        C("receipt_ts", "ts", False, time_role="receipt"),
    ),
    ("index_id", "date"), required_for=("S08",),
)

ALL_SCHEMAS: dict[str, TableSchema] = {
    s.name: s for s in (
        SECURITY_MASTER, DAILY_BARS, AUCTION_INDICATIVE, INTRADAY_TRADES, INTRADAY_QUOTES,
        ORDER_BOOK_DEPTH, INDEX_MEMBERSHIP, INDEX_EVENTS, INDEX_LEVELS, CORPORATE_ACTIONS,
        BORROW, FUTURES_DAILY,
    )
}


def data_dictionary_markdown() -> str:
    lines = ["# Data Dictionary", "",
             "_Generated from `auction_alpha/data/schemas.py` - do not edit by hand._", "",
             "All timestamps are tz-aware UTC. `time_role` distinguishes event time, receipt time and "
             "trading date. Signal **availability** timestamps are derived in `auction_alpha/features` "
             "and are never earlier than the receipt time of every input.", ""]
    for s in ALL_SCHEMAS.values():
        lines += [f"## `{s.name}`", "", s.description, "",
                  f"- Granularity: {s.granularity}",
                  f"- Primary key: `{', '.join(s.primary_key)}`",
                  f"- Required by strategies: {', '.join(s.required_for) or '-'}"]
        if s.notes:
            lines.append(f"- Notes: {s.notes}")
        lines += ["", "| column | type | nullable | time role | unit | description |", "|---|---|---|---|---|---|"]
        for c in s.columns:
            lines.append(f"| `{c.name}` | {c.dtype} | {'yes' if c.nullable else 'no'} | {c.time_role} | {c.unit} | {c.description} |")
        lines.append("")
    return "\n".join(lines)
