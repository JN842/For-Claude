import pandas as pd

from thaiwarrant import parse_history, parse_indicative, parse_terms, thai_date
from vega_arb import iv_stickiness

PAGE = """
<html><body>
<div>ลักษณะของ DW ดัชนีอ้างอิง (ดูซีรีส์ SET50 futures อ้างอิงที่หัวตาราง) : SET50
ประเภท : Call ผู้ออก : KGI (DW13) จำนวนหุ้นจดทะเบียนกับตลาดหลักทรัพย์ : 400,000,000
ราคาใช้สิทธิ
   (จุด) : 1,280.000 อัตราใช้สิทธิ (DW: 1 หลักทรัพย์อ้างอิง) : 17.99856
วันซื้อขายวันแรก : 10 ส.ค. 69 วันซื้อขายวันสุดท้าย : 29 ธ.ค. 69 วันครบกำหนดอายุ : 05 ม.ค. 70</div>
<div>ข้อมูล ณ วันที่ 09 ต.ค. 69 Indicators อัตราทด (เท่า) : 20.02 Implied
   Volatility (%) : 24 (ข้อมูลอัพเดททุกสิ้นวัน)
Outstanding
   (หน่วย) : 97,198,200 (ณ วันที่ 02 ต.ค. 69)</div>
<table id="MainContent_gvIndicative">
<tr><th>S50Z26</th><th colspan="3">SET5013C2612A</th></tr>
<tr><th>(ราคา Bid)</th><th>29 ธ.ค.</th><th>05 ม.ค.</th><th></th></tr>
<tr><td>คลิก เพื่อดูตารางเพิ่มเติม</td><td></td><td></td><td></td></tr>
<tr><td>1,009.00</td><td>0.06</td><td>0.05</td><td></td></tr>
<tr><td>1,009.50</td><td>0.07</td><td>0.05</td><td></td></tr>
</table>
<script>
var data = new google.visualization.DataTable('{  "cols": [{"id": "TradeDate", "label": "DATE"," pattern": "", "type": "date"}, {"id": "ulprice", "label": "Underlying Price(THB)"," pattern": "", "type": "number"}, {"id": "dwprice", "label": "Warrant Price(THB)"," pattern": "#,#0.00", "type": "number"}],   "rows": [ {"c":[{"v":"Date(2026,7,7)"} ,{"v": "1080.4" ,"f":null},  {"v": "0.82" ,"f":null} ]}, {"c":[{"v":"Date(2026,7,10)"} ,{"v": "1082.1" ,"f":null},  {"v": "0.81" ,"f":null} ]}]}');
var data = new google.visualization.DataTable('{ "cols": [{"id": "TradeDate", "label": "DATE","pattern":"", "type": "date"},  {"id": "impvol", "label": "Implied Volatility(%)","pattern":"", "type": "number"}], "rows": [ {"c":[{"v":"Date(2026,7,7)"} ,{"v": "" ,"f":null} ]}, {"c":[{"v":"Date(2026,7,10)"} ,{"v": "23.91" ,"f":null} ]}]}');
</script>
</body></html>
"""


def test_thai_date_buddhist_era():
    assert thai_date("29 ธ.ค. 69") == pd.Timestamp("2026-12-29")
    assert thai_date("05 ม.ค. 2570") == pd.Timestamp("2027-01-05")


def test_parse_terms():
    terms = parse_terms(PAGE, "SET5013C2612A")
    assert terms["underlying"] == "SET50"
    assert (terms["issuer"], terms["issuer_code"], terms["cp"]) == ("KGI", "DW13", "C")
    assert terms["strike"] == 1280.0
    assert terms["ratio"] == 17.99856
    assert terms["first_trade"] == pd.Timestamp("2026-08-10")
    assert terms["last_trade"] == pd.Timestamp("2026-12-29")
    assert terms["expiry"] == pd.Timestamp("2027-01-05")
    assert terms["issuer_iv_now"] == 0.24
    assert terms["outstanding"] == 97198200
    assert terms["as_of"] == pd.Timestamp("2026-10-09")


def test_parse_history_months_are_zero_based_and_blank_iv_is_nan():
    history = parse_history(PAGE, "SET5013C2612A")
    assert history["date"].tolist() == [pd.Timestamp("2026-08-07"), pd.Timestamp("2026-08-10")]
    assert history["underlying"].tolist() == [1080.4, 1082.1]
    assert history["dw_price"].tolist() == [0.82, 0.81]
    assert pd.isna(history["iv"].iloc[0])
    assert history["iv"].iloc[1] == 0.2391


def test_parse_indicative_rolls_year_and_drops_blank_columns():
    grid = parse_indicative(PAGE, "SET5013C2612A", as_of="2026-12-20")
    assert set(grid["futures"]) == {"S50Z26"}
    assert sorted(grid["date"].unique()) == [pd.Timestamp("2026-12-29"), pd.Timestamp("2027-01-05")]
    row = grid.loc[(grid["F"] == 1009.5) & (grid["date"] == pd.Timestamp("2026-12-29"))]
    assert row["bid"].item() == 0.07
    assert len(grid) == 4


def test_iv_stickiness_flags_constant_iv():
    daily = pd.DataFrame({
        "date": pd.bdate_range("2026-09-01", periods=5).tolist() * 2,
        "symbol": ["A"] * 5 + ["B"] * 5,
        "underlying": [1000, 1010, 990, 1000, 1020] * 2,
        "iv": [0.24] * 5 + [0.20, 0.21, 0.19, 0.20, 0.22],
    })
    result = iv_stickiness(daily)
    assert result.loc["A", "iv_std_pts"] == 0
    assert result.loc["A", "mean_abs_d_iv_pts"] == 0
    assert result.loc["B", "corr_d_iv_ret"] > 0.99
