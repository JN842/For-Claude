import numpy as np
import pandas as pd


def _calendar_date(series):
    return pd.to_datetime(
        series.astype("string").str.slice(0, 10),
        errors="coerce",
    )


def _as_of_date(value):
    return pd.Timestamp(value).normalize()


def _month_delta(start, end):
    return (
        (end.dt.year - start.dt.year) * 12
        + (end.dt.month - start.dt.month)
    )


def _prepare_events(events):
    prepared = events.copy()
    prepared["symbol"] = (
        prepared["symbol"].astype("string").str.strip().str.upper()
    )
    prepared["xDate"] = _calendar_date(prepared["xDate"])
    prepared["announceDate"] = _calendar_date(prepared["announceDate"])
    prepared["adjustedDPS"] = pd.to_numeric(
        prepared["adjustedDPS"],
        errors="coerce",
    )

    for raw_column, normalized_column in [
        ("beginOperationPeriod", "period_start_date"),
        ("endOperationPeriod", "period_end_date"),
    ]:
        if normalized_column in prepared.columns:
            prepared[normalized_column] = _calendar_date(
                prepared[normalized_column]
            )
        elif raw_column in prepared.columns:
            prepared[normalized_column] = _calendar_date(
                prepared[raw_column]
            )
        else:
            prepared[normalized_column] = pd.NaT

    if "operation_period_months" not in prepared.columns:
        valid = (
            prepared["period_start_date"].notna()
            & prepared["period_end_date"].notna()
            & prepared["period_end_date"].ge(prepared["period_start_date"])
        )
        prepared["operation_period_months"] = pd.Series(
            pd.NA,
            index=prepared.index,
            dtype="Int64",
        )
        prepared.loc[valid, "operation_period_months"] = (
            (prepared.loc[valid, "period_end_date"].dt.year
             - prepared.loc[valid, "period_start_date"].dt.year)
            * 12
            + (prepared.loc[valid, "period_end_date"].dt.month
               - prepared.loc[valid, "period_start_date"].dt.month)
            + 1
        )
    else:
        prepared["operation_period_months"] = pd.to_numeric(
            prepared["operation_period_months"],
            errors="coerce",
        ).astype("Int64")

    if "cycle" not in prepared.columns:
        has_period_end = prepared["period_end_date"].notna()
        period_cycle = (
            "P"
            + prepared["period_end_date"].dt.month.astype("Int64")
            .astype("string")
            .str.zfill(2)
        )
        xd_cycle = (
            "XD_M"
            + prepared["xDate"].dt.month.astype("Int64")
            .astype("string")
            .str.zfill(2)
        )
        prepared["cycle"] = np.where(
            has_period_end,
            period_cycle,
            xd_cycle,
        )

    return prepared


def annotate_observed_regime(events, as_of_date, regime_recent_n=5):
    as_of_date = _as_of_date(as_of_date)
    annotated = _prepare_events(events)
    historical = annotated.loc[
        annotated["xDate"].le(as_of_date)
        & annotated["operation_period_months"].notna()
    ].copy()
    recent = (
        historical.sort_values("xDate")
        .groupby("symbol", group_keys=False)
        .tail(int(regime_recent_n))
    )
    duration_stats = (
        recent.groupby(["symbol", "operation_period_months"], as_index=False)
        .agg(
            duration_count=("xDate", "size"),
            latest_duration_xd=("xDate", "max"),
        )
        .sort_values(
            [
                "symbol",
                "duration_count",
                "latest_duration_xd",
                "operation_period_months",
            ],
            ascending=[True, False, False, True],
            kind="stable",
        )
        .drop_duplicates("symbol")
        .rename(
            columns={"operation_period_months": "regular_period_months"}
        )
    )
    annotated = annotated.merge(
        duration_stats[["symbol", "regular_period_months"]],
        on="symbol",
        how="left",
    )
    annotated["regular_period_months"] = pd.to_numeric(
        annotated["regular_period_months"],
        errors="coerce",
    ).astype("Int64")
    annotated["period_is_regular"] = (
        annotated["operation_period_months"].notna()
        & annotated["regular_period_months"].notna()
        & annotated["operation_period_months"].eq(
            annotated["regular_period_months"]
        )
    )
    annotated["timing_eligible"] = (
        annotated["period_end_date"].notna()
        & annotated["announceDate"].notna()
        & annotated["xDate"].notna()
        & annotated["xDate"].gt(annotated["announceDate"])
    )
    annotated["dps_eligible"] = (
        annotated["period_is_regular"]
        & annotated["adjustedDPS"].gt(0)
    )
    annotated["forecast_eligible"] = annotated["dps_eligible"]
    annotated["exclusion_reason"] = ""
    timing_only = (
        annotated["timing_eligible"]
        & annotated["regular_period_months"].notna()
        & ~annotated["period_is_regular"]
    )
    annotated.loc[
        timing_only,
        "exclusion_reason",
    ] = "timing_only_nonstandard_period"
    missing_period = annotated["period_end_date"].isna()
    annotated.loc[
        missing_period,
        "exclusion_reason",
    ] = "missing_operation_period"

    return annotated


