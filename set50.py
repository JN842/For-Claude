"""SET50 futures calendar, index membership scenarios and per-contract dividends.

Membership is kept per index half-year ("2026H2", "2027H1", ...) because
SET50 constituents change with the semi-annual review, effective in January
and July.  A dividend lowers the index only if the stock is a constituent on
its XD date, so each event is checked against the half-year of its XD date.
"""

import re

import numpy as np
import pandas as pd


MONTH_CODES = "FGHJKMNQUVXZ"
_INSTRUMENT = re.compile(
    r"^S50([FGHJKMNQUVXZ])(\d{2})(?:[-/]?([FGHJKMNQUVXZ])(\d{2}))?$"
)


# ---------------------------------------------------------------------------
# Contract calendar
# ---------------------------------------------------------------------------

def contract_code(year, month):
    return f"S50{MONTH_CODES[month - 1]}{year % 100:02d}"


def parse_instrument(code):
    """``"S50Z26"`` -> ((2026, 12), None); ``"S50Z26H27"`` -> (near, far)."""
    match = _INSTRUMENT.match(str(code).strip().upper())
    if not match:
        raise ValueError(f"Not a SET50 futures code: {code!r}")
    legs = []
    for letter, yy in [match.group(1, 2), match.group(3, 4)]:
        if letter is None:
            legs.append(None)
        else:
            legs.append((2000 + int(yy), MONTH_CODES.index(letter) + 1))
    if legs[1] is not None and legs[1] <= legs[0]:
        raise ValueError(f"Spread far leg must expire after near leg: {code}")
    return legs[0], legs[1]


def contract_expiry(year, month, holidays=()):
    """Last trading day: the business day before the month's last business day.

    Pass the SET holiday calendar in ``holidays``; without it only weekends
    are skipped and an expiry next to a holiday will be off by a day.
    """
    holidays = list(pd.to_datetime(list(holidays)))
    last_business_day = pd.offsets.CustomBusinessMonthEnd(
        holidays=holidays
    ).rollforward(pd.Timestamp(year, month, 1))
    return (
        last_business_day - pd.offsets.CustomBusinessDay(holidays=holidays)
    ).normalize()


def instrument_table(instruments, as_of, holidays=()):
    """Expiry dates and the XD range each instrument's carry depends on.

    An outright contract covers XD dates in (as_of, expiry]; a calendar
    spread covers (near expiry, far expiry], or from as_of if the near leg
    window has already started.
    """
    as_of = pd.Timestamp(as_of).normalize()
    rows = []
    for code in instruments:
        near, far = parse_instrument(code)
        near_expiry = contract_expiry(*near, holidays=holidays)
        far_expiry = (
            contract_expiry(*far, holidays=holidays) if far else pd.NaT
        )
        if far is None:
            range_start, range_end = as_of, near_expiry
        else:
            range_start, range_end = max(as_of, near_expiry), far_expiry
        rows.append(
            {
                "instrument": str(code).strip().upper(),
                "type": "SPREAD" if far else "OUTRIGHT",
                "near_expiry": near_expiry,
                "far_expiry": far_expiry,
                "xd_after": range_start,
                "xd_until": range_end,
                "expired": range_end <= as_of,
            }
        )
    return pd.DataFrame(rows)


def default_instruments(as_of, holidays=(), quarterly=2):
    """Next ``quarterly`` H/M/U/Z contracts plus the spread between the first two."""
    as_of = pd.Timestamp(as_of).normalize()
    codes = []
    year, month = as_of.year, as_of.month
    while len(codes) < quarterly:
        if month % 3 == 0 and contract_expiry(year, month, holidays) > as_of:
            codes.append(contract_code(year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    if len(codes) >= 2:
        codes.append(codes[0] + codes[1][3:])
    return codes


# ---------------------------------------------------------------------------
# Membership scenarios
# ---------------------------------------------------------------------------

def index_period(date):
    """Half-year label of the SET50 constituent list in force on ``date``."""
    date = pd.Timestamp(date)
    return f"{date.year}H{1 if date.month <= 6 else 2}"


def _period_key(period):
    match = re.fullmatch(r"(\d{4})H([12])", str(period))
    if not match:
        raise ValueError(f"Index period must look like '2027H1': {period!r}")
    return int(match.group(1)), int(match.group(2))


def period_range(first, last):
    year, half = _period_key(first)
    end = _period_key(last)
    periods = []
    while (year, half) <= end:
        periods.append(f"{year}H{half}")
        year, half = (year, 2) if half == 1 else (year + 1, 1)
    return periods


def membership_table(symbols, period, through=None):
    """Constituents ``symbols`` in ``period``, assumed unchanged through ``through``.

    The result is a plain boolean DataFrame (rows = symbols, columns =
    periods) and can be edited directly, e.g.
    ``table.loc["THAI", "2027H1"] = False``.
    """
    periods = period_range(period, through or period)
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols))
    table = pd.DataFrame(True, index=symbols, columns=periods)
    table.index.name = "symbol"
    return table


