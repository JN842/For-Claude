"""Dividend event model: announced API events plus slot-based forecasts.

A *slot* is a recurring time of year at which a symbol goes XD, e.g. PTT's
interim around September and its final around April.  Slots are found by
clustering recent XD dates by day of year, so the model needs no ticker
table, operation-period metadata or payment-frequency lookup.

Every function takes an explicit ``as_of`` date and only looks at events
announced on or before it, so the same code serves a live run and a
point-in-time backtest.
"""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


MODEL_VERSION = "v4.0_xd_slots"
YEAR_DAYS = 365
DEFAULT_ANNOUNCE_LAG_DAYS = 14

EVENT_COLUMNS = [
    "symbol",
    "slot",
    "status",
    "xd_date",
    "xd_window_start",
    "xd_window_end",
    "dps",
    "probability",
    "expected_dps",
    "announce_date",
    "watch_status",
    "date_confidence",
    "amount_confidence",
    "event_key",
]

REVIEW_COLUMNS = [
    "symbol",
    "slot",
    "reason",
    "last_xd",
    "last_dps",
    "hits",
    "opportunities",
]


@dataclass(frozen=True)
class ModelParams:
    # History (years before as_of) used to find slots and occurrence rates.
    lookback_years: int = 5
    # XD dates further apart than this (by day of year) start a new slot.
    slot_gap_days: int = 45
    timing_recent_n: int = 3
    dps_recent_n: int = 3
    # A slot seen fewer times than this is treated as a one-off (special).
    min_occurrences: int = 2
    # A slot missed this many years in a row is treated as discontinued.
    stale_years: int = 2
    # Widens the observed XD range on both sides to form the timing window.
    window_pad_days: int = 7
    # A slot spanning more days than this has no usable timing.
    max_slot_span_days: int = 120
    # Assumed announce-to-XD days when the API gives no announce/board date.
    imputed_announce_days: int = 7
    # Beta prior on the occurrence rate; (0, 0) is the raw hit rate.
    prior_alpha: float = 0.0
    prior_beta: float = 0.0

    def as_dict(self):
        return asdict(self)


def _as_date(value):
    return pd.Timestamp(value).normalize()


def _calendar_date(series):
    """Keep the calendar date as written by the API (drops time and zone)."""
    return pd.to_datetime(
        series.astype("string").str.slice(0, 10),
        errors="coerce",
    )


def _optional_date(raw, column):
    if column not in raw.columns:
        return pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    return _calendar_date(raw[column])


def prepare_events(raw, imputed_announce_days=7):
    """Normalise API rows to one row per (symbol, XD date) cash event.

    ``announce_date`` falls back to ``boardDate`` and then to XD minus
    ``imputed_announce_days``; ``announce_imputed`` marks the last case.
    Rows paid on the same XD date (e.g. regular plus special) are summed.
    """
    missing = {"symbol", "xDate", "adjustedDPS"}.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    events = pd.DataFrame(
        {
            "symbol": raw["symbol"].astype("string").str.strip().str.upper(),
            "xd_date": _calendar_date(raw["xDate"]),
            "dps": pd.to_numeric(raw["adjustedDPS"], errors="coerce"),
            "announce_date": _optional_date(raw, "announceDate").fillna(
                _optional_date(raw, "boardDate")
            ),
        },
        index=raw.index,
    )
    events = events.loc[
        events["symbol"].notna()
        & events["xd_date"].notna()
        & events["dps"].gt(0)
    ]
    events = events.drop_duplicates(["symbol", "xd_date", "dps"])

    events["announce_imputed"] = events["announce_date"].isna()
    events["announce_date"] = events["announce_date"].fillna(
        events["xd_date"] - pd.Timedelta(days=int(imputed_announce_days))
    )
    events["announce_date"] = events[["announce_date", "xd_date"]].min(axis=1)

    events = (
        events.groupby(["symbol", "xd_date"], as_index=False)
        .agg(
            dps=("dps", "sum"),
            announce_date=("announce_date", "min"),
            announce_imputed=("announce_imputed", "all"),
        )
        .sort_values(["symbol", "xd_date"], kind="stable")
        .reset_index(drop=True)
    )
    events["symbol"] = events["symbol"].astype(str)
    return events


def point_in_time(events, as_of):
    """Events that were public on ``as_of`` (announced on or before it)."""
    return events.loc[events["announce_date"].le(_as_date(as_of))].copy()


