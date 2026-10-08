"""Wire the Python dividend output into the trading workbook (prototype_TQ.xlsx).

    python tools/update_prototype_tq.py prototype_TQ.xlsx prototype_TQ_linked.xlsx

Adds/rebuilds, leaving the other sheets untouched:

Python       paste target for dividend_latest.xlsx > Link (A1); left empty
Basket Next  projected basket for the next index half-year under the
             membership bet, same method as Q Rebalance (unrounded shares)
Dividend     expected DPS per symbol x Monitor series x half-year, times the
             basket of that half-year, / (multiplier x lots) = index points
Monitor      Div pts, fair value at risk-free and borrowing rates, and a
             calendar-spread table; holiday ranges in M7:M10 made absolute

openpyxl drops Excel-2010 conditional-format extensions, so the Monitor
sheet's <extLst> is copied back from the source file after saving.
"""

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

FIRST, LAST = 9, 88          # symbol rows in Dividend and Basket Next
SERIES_ROWS = [7, 8, 9, 10]  # Monitor rows holding the rolling series

BASE = Font(name="Calibri", size=11)
BOLD = Font(name="Calibri", size=11, bold=True)
INPUT = Font(name="Calibri", size=11, color="FFFF0000")  # workbook convention
HEAD_FILL = PatternFill("solid", fgColor="FFD9E1F2")
THIN = Side(style="thin", color="FFBFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NUM = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'
DPS = '0.0000;-0.0000;"-"'
PTS = '0.00'


def _header(cell, text):
    cell.value = text
    cell.font = BOLD
    cell.fill = HEAD_FILL
    cell.border = BOX
    cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _put(ws, ref, value, fmt=None, font=BASE):
    cell = ws[ref]
    cell.value = value
    cell.font = font
    if fmt:
        cell.number_format = fmt
    return cell


def _match_text(range_ref, key):
    # Compare as text: Excel turns the symbol TRUE into a boolean.
    return f'--({range_ref}&""={key}&"")'


def build_python(ws):
    ws.delete_rows(1, ws.max_row)
    ws["A1"].comment = Comment(
        "Paste dividend_latest.xlsx > Link here at A1 (values), or load it "
        "with Power Query to A1. Columns: A:E carry, F universe, "
        "H:K membership, L:M info. Do not type in this sheet.",
        "python",
    )


def build_basket_next(wb):
    if "Basket Next" in wb.sheetnames:
        del wb["Basket Next"]
    ws = wb.create_sheet("Basket Next", wb.sheetnames.index("Q Rebalance") + 1)
    ws["B1"] = "Projected basket for the next index half-year (membership bet from Python)"
    ws["B1"].font = BOLD

    labels = [
        ("B3", "Index period", "C3", "=Dividend!$D$8", "@"),
        ("B4", "Date as of (prices)", "C4", "='Q Rebalance'!$K$3", "dd/mm/yyyy"),
        ("B5", "SET50 close", "C5",
         '=RTD("profrtd",,"history","SET50,DAILY,PCLOSE,E="&TEXT($C$4,"ddmmyyyy")'
         '&TEXT("","hhmm")&"",1)', "#,##0.00"),
        ("B6", "Weight cap", "C6", 0.1, "0.0%"),
        ("B7", "Basket value (THB)", "C7", "=C5*Dividend!$C$3*Dividend!$C$4", NUM),
    ]
    for label_ref, label, value_ref, value, fmt in labels:
        _put(ws, label_ref, label)
        _put(ws, value_ref, value, fmt,
             INPUT if value_ref in ("C4", "C6") else BASE)
    ws["C4"].comment = Comment(
        "Price date for the projected weights. Defaults to the Q Rebalance "
        "date; type a date to override (e.g. the review cut-off).", "python")
    ws["C6"].comment = Comment(
        "Same 10% cap Q Rebalance applies to DELTA, applied to any member "
        "above the cap in one pass.", "python")

    checks = [
        ("E3", "Members", "F3", f"=SUM(C{FIRST}:C{LAST})"),
        ("E4", "Members missing price", "F4",
         f"=COUNTIFS(C{FIRST}:C{LAST},1,D{FIRST}:D{LAST},0)"),
        ("E5", "Members missing shares", "F5",
         f"=COUNTIFS(C{FIRST}:C{LAST},1,G{FIRST}:G{LAST},0)"),
        ("E6", "Sum of weights", "F6", f"=SUM(K{FIRST}:K{LAST})"),
    ]
    for label_ref, label, value_ref, formula in checks:
        _put(ws, label_ref, label)
        _put(ws, value_ref, formula, "0.0000" if value_ref == "F6" else "0")

    headers = ["Symbol", "Member", "Close", "Shares (Q Rebalance)",
               "Shares override", "Shares used", "Mkt Cap (M)", "Raw weight",
               "Capped Mkt Cap (M)", "Weight", "Value (THB)",
               "Q Stock (unrounded)"]
    for offset, text in enumerate(headers):
        _header(ws.cell(row=8, column=2 + offset), text)
    ws["F8"].comment = Comment(
        "Type listed shares here for a stock that is not in Q Rebalance "
        "(e.g. a stock you bet will enter).", "python")
    _put(ws, "H7", f"=SUM(H{FIRST}:H{LAST})", NUM)
    _put(ws, "J7", f"=SUM(J{FIRST}:J{LAST})", NUM)

    for row in range(FIRST, LAST + 1):
        link_row = row - FIRST + 2
        r = str(row)
        _put(ws, "B" + r, f'=IF(Python!$F${link_row}="","",Python!$F${link_row}&"")')
        _put(ws, "C" + r,
             f'=IF($B{r}="",0,SUMIFS(Python!$J:$J,Python!$K:$K,$B{r}&"|"&$C$3))', "0")
        _put(ws, "D" + r,
             f'=IF($C{r}=0,0,IFERROR(RTD("profrtd",,"history",$B{r}&",DAILY,PCLOSE,E="'
             f'&TEXT($C$4,"ddmmyyyy")&TEXT("","hhmm")&"",1),0))', "#,##0.00")
        _put(ws, "E" + r,
             f"=IF($B{r}=\"\",0,SUMPRODUCT({_match_text(chr(39) + 'Q Rebalance' + chr(39) + '!$J$8:$J$57', '$B' + r)},"
             f"'Q Rebalance'!$L$8:$L$57))", NUM)
        ws["F" + r].font = INPUT
        ws["F" + r].number_format = NUM
        _put(ws, "G" + r, f'=IF(F{r}<>"",F{r},E{r})', NUM)
        _put(ws, "H" + r, f"=IF($C{r}=1,D{r}*G{r}/1000000,0)", NUM)
        _put(ws, "I" + r, f"=IF($H$7=0,0,H{r}/$H$7)", "0.00%")
        _put(ws, "J" + r,
             f"=IF(I{r}>$C$6,$C$6/(1-COUNTIF($I${FIRST}:$I${LAST},\">\"&$C$6)*$C$6)"
             f"*SUMIF($I${FIRST}:$I${LAST},\"<=\"&$C$6,$H${FIRST}:$H${LAST}),H{r})", NUM)
        _put(ws, "K" + r, f"=IF($J$7=0,0,J{r}/$J$7)", "0.00%")
        _put(ws, "L" + r, f"=K{r}*$C$7", NUM)
        _put(ws, "M" + r, f"=IF(D{r}>0,L{r}/D{r},0)", "#,##0.00")

    for col, width in zip("ABCDEFGHIJKLM", [2, 12, 9, 10, 18, 16, 16, 14, 11, 16, 9, 14, 16]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "C9"


def build_dividend(wb):
    ws = wb["Dividend"]
    ws.delete_rows(1, ws.max_row)
    _put(ws, "B1", "Expected dividend points per series (Python carry x basket of the XD half-year)", font=BOLD)

    _put(ws, "B3", "Multiplier")
    _put(ws, "C3", 200, "0", INPUT)
    _put(ws, "B4", "Lots in basket")
    _put(ws, "C4", 50, "0", INPUT)
    _put(ws, "B5", "Python as of")
    _put(ws, "C5", '=IF(Python!$M$2="","no data",Python!$M$2)', "dd/mm/yyyy")
    _put(ws, "D5",
         '=IF(Python!$M$2="","Paste Link into Python!A1",'
         'IF(INT(Python!$M$2)=TODAY(),"","STALE: rerun Python"))', font=INPUT)

    # Summary: one column per Monitor series.
    _header(ws["F2"], "Series")
    _header(ws["F3"], "Div points")
    _header(ws["F4"], "Current half-year")
    _header(ws["F5"], "Later half-years")

    # Detail header.
    _header(ws["B7"], "Series ->")
    _header(ws["B8"], "Symbol")
    for col, text in zip("CDE", ["Shares", "Shares", "Shares"]):
        _header(ws[col + "7"], text)
    _put(ws, "C8", '=YEAR(TODAY())&"H"&IF(MONTH(TODAY())<=6,1,2)', font=BOLD)
    _put(ws, "D8", '=IF(RIGHT(C8,1)="1",LEFT(C8,4)&"H2",(LEFT(C8,4)+1)&"H1")', font=BOLD)
    _put(ws, "E8", '=IF(RIGHT(D8,1)="1",LEFT(D8,4)&"H2",(LEFT(D8,4)+1)&"H1")', font=BOLD)
    ws["C7"].comment = Comment("Current half-year: Q Rebalance column P (unrounded).", "python")
    ws["D7"].comment = Comment("Next half-year: Basket Next column M (membership bet).", "python")
    ws["E7"].comment = Comment("Assumed same basket as the next half-year.", "python")

    detail_cols = "GHIJKLMNOPQR"
    for k, series_row in enumerate(SERIES_ROWS):
        cols = detail_cols[3 * k: 3 * k + 3]
        summary_col = "GHIJ"[k]
        _put(ws, f"{summary_col}2", f"=Monitor!$I${series_row}", font=BOLD)
        products = [
            f"SUMPRODUCT({c}${FIRST}:{c}${LAST},${s}${FIRST}:${s}${LAST})"
            for c, s in zip(cols, "CDE")
        ]
        lots = "($C$3*$C$4)"
        _put(ws, f"{summary_col}3", f"=({'+'.join(products)})/{lots}", PTS, BOLD)
        _put(ws, f"{summary_col}4", f"={products[0]}/{lots}", PTS)
        _put(ws, f"{summary_col}5", f"=({products[1]}+{products[2]})/{lots}", PTS)
        for c, shares_col in zip(cols, "CDE"):
            _put(ws, f"{c}7", f"=Monitor!$I${series_row}", font=BOLD)
            _put(ws, f"{c}8", f"=${shares_col}$8", font=BOLD)
            ws[f"{c}7"].fill = ws[f"{c}8"].fill = HEAD_FILL

    _header(ws["S8"], "Check")
    for row in range(FIRST, LAST + 1):
        link_row = row - FIRST + 2
        r = str(row)
        _put(ws, "B" + r, f'=IF(Python!$F${link_row}="","",Python!$F${link_row}&"")')
        _put(ws, "C" + r,
             f"=IF($B{r}=\"\",0,SUMPRODUCT({_match_text(chr(39) + 'Q Rebalance' + chr(39) + '!$J$8:$J$57', '$B' + r)},"
             f"'Q Rebalance'!$P$8:$P$57))", "#,##0")
        _put(ws, "D" + r,
             f"=IF($B{r}=\"\",0,SUMPRODUCT({_match_text(chr(39) + 'Basket Next' + chr(39) + '!$B$9:$B$88', '$B' + r)},"
             f"'Basket Next'!$M$9:$M$88))", "#,##0")
        _put(ws, "E" + r, f"=D{r}", "#,##0")
        for c in detail_cols:
            _put(ws, c + r,
                 f'=IF($B{r}="",0,SUMIFS(Python!$D:$D,Python!$E:$E,'
                 f'$B{r}&"|"&{c}$7&"|"&{c}$8))', DPS)
        _put(ws, "S" + r,
             f'=IF(OR(AND(G{r}+J{r}+M{r}+P{r}>0,C{r}=0),AND(H{r}+K{r}+N{r}+Q{r}>0,D{r}=0),'
             f'AND(I{r}+L{r}+O{r}+R{r}>0,E{r}=0)),"DPS but no shares","")', font=INPUT)

    for col, width in zip("ABCDEF", [2, 12, 12, 12, 12, 18]):
        ws.column_dimensions[col].width = width
    for col in detail_cols:
        ws.column_dimensions[col].width = 10
    ws.column_dimensions["S"].width = 18
    ws.freeze_panes = "C9"


def build_monitor(ws):
    # Holiday list must not shift row by row.
    for row in SERIES_ROWS:
        cell = ws[f"M{row}"]
        cell.value = re.sub(r"Other!\$?A\$?\d+:\$?A\$?\d+", "Other!$A$2:$A$30", cell.value)

    for col, text in zip("PQR", ["Div pts", "Fair (Rf)", "Fair (Borrow)"]):
        _put(ws, f"{col}6", text)
    for k, row in enumerate(SERIES_ROWS):
        _put(ws, f"P{row}", f"=Dividend!{'GHIJ'[k]}$3", PTS)
        _put(ws, f"Q{row}", f"=$D$6*(1+$I$3*O{row})-P{row}", PTS)
        _put(ws, f"R{row}", f"=$D$6*(1+$J$3*O{row})-P{row}", PTS)
    _put(ws, "P5", '=Dividend!$D$5', font=INPUT)

    _put(ws, "I12", "Calendar spread (far - near)")
    for col, text in zip("IJKLMNO", ["Spread", "Bid", "Ask", "Mid", "Div pts",
                                      "Fair (Rf)", "Fair (Borrow)"]):
        _put(ws, f"{col}13", text)
    for i, (near, far) in enumerate(zip(SERIES_ROWS, SERIES_ROWS[1:])):
        row = 14 + i
        _put(ws, f"I{row}", f'=I{near}&MID(I{far},4,3)')
        _put(ws, f"J{row}", f"=J{far}-K{near}", PTS)   # sell far, buy near
        _put(ws, f"K{row}", f"=K{far}-J{near}", PTS)   # buy far, sell near
        _put(ws, f"L{row}", f"=L{far}-L{near}", PTS)
        _put(ws, f"M{row}", f"=P{far}-P{near}", PTS)
        _put(ws, f"N{row}", f"=Q{far}-Q{near}", PTS)
        _put(ws, f"O{row}", f"=R{far}-R{near}", PTS)
    ws["P6"].comment = Comment(
        "Expected dividend index points with XD after the Python as-of date "
        "and on or before this series' last trading day (Dividend sheet).",
        "python")
    ws["Q6"].comment = Comment("Spot x (1 + risk-free x t) - Div pts", "python")
    ws["R6"].comment = Comment("Spot x (1 + borrowing x t) - Div pts", "python")


def build_other(ws):
    if ws["A1"].value in (None, ""):
        ws["A1"] = "SET holidays (dates, A2:A30)"
        ws["A1"].font = BOLD


def _sheet_xml(path, sheet_name):
    """Zip path of a sheet's XML, whatever attribute order the writer used."""
    with zipfile.ZipFile(path) as z:
        workbook = z.read("xl/workbook.xml").decode()
        rels = z.read("xl/_rels/workbook.xml.rels").decode()
    for tag in re.findall(r"<sheet\b[^>]*>", workbook):
        if re.search(rf'\bname="{re.escape(sheet_name)}"', tag):
            rid = re.search(r'\br:id="([^"]+)"', tag).group(1)
            break
    else:
        raise KeyError(sheet_name)
    for tag in re.findall(r"<Relationship\b[^>]*>", rels):
        if re.search(rf'\bId="{rid}"', tag):
            target = re.search(r'\bTarget="([^"]+)"', tag).group(1)
            return "xl/" + target.lstrip("/").removeprefix("xl/")
    raise KeyError(rid)


def main(source, target):
    source, target = Path(source), Path(target)
    monitor_xml_src = _sheet_xml(source, "Monitor")
    wb = load_workbook(source)
    build_other(wb["Other"])
    build_python(wb["Python"])
    build_basket_next(wb)
    build_dividend(wb)
    build_monitor(wb["Monitor"])
    wb.calculation.fullCalcOnLoad = True
    wb.save(target)
    with zipfile.ZipFile(source) as z:
        original = z.read(monitor_xml_src).decode()
    _restore_ext_lst_text(original, target, _sheet_xml(target, "Monitor"))


def _restore_ext_lst_text(original_xml, target, sheet_xml):
    match = re.search(r"<extLst>.*</extLst>", original_xml, re.S)
    if not match:
        return
    tmp = Path(tempfile.mkdtemp()) / "out.xlsx"
    with zipfile.ZipFile(target) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == sheet_xml:
                text = data.decode("utf-8")
                if "<extLst>" not in text:
                    text = text.replace("</worksheet>", match.group(0) + "</worksheet>")
                data = text.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp, target)


if __name__ == "__main__":
    main(*sys.argv[1:3])
