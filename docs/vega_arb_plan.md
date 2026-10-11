# Vega Arbitrage: SET50 DW vs TFEX S50 Options

> สถานะ: research plan (ยังไม่มี backtest)
> สร้าง: 2026-10-11
> ต่อยอดจาก: งาน KCE DW earnings straddle (พบว่า IV ของ DW คงที่ ไม่มี IV crush)
> เกี่ยวข้อง: SET refdata layer (TFEX S50 contracts, trading calendar), งาน inverse ETF / SET50 DW arb
> โค้ด: `vega_arb.py` (pricing, IV, จับคู่, sizing, H1/H3), `refdata/vega_events.csv` (§8) — ดู §9

ข้อความที่มีป้าย **[ต้องยืนยัน]** คือข้อเท็จจริงที่ยังไม่ได้ตรวจกับแหล่งทางการ ต้องเช็คก่อนนำไปใช้ใน code

---

## 1. สมมติฐาน

| | IV ของ SET50 DW | IV ของ S50 Options (TFEX) |
|---|---|---|
| ใครกำหนด | ผู้ออก / market maker (ตั้งเป็นนโยบาย) | แรงซื้อขายในตลาด |
| พฤติกรรม | **นิ่ง** ปรับเป็นขั้นๆ ไม่ค่อยตอบสนองต่อเหตุการณ์ | ขึ้นก่อนเหตุการณ์ที่รู้วันล่วงหน้า, พุ่งตอนตลาดช็อก, ร่วงหลังเหตุการณ์ (IV crush) |
| ระดับปกติ | สูงกว่า IV ของ options (ผู้ออกบวก markup) | ต่ำกว่า |

หลักฐานเบื้องต้น: กราฟ IV ของ KGI สำหรับ KCE13C2612A และ KCE13P2612A ช่วงงบ Q2/2026 (11 → 13 ส.ค. 2026) IV ของ CALL อยู่ที่ ~83–84% และ PUT อยู่ที่ ~108% ทั้งก่อนและหลังงบ ทั้งที่หุ้นขยับ +7.3% (นี่คือ DW หุ้นรายตัว ยังต้องยืนยันว่า DW ที่อ้างอิง SET50 มีพฤติกรรมแบบเดียวกัน)

**สมมติฐานหลัก (H1):** เมื่อ IV ของ S50 options พุ่งขึ้น (ก่อนเหตุการณ์ใหญ่หรือหลังตลาดช็อก) IV ของ SET50 DW จะไม่ขึ้นตาม หรือขึ้นช้า/น้อยกว่ามาก ทำให้มีช่วงที่ **IV_opt > IV_DW + ต้นทุน**

**เทรดที่ทำได้ทางเดียว:** ซื้อ DW (long vega ราคาถูก) + ขาย S50 option (short vega ราคาแพง) แล้ว hedge delta ที่เหลือด้วย S50 futures
ทำฝั่งกลับไม่ได้ เพราะ short DW ไม่ได้ ในภาวะปกติที่ IV_DW > IV_opt จึงไม่มีเทรด

---

## 2. เครื่องมือ

| เครื่องมือ | ใช้ทำอะไร | หมายเหตุ |
|---|---|---|
| SET50 DW (call/put หลายผู้ออก) | ขา long vega | ratio, strike, expiry, วิธีคิดราคา settlement ต่างกันในแต่ละรุ่น ต้องดึง term sheet ทุกรุ่น |
| S50 Options (TFEX) | ขา short vega | European, cash settled; multiplier **200 บาท/จุด [ต้องยืนยัน]**; สภาพคล่องและ strike ที่มีอาจจำกัด |
| S50 Futures (TFEX) | hedge delta ที่เหลือ | multiplier **200 บาท/จุด [ต้องยืนยัน]**; ใช้ refdata `tfex_contracts` ที่มีอยู่ |

**ประเด็น settlement ที่ต้องเช็ค**
- ราคา settlement ของ S50 options/futures = ค่าเฉลี่ยของ SET50 ช่วง 15 นาทีสุดท้ายรวมกับราคาปิดของวันซื้อขายสุดท้าย **[ต้องยืนยัน]**
- SET50 DW หลายรุ่นอาจใช้ราคา settlement ของ S50 futures เป็นราคาอ้างอิงตอนหมดอายุ หรือใช้วิธีอื่น **[ต้องยืนยันรายรุ่นจาก term sheet]**
- ถ้าวิธีคิด settlement หรือวันหมดอายุไม่ตรงกัน จะเกิด basis risk ตอนหมดอายุ ควรปิดสถานะก่อนหมดอายุเสมอ