# ---------------------------------------------------------------------------
# Slot detection
# ---------------------------------------------------------------------------

def _day_of_year(dates):
    return np.minimum(pd.DatetimeIndex(dates).dayofyear, YEAR_DAYS)


def _anchor_date(year, anchor_doy):
    return pd.Timestamp(int(year), 1, 1) + pd.Timedelta(days=int(anchor_doy) - 1)


def _anchored_year(date, anchor_doy):
    date = pd.Timestamp(date)
    if date >= _anchor_date(date.year, anchor_doy):
        return date.year
    return date.year - 1


def _circular_distance(a, b):
    diff = abs(int(a) - int(b)) % YEAR_DAYS
    return min(diff, YEAR_DAYS - diff)


def _distance_to_arc(doy, start, end):
    inside = start <= doy <= end if start <= end else doy >= start or doy <= end
    if inside:
        return 0
    return min(_circular_distance(doy, start), _circular_distance(doy, end))


def _cluster_days(doys, gap_days):
    """Split unique days of year into circular clusters.

    Returns a list of (member_days, gap_before) in calendar order, where
    ``gap_before`` is the empty stretch preceding the cluster.
    """
    days = sorted(set(int(day) for day in doys))
    gaps = [days[i + 1] - days[i] for i in range(len(days) - 1)]
    gaps.append(days[0] + YEAR_DAYS - days[-1])
    cuts = [i for i, gap in enumerate(gaps) if gap > gap_days]
    if not cuts:
        cuts = [int(np.argmax(gaps))]

    clusters = []
    for position, cut in enumerate(cuts):
        next_cut = cuts[(position + 1) % len(cuts)]
        start = (cut + 1) % len(days)
        members = []
        index = start
        while True:
            members.append(days[index])
            if index == next_cut:
                break
            index = (index + 1) % len(days)
        clusters.append((members, gaps[cut]))
    return sorted(clusters, key=lambda cluster: cluster[0][0])


