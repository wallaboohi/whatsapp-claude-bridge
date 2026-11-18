"""מעקב לינה וארוחות — קובץ אקסל ראשוני (שלב 1). כותב קובץ חדש בלבד; לא נוגע בשום דבר אחר.
שימוש: uv run -q --no-project --with openpyxl python -I -X utf8 build_tracker.py <נתיב יעד.xlsx>
"""
import sys
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation

out = sys.argv[1]
wb = Workbook()

HDR = PatternFill("solid", fgColor="17263C")
HFONT = Font(name="Arial", bold=True, color="FFFFFF")
BODY = Font(name="Arial")


def header(ws, cols):
    ws.append(cols)
    for c in ws[1]:
        c.fill, c.font = HDR, HFONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    for i, w in enumerate([22, 14, 20, 30], 1):
        ws.column_dimensions[chr(64 + i)].width = w


# ---- לינה ----
lod = wb.active
lod.title = "לינה"
header(lod, ["שם", "תאריך", "מקום / מיטה", "הערה"])
lod.column_dimensions["E"].width = 30

# ---- ארוחות ----
meals = wb.create_sheet("ארוחות")
header(meals, ["שם", "תאריך", "ארוחה", "מה אכל", "הערה"])
meals.column_dimensions["E"].width = 30

dv_meal = DataValidation(type="list", formula1='"בוקר,צהריים,ערב,ארוחה"', allow_blank=True)
meals.add_data_validation(dv_meal)
dv_meal.add("C2:C2000")

# ---- סיכום חודש ----
summ = wb.create_sheet("סיכום חודש")
summ["A1"] = "חודש (MM/YYYY)"
summ["B1"] = "11/2026"
summ["A1"].font = summ["B1"].font = BODY
summ["A3"] = "שם"
summ["B3"] = "ימי לינה"
summ["C3"] = "ארוחות"
for c in summ[3]:
    c.fill, c.font = HDR, HFONT
    c.alignment = Alignment(horizontal="center")
summ.column_dimensions["A"].width = 24
summ.column_dimensions["B"].width = 14
summ.column_dimensions["C"].width = 14

# שורות השמות וחישוב הנוסחאות (COUNTIFS לפי שם וחודש ב-B1) יתווספו בשלב הבא
summ["A2"] = "(שלב 1: עדיין בלי נוסחאות סיכום)"
summ["A2"].font = Font(name="Arial", italic=True, color="6B7280")
summ.sheet_view.rightToLeft = True
for ws in (lod, meals, summ):
    ws.sheet_view.rightToLeft = True

wb.save(out)
print(out)