def change_membership(table, period, add=(), remove=(), carry_forward=True):
    """Return a copy with ``add``/``remove`` applied from ``period``.

    With ``carry_forward`` the change also applies to every later period in
    the table (a stock that enters in 2027H1 is assumed to stay).
    """
    table = table.copy()
    if period not in table.columns:
        table = extend_membership(table, period)
    columns = [
        column for column in table.columns
        if column == period
        or (carry_forward and _period_key(column) > _period_key(period))
    ]
    for symbol in add:
        symbol = str(symbol).strip().upper()
        if symbol not in table.index:
            table.loc[symbol] = False
        table.loc[symbol, columns] = True
    for symbol in remove:
        symbol = str(symbol).strip().upper()
        if symbol not in table.index:
            raise KeyError(f"{symbol} is not in the membership table")
        table.loc[symbol, columns] = False
    return table.astype(bool)


def extend_membership(table, through):
    """Add periods up to ``through``, each a copy of the latest period."""
    table = table.copy()
    last = sorted(table.columns, key=_period_key)[-1]
    for period in period_range(last, through)[1:]:
        table[period] = table[last]
    return table[sorted(table.columns, key=_period_key)]


def membership_changes(table):
    """List symbols entering/leaving between consecutive periods."""
    columns = sorted(table.columns, key=_period_key)
    rows = []
    for before, after in zip(columns, columns[1:]):
        for symbol in table.index[~table[before] & table[after]]:
            rows.append({"period": after, "symbol": symbol, "change": "IN"})
        for symbol in table.index[table[before] & ~table[after]]:
            rows.append({"period": after, "symbol": symbol, "change": "OUT"})
    return pd.DataFrame(rows, columns=["period", "symbol", "change"])


def _is_member(table, symbol, date):
    period = index_period(date)
    if symbol not in table.index:
        return False
    if period not in table.columns:
        columns = sorted(table.columns, key=_period_key)
        if _period_key(period) < _period_key(columns[0]):
            raise ValueError(
                f"Membership table starts at {columns[0]}; no constituents "
                f"for {period}"
            )
        # Assumption: constituents stay as in the latest period given.
        period = columns[-1]
    return bool(table.at[symbol, period])


# ---------------------------------------------------------------------------
# Per-instrument dividends
# ---------------------------------------------------------------------------

def _triangular_cdf(x, low, mode, high):
    if x <= low:
        return 0.0
    if x >= high:
        return 1.0
    if x <= mode:
        return (x - low) ** 2 / ((high - low) * (mode - low))
    return 1.0 - (high - x) ** 2 / ((high - low) * (high - mode))


def xd_probability_in_range(event, after, until, as_of):
    """P(after < XD <= until | XD > as_of) for one event row.

    Confirmed events are a point mass.  For a forecast the XD date is taken
    as triangular over its window with the mode at the expected XD, and
    conditioned on not having happened by ``as_of`` (an overdue forecast
    keeps only the remaining part of its window).
    """
    if event["status"] == "CONFIRMED":
        return float(after < event["xd_date"] <= until)

    def day(value):
        return pd.Timestamp(value).value / 86_400e9

    low = day(event["xd_window_start"])
    high = day(event["xd_window_end"])
    mode = min(max(day(event["xd_date"]), low), high)
    if high <= low:
        return float(after < event["xd_date"] <= until)
    if mode in (low, high):
        # Shift an end-point mode by a hair to keep the CDF well defined.
        mode = low + 1e-6 if mode == low else high - 1e-6

    def cdf(value):
        return _triangular_cdf(day(value), low, mode, high)

    remaining = 1.0 - cdf(as_of)
    if remaining <= 0:
        return 0.0
    probability = cdf(until) - cdf(max(after, as_of))
    return float(np.clip(probability / remaining, 0.0, 1.0))


def instrument_dividends(events, membership, instruments, as_of, holidays=()):
    """Expected DPS per symbol for each instrument.

    Returns ``matrix`` (symbols x instruments, THB per share, constituents
    only), ``detail`` (one row per event and instrument, with the factors
    behind each contribution) and ``instruments`` (expiries and XD ranges).

    contribution = probability x P(XD in range) x DPS x is_member(XD date)
    """
    as_of = pd.Timestamp(as_of).normalize()
    table = instrument_table(instruments, as_of, holidays)
    live = table.loc[~table["expired"]]

    detail_rows = []
    for instrument in live.itertuples(index=False):
        for event in events.to_dict("records"):
            in_range = xd_probability_in_range(
                event, instrument.xd_after, instrument.xd_until, as_of
            )
            if in_range <= 0:
                continue
            member = _is_member(membership, event["symbol"], event["xd_date"])
            detail_rows.append(
                {
                    "instrument": instrument.instrument,
                    "symbol": event["symbol"],
                    "slot": event["slot"],
                    "status": event["status"],
                    "xd_date": event["xd_date"],
                    "index_period": index_period(event["xd_date"]),
                    "is_member": member,
                    "dps": event["dps"],
                    "probability": event["probability"],
                    "p_xd_in_range": in_range,
                    "contribution": (
                        event["probability"] * in_range * event["dps"]
                        if member else 0.0
                    ),
                    "event_key": event["event_key"],
                }
            )
    detail = pd.DataFrame(
        detail_rows,
        columns=[
            "instrument", "symbol", "slot", "status", "xd_date",
            "index_period", "is_member", "dps", "probability",
            "p_xd_in_range", "contribution", "event_key",
        ],
    )

    symbols = list(membership.index)
    matrix = (
        detail.pivot_table(
            index="symbol",
            columns="instrument",
            values="contribution",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reindex(index=symbols, columns=list(live["instrument"]))
        .fillna(0.0)
    )
    matrix.index.name = "symbol"
    matrix.columns.name = None
    return {"matrix": matrix, "detail": detail, "instruments": table}
