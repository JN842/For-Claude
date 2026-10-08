import calendar

import numpy as np
import pandas as pd

from dividend_regimes import (
    annotate_observed_regime,
    assign_timing_families,
    build_target_periods,
    infer_symbol_cadences,
)


MODEL_VERSION = "v3.2_announced_event_suppression"

FEATURE_COLUMNS = [
    "symbol",
    "cycle",
    "target_period_end",
    "cadence_months",
    "regular_period_months",
    "period_is_regular",
    "timing_family",
    "timing_family_source",
    "timing_observation_count",
    "latest_historical_xd",
    "recent_median_xd_doy",
    "recent_min_xd_doy",
    "recent_max_xd_doy",
    "recent_median_announce_to_xd_days",
    "recent_min_announce_to_xd_days",
    "recent_max_announce_to_xd_days",
    "expected_announce_date",
    "announce_window_start",
    "announce_window_end",
    "expected_xd",
    "expected_xd_window_start",
    "expected_xd_window_end",
    "expected_dps",
    "event_probability",
    "historical_n_dps",
    "same_cycle_dps_count",
    "regime_dps_count",
    "historical_n_events",
    "regular_occurrences",
    "eligible_opportunities",
    "historical_occurrence_count",
    "historical_opportunity_count",
    "historical_occurrence_rate",
    "timing_method",
    "dps_method",
    "date_confidence",
    "amount_confidence",
    "candidate_status",
    "review_reason",
    "logical_event_key",
]

REVIEW_COLUMNS = [
    "symbol",
    "cycle",
    "target_period_end",
    "cadence_months",
    "regular_period_months",
    "timing_family",
    "timing_family_source",
    "review_reason",
    "timing_observation_count",
    "same_cycle_dps_count",
    "regime_dps_count",
    "regular_occurrences",
    "eligible_opportunities",
    "event_probability",
    "date_confidence",
    "amount_confidence",
    "timing_method",
    "dps_method",
    "logical_event_key",
    "event_key",
    "as_of_date",
    "model_version",
]

EVENT_COLUMNS = FEATURE_COLUMNS + [
    "status",
    "watch_status",
    "forecast_sequence",
    "effective_xd",
    "inclusion_xd",
    "effective_dps",
    "event_key",
    "as_of_date",
    "output_scope",
    "model_version",
]


def _as_of_date(value):
    return pd.Timestamp(value).normalize()


def _calendar_date(series):
    return pd.to_datetime(
        series.astype("string").str.slice(0, 10),
        errors="coerce",
    )


def _date_from_doy(year, doy):
    if pd.isna(doy):
        return pd.NaT

    max_doy = 366 if calendar.isleap(int(year)) else 365
    safe_doy = max(1, min(max_doy, int(round(float(doy)))))
    return pd.Timestamp(int(year), 1, 1) + pd.Timedelta(days=safe_doy - 1)


def _next_annual_date_after(reference_date, doy):
    reference_date = pd.Timestamp(reference_date).normalize()
    candidate = _date_from_doy(reference_date.year, doy)
    if candidate <= reference_date:
        candidate = _date_from_doy(reference_date.year + 1, doy)
    return candidate


def _month_delta(start, end):
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    return (end.year - start.year) * 12 + (end.month - start.month)


def _target_key(symbol, cycle, target_period_end):
    if pd.isna(target_period_end):
        return None
    target_period_end = pd.Timestamp(target_period_end)
    return f"{symbol}|{cycle}|{target_period_end:%Y-%m-%d}"


def _empty_features():
    return pd.DataFrame(columns=FEATURE_COLUMNS)


def _empty_event_export():
    return pd.DataFrame(columns=EVENT_COLUMNS)


def _insufficient_cadence_reviews(assigned, cadences, as_of_date):
    unavailable = cadences.loc[cadences["cadence_months"].isna(), "symbol"]
    historical = assigned.loc[assigned["xDate"].le(as_of_date)].copy()
    rows = []
    for symbol in unavailable:
        latest_by_cycle = (
            historical.loc[historical["symbol"].eq(symbol)]
            .sort_values("xDate")
            .drop_duplicates("cycle", keep="last")
        )
        for event in latest_by_cycle.itertuples(index=False):
            row = {column: np.nan for column in FEATURE_COLUMNS}
            row.update(
                {
                    "symbol": event.symbol,
                    "cycle": event.cycle,
                    "target_period_end": pd.NaT,
                    "cadence_months": pd.NA,
                    "regular_period_months": event.regular_period_months,
                    "period_is_regular": False,
                    "timing_family": pd.NA,
                    "timing_family_source": "",
                    "timing_observation_count": 0,
                    "latest_historical_xd": event.xDate,
                    "expected_announce_date": pd.NaT,
                    "announce_window_start": pd.NaT,
                    "announce_window_end": pd.NaT,
                    "expected_xd": pd.NaT,
                    "expected_xd_window_start": pd.NaT,
                    "expected_xd_window_end": pd.NaT,
                    "timing_method": "insufficient_cadence_history",
                    "dps_method": "insufficient_cadence_history",
                    "date_confidence": "NONE",
                    "amount_confidence": "NONE",
                    "candidate_status": "REVIEW",
                    "review_reason": "INSUFFICIENT_CADENCE_HISTORY",
                    "logical_event_key": (
                        f"{event.symbol}|{event.cycle}|INSUFFICIENT_CADENCE"
                    ),
                }
            )
            rows.append(row)
    if not rows:
        return _empty_features()
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)


