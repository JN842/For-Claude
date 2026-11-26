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

# SET sector of each name above (SET classification: sector -> industry).
# Built by hand; check names whose classification may have changed.
SET_SECTOR = {
    # Resources / Energy & Utilities
    "BANPU": "ENERG", "BCP": "ENERG", "BCPG": "ENERG", "BGRIM": "ENERG",
    "CKP": "ENERG", "EA": "ENERG", "EGCO": "ENERG", "GPSC": "ENERG",
    "GULF": "ENERG", "GUNKUL": "ENERG", "IRPC": "ENERG", "OR": "ENERG",
    "PTG": "ENERG", "PTT": "ENERG", "PTTEP": "ENERG", "RATCH": "ENERG",
    "SPRC": "ENERG", "TOP": "ENERG", "TTW": "ENERG",
    # Financials
    "BBL": "BANK", "KBANK": "BANK", "KKP": "BANK", "KTB": "BANK",
    "SCB": "BANK", "TCAP": "BANK", "TISCO": "BANK", "TTB": "BANK",
    "BAM": "FIN", "JMT": "FIN", "KTC": "FIN", "MTC": "FIN", "SAWAD": "FIN",
    "TIDLOR": "FIN",
    "BLA": "INSUR", "TLI": "INSUR",
    # Services
    "BJC": "COMM", "COM7": "COMM", "CPALL": "COMM", "CRC": "COMM",
    "DOHOME": "COMM", "GLOBAL": "COMM", "HMPRO": "COMM", "JMART": "COMM",
    "MRDIYT": "COMM",
    "BCH": "HELTH", "BDMS": "HELTH", "BH": "HELTH", "CHG": "HELTH",
    "MEGA": "HELTH", "PR9": "HELTH", "THG": "HELTH",
    "AWC": "TOURISM", "CENTEL": "TOURISM", "ERW": "TOURISM", "MINT": "TOURISM",
    "AAV": "TRANS", "AOT": "TRANS", "BA": "TRANS", "BEM": "TRANS",
    "BTS": "TRANS", "PRM": "TRANS", "RCL": "TRANS", "SJWD": "TRANS",
    "THAI": "TRANS",
    "PLANB": "MEDIA", "VGI": "MEDIA",
    "SISB": "PROF",
    # Agro & Food
    "CBG": "FOOD", "CPF": "FOOD", "BTG": "FOOD", "ICHI": "FOOD", "ITC": "FOOD",
    "M": "FOOD", "OSP": "FOOD", "TFG": "FOOD", "TU": "FOOD",
    "STA": "AGRI", "STGT": "AGRI",
    # Property & Construction
    "AMATA": "PROP", "AP": "PROP", "CPN": "PROP", "LH": "PROP", "ORI": "PROP",
    "QH": "PROP", "SIRI": "PROP", "SPALI": "PROP", "WHA": "PROP",
    "SCC": "CONMAT", "TASCO": "CONMAT", "TOA": "CONMAT",
    "CK": "CONS",
    # Industrials
    "IVL": "PETRO", "PTTGC": "PETRO",
    "SCGP": "PKG",
    # Technology
    "CCET": "ETRON", "DELTA": "ETRON", "HANA": "ETRON", "KCE": "ETRON",
    "ADVANC": "ICT", "TRUE": "ICT",
}

SECTOR_INDUSTRY = {
    "ENERG": "RESOURC",
    "BANK": "FINCIAL", "FIN": "FINCIAL", "INSUR": "FINCIAL",
    "COMM": "SERVICE", "HELTH": "SERVICE", "TOURISM": "SERVICE",
    "TRANS": "SERVICE", "MEDIA": "SERVICE", "PROF": "SERVICE",
    "FOOD": "AGRO", "AGRI": "AGRO",
    "PROP": "PROPCON", "CONMAT": "PROPCON", "CONS": "PROPCON",
    "PETRO": "INDUS", "PKG": "INDUS",
    "ETRON": "TECH", "ICT": "TECH",
}


def industry_groups(symbols=None, level="sector"):
    """``{symbol: group}`` at SET ``"sector"`` or ``"industry"`` level."""
    if level not in ("sector", "industry"):
        raise ValueError("level must be sector or industry")
    symbols = SET_SECTOR if symbols is None else symbols
    out = {}
    for symbol in symbols:
        sector = SET_SECTOR.get(str(symbol).upper())
        if sector is None:
            continue
        out[symbol] = sector if level == "sector" else SECTOR_INDUSTRY[sector]
    return out


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
