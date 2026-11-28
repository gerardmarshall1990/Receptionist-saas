"""Shortlist workbook (.xlsx) for sharing / uploading to Google Sheets.

Tabs: Shortlist (one row per site), Gates (every gate + reason), Assumptions, Inbox (paste new listings).
"""
from __future__ import annotations

from datetime import datetime

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .report import assumptions

HEAD_FILL = PatternFill("solid", fgColor="1F5C46")
HEAD_FONT = Font(bold=True, color="FFFFFF")
STATUS_FILL = {"pass": "E3F2E9", "check": "FBF0D9", "incomplete": "ECEEF0", "fail": "FBE5E2"}

COLS = [  # header, key, number format, width
    ("Rank", "rank", "0", 6), ("Status", "status", None, 11), ("Address", "matched_address", None, 42),
    ("Price", "price", '"$"#,##0', 12), ("Land m²", "land_m2", "#,##0", 9), ("Zone", "zone", None, 30),
    ("Title", "title_type", None, 22), ("LTV", "ltv", "0.0%", 8), ("Stress LTV", "stress_ltv", "0.0%", 10),
    ("IO cover", "io_cover", "0.00", 9), ("Equity", "equity", '"$"#,##0;[Red]-"$"#,##0', 12),
    ("End value", "end_value", '"$"#,##0', 12), ("Loan", "loan", '"$"#,##0', 12),
    ("House rent $/wk", "existing_rent_pw", '"$"#,##0', 11), ("Unit rent $/wk", "unit_rent_pw", '"$"#,##0', 11),
    ("Why not pass", "why", None, 60), ("Flags", "flags", None, 50), ("Rent source", "rent_source", None, 60),
    ("Map", "map", None, 12), ("Listing", "url", None, 12),
]


def _header(ws, headers, widths):
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        c = ws.cell(row=1, column=i)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 30


def build_workbook(rows: list[dict], sites: list[dict], cfg: dict, path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Shortlist"
    _header(ws, [c[0] for c in COLS], [c[3] for c in COLS])
    for r in rows:
        why = r["fails"] or r["checks"] or ""
        if r.get("feaso_status") == "incomplete" and r.get("feaso_note"):
            why += (" · " if why else "") + "Feaso: " + r["feaso_note"]
        vals = dict(r, why=why, matched_address=r["matched_address"] or r["address"])
        line = []
        for _, key, _, _ in COLS:
            if key == "map":
                line.append(None)
            else:
                line.append(vals.get(key))
        ws.append(line)
        n = ws.max_row
        for i, (_, key, fmt, _) in enumerate(COLS, 1):
            c = ws.cell(row=n, column=i)
            c.alignment = Alignment(vertical="top", wrap_text=key in ("why", "flags", "rent_source", "matched_address"))
            if fmt:
                c.number_format = fmt
        if r.get("lat") is not None:
            c = ws.cell(row=n, column=[k for _, k, _, _ in COLS].index("map") + 1)
            c.value = f'=HYPERLINK("https://www.google.com/maps?q={r["lat"]},{r["lon"]}","map")'
        if r.get("url"):
            c = ws.cell(row=n, column=[k for _, k, _, _ in COLS].index("url") + 1)
            c.value = f'=HYPERLINK("{r["url"]}","listing")'
    last = max(ws.max_row, 2)
    for status, color in STATUS_FILL.items():
        ws.conditional_formatting.add(f"A2:{get_column_letter(len(COLS))}{last}",
                                      FormulaRule(formula=[f'$B2="{status}"'], fill=PatternFill("solid", fgColor=color)))
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}{last}"

    g = wb.create_sheet("Gates")
    _header(g, ["Address", "Gate", "Status", "Hard gate", "Reason"], [42, 22, 11, 10, 100])
    for s in sites:
        addr = ((s.get("facts") or {}).get("matched_address") or {}).get("value") or s["listing"]["address"]
        for x in s.get("gates", []):
            g.append([addr, x["gate"].replace("_", " "), x["status"], "yes" if x["hard"] else "flag", x["reason"]])
            g.cell(row=g.max_row, column=5).alignment = Alignment(wrap_text=True, vertical="top")
    g.auto_filter.ref = f"A1:E{max(g.max_row, 2)}"

    a = wb.create_sheet("Assumptions")
    _header(a, ["Assumption"], [140])
    a.append([f"Generated {datetime.now():%Y-%m-%d %H:%M} by Granny-Site Finder. Screening estimates, not valuations."])
    for line in assumptions(cfg):
        a.append([line])
        a.cell(row=a.max_row, column=1).alignment = Alignment(wrap_text=True)

    i = wb.create_sheet("Inbox")
    _header(i, ["Address", "Price", "Bedrooms", "Existing dwellings", "Listing URL", "Notes / listing text", "Added by"],
            [42, 14, 10, 12, 40, 60, 14])
    i.append(["14 Example Street, Suburb", 950000, 3, 1, "", "Example row: delete me", ""])
    i.cell(row=2, column=2).number_format = '"$"#,##0'
    for c in i[2]:
        c.font = Font(italic=True, color="7C8087")
    wb.save(path)
    return path
