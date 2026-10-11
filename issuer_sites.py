"""DW price tables from issuers' own sites: Yuanta (DW19) and J.P. Morgan (DW41).

Each returns the issuer's published IV and bid grid in the same long format
as ``thaiwarrant.parse_indicative`` (symbol, futures, F, bid, date), so
``mm_pricing.fit_conventions`` can recover each issuer's formula.

Plain GETs/POSTs of the endpoints the public pages themselves call, spaced
by ``delay`` seconds.
"""

import re
import time
from io import StringIO

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

YUANTA = "https://www.dw19club.com"
JPM = "https://www.jpmorgandw41.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def _session():
    session = requests.Session()
    session.headers["User-Agent"] = UA
    return session


def _get(session, url, retries=2, **kwargs):
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=60, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(5)


def _dated(labels, as_of):
    """'12 Oct' / 'Mon  12 Oct' -> Timestamps, rolling the year past December."""
    as_of = pd.Timestamp(as_of)
    year, last_month, out = as_of.year, as_of.month, {}
    for label in labels:
        day, month = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})", str(label)).groups()
        month = MONTHS[month]
        if month < last_month:
            year += 1
        last_month = month
        out[label] = pd.Timestamp(year, month, int(day))
    return out


def _long(grid, symbol, futures, as_of):
    grid = grid.copy()
    grid.columns = ["F"] + list(grid.columns[1:])
    grid["F"] = pd.to_numeric(grid["F"].astype(str).str.replace(",", ""), errors="coerce")
    grid = grid.dropna(subset=["F"])
    dates = _dated(grid.columns[1:], as_of)
    long = grid.melt(id_vars="F", var_name="label", value_name="bid")
    long["bid"] = pd.to_numeric(long["bid"], errors="coerce")
    long["date"] = long["label"].map(dates)
    long.insert(0, "futures", futures)
    long.insert(0, "symbol", symbol)
    return long.drop(columns="label").dropna(subset=["bid"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Yuanta DW19 (dw19club.com)
# ---------------------------------------------------------------------------

def yuanta_search(underlying="SET50", session=None):
    """Every DW on ``underlying`` across issuers, with Yuanta's IV estimate."""
    session = session or _session()
    payload = {"underlying": underlying, "callorput": "", "issuer": ["All"],
               "gearing_start": "", "gearing_end": "", "sensitivity_start": "",
               "sensitivity_end": "", "price_start": "", "price_end": "",
               "moneyness_start": "", "moneyness_end": "", "days_ltd_start": "",
               "days_ltd_end": "", "offset": 1, "limit": 1000}
    response = session.post(f"{YUANTA}/api/search/advance", json=payload, timeout=60)
    response.raise_for_status()
    rows = pd.DataFrame(response.json()["result_set"])
    rows["last_trade"] = pd.to_datetime(rows["last_trade"], format="%d/%m/%Y %H:%M:%S")
    rows["implied_vol"] = rows["implied_vol"] / 100
    rows["dw_per_point"] = 1 / rows["ratio"]       # Yuanta quotes ratio as points per DW
    return rows


def fetch_yuanta(symbol, session=None):
    session = session or _session()
    data = _get(session, f"{YUANTA}/api/DWInfo",
                params={"symbol": symbol, "price_matrix_width": 1200,
                        "price_matrix_height": 920, "language": "EN"}).json()
    as_of = pd.Timestamp(data["last_update_at"]).normalize()
    terms = {
        "symbol": symbol, "issuer": data["term_info"]["issuer"],
        "cp": data["trading_info"]["type"], "strike": float(data["term_info"]["exercise_price"]),
        "ratio": 1 / float(data["term_info"]["ratio"]),
        "last_trade": pd.Timestamp(data["term_info"]["last_trade"]),
        "expiry": pd.Timestamp(data["maturity_date"]),
        "published_iv": data["trading_info"]["implied_vol"] / 100,
        "underlying_last": data["ulprice"]["last"], "dw_last": data["dwprice"]["last"],
        "as_of": as_of,
    }
    grid = pd.DataFrame()
    if data.get("price_matrix_html"):
        table = pd.read_html(StringIO(data["price_matrix_html"]))[0]
        header = table.iloc[1].tolist()
        body = table.iloc[2:].reset_index(drop=True)
        body.columns = header
        grid = _long(body, symbol, header[0], as_of)
    return {"terms": terms, "grid": grid}


# ---------------------------------------------------------------------------
# J.P. Morgan DW41 (jpmorgandw41.com)
# ---------------------------------------------------------------------------

def fetch_jpm(symbol, session=None):
    session = session or _session()
    html = _get(session, f"{JPM}/th/terms/code/{symbol}").text
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))
    iv = re.search(r"Implied Volatility \(%\) ([\d.]+)%", text)
    stamp = re.search(r"ข้อมูลล่าสุด ณ (\d{2}) (\S+) (\d{4})", text)
    futures = re.search(r"\(((?:S50|ES|NQ|YM)[A-Z]\d{1,2})\)", text)
    as_of = pd.Timestamp.today().normalize()
    if stamp:
        thai = "ม.ค. ก.พ. มี.ค. เม.ย. พ.ค. มิ.ย. ก.ค. ส.ค. ก.ย. ต.ค. พ.ย. ธ.ค.".split()
        if stamp.group(2) in thai:
            as_of = pd.Timestamp(int(stamp.group(3)), thai.index(stamp.group(2)) + 1,
                                 int(stamp.group(1)))
    grid = pd.DataFrame()
    for table in pd.read_html(StringIO(html)):
        if str(table.columns[0]).startswith("Futures Last Price"):
            grid = _long(table, symbol, futures.group(1) if futures else None, as_of)
            break
    terms = {"symbol": symbol, "issuer": "JPM",
             "published_iv": float(iv.group(1)) / 100 if iv else float("nan"),
             "as_of": as_of}
    return {"terms": terms, "grid": grid}


