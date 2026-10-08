"""Fetch XD corporate-action history from the internal API."""

import pandas as pd
import requests


BASE_URL = "http://172.16.8.98/api/corporateaction"
DATE_COLUMNS = ["announceDate", "xDate", "paymentDate", "recordDate", "boardDate"]


def get_dividend(symbol, token, base_url=BASE_URL, timeout=20):
    """All XD rows for one symbol, dates converted to Bangkok calendar dates."""
    token = str(token).strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    response = requests.get(
        f"{base_url}/{symbol.upper()}/XD",
        params={
            "beginDate": "",
            "endDate": "",
            "displayType": "selectedSymbol",
            "securityType": "",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    frame = pd.DataFrame(response.json())
    for column in DATE_COLUMNS:
        if column in frame.columns:
            frame[column] = (
                pd.to_datetime(frame[column], errors="coerce", utc=True)
                .dt.tz_convert("Asia/Bangkok")
                .dt.tz_localize(None)
            )
    return frame


def fetch_dividends(symbols, token, base_url=BASE_URL, timeout=20):
    """Fetch every symbol; one failure does not stop the others.

    Returns ``(dividend_raw, fetch_log)``.  ``fetch_log.status`` is OK,
    EMPTY, SYMBOL_MISMATCH or ERROR (with the exception in ``error``).
    """
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols))
    frames, log = [], []
    for symbol in symbols:
        try:
            frame = get_dividend(symbol, token, base_url, timeout)
            returned = set()
            if "symbol" in frame.columns:
                returned = {
                    str(value).strip().upper()
                    for value in frame["symbol"].dropna()
                }
            if frame.empty:
                status = "EMPTY"
            elif returned and returned != {symbol}:
                status = "SYMBOL_MISMATCH"
            else:
                status = "OK"
            if not frame.empty:
                frames.append(frame.assign(requested_symbol=symbol))
            log.append({"symbol": symbol, "status": status, "rows": len(frame),
                        "returned_symbols": ", ".join(sorted(returned)),
                        "error": None})
        except Exception as exc:  # keep going; the log reports it
            log.append({"symbol": symbol, "status": "ERROR", "rows": 0,
                        "returned_symbols": "", "error": repr(exc)})

    dividend_raw = (
        pd.concat(frames, ignore_index=True, sort=False)
        .drop_duplicates()
        .reset_index(drop=True)
        if frames else pd.DataFrame()
    )
    return dividend_raw, pd.DataFrame(log)
