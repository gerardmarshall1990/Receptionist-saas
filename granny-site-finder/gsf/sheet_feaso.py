"""Google Sheet 'Feaso' + 'Settings' tabs (live formulas) and per-site feasibility docs (HTML -> Google Doc).

The Feaso tab mirrors gsf/feaso.py exactly, as spreadsheet formulas that read the Settings tab, so a
partner can type a price or change an assumption and every row recalculates and can be sorted by profit.
Qualifying = any site whose overall status is not 'fail'.
"""
from __future__ import annotations

import html
from datetime import date

from .report import assumptions

SETTINGS = [  # label, config key, notes
    ("Interest rate", "interest_rate", "annual"),
    ("Capitalised months", "cap_months", "build period, interest capitalised"),
    ("Draw factor", "draw_factor", "1.0 = all funds drawn on day 1 (conservative); 0.6 allowed"),
    ("Target yield", "target_yield", "end value = NOI / yield"),
    ("Yield basis", "yield_basis", "gross or net"),
    ("Opex ratio (net basis)", "opex_ratio", "only used when basis = net"),
    ("LTV cap", "ltv_cap", "bank limit"),
    ("Unit cost installed (each)", "unit_cost_installed", "supplier + foundations + services"),
    ("Misc per site", "misc", "legal, LIM, PIM, consents, design, DCs"),
    ("Stress: rent change", ("stress", "rent_change"), ""),
    ("Stress: dev cost change", ("stress", "cost_change"), "units + misc, not purchase price"),
    ("Stress: yield", ("stress", "target_yield"), ""),
    ("Stress: interest rate", ("stress", "interest_rate"), ""),
    ("Min interest cover", "min_io_cover", ""),
]
# Settings!B2.. in the order above
S = {k if isinstance(k, str) else k[1] + "_s": f"Settings!$B${i + 2}" for i, (_, k, _) in enumerate(SETTINGS)}

FEASO_HEADERS = [
    "Address", "Status", "Zone", "Land m²", "Price (enter)", "Bedrooms", "House rent $/wk", "Unit rent $/wk",
    "Units", "Cost base", "Loan (incl. interest)", "Capitalised interest", "Annual rent", "NOI", "End value",
    "Profit (equity)", "Profit / loan", "LTV", "IO cover", "Cash left in after refi", "Stress LTV",
    "Stress IO cover", "Feaso verdict", "Feasibility doc", "Map", "Still to check",
]


def settings_rows(cfg: dict) -> list[list]:
    f = cfg["feaso"]
    rows = [["Setting", "Value", "Notes"]]
    for label, key, note in SETTINGS:
        v = f["stress"][key[1]] if isinstance(key, tuple) else f.get(key, 1.0 if key == "min_io_cover" else None)
        rows.append([label, v, note])
    return rows


def feaso_formulas(r: int) -> list[str]:
    """Formulas for columns J..W of row r (1-based sheet row)."""
    E, G, H, I = f"$E{r}", f"$G{r}", f"$H{r}", f"$I{r}"
    denom = f"(1-{S['interest_rate']}*{S['cap_months']}/12*{S['draw_factor']})"
    net = f"IF({S['yield_basis']}=\"net\",1-{S['opex_ratio']},1)"
    loan_s = (f"(({E}+{I}*{S['unit_cost_installed']}*(1+{S['cost_change_s']})+{S['misc']}*(1+{S['cost_change_s']}))"
              f"/(1-{S['interest_rate_s']}*{S['cap_months']}/12*{S['draw_factor']}))")
    noi_s = f"(52*({G}+{I}*{H})*(1+{S['rent_change_s']})*{net})"
    ok = f"P{r}=\"\""
    return [
        f'=IF({E}="","",{E}+{I}*{S["unit_cost_installed"]}+{S["misc"]})',                       # J cost base
        f'=IF({E}="","",J{r}/{denom})',                                                          # K loan
        f'=IF({E}="","",K{r}-J{r})',                                                             # L cap interest
        f'=IF(OR({G}="",{H}=""),"",52*({G}+{I}*{H}))',                                           # M annual rent
        f'=IF(M{r}="","",M{r}*{net})',                                                           # N NOI
        f'=IF(N{r}="","",N{r}/{S["target_yield"]})',                                             # O end value
        f'=IF(OR(K{r}="",O{r}=""),"",O{r}-K{r})',                                                # P profit
        f'=IF({ok},"",P{r}/K{r})',                                                               # Q profit/loan
        f'=IF({ok},"",K{r}/O{r})',                                                               # R LTV
        f'=IF({ok},"",IF({S["interest_rate"]}=0,"n/a",N{r}/(K{r}*{S["interest_rate"]})))',      # S IO cover
        f'=IF({ok},"",MAX(0,K{r}-{S["ltv_cap"]}*O{r}))',                                         # T cash left in
        f'=IF({ok},"",{loan_s}/({noi_s}/{S["target_yield_s"]}))',                                # U stress LTV
        f'=IF({ok},"",{noi_s}/({loan_s}*{S["interest_rate_s"]}))',                               # V stress IO
        f'=IF({E}="","Enter price",IF(M{r}="","Enter rents",IF(AND(R{r}<={S["ltv_cap"]},S{r}>={S["min_io_cover"]}),"PASS","FAIL")))',  # W
    ]


