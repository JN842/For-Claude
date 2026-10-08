import pandas as pd
import requests

import dividend_api


class _Response:
    def __init__(self, symbol):
        self.symbol = symbol

    def raise_for_status(self):
        if self.symbol == "BAD":
            raise requests.HTTPError("404")

    def json(self):
        if self.symbol == "EMPTY":
            return []
        returned = "OTHER" if self.symbol == "MIX" else self.symbol
        return [{"symbol": returned, "xDate": "2026-03-04T17:00:00Z",
                 "adjustedDPS": 1.0}]


def test_fetch_dividends_logs_each_symbol(monkeypatch):
    def fake_get(url, params, headers, timeout):
        assert headers == {"Authorization": "Bearer tok"}
        return _Response(url.split("/")[-2])

    monkeypatch.setattr(dividend_api.requests, "get", fake_get)
    raw, log = dividend_api.fetch_dividends(
        ["ptt", "EMPTY", "BAD", "MIX", "PTT"], token="tok"
    )
    assert log.set_index("symbol")["status"].to_dict() == {
        "PTT": "OK", "EMPTY": "EMPTY", "BAD": "ERROR", "MIX": "SYMBOL_MISMATCH",
    }
    # UTC 17:00 on the 4th is the 5th in Bangkok.
    assert raw.loc[raw["symbol"].eq("PTT"), "xDate"].iloc[0] == pd.Timestamp("2026-03-05")


def test_bearer_prefix_is_accepted(monkeypatch):
    seen = []
    monkeypatch.setattr(
        dividend_api.requests, "get",
        lambda url, params, headers, timeout: seen.append(headers) or _Response("PTT"),
    )
    dividend_api.get_dividend("PTT", " Bearer tok ")
    assert seen == [{"Authorization": "Bearer tok"}]
