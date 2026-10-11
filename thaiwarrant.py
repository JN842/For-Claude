"""Scrape DW term sheets and daily history from thaiwarrant.com (KGI).

Each listed DW has a public page ``/dw/<SYMBOL>`` with the term sheet, the
daily underlying and DW closing prices since listing, the daily implied vol
(the issuer's own for DW13; thaiwarrant's estimate for other issuers) and
KGI's indicative bid grid.  Expired series drop off the site, so history
has to be snapshotted while a series is still listed.

Only plain GETs of public pages, spaced by ``delay`` seconds.  The site's
WAF rejects form postbacks; do not try to work around it.
"""

import json
import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup


BASE = "https://www.thaiwarrant.com"
DATA_DIR = Path(__file__).resolve().parent / "data" / "thaiwarrant"
THAI_MONTHS = {m: i + 1 for i, m in enumerate(
    "ม.ค. ก.พ. มี.ค. เม.ย. พ.ค. มิ.ย. ก.ค. ส.ค. ก.ย. ต.ค. พ.ย. ธ.ค.".split()
)}
_SYMBOL = re.compile(r"SET50\d\d[CP]\d{4}[A-Z]")


def thai_date(text):
    """``"29 ธ.ค. 69"`` -> 2026-12-29 (Buddhist-era 2-digit year)."""
    day, month, year = str(text).split()
    year = int(year)
    year = year + 2500 - 543 if year < 100 else year - 543
    return pd.Timestamp(year, THAI_MONTHS[month], int(day))


def _session():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    return session


def fetch_page(path, session=None):
    session = session or _session()
    response = session.get(f"{BASE}/{path.lstrip('/')}", timeout=60,
                           allow_redirects=False)
    response.raise_for_status()
    if "Request Rejected" in response.text[:500]:
        raise RuntimeError(f"thaiwarrant WAF rejected {path}")
    return response


def set50_dw13_symbols(session=None):
    """SET50 DW13 series currently listed (from the KGI outstanding list)."""
    html = fetch_page("/kgi-dw/outstanding", session).text
    return sorted(set(_SYMBOL.findall(html)))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _after(text, label, pattern=r"([^\s].*?)\s"):
    match = re.search(re.escape(label) + r"\s*:\s*" + pattern, text)
    return match.group(1) if match else None


def _number(value):
    return float(value.replace(",", "")) if value else float("nan")


def _thai_date_after(text, label):
    match = re.search(re.escape(label) + r"\s*:\s*(\d{1,2} \S+ \d{2})", text)
    return thai_date(match.group(1)) if match else pd.NaT


def parse_terms(html, symbol):
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))
    issuer = re.search(r"ผู้ออก : (\S+) \((DW\d+)\)", text)
    cp = _after(text, "ประเภท", r"(Call|Put)")
    as_of = re.search(r"ข้อมูล ณ วันที่ (\d{1,2} \S+ \d{2}) Indicators", text)
    outstanding = re.search(r"Outstanding \(หน่วย\) : ([\d,]+)", text)
    return {
        "symbol": symbol,
        "underlying": _after(text, "ดัชนีอ้างอิง (ดูซีรีส์ SET50 futures อ้างอิงที่หัวตาราง)")
        or _after(text, "ดัชนีอ้างอิง"),
        "issuer": issuer.group(1) if issuer else None,
        "issuer_code": issuer.group(2) if issuer else None,
        "cp": cp[0] if cp else symbol[7],
        "strike": _number(_after(text, "ราคาใช้สิทธิ (จุด)", r"([\d,\.]+)")),
        "ratio": _number(_after(text, "อัตราใช้สิทธิ (DW: 1 หลักทรัพย์อ้างอิง)", r"([\d,\.]+)")),
        "first_trade": _thai_date_after(text, "วันซื้อขายวันแรก"),
        "last_trade": _thai_date_after(text, "วันซื้อขายวันสุดท้าย"),
        "expiry": _thai_date_after(text, "วันครบกำหนดอายุ"),
        "issuer_iv_now": _number(_after(text, "Volatility (%)", r"([\d\.]+)")) / 100,
        "outstanding": _number(outstanding.group(1)) if outstanding else float("nan"),
        "as_of": thai_date(as_of.group(1)) if as_of else pd.NaT,
    }