def qualifying(sites: list[dict]) -> list[dict]:
    return [s for s in sites if s.get("overall") not in ("fail", "error", None)]


def feaso_rows(sites: list[dict], doc_links: dict[int, str] | None = None, first_row: int = 2) -> list[list]:
    rows = []
    for n, s in enumerate(qualifying(sites)):
        r = first_row + n
        F, L, fe = s.get("facts") or {}, s["listing"], s.get("feaso") or {}
        val = lambda k: (F.get(k) or {}).get("value")  # noqa: E731
        checks = "; ".join(g["reason"] for g in s["gates"] if g["hard"] and g["status"] in ("check", "incomplete")
                           and g["gate"] != "price")
        lat, lon = F.get("lat"), F.get("lon")
        link = (doc_links or {}).get(s["id"])
        rows.append([
            val("matched_address") or L["address"], s["overall"], val("zone") or "", val("land_area_m2") or "",
            L.get("price") or "", L.get("bedrooms") or "",
            (fe.get("existing_rent") or {}).get("weekly") or "", (fe.get("unit_rent") or {}).get("weekly") or "",
            int(s.get("units_override") or 2),
            *feaso_formulas(r),
            f'=HYPERLINK("{link}","feasibility")' if link else "",
            f'=HYPERLINK("https://www.google.com/maps?q={lat},{lon}","map")' if lat is not None else "",
            checks,
        ])
    return rows


# ------------------------------------------------------------------ docs --
def _m(v):
    return "—" if v is None else ("−$" if v < 0 else "$") + f"{abs(v):,.0f}"


def _p(v):
    return "—" if v is None else f"{v:.1%}"


