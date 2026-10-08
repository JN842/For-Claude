# SET50 Dividend Carry Model

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
| A:E | symbol, instrument, index_period (ครึ่งปีของวัน XD), expected_dps, key = `SYMBOL\|SERIES\|PERIOD` |
| F | universe (ทุก symbol ใน scenario) |
| H:K | membership: symbol, period, is_member, key = `SYMBOL\|PERIOD` |
| L:M | as_of, scenario, model_version, generated_at |

วางทั้งชีทลง `prototype_TQ.xlsx > Python!A1` (paste values หรือ Power Query)
สูตรที่ผูกไว้สร้างด้วย `python tools/update_prototype_tq.py <in.xlsx> <out.xlsx>`:

- **Basket Next**: basket ของครึ่งปีถัดไปตาม membership bet — วิธีเดียวกับ
  Q Rebalance (ราคาปิดวันที่กำหนด × จำนวนหุ้น, cap 10%, ไม่ปัด) หุ้นที่ไม่มีใน
  Q Rebalance ใส่จำนวนหุ้นในคอลัมน์ `Shares override`
- **Dividend**: DPS ต่อหุ้น × series × ครึ่งปี คูณ basket ของครึ่งปีนั้น
  (ครึ่งปีปัจจุบัน = Q Rebalance P, ครึ่งปีถัดไป = Basket Next, ถัดจากนั้นใช้
  Basket Next ต่อ) ÷ (200 × 50) = จุดดัชนี
- **Monitor**: Div pts, Fair (Rf) = S(1+r_f·t) − D, Fair (Borrow) = S(1+r_b·t) − D
  และตาราง calendar spread (ไกล − ใกล้) ทั้ง bid/ask/mid/fair

## Tests

```bash
pip install pytest
python -m pytest tests
```
