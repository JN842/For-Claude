import pandas as pd
import pytest

from dividend_model import ModelParams, build_event_table, point_in_time, prepare_events
from dividend_run import backtest_snapshot, run_snapshot
from set50 import (
    change_membership,
    contract_expiry,
    default_instruments,
    instrument_dividends,
    membership_changes,
    membership_table,
    parse_instrument,
    xd_probability_in_range,
)
from synthetic import _row, annual, frame, quarterly, semiannual

AS_OF = pd.Timestamp("2026-10-08")
HORIZON = pd.Timestamp("2027-12-31")


def build(raw, as_of=AS_OF, horizon=HORIZON, **kwargs):
    return build_event_table(prepare_events(raw), as_of, horizon, **kwargs)


def forecasts(result, symbol=None):
    events = result["events"]
    events = events.loc[events["status"].eq("FORECAST")]
    if symbol:
        events = events.loc[events["symbol"].eq(symbol)]
    return events


# --- slots and forecasts ---------------------------------------------------

def test_semiannual_payer_has_two_slots_with_full_probability():
    # The 2026 final (XD Apr-2027) is announced after AS_OF, so unseen.
    result = build(frame(semiannual("PTT", range(2018, 2027))))
    slots = result["slots"]
    assert list(slots["slot"]) == ["M04", "M08"]
    ptt = forecasts(result, "PTT")
    assert set(ptt["slot"]) == {"M04", "M08"}
    assert ptt["probability"].eq(1.0).all()
    # Aug-2026 interim already went XD, so the next M08 forecast is 2027.
    assert ptt.loc[ptt["slot"].eq("M08"), "xd_date"].dt.year.tolist() == [2027]


def test_interim_and_full_year_final_are_separate_slots():
    # Interim covers Jan-Jun (6 months), final covers the full year
    # (12 months): different operation-period lengths must not merge them.
    interim = [
        row for row in semiannual("BCP", range(2021, 2026), interim_xd=(9, 1))
        if row["endOperationPeriod"].month == 6
    ]
    result = build(frame(interim, annual("BCP", range(2021, 2026))))
    windows = forecasts(result, "BCP")[["slot", "xd_window_start",
                                         "xd_window_end"]]
    assert set(windows["slot"]) == {"M04", "M09"}
    span = (windows["xd_window_end"] - windows["xd_window_start"]).dt.days
    assert span.max() <= 30


def test_quarterly_payer_has_four_slots():
    result = build(frame(quarterly("TISCO", range(2020, 2026))))
    assert len(result["slots"]) == 4


def test_year_end_slot_wraps_across_new_year():
    rows = []
    for year in range(2019, 2026):
        xd = pd.Timestamp(year, 12, 28) if year % 2 else pd.Timestamp(year + 1, 1, 4)
        rows.append(_row("WRAP", f"{year}-01-01", f"{year}-09-30",
                         xd - pd.Timedelta(days=20), xd, 1.0))
    result = build(pd.DataFrame(rows), horizon=pd.Timestamp("2027-06-30"))
    assert len(result["slots"]) == 1
    assert forecasts(result, "WRAP")["event_key"].tolist() == [
        "WRAP|M01|2026|FORECAST"
    ]


def test_discontinued_slot_is_reviewed_not_forecast_decades_ahead():
    # Paid an interim until 2015 then only finals: the old interim slot
    # must not produce a far-future forecast (the old BCP 2066 row).
    raw = frame(
        semiannual("BCP", range(2019, 2022), interim_xd=(9, 1)),
        annual("BCP", range(2022, 2026)),
    )
    result = build(raw, params=ModelParams(lookback_years=8))
    assert forecasts(result)["xd_date"].max() <= HORIZON
    review = result["review"].set_index("slot")
    assert review.loc["M09", "reason"] == "STALE_SLOT"


def test_one_off_special_is_reviewed():
    raw = frame(
        annual("SCC", range(2018, 2026)),
        [_row("SCC", "2023-01-01", "2023-06-30", "2023-10-01", "2023-10-15", 2.0)],
    )
    result = build(raw)
    assert result["review"]["reason"].tolist() == ["ONE_OFF"]
    assert set(forecasts(result)["slot"]) == {"M04"}


def test_missed_years_lower_probability():
    years = [2021, 2022, 2024]  # 2023 and 2025 skipped
    result = build(frame(annual("SKIP", years)), params=ModelParams(stale_years=3))
    assert forecasts(result, "SKIP")["probability"].iloc[0] == pytest.approx(3 / 5)


def test_universe_symbols_without_data_are_reviewed():
    result = build(frame(annual("SCC", range(2018, 2026))),
                   universe=["SCC", "NEWCO"])
    assert result["review"].set_index("symbol").loc["NEWCO", "reason"] == "NO_DATA"


# --- announced events ------------------------------------------------------