def doc_html(site: dict, cfg: dict, sheet_url: str, feaso_row: int | None) -> str:
    F, L, fe = site.get("facts") or {}, site["listing"], site.get("feaso") or {}
    e = html.escape
    val = lambda k: (F.get(k) or {}).get("value")  # noqa: E731
    addr = val("matched_address") or L["address"]
    gates = "".join(f"<tr><td>{e(g['gate'].replace('_', ' '))}</td><td><b>{e(g['status'].upper())}</b></td><td>{e(g['reason'])}</td></tr>"
                    for g in site["gates"] if g["hard"])
    flags = "".join(f"<li>{e(g['reason'])}</li>" for g in site["gates"] if not g["hard"])
    ex, un = fe.get("existing_rent") or {}, fe.get("unit_rent") or {}
    rep = fe.get("report")
    if rep:
        b = rep["base"]
        io_txt = "n/a" if b["io_cover"] is None else f"{b['io_cover']:.2f}x"
        feaso = (f"<table border='1' cellpadding='4'><tr><th>Item</th><th>Value</th></tr>"
                 f"<tr><td>Price</td><td>{_m(rep['inputs']['price'])}</td></tr>"
                 f"<tr><td>Cost base (price + {rep['inputs']['units']} units + misc)</td><td>{_m(b['cost_base'])}</td></tr>"
                 f"<tr><td>Loan incl. capitalised interest</td><td>{_m(b['loan'])}</td></tr>"
                 f"<tr><td>Annual rent / NOI</td><td>{_m(b['annual_rent'])} / {_m(b['noi'])}</td></tr>"
                 f"<tr><td>End value</td><td>{_m(b['end_value'])}</td></tr>"
                 f"<tr><td><b>Profit (equity)</b></td><td><b>{_m(b['equity'])}</b></td></tr>"
                 f"<tr><td>LTV (cap {_p(rep['inputs']['ltv_cap'])})</td><td>{_p(b['ltv'])} {'PASS' if b['ltv_pass'] else 'FAIL'}</td></tr>"
                 f"<tr><td>Interest cover</td><td>{io_txt}</td></tr>"
                 f"<tr><td>Refinance headroom</td><td>{_m(b['refi_headroom'])}</td></tr></table>"
                 "<h3>Stress cases</h3><table border='1' cellpadding='4'><tr><th>Case</th><th>LTV</th><th>IO cover</th><th>Equity</th></tr>"
                 + "".join(f"<tr><td>{e(k)}</td><td>{_p(x['ltv'])}</td><td>{'n/a' if x['io_cover'] is None else format(x['io_cover'], '.2f')}</td><td>{_m(x['equity'])}</td></tr>"
                           for k, x in rep["scenarios"].items()) + "</table>")
    else:
        where = f"row {feaso_row} of the Feaso tab" if feaso_row else "the Feaso tab"
        feaso = (f"<p><b>Not run yet: {e(fe.get('note') or 'missing inputs')}.</b> Enter the price in {where} of the "
                 f"<a href='{e(sheet_url)}'>shortlist sheet</a>; the profit, LTV and stress numbers calculate live there.</p>")
    facts = [("Legal description", val("legal_description")), ("Title", F.get("title_type", {}).get("note") or val("title_type")),
             ("Land area (council boundary)", f"{val('land_area_m2'):,.0f} m²" if val("land_area_m2") else None),
             ("Shape (min width × depth)", f"{val('width_m')} m × {val('depth_m')} m" if val("width_m") else None),
             ("Road frontage", "yes" if val("road_frontage") else "no / unknown"),
             ("Zone (operative)", val("zone")), ("PC120 proposed zone", val("pc120_zone") or "none"),
             ("Building outlines on parcel (LINZ)", val("building_outlines")),
             ("SA2", (val("sa2") or {}).get("name") if isinstance(val("sa2"), dict) else None)]
    lat, lon = F.get("lat"), F.get("lon")
    return f"""<html><body>
<h1>Feasibility: {e(addr)}</h1>
<p>Status: <b>{e(site['overall'].upper())}</b> · Generated {date.today():%d %b %Y} by Granny-Site Finder · Screening estimate, not a valuation.</p>
<p>{'<a href="' + e(L['url']) + '">Listing</a> · ' if L.get('url') else ''}<a href="https://www.google.com/maps?q={lat},{lon}">Map</a> · <a href="{e(sheet_url)}">Shortlist sheet</a></p>
<h2>1. Feasibility</h2>{feaso}
<h2>2. Rents used</h2><ul><li>House: {e(ex.get('label') or '—')}</li><li>Units: {e(un.get('label') or '—')}</li></ul>
<h2>3. Site qualification</h2><table border='1' cellpadding='4'><tr><th>Gate</th><th>Result</th><th>Reason</th></tr>{gates}</table>
<h3>Flags to check</h3><ul>{flags}</ul>
<h2>4. Site facts</h2><table border='1' cellpadding='4'>{''.join(f'<tr><td>{e(k)}</td><td>{e(str(v))}</td></tr>' for k, v in facts if v not in (None, ''))}</table>
<h2>5. Assumptions</h2><ul>{''.join(f'<li>{e(a)}</li>' for a in assumptions(cfg))}</ul>
</body></html>"""