# ---------------------------------------------------------------------------
# Bualuang DW01 (blswarrant.com)
# ---------------------------------------------------------------------------

BLS = "https://www.blswarrant.com"


def fetch_bls(symbol, session=None, futures_side="bid"):
    """BLS shows futures bid and offer per row; ``futures_side`` picks F
    ("bid", "offer" or "mid")."""
    session = session or _session()
    html = _get(session, f"{BLS}/mydw/{symbol}").text
    soup = BeautifulSoup(html, "lxml")
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    iv = re.search(r"Implied Volatility : ([\d.]+)%", text)
    stamp = re.search(r"\(as of (\d{1,2}) ([A-Z][a-z]{2}) (\d{2})\)", text)
    as_of = (pd.Timestamp(2000 + int(stamp.group(3)), MONTHS[stamp.group(2)], int(stamp.group(1)))
             if stamp else pd.Timestamp.today().normalize())
    table = soup.find("table", id="tb_detail_mydw")
    rows = [[c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            for tr in table.find_all("tr")] if table else []
    grid = pd.DataFrame()
    header = next((r for r in rows if r and "Bid" in r[0] and "Offer" in r[0]), None)
    if header:
        futures = re.match(r"(S50[A-Z]\d{2}|[A-Z0-9]+)", header[0]).group(1)
        labels = [re.sub(r"(\d{1,2})([A-Z][a-z]{2})(\d{2})", r"\1 \2", h) for h in header[1:]]
        records = []
        for r in rows[rows.index(header) + 1:]:
            if len(r) < 3 + len(labels):
                continue
            bid_f, offer_f = float(r[0].replace(",", "")), float(r[2].replace(",", ""))
            F = {"bid": bid_f, "offer": offer_f, "mid": (bid_f + offer_f) / 2}[futures_side]
            records.append([F] + r[3:3 + len(labels)])
        if records:
            grid = _long(pd.DataFrame(records, columns=["F"] + labels), symbol, futures, as_of)
    terms = {"symbol": symbol, "issuer": "BLS",
             "published_iv": float(iv.group(1)) / 100 if iv else float("nan"), "as_of": as_of}
    return {"terms": terms, "grid": grid}


# ---------------------------------------------------------------------------
# Macquarie DW28 (thaidw.com)
# ---------------------------------------------------------------------------

MACQ = "https://www.thaidw.com"


def macq_ric(symbol):
    """SPX28C2612A -> SPX28C612A.BK (Reuters code drops the year's tens digit)."""
    m = re.match(r"^(.*\d\d[CP])\d(\d{3}[A-Z])$", symbol)
    return f"{m.group(1)}{m.group(2)}.BK"


def fetch_macquarie(symbol, session=None):
    """Terms, published IV and today's live matrix (DW bid/ask at each
    underlying bid/ask).  ``fx`` is backed out of the issuer's own
    baht-adjusted underlying price."""
    session = session or _session()
    data = _get(session, f"{MACQ}/apimqth/LiveMatrixJSON",
                params={"ric": macq_ric(symbol), "mode": 0},
                headers={"Referer": f"{MACQ}/tools/livematrix"}).json()
    ric = data.get("ric_data") or {}
    num = lambda v: float(str(v).replace(",", "")) if v not in (None, "", "N/A") else float("nan")
    under = num(ric.get("underlying_price"))
    terms = {
        "symbol": symbol, "issuer": "MACQ", "cp": "C" if ric.get("type") == "CALL" else "P",
        "underlying": ric.get("underlying_ticker"), "underlying_ric": ric.get("underlying_ric"),
        "strike": num(ric.get("exercise_price")), "ratio": num(ric.get("conv_ratio")),
        "last_trade": pd.to_datetime(ric.get("last_trading_date"), format="%d %b %y"),
        "published_iv": num(ric.get("implied_volatility")) / 100,
        "underlying_price": under,
        "fx": num(ric.get("underlying_price_adj")) / under if under else float("nan"),
        "as_of": pd.to_datetime(ric.get("update_time"), format="%d %b %y"),
    }
    grid = pd.DataFrame(data.get("livematrix") or [])
    if not grid.empty:
        grid = pd.DataFrame({
            "symbol": symbol, "futures": ric.get("underlying_ric"),
            "F": (grid["underlying_bid"].astype(float) + grid["underlying_ask"].astype(float)) / 2,
            "bid": grid["bid"].astype(float), "ask": grid["ask"].astype(float),
            "date": pd.to_datetime(grid["create_for_date"]),
        }).drop_duplicates(["F", "bid"]).reset_index(drop=True)
    return {"terms": terms, "grid": grid}


def fetch_many(fetch, symbols, delay=2.0, **kwargs):
    session = _session()
    terms, grids, errors = [], [], []
    for i, symbol in enumerate(symbols):
        if i:
            time.sleep(delay)
        try:
            page = fetch(symbol, session, **kwargs)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            continue
        terms.append(page["terms"])
        if not page["grid"].empty:
            grids.append(page["grid"].assign(as_of=page["terms"]["as_of"]))
    return (pd.DataFrame(terms), pd.concat(grids, ignore_index=True) if grids else pd.DataFrame(),
            pd.DataFrame(errors, columns=["symbol", "error"]))


def sweep_iv(listing, terms, grids, F, date):
    """Every DW's vol on one common basis, cheapest first within call/put.

    Price = the issuer's own bid at futures ``F`` on ``date`` where a grid
    is available, else the listing's last price.  Vol is Black-76 on F, no
    carry, r = 0, calendar days to last trade / 365, so issuers with
    different clocks compare like for like.  ``iv_ask`` prices one tick
    above the bid: what a buyer pays.
    """
    from mm_pricing import _vol_for_price, tick_size, year_fraction

    terms = terms.set_index("symbol")
    date = pd.Timestamp(date)
    at = grids[(grids["date"] == date) & (grids["F"] == F)].set_index("symbol")["bid"]

    def vol(px, term):
        T = year_fraction([date], term["last_trade"], "calendar")
        sign = 1.0 if term["cp"] == "C" else -1.0
        return float(_vol_for_price(np.array([px * term["ratio"]]), np.array([F]),
                                    term["strike"], T, 0.0, 0.0, sign)[0])

    rows = []
    for _, row in listing.iterrows():
        term = terms.loc[row["symbol"]]
        from_grid = row["symbol"] in at.index
        bid = float(at[row["symbol"]]) if from_grid else float(row["price"])
        ask = bid + float(tick_size([bid])[0])
        otm = (term["strike"] / F - 1) * (1 if term["cp"] == "C" else -1)
        rows.append({
            "symbol": row["symbol"], "issuer": row["issuer"], "cp": term["cp"],
            "strike": term["strike"], "otm_pct": round(otm * 100, 1),
            "last_trade": term["last_trade"].date(), "bid": bid,
            "source": "issuer grid" if from_grid else "last price",
            "iv_bid": vol(bid, term), "iv_ask": vol(ask, term),
            "iv_published_yuanta": row["implied_vol"],
        })
    out = pd.DataFrame(rows)
    out["tick_cost_vol_pts"] = (out["iv_ask"] - out["iv_bid"]) * 100
    return out.sort_values(["cp", "iv_ask"], ignore_index=True)