def infer_symbol_cadences(
    annotated_events,
    as_of_date,
    min_cadence_gaps=2,
):
    as_of_date = _as_of_date(as_of_date)
    symbols = (
        annotated_events["symbol"]
        .astype("string")
        .dropna()
        .drop_duplicates()
        .sort_values()
    )
    historical = annotated_events.loc[
        annotated_events["xDate"].le(as_of_date)
        & annotated_events["forecast_eligible"].fillna(False)
        & annotated_events["period_end_date"].notna()
    ].copy()
    rows = []
    for symbol in symbols:
        group = historical.loc[historical["symbol"].eq(symbol)]
        if group.empty:
            rows.append(
                {
                    "symbol": symbol,
                    "cadence_months": pd.NA,
                    "cadence_gap_count": 0,
                    "latest_regular_period_end": pd.NaT,
                }
            )
            continue
        period_ends = (
            group["period_end_date"].drop_duplicates().sort_values()
        )
        gap_months = _month_delta(
            period_ends.shift(),
            period_ends,
        ).dropna()
        positive_gaps = gap_months.loc[gap_months.gt(0)]
        cadence = pd.NA
        if len(positive_gaps) >= int(min_cadence_gaps):
            cadence = int(round(float(positive_gaps.median())))
        rows.append(
            {
                "symbol": symbol,
                "cadence_months": cadence,
                "cadence_gap_count": int(len(positive_gaps)),
                "latest_regular_period_end": period_ends.max(),
            }
        )
    result = pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "cadence_months",
            "cadence_gap_count",
            "latest_regular_period_end",
        ],
    )
    result["cadence_months"] = pd.to_numeric(
        result["cadence_months"],
        errors="coerce",
    ).astype("Int64")
    result["latest_regular_period_end"] = pd.to_datetime(
        result["latest_regular_period_end"],
        errors="coerce",
    )
    return result


def _timing_family_count(cadence_months, anchor):
    if pd.isna(cadence_months) or int(cadence_months) <= 0:
        return 0
    seen_months = set()
    cursor = pd.Timestamp(anchor)
    while cursor.month not in seen_months:
        seen_months.add(cursor.month)
        cursor = cursor + pd.DateOffset(months=int(cadence_months))
    return len(seen_months)


def _cluster_circular_days(events, family_count):
    ordered = events.sort_values(["xd_dayofyear", "xDate"], kind="stable")
    if ordered.empty:
        return pd.Series(dtype="string")

    family_count = max(1, min(int(family_count), len(ordered)))
    if family_count == 1:
        return pd.Series(
            "F01",
            index=ordered.index,
            dtype="string",
        )

    day_values = ordered["xd_dayofyear"].astype(int).tolist()
    calendar_days = int(
        max(
            pd.Timestamp(year=int(year), month=12, day=31).dayofyear
            for year in ordered["xDate"].dt.year
        )
    )
    gaps = [
        day_values[position + 1] - day_values[position]
        for position in range(len(day_values) - 1)
    ]
    gaps.append(day_values[0] + calendar_days - day_values[-1])
    cut_positions = set(
        sorted(
            range(len(gaps)),
            key=lambda position: (gaps[position], position),
            reverse=True,
        )[:family_count]
    )
    start_position = min(
        ((position + 1) % len(day_values) for position in cut_positions),
        key=lambda position: day_values[position],
    )

    values = pd.Series(pd.NA, index=ordered.index, dtype="string")
    family_number = 1
    for offset in range(len(ordered)):
        position = (start_position + offset) % len(ordered)
        values.iloc[position] = f"F{family_number:02d}"
        if position in cut_positions:
            family_number += 1
    return values


