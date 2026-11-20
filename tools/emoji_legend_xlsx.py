"""Owner 20.11: legend -> xlsx with an empty column for the owner's extra contexts (read back later to merge into emoji_legend)."""
import json, os, sys
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.worksheet.table import Table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "agent", "emoji_legend_contexts.xlsx")
d = json.load(open(os.path.join(ROOT, "agent", "emoji_legend.json"), encoding="utf-8"))
wb = Workbook(); ws = wb.active; ws.title = "הקשרי אייקונים"; ws.sheet_view.rightToLeft = True
ws.append(["#", "אייקון", "הקשרים קיימים", "הקשרים נוספים (יקיר)"])
for i, (k, v) in enumerate(d.items(), 1):
    ws.append([i, k, ", ".join(v), None])
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="032331")
ws.column_dimensions["A"].width = 6; ws.column_dimensions["B"].width = 9
ws.column_dimensions["C"].width = 70; ws.column_dimensions["D"].width = 45
for r in ws.iter_rows(min_row=2):
    r[1].font = Font(name="Segoe UI Emoji", size=16); r[1].alignment = Alignment(horizontal="center", vertical="center")
    r[2].alignment = Alignment(wrap_text=True, vertical="center"); r[3].fill = PatternFill("solid", fgColor="FFF4CC")
ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
wb.save(out); print(out, len(d))