---

## 3. การสร้างสถานะ

ให้ `S` = SET50, `n_dw` = จำนวน DW ต่อ 1 หน่วยของ underlying (ตาม term sheet), `M` = multiplier ของ option/futures (บาท/จุด)

ต่อ DW 1 หน่วย:
- Vega_dw = Vega_BS(S, K_dw, T_dw, IV_dw) / n_dw
- Delta_dw = Delta_BS(...) / n_dw

ต่อ S50 option 1 สัญญา:
- Vega_opt = Vega_BS(S, K_opt, T_opt, IV_opt) × M
- Delta_opt = Delta_BS(...) × M

**ขนาดสถานะ (vega-neutral):**
```
N_dw  = N_opt × Vega_opt / Vega_dw          # ซื้อ DW
N_opt = จำนวนสัญญาที่ขาย
N_fut = -(N_dw·Delta_dw − N_opt·Delta_opt) / M   # hedge delta ที่เหลือด้วย futures
```

**เลือกคู่ให้ใกล้กันที่สุด**
1. ประเภทเดียวกัน (DW call กับ option call หรือ DW put กับ option put) เพื่อลดปัญหา skew
2. strike ใกล้กัน (moneyness ต่างกันไม่เกิน ~2–3%)
3. อายุใกล้กัน (ถ้า T ต่างกันมาก gamma และ theta จะไม่หักล้างกัน กลายเป็น calendar spread แทน)
4. ราคา DW ตั้งแต่ประมาณ 0.30 บาท เพื่อให้ tick size ไม่กินกำไร

**P&L ที่คาด (ต่อ 1 รอบ)**
```
P&L ≈ Vega_pos × [ΔIV_dw − ΔIV_opt]           # ส่วนที่ต้องการ: IV_opt ร่วงกลับ ขณะที่ IV_dw นิ่ง
    + ½ Γ_net × ΔS² + Θ_net × Δt              # gamma/theta ที่หักล้างกันไม่หมด
    − spread ของ DW − spread ของ option − ค่าคอมมิชชั่น futures
```
กำไรมาจาก IV_opt ที่สูงผิดปกติกลับลงมา (เช่น IV crush หลังเหตุการณ์) ขณะที่ IV_dw นิ่ง

---

## 4. สองรูปแบบของเทรด

### A. ก่อนเหตุการณ์ที่รู้วันล่วงหน้า (pre-event)
- IV ของ options มักขึ้นก่อนวันประชุม กนง./FOMC หรือวันเลือกตั้ง แต่ DW ไม่ขึ้นตาม
- **เข้า:** เมื่อ IV_opt − IV_dw > threshold ในช่วง 1–5 วันก่อนเหตุการณ์
- **ออก:** หลังเหตุการณ์ เมื่อ IV ของ options crush กลับ (วันแรกหรือวันที่สองหลังเหตุการณ์)
- ความเสี่ยง: ถ้าตลาดขยับแรงมากในวันเหตุการณ์ ขาที่ขาย option จะเสีย gamma ซึ่งหักล้างกับขา DW ได้แค่บางส่วน

### B. หลังตลาดช็อกที่ไม่มีใครรู้ล่วงหน้า (post-shock)
- ตลาดร่วงแรง ทำให้ IV ของ options พุ่ง แต่ IV ของ DW ยังนิ่งหรือปรับขึ้นช้า
- **เข้า:** วันที่เกิดช็อกหรือวันถัดไป เมื่อช่องว่างของ IV เกิน threshold
- **ออก:** เมื่อช่องว่างแคบลง หรือครบเวลาที่กำหนด (เช่น 5–10 วัน)
- ความเสี่ยงหลัก:
  1. ผู้ออกปรับ IV ของ DW ขึ้นตาม (edge หายไป)
  2. market maker ถอน quote หรือ DW หมด quota
  3. หลังช็อกอาจมีช็อกตามมาอีก

---

## 5. ความเสี่ยงและข้อจำกัด

