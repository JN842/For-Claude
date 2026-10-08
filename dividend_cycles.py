import numpy as np
import pandas as pd


def _calendar_date(series):
    return pd.to_datetime(
        series.astype("string").str.slice(0, 10),
        errors="coerce",
    )


def _calendar_month_span(start, end):
    valid = start.notna() & end.notna() & end.ge(start)
    result = pd.Series(pd.NA, index=start.index, dtype="Int64")
    result.loc[valid] = (
        (end.loc[valid].dt.year - start.loc[valid].dt.year) * 12
        + (end.loc[valid].dt.month - start.loc[valid].dt.month)
        + 1
    )
    return result


def build_dividend_events(dividend_normalized, as_of_date=None):
    required_columns = {
        "symbol",
        "announceDate",
        "adjustedDPS",
        "xDate",
    }
    missing_columns = required_columns.difference(dividend_normalized.columns)
    if missing_columns:
        raise ValueError(
            f"Missing required columns: {sorted(missing_columns)}"
        )

    events = dividend_normalized.copy()

    if as_of_date is None:
        as_of_date = pd.Timestamp.today().normalize()
    else:
        as_of_date = pd.Timestamp(as_of_date).normalize()

    events["symbol"] = (
        events["symbol"].astype("string").str.strip().str.upper()
    )
    events["announceDate"] = _calendar_date(events["announceDate"])
    events["xDate"] = _calendar_date(events["xDate"])
    events["adjustedDPS"] = pd.to_numeric(
        events["adjustedDPS"],
        errors="coerce",
    )

    for column in ["beginOperationPeriod", "endOperationPeriod"]:
        if column in events.columns:
            events[column] = _calendar_date(events[column])
        else:
            events[column] = pd.NaT

    events = events.loc[
        events["symbol"].notna()
        & events["xDate"].notna()
        & events["adjustedDPS"].notna()
        & events["adjustedDPS"].gt(0)
    ].copy()

    events = (
        events
        .drop_duplicates()
        .sort_values(["symbol", "xDate", "announceDate"])
        .reset_index(drop=True)
    )

    events["xd_year"] = events["xDate"].dt.year
    events["xd_month"] = events["xDate"].dt.month
    events["xd_quarter"] = "Q" + events["xDate"].dt.quarter.astype("string")
    events["period_start_date"] = events["beginOperationPeriod"]
    events["period_end_date"] = events["endOperationPeriod"]
    events["period_end_month"] = events["period_end_date"].dt.month
    events["operation_period_months"] = _calendar_month_span(
        events["period_start_date"],
        events["period_end_date"],
    )

    has_period_end = events["period_end_date"].notna()
    period_cycle = (
        "P"
        + events["period_end_month"].astype("Int64").astype("string").str.zfill(2)
    )
    xd_month_fallback_cycle = (
        "XD_M"
        + events["xd_month"].astype("Int64").astype("string").str.zfill(2)
    )
    events["cycle"] = np.where(
        has_period_end,
        period_cycle,
        xd_month_fallback_cycle,
    )
    events["cycle_source"] = np.where(
        has_period_end,
        "operation_period",
        "xd_month_fallback",
    )

    events["announce_to_xd_days"] = (
        events["xDate"] - events["announceDate"]
    ).dt.days

    events["status"] = np.where(
        events["xDate"].gt(as_of_date),
        "CONFIRMED",
        "HISTORICAL",
    )

    return events
