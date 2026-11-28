"""Shortlist rows, CSV export and markdown report. Assumptions are printed in every report."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

STATUS_RANK = {"pass": 0, "check": 1, "incomplete": 2, "fail": 3, "error": 4, "pending": 5}

CSV_COLUMNS = ["rank", "status", "address", "matched_address", "price", "land_m2", "zone", "title_type",
               "fails", "checks", "flags", "cost_base", "loan", "end_value", "ltv", "equity", "io_cover",
               "stress_ltv", "stress_io_cover", "existing_rent_pw", "unit_rent_pw", "rent_source", "url", "id"]


def _f(facts: dict | None, key: str):
    return ((facts or {}).get(key) or {}).get("value")


def summary_row(site: dict[str, Any]) -> dict[str, Any]:
    facts = site.get("facts") or {}
    gates = site.get("gates") or []
    fe = site.get("feaso") or {}
    rep = fe.get("report") or {}
    base = rep.get("base") or {}
    ex, un = fe.get("existing_rent") or {}, fe.get("unit_rent") or {}
    title = _f(facts, "title_type")
    tstatus = ((facts.get("title_type") or {}).get("status"))
    return {
        "id": site["id"],
        "status": site.get("overall"),
        "address": site["listing"]["address"],
        "matched_address": _f(facts, "matched_address"),
        "price": site["listing"].get("price"),
        "price_text": site["listing"].get("price_text"),
        "land_m2": _f(facts, "land_area_m2"),
        "zone": _f(facts, "zone") or ((facts.get("zone") or {}).get("note") and "unverified"),
        "title_type": f"{title} ({tstatus})" if title and tstatus != "verified" else (title or "unverified"),
        "fails": "; ".join(g["reason"] for g in gates if g["hard"] and g["status"] == "fail"),
        "checks": "; ".join(g["reason"] for g in gates if g["hard"] and g["status"] in ("check", "incomplete")),
        "flags": "; ".join(g["reason"] for g in gates if not g["hard"]
                           and g["gate"] not in ("slope", "manual_title_check")),
        "cost_base": base.get("cost_base"),
        "loan": base.get("loan"),
        "end_value": base.get("end_value"),
        "ltv": base.get("ltv"),
        "equity": base.get("equity"),
        "io_cover": base.get("io_cover"),
        "stress_ltv": rep.get("worst_ltv"),
        "stress_io_cover": rep.get("worst_io_cover"),
        "existing_rent_pw": ex.get("weekly"),
        "unit_rent_pw": un.get("weekly"),
        "rent_source": f"existing: {ex.get('label')} | units: {un.get('label')}" if ex or un else "",
        "feaso_status": fe.get("status"),
        "feaso_note": fe.get("note"),
        "url": site["listing"].get("url"),
        "lat": facts.get("lat"),
        "lon": facts.get("lon"),
        "errors": len(facts.get("errors") or []),
    }


def ranked(sites: list[dict]) -> list[dict]:
    rows = [summary_row(s) for s in sites]
    rows.sort(key=lambda r: (STATUS_RANK.get(r["status"], 9),
                             r["ltv"] if r["ltv"] is not None else 99, -(r["equity"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    return buf.getvalue()


def assumptions(cfg: dict) -> list[str]:
    f, g, r = cfg["feaso"], cfg["gates"], cfg["rents"]
    s = f["stress"]
    return [
        "Screening estimates only, not valuations. Confirm everything before relying on it.",
        f"Feaso: rate {f['interest_rate']:.2%}, {f['cap_months']} months capitalised, draw factor {f['draw_factor']}, "
        f"target yield {f['target_yield']:.2%} ({f['yield_basis']}{', opex ' + format(f['opex_ratio'], '.0%') if f['yield_basis'] == 'net' else ''}), "
        f"LTV cap {f['ltv_cap']:.0%}, {f['units']} units × ${f['unit_cost_installed']:,.0f} installed, misc ${f['misc']:,.0f}.",
        f"Stress (each alone and combined): rents {s['rent_change']:+.0%}, dev cost {s['cost_change']:+.0%} "
        f"(units + misc, not purchase price), yield {s['target_yield']:.1%}, rate {s['interest_rate']:.1%}.",
        f"Rents: Tenancy Services bond data by SA2 2018, {r['existing_rent_stat']} for listing bedrooms "
        f"({'/'.join(r['existing_dwelling_types'])}); units {r['unit_rent_stat']} {r['unit_bedrooms']}-bed "
        f"({'/'.join(r['unit_dwelling_types'])}); min {r['min_bonds']} bonds, else last {r.get('pool_quarters', 4)} quarters pooled, then SA2s within {r.get('neighbour_radius_m', 0)} m.",
        f"Gates: zone in {', '.join(z.replace('Residential - ', '') for z in g['allowed_zones'])} (operative AUP); "
        f"freehold title; land ≥ {g['min_land_m2']} m²; 1 existing dwelling; price ≤ ${g['max_price']:,.0f}; "
        f"no flood/OLFP on the central {g['building_core_scale']:.0%}-scaled 'likely building area'.",
        "Title type is only 'verified' from LINZ titles. Council legal descriptions can show cross-lease/unit "
        "title but cannot prove fee simple.",
        "Land area is computed from the council property boundary in NZTM; for cross-leases it is the shared lot.",
        "PC120 (proposed plan change) is shown for information; it has no final decision (expected mid-2027).",
    ]


def _money(v):
    return "" if v is None else f"${v:,.0f}"


def _pct(v):
    return "" if v is None else f"{v:.1%}"


def to_markdown(rows: list[dict], cfg: dict, title: str = "Granny-site shortlist") -> str:
    out = [f"# {title}", f"_Generated {datetime.now():%Y-%m-%d %H:%M}_", "", "## Assumptions"]
    out += [f"- {a}" for a in assumptions(cfg)]
    out += ["", "## Shortlist", "",
            "| # | Status | Address | Price | Land m² | Zone | Title | LTV | Stress LTV | IO cover | Equity | Notes |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        notes = r["fails"] or r["checks"] or ""
        if r["feaso_status"] == "incomplete":
            notes += (" · " if notes else "") + f"feaso: {r['feaso_note']}"
        io_c = "" if r["io_cover"] is None else f"{r['io_cover']:.2f}"
        out.append("| {rank} | {st} | {addr} | {price} | {land} | {zone} | {title} | {ltv} | {sltv} | {io} | {eq} | {notes} |".format(
            rank=r["rank"], st=r["status"].upper(), addr=r["matched_address"] or r["address"],
            price=_money(r["price"]) or (r["price_text"] or "—"),
            land="" if r["land_m2"] is None else f"{r['land_m2']:,.0f}",
            zone=(r["zone"] or "").replace("Residential - ", "").replace(" Zone", ""),
            title=r["title_type"], ltv=_pct(r["ltv"]), sltv=_pct(r["stress_ltv"]), io=io_c,
            eq=_money(r["equity"]), notes=notes.replace("|", "/")))
    out += ["", "## Rent sources", ""]
    out += [f"- **{r['matched_address'] or r['address']}**: {r['rent_source']}" for r in rows]
    return "\n".join(out) + "\n"