| ความเสี่ยง | ผลกระทบ | วิธีจัดการ / สิ่งที่ต้องวัด |
|---|---|---|
| ผู้ออกปรับ IV ตาม | edge หาย | วัดว่าผู้ออกปรับ IV ช้าแค่ไหนหลังช็อก (เป็นคำถามวิจัยหลัก) |
| MM ถอน quote หรือ DW หมด quota | เข้าหรือออกสถานะไม่ได้ / spread กว้าง | เก็บ bid/ask และ outstanding ของ DW ย้อนหลัง |
| สภาพคล่องของ S50 options ต่ำ | ราคาปิด/settlement ไม่ใช่ราคาที่ซื้อขายได้จริง | ใช้ bid/ask ถ้ามี, จำกัดเฉพาะ strike ที่มีปริมาณซื้อขายจริง |
| tick size ของ DW | ต้นทุนสูงเมื่อ DW ราคาต่ำ | กรอง DW ที่ราคาต่ำกว่า ~0.30 บาทออก |
| margin ของ short option | เงินทุนที่ต้องวาง, โดน margin call ตอนตลาดช็อก | คำนวณ margin ตาม TFEX ในการ backtest |
| gamma/theta ไม่ตรงกัน | P&L ผันผวนจากราคา ไม่ใช่จาก vol | เลือกคู่ที่ T และ K ใกล้กัน; ปรับ hedge delta ทุกวัน |
| skew | ขา put ของ options มี IV สูงกว่าเสมอ อาจดูเหมือนมีช่องว่าง IV ทั้งที่ไม่มี | เทียบ call กับ call และ put กับ put ที่ moneyness ใกล้กัน |
| settlement ไม่ตรงกัน | basis ตอนหมดอายุ | ปิดสถานะก่อนช่วงคำนวณราคา settlement |

---

## 6. คำถามวิจัย (ลำดับที่ควรทำ)

1. **H1: IV ของ DW นิ่งจริงไหมเมื่อเทียบกับ options**
   - คำนวณ IV รายวันของ SET50 DW ทุกรุ่น และ ATM IV ของ S50 options
   - regression: ΔIV_dw = α + β·ΔIV_opt (คาดว่า β ≪ 1)
   - ดูช่วงรอบเหตุการณ์ในส่วนที่ 8 แยกต่างหาก
2. **H2: ผู้ออกปรับ IV ช้าแค่ไหน**
   - หลังช็อกแต่ละครั้ง วัดว่าใช้กี่วันจน IV_dw ปรับขึ้นเกิน 50% ของการขยับของ IV_opt
   - แยกตามผู้ออก (บางรายอาจปรับเร็วกว่ารายอื่น)
3. **H3: ช่องว่าง IV เกินต้นทุนบ่อยแค่ไหน**
   - นับวันที่ IV_opt − IV_dw (คู่ที่ match กันแล้ว) > threshold โดยทดสอบ threshold ที่ 2, 5 และ 10 vol points
4. **H4: backtest P&L ของรูปแบบ A และ B**
   - ใช้ราคาปิด ราคาที่ปรับด้วย spread และ bid/ask ถ้ามี
   - รายงาน: P&L ต่อรอบ, win rate, ขาดทุนสูงสุด, P&L ที่แยกเป็น vega / gamma / theta / cost

---

## 7. ข้อมูลที่ต้องใช้

| ข้อมูล | แหล่ง (ที่เป็นไปได้) | หมายเหตุ |
|---|---|---|
| term sheet ของ SET50 DW ทุกรุ่น (strike, ratio, expiry, settlement) | SET, เว็บผู้ออก, thaiwarrant.com | thaiwarrant บล็อกการดึงอัตโนมัติ อาจต้อง export เอง |
| ราคา DW รายวัน (OHLC, bid/ask ถ้ามี) และ IV ที่ผู้ออกประกาศ | SETSMART, เว็บผู้ออก | IV ของผู้ออกช่วยยืนยันว่า IV ที่ back out ได้ถูกต้อง |
| ราคา settlement / bid-ask ของ S50 options ทุก strike | TFEX | ใช้คำนวณ ATM IV และ skew |
| SET50 index, S50 futures | refdata ที่มีอยู่ | ใช้ trading calendar ใน refdata ด้วย |
| อัตราดอกเบี้ยระยะสั้น, ปันผลคาดการณ์ของ SET50 | BOT / ประมาณการ | ปันผลมีผลกับ forward โดยเฉพาะช่วงที่หุ้นขึ้น XD เยอะ (มี.ค.–พ.ค., ส.ค.–ก.ย.) |