def _regime_artifacts(
    events,
    as_of_date,
    regime_recent_n,
    min_cadence_gaps,
):
    annotated = annotate_observed_regime(
        events,
        as_of_date=as_of_date,
        regime_recent_n=regime_recent_n,
    )
    cadences = infer_symbol_cadences(
        annotated,
        as_of_date=as_of_date,
        min_cadence_gaps=min_cadence_gaps,
    )
    assigned = assign_timing_families(
        annotated,
        cadences,
        as_of_date=as_of_date,
    )
    targets = build_target_periods(
        assigned,
        cadences,
        as_of_date=as_of_date,
    )
    return assigned, cadences, targets


def _occurrence_statistics(
    regular_events,
    cadence_months,
    opportunity_interval_months,
    occurrence_prior_alpha,
    occurrence_prior_beta,
):
    period_ends = (
        regular_events["period_end_date"]
        .dropna()
        .drop_duplicates()
        .sort_values()
    )
    regular_occurrences = int(len(period_ends))
    if regular_occurrences == 0 or pd.isna(cadence_months):
        return {
            "regular_occurrences": 0,
            "eligible_opportunities": 0,
            "historical_n_events": 0,
            "historical_occurrence_count": 0,
            "historical_opportunity_count": 0,
            "historical_occurrence_rate": np.nan,
            "event_probability": np.nan,
        }

    opportunity_interval_months = int(
        opportunity_interval_months
        if pd.notna(opportunity_interval_months)
        else cadence_months
    )
    opportunity_interval_months = max(1, opportunity_interval_months)
    span_months = _month_delta(period_ends.iloc[0], period_ends.iloc[-1])
    eligible_opportunities = max(
        regular_occurrences,
        int(span_months // opportunity_interval_months) + 1,
    )
    denominator = (
        eligible_opportunities
        + float(occurrence_prior_alpha)
        + float(occurrence_prior_beta)
    )
    if denominator <= 0:
        raise ValueError("occurrence priors must produce a positive denominator")

    return {
        "regular_occurrences": regular_occurrences,
        "eligible_opportunities": eligible_opportunities,
        "historical_n_events": regular_occurrences,
        "historical_occurrence_count": regular_occurrences,
        "historical_opportunity_count": eligible_opportunities,
        "historical_occurrence_rate": (
            regular_occurrences / eligible_opportunities
        ),
        "event_probability": (
            regular_occurrences + float(occurrence_prior_alpha)
        ) / denominator,
    }


def _dps_statistics(
    symbol_regular,
    cycle,
    timing_family,
    dps_recent_n,
):
    dps_recent_n = int(dps_recent_n)
    if (
        timing_family is not None
        and pd.notna(timing_family)
        and "timing_family" in symbol_regular.columns
    ):
        same_cycle = symbol_regular.loc[
            symbol_regular["timing_family"].eq(timing_family)
        ].sort_values("xDate").tail(dps_recent_n)
        same_cycle_method = "same_timing_family"
    else:
        same_cycle = (
            symbol_regular.loc[symbol_regular["cycle"].eq(cycle)]
            .sort_values("xDate")
            .tail(dps_recent_n)
        )
        same_cycle_method = "same_cycle"
    regime_recent = symbol_regular.sort_values("xDate").tail(dps_recent_n)
    same_cycle_count = int(len(same_cycle))
    regime_count = int(len(regime_recent))

    if same_cycle_count >= dps_recent_n:
        return {
            "expected_dps": same_cycle["adjustedDPS"].median(),
            "historical_n_dps": same_cycle_count,
            "same_cycle_dps_count": same_cycle_count,
            "regime_dps_count": regime_count,
            "dps_method": (
                f"median_last_{dps_recent_n}_{same_cycle_method}_regular_dps"
            ),
            "amount_confidence": "HIGH",
        }
    if regime_count >= dps_recent_n:
        return {
            "expected_dps": regime_recent["adjustedDPS"].median(),
            "historical_n_dps": regime_count,
            "same_cycle_dps_count": same_cycle_count,
            "regime_dps_count": regime_count,
            "dps_method": "cadence_regime_fallback",
            "amount_confidence": "MEDIUM",
        }
    return {
        "expected_dps": np.nan,
        "historical_n_dps": regime_count,
        "same_cycle_dps_count": same_cycle_count,
        "regime_dps_count": regime_count,
        "dps_method": "insufficient_dps_history",
        "amount_confidence": "NONE",
    }


def _timing_statistics(family_events, target_period_end):
    family_events = family_events.sort_values("xDate")
    xd_doy = family_events["xDate"].dt.dayofyear
    announce_to_xd_days = (
        family_events["xDate"] - family_events["announceDate"]
    ).dt.days
    median_doy = xd_doy.median()
    min_doy = xd_doy.min()
    max_doy = xd_doy.max()
    median_lag = announce_to_xd_days.median()
    min_lag = announce_to_xd_days.min()
    max_lag = announce_to_xd_days.max()
    reference_date = pd.Timestamp(target_period_end).normalize()
    expected_xd = _next_annual_date_after(reference_date, median_doy)
    expected_xd_window_start = _date_from_doy(expected_xd.year, min_doy)
    expected_xd_window_end = _date_from_doy(expected_xd.year, max_doy)
    expected_announce_date = expected_xd - pd.Timedelta(
        days=int(round(float(median_lag)))
    )
    announce_window_start = expected_xd_window_start - pd.Timedelta(
        days=int(round(float(max_lag)))
    )
    announce_window_end = expected_xd_window_end - pd.Timedelta(
        days=int(round(float(min_lag)))
    )
    return {
        "timing_observation_count": int(len(family_events)),
        "latest_historical_xd": family_events["xDate"].max(),
        "recent_median_xd_doy": median_doy,
        "recent_min_xd_doy": min_doy,
        "recent_max_xd_doy": max_doy,
        "recent_median_announce_to_xd_days": median_lag,
        "recent_min_announce_to_xd_days": min_lag,
        "recent_max_announce_to_xd_days": max_lag,
        "expected_announce_date": expected_announce_date,
        "announce_window_start": announce_window_start,
        "announce_window_end": announce_window_end,
        "expected_xd": expected_xd,
        "expected_xd_window_start": expected_xd_window_start,
        "expected_xd_window_end": expected_xd_window_end,
    }


def _watch_status(as_of_date, announce_window_start, announce_window_end):
    as_of_date = _as_of_date(as_of_date)
    if pd.isna(announce_window_start) or pd.isna(announce_window_end):
        return "NOT_DUE"
    if as_of_date < announce_window_start:
        return "NOT_DUE"
    if as_of_date <= announce_window_end:
        return "WATCH"
    return "OVERDUE"


def build_recent_cycle_features(
    events,
    as_of_date,
    timing_recent_n=2,
    dps_recent_n=3,
    occurrence_lookback_years=8,
    occurrence_prior_alpha=1.0,
    occurrence_prior_beta=1.0,
    regime_recent_n=5,
    min_cadence_gaps=2,
    minimum_timing_observations=1,
):
    """Build generic forecast and review candidates from observable events.

    `cycle` identifies the operation-period slot, while `timing_family`
    identifies the observed calendar schedule. Neither depends on a ticker,
    named month group, or a payment-frequency lookup table.
    """
    as_of_date = _as_of_date(as_of_date)
    timing_recent_n = max(1, int(timing_recent_n))
    dps_recent_n = max(1, int(dps_recent_n))
    minimum_timing_observations = max(1, int(minimum_timing_observations))

    assigned, cadences, targets = _regime_artifacts(
        events,
        as_of_date=as_of_date,
        regime_recent_n=regime_recent_n,
        min_cadence_gaps=min_cadence_gaps,
    )
    cadence_reviews = _insufficient_cadence_reviews(
        assigned,
        cadences,
        as_of_date=as_of_date,
    )
    if targets.empty:
        return cadence_reviews

    historical = assigned.loc[assigned["xDate"].le(as_of_date)].copy()
    timing_history = historical.loc[
        historical["timing_eligible"].fillna(False)
        & historical["timing_family"].notna()
    ].copy()
    regular_history = historical.loc[
        historical["dps_eligible"].fillna(False)
        & historical["period_end_date"].notna()
    ].copy()
    regular_period_by_symbol = (
        assigned.dropna(subset=["regular_period_months"])
        .drop_duplicates("symbol")
        .set_index("symbol")["regular_period_months"]
    )
    if occurrence_lookback_years is not None:
        occurrence_start = as_of_date - pd.DateOffset(
            years=int(occurrence_lookback_years)
        )
    else:
        occurrence_start = None

    rows = []
    for target in targets.itertuples(index=False):
        target_period_end = pd.Timestamp(target.target_period_end)
        cadence_months = int(target.cadence_months)
        symbol_timing = timing_history.loc[
            timing_history["symbol"].eq(target.symbol)
        ]
        if pd.notna(target.timing_family):
            precedents = symbol_timing.loc[
                symbol_timing["timing_family"].eq(target.timing_family)
            ].sort_values("xDate")
        else:
            precedents = symbol_timing.loc[
                symbol_timing["cycle"].eq(target.cycle)
            ].sort_values("xDate")
        symbol_regular = regular_history.loc[
            regular_history["symbol"].eq(target.symbol)
        ].sort_values("xDate")
        if (
            pd.notna(target.timing_family)
            and "timing_family" in symbol_regular.columns
        ):
            probability_events = symbol_regular.loc[
                symbol_regular["timing_family"].eq(target.timing_family)
            ]
        else:
            probability_events = symbol_regular
        if occurrence_start is not None:
            probability_events = probability_events.loc[
                probability_events["period_end_date"].ge(occurrence_start)
            ]

        row = {
            "symbol": target.symbol,
            "cycle": target.cycle,
            "target_period_end": target_period_end,
            "cadence_months": cadence_months,
            "regular_period_months": regular_period_by_symbol.get(
                target.symbol,
                pd.NA,
            ),
            "period_is_regular": True,
            "timing_family": pd.NA,
            "timing_family_source": "",
            "timing_observation_count": 0,
            "latest_historical_xd": pd.NaT,
            "recent_median_xd_doy": np.nan,
            "recent_min_xd_doy": np.nan,
            "recent_max_xd_doy": np.nan,
            "recent_median_announce_to_xd_days": np.nan,
            "recent_min_announce_to_xd_days": np.nan,
            "recent_max_announce_to_xd_days": np.nan,
            "expected_announce_date": pd.NaT,
            "announce_window_start": pd.NaT,
            "announce_window_end": pd.NaT,
            "expected_xd": pd.NaT,
            "expected_xd_window_start": pd.NaT,
            "expected_xd_window_end": pd.NaT,
            "timing_method": "insufficient_timing_history",
            "date_confidence": "NONE",
            "candidate_status": "REVIEW",
            "review_reason": "",
            "logical_event_key": _target_key(
                target.symbol,
                target.cycle,
                target_period_end,
            ),
        }

        if precedents.empty:
            row["review_reason"] = "INSUFFICIENT_TIMING_HISTORY"
        else:
            precedent = precedents.iloc[-1]
            timing_family = precedent["timing_family"]
            family_events = symbol_timing.loc[
                symbol_timing["timing_family"].eq(timing_family)
            ].sort_values("xDate").tail(timing_recent_n)
            row["timing_family"] = timing_family
            row["timing_family_source"] = precedent[
                "timing_family_source"
            ]
            if len(family_events) < minimum_timing_observations:
                row["review_reason"] = "INSUFFICIENT_TIMING_HISTORY"
            else:
                row.update(
                    _timing_statistics(
                        family_events,
                        target_period_end=target_period_end,
                    )
                )
                row["timing_method"] = (
                    f"median_last_{len(family_events)}_timing_family_xd_dates"
                )
                row["date_confidence"] = (
                    "HIGH"
                    if len(family_events) >= timing_recent_n
                    else "LOW"
                )

        row.update(
            _dps_statistics(
                symbol_regular,
                target.cycle,
                target.timing_family,
                dps_recent_n,
            )
        )
        row.update(
            _occurrence_statistics(
                probability_events,
                cadence_months=cadence_months,
                opportunity_interval_months=getattr(
                    target,
                    "slot_interval_months",
                    cadence_months,
                ),
                occurrence_prior_alpha=occurrence_prior_alpha,
                occurrence_prior_beta=occurrence_prior_beta,
            )
        )

        review_reasons = [
            reason
            for reason in [row["review_reason"]]
            if reason
        ]
        if pd.isna(row["expected_dps"]):
            review_reasons.append("INSUFFICIENT_DPS_HISTORY")
        window_closed = (
            pd.notna(row["expected_xd_window_end"])
            and row["expected_xd_window_end"] <= as_of_date
        )
        expected_xd_is_past_without_window = (
            pd.notna(row["expected_xd"])
            and row["expected_xd"] <= as_of_date
            and pd.isna(row["expected_xd_window_end"])
        )
        if window_closed or expected_xd_is_past_without_window:
            review_reasons.append("STALE_TARGET_PERIOD")
        if not review_reasons:
            row["candidate_status"] = "FORECAST"
        row["review_reason"] = ";".join(review_reasons)
        rows.append(row)

    features = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    if not cadence_reviews.empty:
        features = pd.concat(
            [features, cadence_reviews],
            ignore_index=True,
            sort=False,
        )
    window_still_open = (
        features["expected_xd"].gt(as_of_date)
        | features["expected_xd_window_end"].gt(as_of_date)
    )
    features["_candidate_rank"] = np.where(
        features["candidate_status"].eq("FORECAST")
        & window_still_open,
        0,
        1,
    )
    features["_slot_key"] = features["timing_family"].astype("string")
    features.loc[
        features["timing_family"].isna(),
        "_slot_key",
    ] = "CYCLE|" + features.loc[
        features["timing_family"].isna(),
        "cycle",
    ].astype("string")
    return features.sort_values(
        ["symbol", "_slot_key", "_candidate_rank", "target_period_end"],
        kind="stable",
    ).drop_duplicates(
        ["symbol", "_slot_key"],
        keep="first",
    ).drop(
        columns=["_candidate_rank", "_slot_key"],
    ).sort_values(
        ["target_period_end", "symbol", "cycle"],
        kind="stable",
    ).reset_index(drop=True)


def _forecast_record(row, as_of_date, forecast_sequence=1):
    record = row.to_dict()
    inclusion_xd = row["expected_xd"]
    if (
        pd.isna(inclusion_xd)
        or inclusion_xd <= _as_of_date(as_of_date)
    ):
        inclusion_xd = row["expected_xd_window_end"]
    record.update(
        {
            "status": "FORECAST",
            "watch_status": _watch_status(
                as_of_date,
                row["announce_window_start"],
                row["announce_window_end"],
            ),
            "forecast_sequence": forecast_sequence,
            "effective_xd": row["expected_xd"],
            "inclusion_xd": inclusion_xd,
            "effective_dps": row["expected_dps"],
            "event_key": f"{row['logical_event_key']}|FORECAST",
        }
    )
    return record


def build_confirmed_events(
    actual_events,
    as_of_date,
    forecast_end_date=None,
    regime_recent_n=5,
):
    """Return future API events that were already announced as of the run."""
    as_of_date = _as_of_date(as_of_date)
    actual = annotate_observed_regime(
        actual_events,
        as_of_date=as_of_date,
        regime_recent_n=regime_recent_n,
    )
    confirmed_mask = (
        actual["xDate"].gt(as_of_date)
        & actual["announceDate"].notna()
        & actual["announceDate"].le(as_of_date)
        & actual["adjustedDPS"].gt(0)
    )
    if forecast_end_date is not None:
        confirmed_mask &= actual["xDate"].le(
            _as_of_date(forecast_end_date)
        )
    confirmed = actual.loc[confirmed_mask].copy()
    if confirmed.empty:
        return pd.DataFrame()

    cadences = infer_symbol_cadences(actual, as_of_date=as_of_date)
    confirmed = confirmed.merge(
        cadences[["symbol", "cadence_months"]],
        on="symbol",
        how="left",
    )
    if "special_candidate" not in confirmed.columns:
        confirmed["special_candidate"] = False
    confirmed["target_period_end"] = confirmed["period_end_date"]
    confirmed["status"] = "CONFIRMED"
    confirmed["watch_status"] = "CONFIRMED"
    confirmed["forecast_sequence"] = pd.NA
    confirmed["expected_announce_date"] = pd.NaT
    confirmed["announce_window_start"] = pd.NaT
    confirmed["announce_window_end"] = pd.NaT
    confirmed["expected_xd"] = pd.NaT
    confirmed["expected_xd_window_start"] = pd.NaT
    confirmed["expected_xd_window_end"] = pd.NaT
    confirmed["expected_dps"] = np.nan
    confirmed["effective_xd"] = confirmed["xDate"]
    confirmed["inclusion_xd"] = confirmed["xDate"]
    confirmed["effective_dps"] = confirmed["adjustedDPS"]
    confirmed["event_probability"] = 1.0
    confirmed["timing_family"] = pd.NA
    confirmed["timing_family_source"] = "actual_api_event"
    confirmed["timing_method"] = "actual_api_event"
    confirmed["dps_method"] = "actual_api_event"
    confirmed["date_confidence"] = "CONFIRMED"
    confirmed["amount_confidence"] = "CONFIRMED"
    confirmed["candidate_status"] = "CONFIRMED"
    confirmed["review_reason"] = ""
    confirmed["logical_event_key"] = [
        _target_key(symbol, cycle, period_end)
        or f"{symbol}|{cycle}|XD|{x_date:%Y-%m-%d}"
        for symbol, cycle, period_end, x_date in confirmed[
            ["symbol", "cycle", "period_end_date", "xDate"]
        ].itertuples(index=False, name=None)
    ]
    confirmed["event_key"] = (
        confirmed["logical_event_key"] + "|CONFIRMED"
    )
    return confirmed.reset_index(drop=True)


def _known_confirmed_target_keys(actual_events, as_of_date):
    confirmed = build_confirmed_events(actual_events, as_of_date=as_of_date)
    if confirmed.empty:
        return set()
    return set(
        confirmed.loc[
            confirmed["target_period_end"].notna(),
            "logical_event_key",
        ]
    )


def _announced_events_as_of(actual_events, as_of_date):
    """Return valid API events already announced by the as-of date.

    This intentionally includes events whose XD date has already passed.
    They are not future rows for the confirmed export, but they still must
    suppress a duplicate forecast for the same dividend slot.
    """
    if actual_events is None or actual_events.empty:
        return pd.DataFrame()

    actual = actual_events.copy()
    required = {"symbol", "announceDate", "xDate", "adjustedDPS"}
    if not required.issubset(actual.columns):
        return pd.DataFrame()

    actual["symbol"] = (
        actual["symbol"].astype("string").str.strip().str.upper()
    )
    actual["announceDate"] = _calendar_date(actual["announceDate"])
    actual["xDate"] = _calendar_date(actual["xDate"])
    actual["adjustedDPS"] = pd.to_numeric(
        actual["adjustedDPS"],
        errors="coerce",
    )
    if "period_end_date" not in actual.columns:
        if "endOperationPeriod" in actual.columns:
            actual["period_end_date"] = _calendar_date(
                actual["endOperationPeriod"]
            )
        else:
            actual["period_end_date"] = pd.NaT
    else:
        actual["period_end_date"] = _calendar_date(
            actual["period_end_date"]
        )

    as_of_date = _as_of_date(as_of_date)
    return actual.loc[
        actual["symbol"].notna()
        & actual["announceDate"].notna()
        & actual["announceDate"].le(as_of_date)
        & actual["xDate"].notna()
        & actual["adjustedDPS"].gt(0)
    ].copy()


def _candidate_has_announced_event(candidate, announced_events):
    """Check whether a forecast candidate is already represented by API data."""
    if announced_events.empty:
        return False

    symbol = str(candidate.get("symbol", "")).strip().upper()
    if not symbol:
        return False
    matches = announced_events.loc[
        announced_events["symbol"].eq(symbol)
    ].copy()
    if matches.empty:
        return False

    candidate_cycle = candidate.get("cycle", pd.NA)
    target_period_end = candidate.get("target_period_end", pd.NaT)
    if pd.notna(target_period_end):
        target_period_end = pd.Timestamp(target_period_end).normalize()
        period_match = matches["period_end_date"].eq(target_period_end)
        if pd.notna(candidate_cycle) and "cycle" in matches.columns:
            period_match &= matches["cycle"].eq(candidate_cycle)
        if period_match.any():
            return True

    expected_xd = candidate.get("expected_xd", pd.NaT)
    expected_xd_window_start = candidate.get(
        "expected_xd_window_start",
        pd.NaT,
    )
    expected_xd_window_end = candidate.get(
        "expected_xd_window_end",
        pd.NaT,
    )

    if pd.notna(expected_xd):
        expected_xd = pd.Timestamp(expected_xd).normalize()
        same_expected_year = matches["xDate"].dt.year.eq(
            expected_xd.year
        )
    else:
        same_expected_year = pd.Series(
            False,
            index=matches.index,
        )

    if pd.notna(expected_xd_window_start) and pd.notna(
        expected_xd_window_end
    ):
        window_start = pd.Timestamp(expected_xd_window_start).normalize()
        window_end = pd.Timestamp(expected_xd_window_end).normalize()
        in_expected_window = matches["xDate"].between(
            window_start,
            window_end,
            inclusive="both",
        )
    else:
        in_expected_window = same_expected_year

    # Prefer the explicit cycle/year identity when available.  The XD
    # window remains a generic fallback for rows whose cycle labels changed
    # because operation-period metadata was missing or nonstandard.
    if pd.notna(candidate_cycle) and "cycle" in matches.columns:
        same_cycle = matches["cycle"].eq(candidate_cycle)
        if (same_cycle & same_expected_year).any():
            return True

    return bool(in_expected_window.any())


def _known_announced_forecast_keys(features, actual_events, as_of_date):
    announced = _announced_events_as_of(actual_events, as_of_date)
    if announced.empty or features.empty:
        return set()

    return {
        row["logical_event_key"]
        for _, row in features.iterrows()
        if _candidate_has_announced_event(row, announced)
    }


def generate_next_cycle_forecasts(features, actual_events, as_of_date):
    """Generate one eligible, unannounced forecast per target period."""
    as_of_date = _as_of_date(as_of_date)
    if features.empty:
        return pd.DataFrame()
    known_confirmed_keys = _known_confirmed_target_keys(
        actual_events,
        as_of_date=as_of_date,
    )
    known_confirmed_keys.update(
        _known_announced_forecast_keys(
            features,
            actual_events,
            as_of_date=as_of_date,
        )
    )
    candidates = features.loc[
        features["candidate_status"].eq("FORECAST")
        & (
            features["expected_xd"].gt(as_of_date)
            | features["expected_xd_window_end"].gt(as_of_date)
        )
        & ~features["logical_event_key"].isin(known_confirmed_keys)
    ].copy()
    if candidates.empty:
        return pd.DataFrame()
    records = [
        _forecast_record(row, as_of_date=as_of_date)
        for _, row in candidates.iterrows()
    ]
    return pd.DataFrame(records).sort_values(
        ["effective_xd", "symbol", "cycle"],
        kind="stable",
    ).reset_index(drop=True)


def generate_horizon_forecasts(
    features,
    actual_events,
    as_of_date,
    forecast_end_date,
):
    """Compatibility wrapper retaining generic forecasts relevant to a horizon."""
    as_of_date = _as_of_date(as_of_date)
    forecast_end_date = _as_of_date(forecast_end_date)
    if forecast_end_date <= as_of_date:
        raise ValueError("forecast_end_date must be after as_of_date")

    forecasts = generate_next_cycle_forecasts(
        features,
        actual_events,
        as_of_date=as_of_date,
    )
    if forecasts.empty:
        return forecasts
    return forecasts.loc[
        forecasts["expected_xd_window_start"].le(forecast_end_date)
    ].reset_index(drop=True)


def _review_from_features(features, confirmed, as_of_date):
    if features.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    known_confirmed_keys = set()
    if not confirmed.empty:
        known_confirmed_keys = set(
            confirmed.loc[
                confirmed["target_period_end"].notna(),
                "logical_event_key",
            ]
        )
    review = features.loc[
        features["candidate_status"].eq("REVIEW")
        & ~features["logical_event_key"].isin(known_confirmed_keys)
    ].copy()
    if review.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    review["as_of_date"] = _as_of_date(as_of_date)
    review["model_version"] = MODEL_VERSION
    review["event_key"] = review["logical_event_key"] + "|REVIEW"
    return review.loc[:, REVIEW_COLUMNS].sort_values(
        ["target_period_end", "symbol", "cycle"],
        kind="stable",
    ).reset_index(drop=True)


def _event_export_from_components(forecasts, confirmed, as_of_date):
    frames = [frame for frame in [confirmed, forecasts] if not frame.empty]
    if not frames:
        return _empty_event_export()
    export = pd.concat(frames, ignore_index=True, sort=False)
    export = export.drop_duplicates("event_key", keep="first").copy()
    export["as_of_date"] = _as_of_date(as_of_date)
    export["output_scope"] = (
        "next_forecast_per_symbol_timing_family_plus_all_confirmed"
    )
    export["model_version"] = MODEL_VERSION
    export = export.sort_values(
        ["effective_xd", "status", "symbol"],
        kind="stable",
    ).reset_index(drop=True)

    if not export["effective_xd"].gt(_as_of_date(as_of_date)).all():
        overdue_forecasts = export["status"].eq("FORECAST") & export[
            "effective_xd"
        ].le(_as_of_date(as_of_date))
        if not export.loc[overdue_forecasts, "watch_status"].eq(
            "OVERDUE"
        ).all():
            raise AssertionError(
                "past forecast XD must be explicitly marked OVERDUE"
            )
    if not export["inclusion_xd"].gt(_as_of_date(as_of_date)).all():
        raise AssertionError("event inclusion dates must be after as_of_date")
    forecast_rows = export["status"].eq("FORECAST")
    if forecast_rows.any() and not export.loc[
        forecast_rows,
        "expected_announce_date",
    ].lt(export.loc[forecast_rows, "effective_xd"]).all():
        raise AssertionError("forecast announcement must precede forecast XD")
    if export["event_key"].duplicated().any():
        raise AssertionError("export event keys must be unique")
    return export


def build_dividend_export_bundle(
    events,
    as_of_date,
    timing_recent_n=2,
    dps_recent_n=3,
    occurrence_lookback_years=8,
    occurrence_prior_alpha=1.0,
    occurrence_prior_beta=1.0,
    regime_recent_n=5,
    min_cadence_gaps=2,
    minimum_timing_observations=1,
):
    """Build the contract-agnostic cash-event and review snapshots."""
    as_of_date = _as_of_date(as_of_date)
    features = build_recent_cycle_features(
        events,
        as_of_date=as_of_date,
        timing_recent_n=timing_recent_n,
        dps_recent_n=dps_recent_n,
        occurrence_lookback_years=occurrence_lookback_years,
        occurrence_prior_alpha=occurrence_prior_alpha,
        occurrence_prior_beta=occurrence_prior_beta,
        regime_recent_n=regime_recent_n,
        min_cadence_gaps=min_cadence_gaps,
        minimum_timing_observations=minimum_timing_observations,
    )
    forecasts = generate_next_cycle_forecasts(
        features,
        events,
        as_of_date=as_of_date,
    )
    confirmed = build_confirmed_events(
        events,
        as_of_date=as_of_date,
        regime_recent_n=regime_recent_n,
    )
    return {
        "events": _event_export_from_components(
            forecasts,
            confirmed,
            as_of_date=as_of_date,
        ),
        "review": _review_from_features(
            features,
            confirmed,
            as_of_date=as_of_date,
        ),
    }


def build_excel_event_export(
    events,
    as_of_date,
    timing_recent_n=2,
    dps_recent_n=3,
    occurrence_lookback_years=8,
    occurrence_prior_alpha=1.0,
    occurrence_prior_beta=1.0,
    regime_recent_n=5,
    min_cadence_gaps=2,
    minimum_timing_observations=1,
):
    """Return the contract-agnostic cash-event input for the Excel model."""
    return build_dividend_export_bundle(
        events,
        as_of_date=as_of_date,
        timing_recent_n=timing_recent_n,
        dps_recent_n=dps_recent_n,
        occurrence_lookback_years=occurrence_lookback_years,
        occurrence_prior_alpha=occurrence_prior_alpha,
        occurrence_prior_beta=occurrence_prior_beta,
        regime_recent_n=regime_recent_n,
        min_cadence_gaps=min_cadence_gaps,
        minimum_timing_observations=minimum_timing_observations,
    )["events"]


def build_forecast_review_export(
    events,
    as_of_date,
    timing_recent_n=2,
    dps_recent_n=3,
    occurrence_lookback_years=8,
    occurrence_prior_alpha=1.0,
    occurrence_prior_beta=1.0,
    regime_recent_n=5,
    min_cadence_gaps=2,
    minimum_timing_observations=1,
):
    """Return plausible target periods that lack timing or DPS evidence."""
    return build_dividend_export_bundle(
        events,
        as_of_date=as_of_date,
        timing_recent_n=timing_recent_n,
        dps_recent_n=dps_recent_n,
        occurrence_lookback_years=occurrence_lookback_years,
        occurrence_prior_alpha=occurrence_prior_alpha,
        occurrence_prior_beta=occurrence_prior_beta,
        regime_recent_n=regime_recent_n,
        min_cadence_gaps=min_cadence_gaps,
        minimum_timing_observations=minimum_timing_observations,
    )["review"]


def build_active_event_table(
    events,
    as_of_date,
    forecast_end_date,
    timing_recent_n=2,
    dps_recent_n=3,
    occurrence_lookback_years=8,
    occurrence_prior_alpha=1.0,
    occurrence_prior_beta=1.0,
    regime_recent_n=5,
    min_cadence_gaps=2,
    minimum_timing_observations=1,
):
    """Compatibility view of generic events whose timing overlaps a horizon."""
    as_of_date = _as_of_date(as_of_date)
    forecast_end_date = _as_of_date(forecast_end_date)
    features = build_recent_cycle_features(
        events,
        as_of_date=as_of_date,
        timing_recent_n=timing_recent_n,
        dps_recent_n=dps_recent_n,
        occurrence_lookback_years=occurrence_lookback_years,
        occurrence_prior_alpha=occurrence_prior_alpha,
        occurrence_prior_beta=occurrence_prior_beta,
        regime_recent_n=regime_recent_n,
        min_cadence_gaps=min_cadence_gaps,
        minimum_timing_observations=minimum_timing_observations,
    )
    forecasts = generate_horizon_forecasts(
        features,
        events,
        as_of_date=as_of_date,
        forecast_end_date=forecast_end_date,
    )
    confirmed = build_confirmed_events(
        events,
        as_of_date=as_of_date,
        forecast_end_date=forecast_end_date,
        regime_recent_n=regime_recent_n,
    )
    active = _event_export_from_components(
        forecasts,
        confirmed,
        as_of_date=as_of_date,
    )
    if active.empty:
        return active
    active["forecast_end_date"] = forecast_end_date
    active["model_version"] = MODEL_VERSION
    forecast_rows = active["status"].eq("FORECAST")
    if forecast_rows.any() and not active.loc[
        forecast_rows,
        "expected_xd_window_start",
    ].le(forecast_end_date).all():
        raise AssertionError(
            "forecast timing windows must overlap forecast_end_date"
        )
    return active
