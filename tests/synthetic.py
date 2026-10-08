"""Synthetic API-shaped dividend histories used by the tests."""

import pandas as pd


def _row(symbol, begin, end, announce, xd, dps):
    return {
        "symbol": symbol,
        "beginOperationPeriod": pd.Timestamp(begin),
        "endOperationPeriod": pd.Timestamp(end),
        "announceDate": pd.Timestamp(announce),
        "xDate": pd.Timestamp(xd),
        "adjustedDPS": dps,
    }


def semiannual(symbol, years, interim_xd=(8, 25), final_xd=(4, 20), dps=(0.8, 1.2)):
    """Interim Jan-Jun and final Jul-Dec, final paid the following year."""
    rows = []
    for year in years:
        rows.append(_row(
            symbol, f"{year}-01-01", f"{year}-06-30",
            pd.Timestamp(year, interim_xd[0], interim_xd[1]) - pd.Timedelta(days=14),
            pd.Timestamp(year, *interim_xd), dps[0],
        ))
        rows.append(_row(
            symbol, f"{year}-07-01", f"{year}-12-31",
            pd.Timestamp(year + 1, final_xd[0], final_xd[1]) - pd.Timedelta(days=50),
            pd.Timestamp(year + 1, *final_xd), dps[1],
        ))
    return rows


def annual(symbol, years, xd=(4, 25), dps=1.0):
    return [
        _row(
            symbol, f"{year}-01-01", f"{year}-12-31",
            pd.Timestamp(year + 1, *xd) - pd.Timedelta(days=50),
            pd.Timestamp(year + 1, *xd), dps,
        )
        for year in years
    ]


def quarterly(symbol, years, dps=0.25):
    rows = []
    for year in years:
        for quarter_end, xd in [
            ((3, 31), (5, 20)), ((6, 30), (8, 20)),
            ((9, 30), (11, 20)), ((12, 31), (2, 20)),
        ]:
            end = pd.Timestamp(year, *quarter_end)
            begin = (end - pd.offsets.QuarterBegin(startingMonth=1)).normalize()
            xd_year = year + 1 if quarter_end[0] == 12 else year
            x_date = pd.Timestamp(xd_year, *xd)
            rows.append(_row(
                symbol, begin, end, x_date - pd.Timedelta(days=12), x_date, dps,
            ))
    return rows


def frame(*row_lists):
    rows = [row for row_list in row_lists for row in row_list]
    return pd.DataFrame(rows)