---

## 8. Event Catalog (ยังไม่ได้ยืนยันด้วยตัวเลข)

ใช้สำหรับ (1) วัด H1/H2 ย้อนหลัง และ (2) วางแผนเทรดรูปแบบ A ล่วงหน้า
ฉบับที่โค้ดอ่านได้อยู่ที่ `refdata/vega_events.csv` (`verified=False` ทุกแถวจนกว่าจะเช็คกับแหล่งทางการ)

### 8.1 เหตุการณ์ข้างหน้าที่รู้วันแล้ว (ณ 11 ต.ค. 2026)

| วันที่ | เหตุการณ์ | ทำไมสำคัญ |
|---|---|---|
| 13–16 ต.ค. 2026 | US CPI/PPI/retail sales (ก.ย.), ข้อมูลเศรษฐกิจจีน | ตลาดกังวลว่า Fed อาจขึ้นดอกเบี้ย; yield 10–30 ปีของสหรัฐสูงสุดในกว่า 24 ปี |
| ต.ค. 2026 (ทยอยออก) | งบ Q3 ของบริษัทใน SET50 โดยเฉพาะกลุ่มธนาคาร | น้ำหนักใน SET50 สูง |
| 27–28 ต.ค. 2026 | FOMC | ตลาดเริ่มพูดถึงโอกาสที่ Fed จะขึ้นดอกเบี้ย |
| 28 ต.ค. 2026 | กนง. ครั้งที่ 5/2569 | ประชุมวันเดียวกับวันสุดท้ายของ FOMC |
| 29 ต.ค. 2026 | ECB | |
| 30 ต.ค. 2026 | BOJ | BOJ เพิ่งขึ้นดอกเบี้ยเป็น 1.25%; ความเสี่ยง yen carry unwind |
| 3 พ.ย. 2026 | US midterm elections | Trump ระบุว่าจะไม่โจมตีอิหร่านก่อนวันเลือกตั้ง ทำให้ช่วงหลัง 3 พ.ย. มีความเสี่ยงด้านภูมิรัฐศาสตร์ |
| 11 พ.ย. 2026 | MSCI November Index Review (มีผล 1 ธ.ค. 2026) | flow ต่างชาติในหุ้นใหญ่ของ SET50 |
| กลาง พ.ย. 2026 **[ต้องยืนยันวัน]** | GDP ไทย Q3 (สศช.) | |
| 8–9 ธ.ค. 2026 | FOMC (มี SEP) | |
| 23 ธ.ค. 2026 | กนง. ครั้งที่ 6/2569 | |
| ธ.ค. 2026 **[ต้องยืนยันวัน]** | ประกาศรายชื่อ SET50 รอบใหม่ (มีผล ม.ค. 2027) | |
| ปลาย ธ.ค. 2026 **[ต้องยืนยันวัน]** | วันหมดอายุ S50 futures/options ไตรมาส Z26 | ใช้ refdata `tfex_contracts` (ใน repo นี้: `set50.contract_expiry(2026, 12, holidays)`) |
| 2027 | FOMC: 26–27 ม.ค., 16–17 มี.ค., 27–28 เม.ย., 8–9 มิ.ย., 27–28 ก.ค., 14–15 ก.ย., 26–27 ต.ค., 7–8 ธ.ค. (tentative) | |
| 2027 | MSCI review: 9 ก.พ., 10 พ.ค., 12 ส.ค., 11 พ.ย. | |
| 2027 **[ต้องยืนยัน]** | ตาราง กนง. 2570 | BOT ยังไม่ได้เผยแพร่ตอนที่ค้น |

### 8.2 ความเสี่ยงที่ยังดำเนินอยู่ (ไม่มีวันแน่นอน)

- **ตะวันออกกลาง/อิหร่าน:** มีการเผชิญหน้าทางทหารใกล้ช่องแคบ Hormuz, WTI เคยเกิน $100 ก่อนลงมาต่ำกว่า $90, มีข่าวว่าสหรัฐอาจโจมตีอิหร่าน (กดดัน SET ช่วงกลางสัปดาห์ 5–9 ต.ค. 2026)
- **Fed ขึ้นดอกเบี้ย / yield สหรัฐสูง:** ตลาดคาดว่าอาจขึ้นดอกเบี้ย 1–2 ครั้งก่อนสิ้นปี
- **Flow ต่างชาติ:** ต่างชาติขายสุทธิหุ้นไทยประมาณ 20,000–25,000 ล้านบาทใน ก.ย. 2026
- **น้ำท่วม กทม. และปริมณฑล:** มีวันหยุดพิเศษ 28–29 ก.ย. 2026 (ตลาดยังเปิดวันที่ 28)
- **นโยบายรัฐ:** ข่าวการเก็บภาษีขาออก (departure tax) กดดันหุ้นท่องเที่ยว