def detect_slots(history, params):
    """Find the recurring XD slots of each symbol from historical events."""
    rows = []
    for symbol, group in history.groupby("symbol", sort=True):
        clusters = _cluster_days(
            _day_of_year(group["xd_date"]),
            params.slot_gap_days,
        )
        names = {}
        for members, gap_before in clusters:
            start, end = members[0], members[-1]
            anchor_doy = (start - gap_before // 2 - 1) % YEAR_DAYS + 1
            center_doy = members[len(members) // 2]
            month = (_anchor_date(2001, center_doy)).month
            name = f"M{month:02d}"
            names[name] = names.get(name, 0) + 1
            if names[name] > 1:
                name = f"{name}_{names[name]}"
            rows.append(
                {
                    "symbol": symbol,
                    "slot": name,
                    "start_doy": start,
                    "end_doy": end,
                    "anchor_doy": anchor_doy,
                    "span_days": (end - start) % YEAR_DAYS,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "slot",
            "start_doy",
            "end_doy",
            "anchor_doy",
            "span_days",
        ],
    )


def assign_slots(events, slots, params):
    """Label events with the nearest slot of their symbol and its slot year.

    Events further than ``slot_gap_days`` from every slot get no slot.
    """
    events = events.copy()
    slot_names = []
    slot_years = []
    by_symbol = {
        symbol: group for symbol, group in slots.groupby("symbol", sort=False)
    }
    for symbol, xd_date in events[["symbol", "xd_date"]].itertuples(
        index=False, name=None
    ):
        candidates = by_symbol.get(symbol)
        best = None
        if candidates is not None:
            doy = int(_day_of_year([xd_date])[0])
            distances = [
                _distance_to_arc(doy, slot.start_doy, slot.end_doy)
                for slot in candidates.itertuples(index=False)
            ]
            position = int(np.argmin(distances))
            if distances[position] <= params.slot_gap_days:
                best = candidates.iloc[position]
        if best is None:
            slot_names.append(pd.NA)
            slot_years.append(pd.NA)
        else:
            slot_names.append(best["slot"])
            slot_years.append(_anchored_year(xd_date, best["anchor_doy"]))
    events["slot"] = pd.Series(slot_names, index=events.index, dtype="object")
    events["slot_year"] = pd.Series(slot_years, index=events.index, dtype="Int64")
    return events


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------

def _confidence(count, target):
    if count >= target:
        return "HIGH"
    if count >= 2:
        return "MEDIUM"
    return "LOW"


def _watch_status(as_of, announce_start, announce_end):
    if as_of < announce_start:
        return "NOT_DUE"
    if as_of <= announce_end:
        return "WATCH"
    return "OVERDUE"


def _lag_stats(occurrences, fallback):
    known = occurrences.loc[~occurrences["announce_imputed"]]
    lags = (known["xd_date"] - known["announce_date"]).dt.days
    lags = lags.loc[lags.gt(0)]
    if lags.empty:
        return fallback
    return float(lags.median()), float(lags.min()), float(lags.max())


def _review(symbol, slot, reason, occurrences=None, hits=0, opportunities=0):
    last_xd, last_dps = pd.NaT, np.nan
    if occurrences is not None and not occurrences.empty:
        last_xd = occurrences["xd_date"].iloc[-1]
        last_dps = occurrences["dps"].iloc[-1]
    return {
        "symbol": symbol,
        "slot": slot,
        "reason": reason,
        "last_xd": last_xd,
        "last_dps": last_dps,
        "hits": hits,
        "opportunities": opportunities,
    }


def _slot_forecasts(slot, occurrences, announced_years, symbol_lags, as_of,
                    horizon_end, params):
    """Forecast rows and review rows for one slot of one symbol."""
    anchor_doy = slot["anchor_doy"]
    if slot["span_days"] > params.max_slot_span_days:
        return [], [_review(slot["symbol"], slot["slot"],
                            "IRREGULAR_TIMING", occurrences)]

    # One row per slot year: first XD, total DPS paid in that slot.
    per_year = (
        occurrences.groupby("slot_year", sort=True)
        .agg(
            xd_date=("xd_date", "min"),
            dps=("dps", "sum"),
            announce_date=("announce_date", "min"),
            announce_imputed=("announce_imputed", "all"),
        )
    )
    per_year["offset"] = [
        (xd - _anchor_date(year, anchor_doy)).days
        for year, xd in per_year["xd_date"].items()
    ]

    timing = per_year.tail(params.timing_recent_n)
    median_offset = float(timing["offset"].median())
    min_offset = int(timing["offset"].min()) - params.window_pad_days
    max_offset = int(timing["offset"].max()) + params.window_pad_days

    def window(year):
        anchor = _anchor_date(year, anchor_doy)
        return (
            anchor + pd.Timedelta(days=min_offset),
            anchor + pd.Timedelta(days=round(median_offset)),
            anchor + pd.Timedelta(days=max_offset),
        )

    current_year = _anchored_year(as_of, anchor_doy)
    hit_years = set(int(year) for year in per_year.index)
    latest_closed = current_year
    if current_year not in hit_years and window(current_year)[2] > as_of:
        latest_closed = current_year - 1

    first_year = max(
        min(hit_years),
        latest_closed - params.lookback_years + 1,
    )
    years = range(first_year, latest_closed + 1)
    hits = sum(1 for year in years if year in hit_years)
    opportunities = len(years)
    review_args = (occurrences, hits, opportunities)

    if hits < params.min_occurrences:
        return [], [_review(slot["symbol"], slot["slot"], "ONE_OFF",
                            *review_args)]
    if latest_closed - max(hit_years) >= params.stale_years:
        return [], [_review(slot["symbol"], slot["slot"], "STALE_SLOT",
                            *review_args)]

    denominator = opportunities + params.prior_alpha + params.prior_beta
    probability = (hits + params.prior_alpha) / denominator

    amounts = per_year["dps"].tail(params.dps_recent_n)
    expected_dps = float(amounts.median())
    median_lag, min_lag, max_lag = _lag_stats(per_year, symbol_lags)

    rows = []
    year = latest_closed + 1
    while True:
        start, center, end = window(year)
        if start > horizon_end:
            break
        if year not in announced_years and end > as_of:
            announce_start = start - pd.Timedelta(days=round(max_lag))
            announce_end = end - pd.Timedelta(days=round(min_lag))
            rows.append(
                {
                    "symbol": slot["symbol"],
                    "slot": slot["slot"],
                    "status": "FORECAST",
                    "xd_date": center,
                    "xd_window_start": start,
                    "xd_window_end": end,
                    "dps": expected_dps,
                    "probability": probability,
                    "expected_dps": probability * expected_dps,
                    "announce_date": center - pd.Timedelta(
                        days=round(median_lag)
                    ),
                    "watch_status": _watch_status(
                        as_of, announce_start, announce_end
                    ),
                    "date_confidence": _confidence(
                        len(timing), params.timing_recent_n
                    ),
                    "amount_confidence": _confidence(
                        len(amounts), params.dps_recent_n
                    ),
                    "event_key": (
                        f"{slot['symbol']}|{slot['slot']}|{year}|FORECAST"
                    ),
                }
            )
        year += 1
    return rows, []


def build_event_table(events, as_of, horizon_end, params=None, universe=None):
    """Confirmed and forecast cash events with XD in (as_of, horizon_end].

    ``events`` must come from :func:`prepare_events`; anything announced
    after ``as_of`` is ignored.  Returns ``events`` (one row per expected
    cash event), ``review`` (slots or symbols the model will not forecast)
    and ``slots`` (the detected slot calendar, for diagnostics/backtests).
    """
    params = params or ModelParams()
    as_of = _as_date(as_of)
    horizon_end = _as_date(horizon_end)
    if horizon_end <= as_of:
        raise ValueError("horizon_end must be after as_of")

    known = point_in_time(events, as_of)
    history = known.loc[
        known["xd_date"].le(as_of)
        & known["xd_date"].gt(as_of - pd.DateOffset(years=params.lookback_years))
    ]
    slots = detect_slots(history, params)
    known = assign_slots(known, slots, params)

    forecast_rows = []
    review_rows = []
    for slot in slots.to_dict("records"):
        symbol_events = known.loc[known["symbol"].eq(slot["symbol"])]
        in_slot = symbol_events.loc[symbol_events["slot"].eq(slot["slot"])]
        occurrences = in_slot.loc[
            in_slot["xd_date"].le(as_of)
            & in_slot["xd_date"].gt(
                as_of - pd.DateOffset(years=params.lookback_years)
            )
        ]
        announced_years = set(int(year) for year in in_slot["slot_year"])
        symbol_lags = _lag_stats(
            symbol_events,
            (DEFAULT_ANNOUNCE_LAG_DAYS,) * 3,
        )
        rows, reviews = _slot_forecasts(
            slot,
            occurrences,
            announced_years,
            symbol_lags,
            as_of,
            horizon_end,
            params,
        )
        forecast_rows.extend(rows)
        review_rows.extend(reviews)

    confirmed = known.loc[
        known["xd_date"].gt(as_of) & known["xd_date"].le(horizon_end)
    ]
    confirmed_rows = [
        {
            "symbol": row.symbol,
            "slot": row.slot if pd.notna(row.slot) else "UNSLOTTED",
            "status": "CONFIRMED",
            "xd_date": row.xd_date,
            "xd_window_start": row.xd_date,
            "xd_window_end": row.xd_date,
            "dps": row.dps,
            "probability": 1.0,
            "expected_dps": row.dps,
            "announce_date": row.announce_date,
            "watch_status": "CONFIRMED",
            "date_confidence": "CONFIRMED",
            "amount_confidence": "CONFIRMED",
            "event_key": f"{row.symbol}|{row.xd_date:%Y-%m-%d}|CONFIRMED",
        }
        for row in confirmed.itertuples(index=False)
    ]

    universe = [] if universe is None else list(universe)
    for symbol in sorted(set(universe) - set(slots["symbol"])):
        reason = (
            "NO_RECENT_DIVIDEND"
            if known["symbol"].eq(symbol).any()
            else "NO_DATA"
        )
        review_rows.append(
            _review(symbol, pd.NA, reason,
                    known.loc[known["symbol"].eq(symbol)])
        )

    event_table = pd.DataFrame(
        confirmed_rows + forecast_rows,
        columns=EVENT_COLUMNS,
    )
    event_table = event_table.sort_values(
        ["xd_date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    for column in ["xd_date", "xd_window_start", "xd_window_end",
                   "announce_date"]:
        event_table[column] = pd.to_datetime(event_table[column])
    if event_table["event_key"].duplicated().any():
        raise AssertionError("event keys must be unique")

    review = pd.DataFrame(review_rows, columns=REVIEW_COLUMNS)
    review = review.sort_values(["symbol", "slot"], kind="stable")
    return {
        "events": event_table,
        "review": review.reset_index(drop=True),
        "slots": slots,
    }