def _datatables(html):
    """Google-chart DataTables embedded in the page, as DataFrames."""
    frames = []
    for raw in re.findall(r"DataTable\('(\{.*?\})'\)", html, flags=re.S):
        table = json.loads(raw)
        columns = [c["id"] for c in table["cols"]]
        rows = []
        for row in table["rows"]:
            values = [cell.get("v") if cell else None for cell in row["c"]]
            y, m, d = map(int, re.match(r"Date\((\d+),(\d+),(\d+)\)", values[0]).groups())
            rows.append([pd.Timestamp(y, m + 1, d)]
                        + [pd.to_numeric(v, errors="coerce") if v != "" else float("nan")
                           for v in values[1:]])
        frames.append(pd.DataFrame(rows, columns=columns).set_index("TradeDate"))
    return frames


def parse_history(html, symbol):
    """Daily underlying close, DW close and implied vol (decimal)."""
    frames = _datatables(html)
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "underlying", "dw_price", "iv"])
    history = pd.concat(frames, axis=1)
    history = history.rename(columns={"ulprice": "underlying", "dwprice": "dw_price",
                                      "impvol": "iv"})
    history["iv"] = history.get("iv") / 100
    history.index.name = "date"
    history = history.reset_index()
    history.insert(1, "symbol", symbol)
    return history[["date", "symbol", "underlying", "dw_price", "iv"]]


def parse_indicative(html, symbol, as_of=None):
    """KGI's indicative bid grid: one row per (futures level, future date).

    The grid header names the reference futures series (e.g. S50Z26) and
    dates are day-month in Thai without a year; the year is taken from
    ``as_of`` and rolled forward past December.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="MainContent_gvIndicative")
    if table is None:
        return pd.DataFrame()
    grid = pd.read_html(StringIO(str(table)), flavor="lxml")[0]
    futures = str(grid.columns[0][0] if isinstance(grid.columns[0], tuple) else grid.columns[0])
    labels = [c[-1] if isinstance(c, tuple) else c for c in grid.columns]
    grid.columns = ["F"] + labels[1:]
    labels = [l for l in labels[1:] if re.fullmatch(r"\d{1,2} \S+", str(l))]
    grid = grid[["F"] + labels]
    grid["F"] = pd.to_numeric(grid["F"].astype(str).str.replace(",", ""), errors="coerce")
    grid = grid.dropna(subset=["F"])
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()
    rows, year, last_month = [], as_of.year, as_of.month
    dates = {}
    for label in labels:
        day, month = str(label).split()
        month = THAI_MONTHS[month]
        if month < last_month:
            year += 1
        last_month = month
        dates[label] = pd.Timestamp(year, month, int(day))
    long = grid.melt(id_vars="F", var_name="label", value_name="bid")
    long["date"] = long["label"].map(dates)
    long["bid"] = pd.to_numeric(long["bid"], errors="coerce")
    long.insert(0, "futures", futures)
    long.insert(0, "symbol", symbol)
    return long.drop(columns="label").dropna(subset=["bid"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def fetch_dw(symbol, session=None):
    html = fetch_page(f"/dw/{symbol}", session).text
    terms = parse_terms(html, symbol)
    return {
        "terms": terms,
        "history": parse_history(html, symbol),
        "indicative": parse_indicative(html, symbol, terms["as_of"]),
    }


def snapshot(symbols=None, output_dir=DATA_DIR, delay=2.0):
    """Fetch every symbol and merge into the CSVs under ``output_dir``.

    History rows are upserted on (date, symbol) so re-running keeps the
    days of series that have since expired.  Indicative grids are kept per
    page date.
    """
    session = _session()
    symbols = symbols or set50_dw13_symbols(session)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    terms, history, indicative, errors = [], [], [], []
    for i, symbol in enumerate(symbols):
        if i:
            time.sleep(delay)
        try:
            page = fetch_dw(symbol, session)
        except Exception as exc:  # keep going; report at the end
            errors.append({"symbol": symbol, "error": str(exc)})
            continue
        terms.append(page["terms"])
        history.append(page["history"])
        if not page["indicative"].empty:
            indicative.append(page["indicative"].assign(as_of=page["terms"]["as_of"]))

    def upsert(name, new, keys):
        path = output_dir / name
        if new.empty:
            return
        if path.exists():
            old = pd.read_csv(path, parse_dates=[c for c in new.columns
                                                  if c in ("date", "as_of", "first_trade",
                                                           "last_trade", "expiry")])
            new = pd.concat([old, new]).drop_duplicates(keys, keep="last")
        new.sort_values(keys).to_csv(path, index=False)

    upsert("dw_terms.csv", pd.DataFrame(terms), ["symbol"])
    upsert("dw_daily.csv", pd.concat(history) if history else pd.DataFrame(), ["date", "symbol"])
    upsert("dw_indicative.csv", pd.concat(indicative) if indicative else pd.DataFrame(),
           ["as_of", "symbol", "F", "date"])
    return pd.DataFrame(errors, columns=["symbol", "error"])