### 8.3 เหตุการณ์ในอดีตที่ควรใช้ทดสอบ H1/H2 (post-shock)

| วันที่ | เหตุการณ์ | หมายเหตุ |
|---|---|---|
| 4 มี.ค. 2026 | SET ร่วง 8.01% ช่วงเที่ยง ทำให้ circuit breaker ทำงาน | **[ต้องยืนยันสาเหตุ]** ข่าวที่พบไม่ได้ระบุสาเหตุ; น่าจะเป็นเหตุการณ์ทดสอบที่ดีที่สุดในชุดข้อมูลปัจจุบัน |
| 2026 (ต้นปี) **[ต้องยืนยันวัน]** | การเลือกตั้งทั่วไปของไทย | บทวิเคราะห์ต้นปีพูดถึง "election rally" |
| 2–9 เม.ย. 2025 | สหรัฐประกาศ reciprocal tariffs, ตลาดเอเชียร่วงแรง | |
| 28 มี.ค. 2025 | แผ่นดินไหวเมียนมา, อาคารใน กทม. ถล่ม | |
| 1 ก.ค. 2025 และ 29 ส.ค. 2025 | ศาลรัฐธรรมนูญสั่งนายกฯ หยุดปฏิบัติหน้าที่ และวินิจฉัยให้พ้นจากตำแหน่ง | ความเสี่ยงการเมืองไทย |
| 24 ก.ค. 2025 | การปะทะบริเวณชายแดนไทย–กัมพูชา | |
| 5 ส.ค. 2024 | yen carry trade unwind, ตลาดโลกร่วงแรง | |
| 14 ส.ค. 2024 | ศาลรัฐธรรมนูญวินิจฉัยให้นายกฯ พ้นจากตำแหน่ง | |
| 13–19 ก.ค. 2023 | การโหวตเลือกนายกฯ หลังการเลือกตั้ง (พรรคอันดับ 1 ตั้งรัฐบาลไม่ได้) | |
| 14 พ.ค. 2023 | เลือกตั้งทั่วไปของไทย | เหตุการณ์ที่รู้วันล่วงหน้า (รูปแบบ A) |
| 24 ก.พ. 2022 | รัสเซียบุกยูเครน | |
| 12–13 และ 23 มี.ค. 2020 | COVID, circuit breaker ของ SET หลายรอบ | ช็อกใหญ่ที่สุด ใช้ทดสอบว่าผู้ออกปรับ IV หรือถอน quote |

**เหตุการณ์ที่เกิดซ้ำทุกรอบ** (ใช้ทดสอบรูปแบบ A ได้ทุกปี):
- ประกาศผล กนง. (6 ครั้ง/ปี), FOMC (8 ครั้ง/ปี)
- US CPI และ Nonfarm Payrolls (เดือนละครั้ง)
- GDP ไทยรายไตรมาส
- งบของกลุ่มธนาคาร (น้ำหนักสูงใน SET50)
- MSCI review และการปรับรายชื่อ SET50 (ปีละ 2 ครั้ง)
- วันหมดอายุของ S50 futures/options

---

## 9. Pipeline

### สถานะ (2026-10-11)

ส่วนที่ไม่ต้องใช้ข้อมูลตลาดเขียนไว้แล้วใน `vega_arb.py` (ทดสอบด้วยข้อมูลสังเคราะห์ใน `tests/test_vega_arb.py`)