def assign_timing_families(
    annotated_events,
    cadence_features,
    as_of_date,
):
    as_of_date = _as_of_date(as_of_date)
    assigned = annotated_events.copy()
    assigned["timing_family"] = pd.Series(
        pd.NA,
        index=assigned.index,
        dtype="string",
    )
    assigned["timing_family_source"] = ""
    cadence_by_symbol = cadence_features.set_index("symbol")
    historical = assigned.loc[
        assigned["xDate"].le(as_of_date)
        & assigned["timing_eligible"].fillna(False)
    ].copy()
    historical["xd_dayofyear"] = historical["xDate"].dt.dayofyear

    for symbol, group in historical.groupby("symbol", sort=True):
        cadence = (
            cadence_by_symbol.loc[symbol]
            if symbol in cadence_by_symbol.index
            else None
        )
        if cadence is not None and pd.notna(cadence["cadence_months"]):
            family_count = _timing_family_count(
                cadence["cadence_months"],
                cadence["latest_regular_period_end"],
            )
            labels = _cluster_circular_days(group, family_count)
            assigned.loc[labels.index, "timing_family"] = labels
            assigned.loc[
                labels.index,
                "timing_family_source",
            ] = "clustered_from_xd_history"
        else:
            for cycle, cycle_group in group.groupby("cycle", sort=True):
                label = f"C_{cycle}"
                assigned.loc[cycle_group.index, "timing_family"] = label
                assigned.loc[
                    cycle_group.index,
                    "timing_family_source",
                ] = "same_cycle_fallback"

    return assigned


def build_target_periods(annotated_events, cadence_features, as_of_date):
    as_of_date = _as_of_date(as_of_date)
    historical = annotated_events.loc[
        annotated_events["xDate"].le(as_of_date)
        & annotated_events["period_end_date"].notna()
    ].copy()
    cadence_by_symbol = cadence_features.set_index("symbol")
    rows = []
    for symbol, group in historical.groupby("symbol", sort=True):
        if symbol not in cadence_by_symbol.index:
            continue
        cadence = cadence_by_symbol.loc[symbol, "cadence_months"]
        if pd.isna(cadence) or int(cadence) <= 0:
            continue

        # `cycle` is an operation-period label.  It is not stable enough to
        # be the identity of a future dividend event: a one-off nine-month
        # period, a shortened period, or missing operation metadata can
        # create several cycle labels for the same recurring XD slot.
        # `timing_family` is the generic, data-derived event slot instead.
        family_groups = []
        labelled = group.loc[group["timing_family"].notna()].copy()
        if not labelled.empty:
            family_groups = list(
                labelled.groupby("timing_family", sort=True)
            )
        else:
            # This is only a fallback for symbols without an assignable
            # timing family.  It preserves a review path without inventing a
            # ticker-specific cycle table.
            family_groups = [
                (f"C_{cycle}", cycle_group)
                for cycle, cycle_group in group.groupby("cycle", sort=True)
            ]

        if not family_groups:
            continue

        latest_regular_end = cadence_by_symbol.loc[
            symbol,
            "latest_regular_period_end",
        ]
        family_count = _timing_family_count(cadence, latest_regular_end)
        family_count = max(1, int(family_count or len(family_groups)))
        annual_step_months = int(cadence) * family_count

        for timing_family, family_group in family_groups:
            regular_family = family_group.loc[
                family_group["period_is_regular"].fillna(False)
                & family_group["period_end_date"].notna()
            ].copy()
            source_group = (
                regular_family
                if not regular_family.empty
                else family_group.loc[family_group["period_end_date"].notna()]
            )
            if source_group.empty:
                continue

            representative = source_group.sort_values(
                ["period_end_date", "xDate"],
                kind="stable",
            ).iloc[-1]
            latest_period_end = pd.Timestamp(
                representative["period_end_date"]
            )
            representative_cycle = representative["cycle"]
            if pd.isna(representative_cycle):
                representative_cycle = str(timing_family)

            # Keep enough annual candidates for the feature layer to choose
            # the first candidate whose XD window is still open.  This is
            # essential when the period end has passed but the dividend XD
            # date is normally later (for example PTT's H2/interim event).
            candidate_count = max(
                2,
                int(as_of_date.year - latest_period_end.year) + 2,
            )
            for sequence in range(1, candidate_count + 1):
                target_end = latest_period_end + pd.DateOffset(
                    months=annual_step_months * sequence
                )
                rows.append(
                    {
                        "symbol": symbol,
                        "cycle": representative_cycle,
                        "timing_family": str(timing_family),
                        "target_period_end": target_end,
                        "cadence_months": int(cadence),
                        "slot_interval_months": annual_step_months,
                    }
                )

    if not rows:
        return pd.DataFrame(
            columns=[
                "symbol",
                "cycle",
                "timing_family",
                "target_period_end",
                "cadence_months",
                "slot_interval_months",
            ]
        )
    targets = pd.DataFrame(rows)
    return (
        targets.sort_values(
            ["symbol", "cycle", "target_period_end"],
            kind="stable",
        )
        .drop_duplicates(
            ["symbol", "cycle", "target_period_end"],
            keep="first",
        )
        .reset_index(drop=True)
    )