def test_announced_future_event_replaces_forecast():
    raw = frame(
        semiannual("PTT", range(2018, 2026)),
        [_row("PTT", "2026-07-01", "2026-12-31", "2026-10-01", "2027-04-22", 1.5)],
    )
    result = build(raw)
    events = result["events"].loc[result["events"]["symbol"].eq("PTT")]
    m04 = events.loc[events["slot"].eq("M04") & events["xd_date"].dt.year.eq(2027)]
    assert m04["status"].tolist() == ["CONFIRMED"]
    assert m04["dps"].tolist() == [1.5]


def test_announced_event_with_passed_xd_suppresses_forecast():
    as_of = pd.Timestamp("2026-09-20")
    result = build(frame(semiannual("PTT", range(2018, 2027))), as_of=as_of)
    m08 = forecasts(result, "PTT").loc[lambda f: f["slot"].eq("M08")]
    assert m08["xd_date"].dt.year.tolist() == [2027]


def test_unannounced_event_past_median_stays_as_overdue():
    # 2026 interim not announced; as_of is after its usual XD (Aug 25)
    # but inside the padded window.
    as_of = pd.Timestamp("2026-08-28")
    result = build(frame(semiannual("PTT", range(2018, 2026))), as_of=as_of)
    m08 = forecasts(result, "PTT").loc[lambda f: f["slot"].eq("M08")]
    row = m08.iloc[0]
    assert row["xd_date"] < as_of < row["xd_window_end"]
    assert row["watch_status"] == "OVERDUE"


def test_future_announcements_are_invisible_point_in_time():
    raw = frame(
        semiannual("PTT", range(2018, 2026)),
        [_row("PTT", "2026-07-01", "2026-12-31", "2027-03-01", "2027-04-22", 9.9)],
    )
    events = prepare_events(raw)
    full = build_event_table(events, AS_OF, HORIZON)
    truncated = build_event_table(point_in_time(events, AS_OF), AS_OF, HORIZON)
    pd.testing.assert_frame_equal(full["events"], truncated["events"])
    assert not full["events"]["dps"].eq(9.9).any()


def test_missing_announce_date_uses_board_date_then_imputes():
    raw = pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "xDate": ["2026-11-10", "2026-11-20"],
            "adjustedDPS": [1.0, 2.0],
            "announceDate": [None, None],
            "boardDate": ["2026-10-01", None],
        }
    )
    events = prepare_events(raw)
    assert events["announce_date"].tolist() == [
        pd.Timestamp("2026-10-01"), pd.Timestamp("2026-11-13"),
    ]
    assert events["announce_imputed"].tolist() == [False, True]


# --- contracts and membership ----------------------------------------------

def test_contract_expiry_rule_and_holidays():
    assert contract_expiry(2026, 12) == pd.Timestamp("2026-12-30")
    assert contract_expiry(2026, 12, ["2026-12-31"]) == pd.Timestamp("2026-12-29")
    assert contract_expiry(2027, 3) == pd.Timestamp("2027-03-30")
    # May 2027 ends on a Monday: last business day 31st, expiry Fri 28th.
    assert contract_expiry(2027, 5) == pd.Timestamp("2027-05-28")


def test_parse_instrument():
    assert parse_instrument("S50Z26") == ((2026, 12), None)
    assert parse_instrument("s50z26h27") == ((2026, 12), (2027, 3))
    with pytest.raises(ValueError):
        parse_instrument("S50H27Z26")
    with pytest.raises(ValueError):
        parse_instrument("SET50")


def test_default_instruments():
    assert default_instruments(AS_OF) == ["S50Z26", "S50H27", "S50Z26H27"]
    assert default_instruments("2026-12-30") == ["S50H27", "S50M27", "S50H27M27"]


def test_membership_changes_carry_forward():
    table = membership_table(["PTT", "THAI"], "2026H2", "2027H2")
    table = change_membership(table, "2027H1", add=["NEWCO"], remove=["THAI"])
    assert table.loc["THAI"].tolist() == [True, False, False]
    assert table.loc["NEWCO"].tolist() == [False, True, True]
    assert set(map(tuple, membership_changes(table).to_numpy())) == {
        ("2027H1", "NEWCO", "IN"), ("2027H1", "THAI", "OUT"),
    }


def _confirmed(symbol, xd, dps=1.0):
    return {
        "symbol": symbol, "slot": "X", "status": "CONFIRMED",
        "xd_date": pd.Timestamp(xd), "xd_window_start": pd.Timestamp(xd),
        "xd_window_end": pd.Timestamp(xd), "dps": dps, "probability": 1.0,
        "expected_dps": dps, "event_key": f"{symbol}|{xd}",
    }


def test_membership_is_checked_on_each_xd_date_and_spread_range():
    events = pd.DataFrame([
        _confirmed("THAI", "2026-12-15"),  # before Z26 expiry, 2026H2
        _confirmed("THAI", "2027-02-15"),  # inside the spread, 2027H1
        _confirmed("NEWCO", "2027-02-16", 2.0),
    ])
    table = change_membership(
        membership_table(["THAI"], "2026H2"), "2027H1",
        add=["NEWCO"], remove=["THAI"],
    )
    matrix = instrument_dividends(
        events, table, ["S50Z26", "S50H27", "S50Z26H27"], AS_OF
    )["matrix"]
    assert matrix.loc["THAI"].tolist() == [1.0, 1.0, 0.0]
    assert matrix.loc["NEWCO"].tolist() == [0.0, 2.0, 2.0]


