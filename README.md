# SET50 Dividend Carry Model

> โปรเจกต์นี้มีสองส่วน: โมเดลปันผลสำหรับ SET50 futures (ด้านล่าง) และ
> [Vine Copula Statistical Arbitrage](#vine-copula-statistical-arbitrage-set50set100)
> บนหุ้น SET50/SET100

คาดการณ์เงินปันผลรายหุ้น แล้วรวมเป็นปันผลต่อสัญญา SET50 futures (outright และ
calendar spread) ตาม membership scenario ที่ user กำหนด เพื่อใช้คำนวณ dividend
ใน cost of carry

| ไฟล์ | หน้าที่ |
|---|---|
| `dividend_api.py` | ดึงประวัติ XD จาก API (`fetch_dividends`) |
| `dividend_model.py` | เตรียมข้อมูล API, หา *slot* ของแต่ละหุ้น, สร้าง event CONFIRMED/FORECAST |
| `set50.py` | วันหมดอายุสัญญา, membership รายครึ่งปี, ปันผลต่อสัญญา |
| `dividend_run.py` | snapshot รายวัน → Excel และ backtest |

## หลักการของโมเดล

- **Slot** = ช่วงเวลาในปีที่หุ้นขึ้น XD เป็นประจำ (เช่น PTT `M04` final, `M08`
  interim) หาจากการ cluster วัน XD ย้อนหลัง 5 ปี ไม่ต้องใช้ operation period
  หรือ cadence จึงไม่สับสนกรณี interim 6 เดือน + final 12 เดือน
- **probability** = ปีที่จ่ายใน slot นั้น ÷ ปีที่มีโอกาสจ่าย (นับตั้งแต่ปีแรกที่
  slot ปรากฏ ภายใน lookback) จ่ายครบทุกปี = 1.0
- **วัน XD** = median ของ 3 ครั้งล่าสุด, window = min/max ± 7 วัน
- **DPS** = median ของ 3 ครั้งล่าสุด (ถ้าปีเดียวกันมีหลายครั้งใน slot เดียวกัน รวมกันก่อน)
- ไม่ forecast และส่งไป **Review** เมื่อ: เคยจ่ายครั้งเดียว (`ONE_OFF`), ไม่จ่าย
  2 ปีติด (`STALE_SLOT`), วัน XD กระจายเกิน 120 วัน (`IRREGULAR_TIMING`),
  ไม่มีข้อมูล (`NO_DATA`, `NO_RECENT_DIVIDEND`)
- **Event ที่ประกาศแล้ว** (announce ≤ as_of) แทนที่ forecast ของ slot-ปีเดียวกัน
  แม้ XD ผ่านไปแล้วก็ตาม ไม่มี forecast ซ้ำ
- ไม่มี `announceDate` → ใช้ `boardDate` → ถ้าไม่มีอีก ถือว่าประกาศก่อน XD 7 วัน

## ปันผลต่อสัญญา

```
contribution = probability × P(XD อยู่ในช่วงของสัญญา) × DPS × เป็นสมาชิก SET50 ณ วัน XD
```

- Outright เช่น `S50H27`: XD ใน (as_of, expiry]
- Spread เช่น `S50Z26H27`: XD ใน (expiry Z26, expiry H27]
- วันหมดอายุ = วันทำการก่อนวันทำการสุดท้ายของเดือน (**ต้องส่งวันหยุด SET ให้**
  ไม่งั้นข้ามแค่เสาร์อาทิตย์ เช่น Z26 จะได้ 30 ธ.ค. แทน 29 ธ.ค.)
- P(XD ในช่วง) ของ forecast ใช้การกระจายแบบสามเหลี่ยมบน XD window (mode =
  วัน XD ที่คาด) และ condition ว่ายังไม่เกิดถึง as_of — event ที่ window คร่อม
  expiry จะถูกแบ่งสัดส่วนให้ทั้งสองสัญญา
- Membership ดูตามครึ่งปีของ **วัน XD** (SET50 เปลี่ยนสมาชิกมีผล ม.ค./ก.ค.)
  ช่วงที่ไม่ได้กำหนดจะใช้รายชื่อครึ่งปีล่าสุดที่มี

ผลลัพธ์เป็น DPS (บาท/หุ้น) ต่อหุ้นต่อสัญญา — การแปลงเป็นจุดดัชนี/dividend yield
ด้วยน้ำหนักดัชนีทำใน Excel

## Excel output (`dividend_<YYYYMMDD>_<scenario>.xlsx`)

| Sheet | เนื้อหา |
|---|---|
| `Carry` | หุ้น × สัญญา: expected DPS (เฉพาะสมาชิก) ← ใช้ตัวนี้คูณน้ำหนัก |
| `Instruments` | วันหมดอายุ และช่วง XD ของแต่ละสัญญา |
| `Detail` | ทุก event × สัญญา พร้อม probability, p_xd_in_range, is_member |
| `Events` | event CONFIRMED + FORECAST (14 คอลัมน์) |
| `Membership` | scenario ที่ใช้ (1/0) + รายการเข้า/ออกที่ assume |
| `Review` | slot/หุ้นที่ไม่ forecast พร้อมเหตุผล |
| `Run_Info` | as_of, scenario, พารามิเตอร์ |

ไฟล์ตั้งชื่อตามวันที่และ scenario จึงไม่ทับกัน — ไฟล์ของแต่ละวันคือ state ว่า
วันนั้นคาดการณ์อะไรและ bet membership อย่างไร ไม่ต้องมี database แยก เพราะ API
ให้ประวัติพร้อม announceDate ทำให้ย้อนสร้าง forecast ของวันในอดีตได้เสมอ (backtest)

## ใช้ใน Jupyter

วิธีที่ง่ายที่สุด: เปิด `dividend_carry.ipynb` (อยู่โฟลเดอร์เดียวกับไฟล์ .py) แล้วรันจากบนลงล่าง
ด้านล่างคือโค้ดชุดเดียวกันแยกเป็นขั้นตอน

### 1. Import

```python
import importlib
import pandas as pd

import dividend_api, dividend_model, set50, dividend_run
for module in (dividend_api, dividend_model, set50, dividend_run):
    importlib.reload(module)

from dividend_api import fetch_dividends
from dividend_model import ModelParams
from set50 import (
    membership_table, change_membership, membership_changes,
    default_instruments, instrument_table,
)
from dividend_run import run_snapshot, backtest_snapshot, backtest_many
```

### 2. Membership scenario

```python
SET50_NOW = [
    "ADVANC", "AOT", "AWC", "BANPU", "BBL", "BCP", "BDMS", "BEM", "BH", "BJC",
    "CCET", "COM7", "CPALL", "CPF", "CPN", "CRC", "DELTA", "EGCO", "GPSC", "GULF",
    "HMPRO", "IVL", "KBANK", "KKP", "KTB", "KTC", "LH", "MINT", "MRDIYT", "MTC",
    "OR", "OSP", "PTT", "PTTEP", "PTTGC", "RATCH", "SCB", "SCC", "SCGP", "TCAP",
    "TFG", "THAI", "TIDLOR", "TISCO", "TLI", "TOP", "TRUE", "TTB", "TU", "WHA",
]

# ค่าเริ่มต้น: สมาชิกครึ่งปีถัดไปเหมือนปัจจุบัน
MEMBERSHIP = membership_table(SET50_NOW, "2026H2", through="2027H1")

# bet: รอบ 2027H1 เอา THAI ออก, เอา XYZ เข้า (มีผลต่อไปทุกครึ่งปีหลังจากนั้น)
MEMBERSHIP = change_membership(MEMBERSHIP, "2027H1", add=["XYZ"], remove=["THAI"])
# หรือแก้ทีละช่อง: MEMBERSHIP.loc["THAI", "2027H1"] = False

membership_changes(MEMBERSHIP)
```

### 3. ดึงข้อมูล

ดึงตาม **ทุก symbol ใน scenario** (รวมหุ้นที่ bet ว่าจะเข้า) ไม่ใช่ list ที่ hardcode
ไม่ต้อง normalize เอง โมเดลจัดการให้:

```python
TOKEN = "eyJ..."   # Bearer token ของวันนี้ (ไม่ต้องใส่คำว่า Bearer)

dividend_raw, fetch_log = fetch_dividends(MEMBERSHIP.index.tolist(), token=TOKEN)
display(fetch_log.loc[fetch_log["status"].ne("OK")])
```

### 4. Snapshot วันนี้ → Excel

```python
AS_OF = pd.Timestamp.today().normalize()
SET_HOLIDAYS = ["2026-12-31", "2027-01-01"]  # ใส่ปฏิทินวันหยุด SET ให้ครบ

INSTRUMENTS = ["S50Z26", "S50H27", "S50M27", "S50Z26H27", "S50H27M27"]
# หรือ default_instruments(AS_OF, SET_HOLIDAYS)  -> 2 สัญญาไตรมาสถัดไป + spread

snapshot = run_snapshot(
    dividend_raw,
    as_of=AS_OF,
    membership=MEMBERSHIP,
    instruments=INSTRUMENTS,
    holidays=SET_HOLIDAYS,
    scenario="thai_out",           # ชื่อ scenario อยู่ในชื่อไฟล์
    output_dir="dividend_model_output",
)
print(snapshot["path"])
display(snapshot["instruments"])
display(snapshot["matrix"])        # = sheet Carry
display(snapshot["review"])
```

เปรียบเทียบหลาย scenario: เรียก `run_snapshot` ซ้ำด้วย membership/scenario อื่น

### 5. Backtest

ส่ง membership ที่ "คาด" ไว้ ณ วันนั้น (และรายชื่อจริงถ้าอยากวัด error จาก
membership ด้วย) ระบบจะใช้เฉพาะข้อมูลที่ประกาศแล้ว ณ `as_of`:

```python
# รายชื่อ SET50 ที่ใช้อยู่ช่วง 2024H2 (ใส่รายชื่อจริงของรอบนั้น)
SET50_2024H2 = SET50_NOW
BET = membership_table(SET50_2024H2, "2024H2", through="2025H1")
# รายชื่อจริงรอบ 2025H1 ใส่ add/remove ตามที่เปลี่ยนจริง
ACTUAL = change_membership(BET, "2025H1", add=[], remove=[])

result = backtest_snapshot(
    dividend_raw,
    as_of="2024-10-01",
    membership=BET,
    instruments=["S50Z24", "S50H25", "S50Z24H25"],
    holidays=SET_HOLIDAYS,
    actual_membership=ACTUAL,      # ไม่ใส่ = ใช้ BET (วัดเฉพาะโมเดลปันผล)
)
display(result["carry"])    # forecast vs actual ต่อหุ้นต่อสัญญา
display(result["events"])   # HIT / NO_EVENT / UNEXPECTED, error วัน XD และ DPS

# หลายวัน: membership ต้องครอบคลุมตั้งแต่ครึ่งปีของวันแรก
BET_ALL = membership_table(SET50_2024H2, "2024H2", through="2025H2")
many = backtest_many(
    dividend_raw,
    pd.date_range("2024-07-15", "2025-03-15", freq="MS"),
    membership=BET_ALL,
    holidays=SET_HOLIDAYS,
)
display(many["summary"])
```

ข้อควรระวัง: `adjustedDPS` ถูกปรับย้อนหลังตาม corporate action ที่เกิดทีหลัง
backtest จึงมี look-ahead เล็กน้อยในตัวเลข DPS และต้องดึงข้อมูลหุ้นที่เคยเป็น
สมาชิกในอดีตด้วย

### พารามิเตอร์

```python
params = ModelParams(
    lookback_years=5, slot_gap_days=45, timing_recent_n=3, dps_recent_n=3,
    min_occurrences=2, stale_years=2, window_pad_days=7,
    prior_alpha=0.0, prior_beta=0.0,   # ใส่ prior ถ้าต้องการ shrink probability
)
run_snapshot(..., params=params)
```

## เชื่อมกับ workbook เทรด (prototype_TQ.xlsx)

`run_snapshot` เขียน `dividend_latest.xlsx` (ชื่อคงที่ ทับทุกครั้ง) มีชีท `Link`:

| คอลัมน์ | เนื้อหา |
|---|---|
| A:E | symbol, instrument, basket_quarter (ไตรมาสของวัน XD), expected_dps, key = `SYMBOL\|SERIES\|QUARTER` |
| F | universe (ทุก symbol ใน scenario) |
| H:K | membership: symbol, period, is_member, key = `SYMBOL\|PERIOD` |
| L:M | as_of, scenario, model_version, generated_at |

วางทั้งชีทลง `prototype_TQ.xlsx > Python!A1` (paste values หรือ Power Query)
สูตรที่ผูกไว้สร้างด้วย `python tools/update_prototype_tq.py <in.xlsx> <out.xlsx>`:

- **Basket Proj**: basket ของ 4 ไตรมาสถัดไป แต่ละไตรมาสใช้สมาชิกของครึ่งปีที่
  ไตรมาสนั้นอยู่ (membership bet จาก Python) — วิธีเดียวกับ Q Rebalance
  (ราคาปิดวันที่กำหนด × จำนวนหุ้น, cap 10%, ไม่ปัด) หุ้นที่ไม่มีใน
  Q Rebalance ใส่จำนวนหุ้นในคอลัมน์ `Shares override`
- **Dividend**: DPS ต่อหุ้น × series × ไตรมาสของวัน XD คูณ basket ของไตรมาสนั้น
  (ไตรมาสปัจจุบัน = Q Rebalance P, ไตรมาสถัดไป = Basket Proj) ÷ (200 × 50)
  = จุดดัชนี
- **Monitor**: Div pts, Fair (Rf) = S(1+r_f·t) − D, Fair (Borrow) = S(1+r_b·t) − D
  และตาราง calendar spread (ไกล − ใกล้) ทั้ง bid/ask/mid/fair

## Vine Copula Statistical Arbitrage (SET50/SET100)

| ไฟล์ | หน้าที่ |
|---|---|
| `price_data.py` | รายชื่อ SET50/SET100 และดึงราคาปรับปันผลจาก Yahoo (`.BK`) |
| `vine_arb.py` | เลือกกลุ่มหุ้น, fit marginal + vine copula, mispricing index, backtest แบบ rolling |
| `vine_arb_run.py` | CLI: backtest + สัญญาณวันนี้ → `vine_arb_output/vine_arb_<YYYYMMDD>.xlsx` |

### หลักการ

ทุกรอบ (default: formation 250 วันทำการ → trading 60 วัน แล้ว refit):

1. **เลือกกลุ่ม 4 ตัว**: ต่อหุ้น target หนึ่งตัว เลือก partner 3 ตัวจาก 10 ตัวที่
   Spearman rho กับ target สูงสุด โดยเอาชุดที่ผลรวม rho ทุกคู่ในกลุ่มสูงสุด แล้วเทรด
   10 กลุ่มที่คะแนนสูงสุด (กลุ่มเดียวกันอาจมาหลายครั้งโดยสลับ target)
2. **Marginal**: แปลงผลตอบแทนรายวันเป็น uniform ด้วย empirical CDF (mid-rank
   เพราะ tick size ทำให้มีค่าซ้ำเยอะ) หรือ Student-t — fit จาก formation เท่านั้น
3. **Vine copula** (`pyvinecopulib`): C-vine (default), D-vine หรือ R-vine อิสระ
   เลือก family ทีละคู่ด้วย AIC (Gaussian, t, Clayton, Gumbel, Frank, Joe, BB1, BB7
   + rotation) target อยู่ตัวแรกใน order จึงได้
   `h = P(U_target ≤ u | U_partners)` ตรงจาก Rosenblatt transform
4. **Mispricing**: `MI = h − 0.5`, `CMI` = ผลรวม MI (reset เป็น 0 ทุกครั้งที่ปิด position)
5. **เทรด**: `CMI ≥ entry` → short target / long partners, `CMI ≤ −entry` → กลับกัน
   ปิดเมื่อ CMI กลับผ่าน `exit` (0), `|CMI| ≥ stop` หรือจบรอบ ทั้งสองขา notional 1
   (dollar neutral) แบ่ง partner เท่ากัน (`hedge="equal"`) หรือตาม OLS (`"ols"`)

**จังหวะเวลา**: สัญญาณคำนวณจากราคาปิดวัน t
- `execution_delay=1` (default) ส่งคำสั่งที่ราคาปิดวันถัดไป → ได้ผลตอบแทนตั้งแต่ t+2
- `execution_delay=0` เทรดที่ราคาปิดวัน t (ATC) → ต้องคำนวณสัญญาณจากราคาก่อนปิดจริง
  ซึ่ง backtest นี้ถือว่าเท่ากับราคาปิด (มองโลกในแง่ดี)

**ต้นทุน**: `cost_bps` ต่อข้าง ต่อ notional ทั้งสองขา (เปิด+ปิด 1 รอบ = 4 × cost_bps)
และ `borrow_bps` ต่อปีบนขา short

### ใช้ใน Jupyter

```python
from price_data import SET100_2026H2, fetch_prices
from vine_arb import VineArbParams, fit_periods, backtest, sensitivity, live_signals

prices, log = fetch_prices(SET100_2026H2, start="2014-01-01",
                           cache="price_cache/set100.csv")
# หรือราคาจากระบบภายใน: DataFrame index = วันที่, คอลัมน์ = symbol, ราคาปรับปันผลแล้ว

params = VineArbParams(entry=0.6, stop=2.0, execution_delay=1, cost_bps=15)
fits = fit_periods(prices, params, start="2015-01-01", progress=True)  # ช้าสุด ~1 นาที
result = backtest(prices, params, fits=fits)
result["summary"]; result["trades"]; result["daily"]; result["models"]
result["live"]       # สถานะวันล่าสุด: h, CMI, signal ที่ต้องถือหลังปิดวันนี้

# เปลี่ยนเฉพาะกฎเทรด ไม่ต้อง fit ใหม่
grid = sensitivity(prices, params, fits=fits,
                   grid={"entry": [0.3, 0.6, 1.0], "execution_delay": [0, 1],
                         "cost_bps": [0, 5, 10, 15]})
```

CLI:

```bash
python vine_arb_run.py --universe set100 --sensitivity
python vine_arb_run.py --universe set50 --entry 1.0 --stop 0 --delay 0 --cost-bps 5
```

Excel: `Summary` (สถิติ + รายปี), `Live`, `Trades`, `Daily`, `Models` (กลุ่ม/family
ที่เลือกแต่ละรอบ), `Sensitivity`, `Params`

### ผล backtest (SET100, ม.ค. 2016 – พ.ย. 2026, ราคา Yahoo)

Universe 100 ตัว, 10 กลุ่มต่อรอบ, 44 รอบ (formation 250 / trading 60 วัน)
ทุกตัวเลขเป็น out-of-sample ต่อรอบ แต่ **การเลือกพารามิเตอร์จากตารางนี้เป็น in-sample**

**Default** (C-vine, entry 0.6, stop 2.0, delay 1, cost 15 bps, borrow 1.5%/ปี):

| | ค่า |
|---|---|
| CAGR (net) | −8.1% |
| Gross (ก่อนต้นทุน) | −0.2% ต่อปี |
| Vol / Sharpe | 8.4% / −0.96 |
| Max drawdown | −61% |
| Trades/ปี, win rate | 118, 48% |
| Corr กับ SET100 equal-weight | 0.05 (market neutral จริง) |

**Sharpe ตาม entry / delay / cost** (C-vine, ไม่มี stop):

| delay | entry | 0 bps | 5 | 10 | 15 |
|---|---|---|---|---|---|
| 0 (ATC) | 0.3 | 0.59 | 0.30 | 0.01 | −0.28 |
| 0 (ATC) | 1.5 | 0.42 | 0.33 | 0.24 | 0.15 |
| 1 | 0.3 | 0.29 | 0.00 | −0.28 | −0.57 |
| 1 | 1.5 | 0.24 | 0.15 | 0.06 | −0.03 |

สิ่งที่พบ:

- **มี edge จริงแต่สั้นและเล็ก**: `MI` วันนี้ทำนาย spread วันพรุ่งนี้ได้ (corr ≈ −0.05,
  ติดลบทุกปี 2016–2026) ช่วง h ปลาย ๆ (<0.05 หรือ >0.95) spread กลับตัว ~15–25 bps
  ในวันถัดไป แต่วันที่ 2 เหลือเกือบศูนย์ → ถ้าเทรดช้าไป 1 วัน (`delay=1`) edge หายไปเกือบหมด
- **ต้นทุนกิน edge หมด**: 1 รอบ (เข้า+ออก ทั้งสองขา) ที่ 15 bps/ข้าง = 60 bps ขณะที่
  edge ต่อเทรด ~15–25 bps; break-even อยู่ราว 5–10 bps/ข้าง
- **Stop-loss ที่ |CMI| ≥ 2 ทำร้ายผล**: ตรงนั้นคือจุดที่ mispricing กลับตัวแรงที่สุด
  (spread วันถัดไป ~−35 bps) ปิด stop แล้วดีขึ้นทุกกรณีที่มีต้นทุน
- **ชนิด vine และ marginal แทบไม่มีผล**: C-vine / D-vine / R-vine และ marginal
  แบบ ECDF / Student-t ได้ gross เฉลี่ยใกล้กัน (2.6–3.0% ต่อปี ก่อนต้นทุน)
- **Copula แบบ Gaussian ล้วน (`families=("indep", "gaussian")`) ไม่ได้แย่กว่า**
  — gross เฉลี่ย 4.1% ต่อปี และดีที่สุดที่ entry 1.5 (Sharpe 0.18 ที่ 15 bps, delay 1)
  แปลว่า tail dependence ของ vine ไม่ได้เพิ่ม edge ในข้อมูลรายวันชุดนี้
- แถวที่ดีที่สุด (delay 0, entry 1.5, ไม่มี stop, 15 bps) Sharpe 0.15 — ไม่มีนัยสำคัญ
  (standard error ของ Sharpe 10 ปี ≈ 0.3) และเป็นแถวที่เลือกจาก 64 แถว

สรุป: ในรูปแบบนี้ **ยังไม่ใช่กลยุทธ์ที่เทรดได้หลังหักต้นทุน** ทางที่น่าลองต่อ: ใช้เป็น
filter/overlay ของ market making หรือ execution (ต้นทุนส่วนเพิ่มต่ำ), ข้อมูล intraday
เพื่อจับการกลับตัวภายในวัน, หรือ marginal แบบ GARCH filter

### แยกรายอุตสาหกรรม / sector

`price_data.SET_SECTOR` แมปหุ้น SET100 เข้า sector ของ SET (ENERG, BANK, FIN, COMM,
FOOD, PROP, TRANS, HELTH, ...) และ `SECTOR_INDUSTRY` รวมเป็น industry (RESOURC,
FINCIAL, SERVICE, AGRO, PROPCON, INDUS, TECH) — แมปด้วยมือ ควรเทียบกับ SET

```python
from price_data import industry_groups, SET100_2026H2
from vine_arb import VineArbParams, backtest, backtest_by_group

groups = industry_groups(SET100_2026H2, level="sector")     # หรือ level="industry"

# partner ต้องอยู่ sector เดียวกับ target, เทรดทุก sector รวมเป็นพอร์ตเดียว
params = VineArbParams(groups=groups)
result = backtest(prices, params, start="2015-01-01")

# เลือกเทรดเฉพาะบาง sector
params = VineArbParams(groups=groups, trade_groups=("ENERG", "FOOD"))

# แต่ละ sector เป็นพอร์ตของตัวเอง (3 กลุ่ม/รอบ) -> ตารางเทียบ
by_sector = backtest_by_group(prices, VineArbParams(groups=groups),
                              n_quadruples=3, start="2015-01-01")
by_sector["summary"]
```

```bash
python vine_arb_run.py --by sector --per-group
python vine_arb_run.py --by sector --groups ENERG FOOD
```

เลือกหุ้นเองได้ด้วย `--symbols` (หรือ `VineArbParams(universe=(...))`) เช่นกลุ่มโรงกลั่น/ปิโตรเคมี:

```bash
python vine_arb_run.py --symbols PTT TOP SPRC IRPC PTTGC
```

ผล (5 หุ้น = 5 กลุ่มต่อรอบ, ค่าตั้งต้น): gross −4.3%/ปี, net −12.0%/ปี, Sharpe −1.19
ไม่มีชุดพารามิเตอร์ไหนบวกหลังต้นทุน 15 bps (ดีที่สุด: ATC, entry 1.5, ไม่มี stop
Sharpe −0.02) — correlation รายวันในกลุ่มนี้แค่ 0.33–0.57 และ PTT ผูกกับกลุ่มอ่อนสุด

sector ที่มีหุ้นน้อยกว่า 4 ตัวสร้างกลุ่มไม่ได้ (CONMAT, INSUR, ICT, PETRO, ...)
จึงถูกข้าม ใช้ `level="industry"` ถ้าอยากให้หุ้นเหล่านั้นได้เทรด

**ผล** (default: entry 0.6, stop 2, delay 1; ต่อ sector 3 กลุ่ม/รอบ):

| sector | หุ้น | gross/ปี | net/ปี @15 bps | Sharpe net |
|---|---|---|---|---|
| FOOD | 9 | +10.8% | +1.7% | 0.19 |
| TRANS | 9 | +4.8% | −4.0% | −0.18 |
| ENERG | 19 | +4.7% | −4.1% | −0.22 |
| HELTH | 7 | +2.9% | −5.5% | −0.42 |
| COMM | 9 | +1.4% | −7.8% | −0.45 |
| TOURISM | 4 | +1.0% | −7.3% | −0.48 |
| BANK | 8 | −0.5% | −8.1% | −0.91 |
| PROP | 9 | −0.5% | −7.9% | −0.64 |
| FIN | 6 | −3.8% | −12.4% | −0.80 |
| ETRON | 4 | −12.4% | −20.6% | −0.97 |

| พอร์ตรวม 10 กลุ่ม/รอบ | gross/ปี | net/ปี @15 bps |
|---|---|---|
| ไม่จำกัด sector (เดิม) | −0.2% | −8.1% |
| partner ใน sector เดียวกัน | +2.9% | −5.1% |

- **จำกัด sector ช่วยจริง**: gross ของพอร์ตรวมดีขึ้นราว 3% ต่อปี — กลุ่มที่ข้าม
  sector (เช่น BJC/CPALL จับคู่กับ SAWAD/MTC) เป็น correlation ที่ไม่มีเหตุผลทาง
  เศรษฐกิจรองรับ
- **แต่ยังไม่พอจ่ายต้นทุน** ยกเว้น FOOD ที่ net บวกเล็กน้อย (ที่ entry 1.5 ได้ +1.1%/ปี,
  บวก 6/11 ปี — ยังไม่สม่ำเสมอ)
- ค่าที่ดีที่สุดในตาราง sensitivity คือ ENERG ที่ entry 1.5 (Sharpe 0.55 หลังต้นทุน)
  แต่มาจากปี 2024 ปีเดียว (+41.5%), บวกแค่ 4/11 ปี และกำไรมาจาก BGRIM/EA/PTTEP
  เป็นหลัก — ไม่ควรเชื่อจนกว่าจะทดสอบนอกช่วงนี้
- sector ที่ "ควรจะ" เป็นคู่กันดีอย่าง BANK และ FIN กลับไม่มี edge — หุ้นกลุ่มนี้
  เคลื่อนไหวไปด้วยกันอยู่แล้ว ส่วนต่างรายวันจึงเป็น noise มากกว่า mispricing

### ข้อจำกัด

- **Survivorship bias**: universe คือรายชื่อ SET100 ปัจจุบัน (รายชื่อนอก SET50 ใน
  `price_data.py` เป็นค่าประมาณ ควรเทียบกับประกาศ SET) ไม่ใช่สมาชิก ณ วันนั้น
- ข้อมูล Yahoo มีช่วงหยุดซื้อขาย / corporate action แปลก ๆ (เช่น THAI กลับมาเทรด
  ส.ค. 2025, TRUE ควบรวม 2023) — หุ้นที่ขยับเกิน 40% ใน formation จะถูกตัดออก
- ไม่ได้จำลอง bid-ask, ข้อจำกัด short (ต้องเป็นหุ้นที่ short ได้ + มี SBL) หรือ
  uptick rule; ขา short อาจใช้ single stock futures แทนได้ แต่มี basis/roll
- ผลตอบแทนคิดแบบ rebalance ให้ notional คงที่ทุกวัน

## Tests

```bash
pip install pytest
python -m pytest tests
```