| ฟังก์ชัน | ขั้นใน pipeline |
|---|---|
| `black76`, `implied_vol`, `dw_implied_vol` | 3. `compute_iv` — ใช้ Black-76 บน S50 futures เป็น forward จึงไม่ต้องประมาณปันผลแยก; DW คูณ ratio กลับเป็นราคาต่อ 1 จุดก่อน back out; ราคา DW < 0.30 บาทหรือต่ำกว่า intrinsic → NaN |
| `match_pairs` | 5. `pairs.py` — cp เดียวกัน, moneyness ต่างกัน ≤ 3%, อายุต่างกัน ≤ 30 วัน (ปรับได้) |
| `size_pair` | §3 — vega-neutral + จำนวน futures ที่ hedge delta |
| `iv_beta` | 4. `h1_iv_stickiness` — OLS ΔIV_dw = α + β·ΔIV_opt |
| `gap_frequency` | 4. `h3_gap_frequency` — สัดส่วนวันที่ IV_opt − IV_dw เกิน 2/5/10 vol points |
| `load_events` | 1. `events.csv` → `refdata/vega_events.csv` |

ยังไม่ได้ทำ (รอข้อมูล): term sheet DW (`dw_terms.csv`), ราคา DW/options รายวัน, H2 (remark lag), backtest H4, margin TFEX
ค่า `MULTIPLIER = 200` ใน `vega_arb.py` ยังเป็น **[ต้องยืนยัน]**

### โครงสร้างเป้าหมาย

```
1. refdata/
   - dw_terms.csv          # term sheet SET50 DW ทุกรุ่น: symbol, issuer, cp, strike, ratio, listing, last_trade, expiry, settle_method
   - s50_option_series.csv # strike, expiry, multiplier
   - vega_events.csv       # จาก §8: date, end_date, name, type{scheduled, shock, recurring}, region, verified{bool}, note
2. data/
   - dw_daily.parquet      # date, symbol, OHLC, volume, bid, ask, issuer_iv(ถ้ามี)
   - s50opt_daily.parquet  # date, series, settle, bid, ask, volume, OI
   - set50_daily, s50fut_daily (มีแล้วใน refdata)
3. iv/
   - compute_iv.py         # back out IV จาก DW (หาร ratio) และ options; ตัดราคาต่ำกว่า tick floor ทิ้ง
   - atm_surface.py        # ATM IV ของ options ตามอายุ + skew
4. analysis/
   - h1_iv_stickiness.py   # regression ΔIV_dw ~ ΔIV_opt, event windows
   - h2_remark_lag.py      # จำนวนวันที่ผู้ออกใช้ปรับ IV หลังช็อก แยกตามผู้ออก
   - h3_gap_frequency.py   # จำนวนวันที่ IV gap เกิน threshold ในคู่ที่ match กันแล้ว
5. backtest/
   - pairs.py              # จับคู่ DW กับ option (cp, moneyness, T)
   - vega_arb_bt.py        # รูปแบบ A/B, delta hedge ด้วย futures, cost model, margin
   - report.md             # P&L แยกส่วนประกอบ, win rate, ขาดทุนสูงสุด
```

**เกณฑ์ที่จะบอกว่ากลยุทธ์ไปต่อได้**
- β (H1) < 0.3 และผู้ออกส่วนใหญ่ปรับ IV ช้ากว่า 3 วันหลังช็อก
- ช่องว่าง IV เกิน 5 vol points หลังหักต้นทุน อย่างน้อยหลายครั้งต่อปี
- backtest รูปแบบ A หรือ B ได้กำไรเฉลี่ยเป็นบวกหลังหัก spread, โดยกำไรส่วนใหญ่มาจาก vega ไม่ใช่ delta ที่ hedge ไม่หมด

---

## แหล่งข้อมูล
- ตาราง กนง. 2569: https://www.bot.or.th/th/our-roles/monetary-policy/mpc-meeting.html
- ตาราง FOMC: https://fedratecalc.com/fomc-meeting-schedule/ (ควรยืนยันกับ https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- ตาราง MSCI index review: https://www.msci.com/eqb/pressreleases/archive/ir_dates.pdf
- Circuit breaker 4 มี.ค. 2026: https://www.khaosodenglish.com/tech/2026/03/04/set-trading-halted-after-index-plunges-8-triggers-circuit-breaker/
- ภาวะตลาด 5–9 ต.ค. 2026 (อิหร่าน, yield, flow ต่างชาติ): https://www.bangkokbiznews.com/world/economics/1255764
- มุมมองตลาด ต.ค. 2026 (น้ำมัน, BOJ, น้ำท่วม, ปฏิทินเหตุการณ์): https://mgronline.com/stockmarket/detail/9690000097461
- ข่าวตลาดไทย ก.ค.–ต.ค. 2026: https://www.nationthailand.com/tags/Stock%20Exchange%20of%20Thailand
