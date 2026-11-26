"""Daily SET prices for the vine-copula strategy.

``fetch_prices`` downloads total-return adjusted closes (dividends and
splits folded in) from Yahoo Finance, where SET symbols carry a ``.BK``
suffix.  Any other source works as long as it produces the same shape:
a DataFrame indexed by date with one column per SET symbol.
"""

from pathlib import Path

import pandas as pd


# SET50 for 2026H2, same list as the dividend model README.
SET50_2026H2 = [
    "ADVANC", "AOT", "AWC", "BANPU", "BBL", "BCP", "BDMS", "BEM", "BH", "BJC",
    "CCET", "COM7", "CPALL", "CPF", "CPN", "CRC", "DELTA", "EGCO", "GPSC", "GULF",
    "HMPRO", "IVL", "KBANK", "KKP", "KTB", "KTC", "LH", "MINT", "MRDIYT", "MTC",
    "OR", "OSP", "PTT", "PTTEP", "PTTGC", "RATCH", "SCB", "SCC", "SCGP", "TCAP",
    "TFG", "THAI", "TIDLOR", "TISCO", "TLI", "TOP", "TRUE", "TTB", "TU", "WHA",
]

# SET100 names outside SET50.  Approximate: check against the official SET
# list for the half-year you trade, because the strategy picks pairs from
# whatever universe it is given.
SET100_EXTRA = [
    "AAV", "AMATA", "AP", "BA", "BAM", "BCH", "BCPG", "BGRIM", "BLA", "BTG",
    "BTS", "CBG", "CENTEL", "CHG", "CK", "CKP", "DOHOME", "EA", "ERW", "GLOBAL",
    "GUNKUL", "HANA", "ICHI", "IRPC", "ITC", "JMART", "JMT", "KCE", "M", "MEGA",
    "ORI", "PLANB", "PR9", "PRM", "PTG", "QH", "RCL", "SAWAD", "SIRI", "SISB",
    "SJWD", "SPALI", "SPRC", "STA", "STGT", "TASCO", "THG", "TOA", "TTW", "VGI",
]

SET100_2026H2 = SET50_2026H2 + SET100_EXTRA


def yahoo_ticker(symbol):
    return f"{str(symbol).strip().upper()}.BK"


def fetch_prices(symbols, start="2015-01-01", end=None, cache=None):
    """Adjusted daily closes, one column per symbol (without ``.BK``).

    Returns ``(prices, fetch_log)``.  Symbols Yahoo does not know come back
    with status ``EMPTY`` in the log and no column.  With ``cache`` (a CSV
    path) the download is saved and reused on the next call.
    """
    import yfinance as yf

    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols))
    if cache is not None and Path(cache).exists():
        prices = pd.read_csv(cache, index_col=0, parse_dates=True)
        missing = [s for s in symbols if s not in prices.columns]
        if not missing:
            return prices[symbols].loc[start:end], _log(prices, symbols)

    raw = yf.download(
        [yahoo_ticker(s) for s in symbols],
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    close = close.rename(columns=lambda t: str(t).removesuffix(".BK"))
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    close = close.dropna(how="all", axis=1).dropna(how="all", axis=0)
    prices = close.reindex(columns=[s for s in symbols if s in close.columns])
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        prices.to_csv(cache)
    return prices, _log(prices, symbols)


def _log(prices, symbols):
    rows = []
    for symbol in symbols:
        if symbol not in prices.columns or prices[symbol].notna().sum() == 0:
            rows.append({"symbol": symbol, "status": "EMPTY", "rows": 0,
                         "first": pd.NaT, "last": pd.NaT})
            continue
        series = prices[symbol].dropna()
        rows.append({"symbol": symbol, "status": "OK", "rows": len(series),
                     "first": series.index[0], "last": series.index[-1]})
    return pd.DataFrame(rows)