def test_window_probability_splits_across_expiry():
    event = {
        "status": "FORECAST",
        "xd_date": pd.Timestamp("2026-12-30"),
        "xd_window_start": pd.Timestamp("2026-12-20"),
        "xd_window_end": pd.Timestamp("2027-01-09"),
    }
    expiry = pd.Timestamp("2026-12-30")
    before = xd_probability_in_range(event, AS_OF, expiry, AS_OF)
    after = xd_probability_in_range(event, expiry, pd.Timestamp("2027-03-30"), AS_OF)
    assert before == pytest.approx(0.5)
    assert before + after == pytest.approx(1.0)
    # Late in the window only the remaining part counts.
    late = pd.Timestamp("2027-01-01")
    assert xd_probability_in_range(event, late, pd.Timestamp("2027-03-30"), late) == 1.0


# --- end to end --------------------------------------------------------------

def test_run_snapshot_writes_workbook(tmp_path):
    raw = frame(semiannual("PTT", range(2018, 2026)), annual("SCC", range(2018, 2026)))
    table = membership_table(["PTT", "SCC"], "2026H2")
    snapshot = run_snapshot(raw, AS_OF, table, output_dir=tmp_path)
    with pd.ExcelFile(snapshot["path"]) as workbook:
        sheet_names = workbook.sheet_names
    assert sheet_names == [
        "Carry", "Instruments", "Detail", "Events", "Membership", "Review",
        "Run_Info",
    ]
    assert snapshot["path"].name == "dividend_20261008_base.xlsx"


def test_backtest_regular_history_is_exact():
    raw = frame(semiannual("PTT", range(2015, 2027)), quarterly("TISCO", range(2015, 2027)))
    table = membership_table(["PTT", "TISCO"], "2024H1", "2026H2")
    result = backtest_snapshot(raw, "2024-10-01", table)
    assert result["carry"]["error"].abs().max() == pytest.approx(0.0)
    assert result["events"]["outcome"].eq("HIT").all()
    assert result["events"]["xd_error_days"].abs().max() <= 1


def test_backtest_reports_membership_and_missed_dividend():
    raw = frame(annual("SCC", range(2015, 2023)), annual("PTT", range(2015, 2027)))
    bet = membership_table(["PTT", "SCC"], "2024H1", "2026H2")
    actual = change_membership(bet, "2025H1", remove=["PTT"])
    result = backtest_snapshot(raw, "2024-01-15", bet, ["S50M24"],
                               params=ModelParams(stale_years=3),
                               actual_membership=actual)
    carry = result["carry"].set_index("symbol")
    assert carry.loc["SCC", "forecast"] > 0
    assert carry.loc["SCC", "actual"] == 0
    assert result["events"].set_index("symbol").loc["SCC", "outcome"] == "NO_EVENT"


# --- workbook link ----------------------------------------------------------

def test_default_instruments_without_spreads():
    assert default_instruments(AS_OF, quarterly=4, spreads=False) == [
        "S50Z26", "S50H27", "S50M27", "S50U27",
    ]


def test_link_splits_carry_by_xd_half_year(tmp_path):
    from dividend_run import link_tables

    raw = frame(semiannual("PTT", range(2018, 2027)))
    table = change_membership(
        membership_table(["PTT", "THAI"], "2026H2", "2027H1"), "2027H1",
        remove=["THAI"],
    )
    snapshot = run_snapshot(raw, AS_OF, table, ["S50H27", "S50U27"],
                            output_dir=tmp_path)
    tables = link_tables(snapshot)
    carry = tables["carry"].set_index("key")["expected_dps"]
    # Final (Apr) falls in 2027H1, interim (Aug) in 2027H2.
    assert carry.to_dict() == {
        "PTT|S50U27|2027H1": 1.2, "PTT|S50U27|2027H2": 0.8,
    }
    members = tables["membership"].set_index("member_key")["is_member"]
    assert members["THAI|2026H2"] == 1 and members["THAI|2027H1"] == 0
    link = pd.read_excel(snapshot["link_path"], sheet_name="Link")
    assert list(link.columns[:6]) == [
        "symbol", "instrument", "index_period", "expected_dps", "key", "universe",
    ]
    assert list(link.columns[7:13]) == [
        "member_symbol", "member_period", "is_member", "member_key", "item", "value",
    ]


def test_read_holidays(tmp_path):
    from openpyxl import Workbook

    from dividend_run import read_holidays

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Other"
    sheet["A1"] = "SET holidays"
    sheet["A2"] = pd.Timestamp("2026-12-31").to_pydatetime()
    sheet["A3"] = "2027-01-01"
    workbook.save(tmp_path / "tq.xlsx")
    holidays = read_holidays(tmp_path / "tq.xlsx")
    assert holidays == [pd.Timestamp("2026-12-31"), pd.Timestamp("2027-01-01")]
    assert contract_expiry(2026, 12, holidays) == pd.Timestamp("2026-12-29")
