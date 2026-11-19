"""Plain-language facts about a lead, derived ONLY from stored fields.

These are not "why this lead" reasons (those come from crawled/review evidence in
the `reasons` table, Stage 2+). Facts describe what Stage 1 knows: contact
coverage, locations, data-quality cautions. Nothing here is inferred or scored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from leads_engine.config_loader import CategoryConfig

Kind = Literal["value", "reachability", "caution", "info"]


@dataclass(frozen=True)
class Fact:
    kind: Kind
    text: str
    source: str  # which stored field this comes from


def facts_for(
    b: dict[str, Any], phones: list[dict[str, Any]], emails: list[dict[str, Any]]
) -> list[dict[str, str]]:
    flags = set(b.get("flags") or [])
    out: list[Fact] = []
    if "multiple_locations" in flags:
        out.append(
            Fact("value", f"{b['location_count']} locations share {b['domain']}", "location_count")
        )
    usable = [p for p in phones if p["status"] in {"unverified", "likely", "verified"}]
    if b.get("phone"):
        out.append(
            Fact("reachability", f"Phone listed in Overture ({b['phone_status']})", "phones")
        )
    elif "no_phone" in flags:
        out.append(Fact("caution", "No phone number listed", "flags"))
    if "no_usable_phone" in flags:
        out.append(Fact("caution", "Only toll-free/invalid numbers listed", "flags"))
    if len(usable) > 1:
        out.append(Fact("info", f"{len(usable)} phone numbers listed", "phones"))
    if b.get("domain"):
        out.append(Fact("reachability", f"Own website: {b['domain']}", "domain"))
    else:
        out.append(
            Fact("caution", "No own website found, so site signals can't be checked yet", "domain")
        )
    for f in sorted(flags):
        if f.startswith("not_own_site:"):
            out.append(
                Fact(
                    "info",
                    f"Listed site is a page on {f.split(':', 1)[1]}, not their own site",
                    "flags",
                )
            )
        elif f.startswith("government_page:"):
            out.append(
                Fact("info", f"Listed site is a government page ({f.split(':', 1)[1]})", "flags")
            )
    site_emails = [e for e in emails if e["origin"] == "business_site"]
    listed = [e for e in emails if e["origin"] == "source_listed"]
    if site_emails:
        out.append(
            Fact("reachability", f"{len(site_emails)} email(s) found on their own site", "emails")
        )
    if listed:
        on_domain = sum(1 for e in listed if e["domain_matches_site"])
        text = (
            f"{len(listed)} source-listed email(s), {on_domain} on their own domain;"
            " unverified, not exportable"
        )
        out.append(Fact("info", text, "emails"))
    if "shared_phone" in flags:
        out.append(
            Fact(
                "caution",
                "Phone is also listed by other businesses (call center or shared line?)",
                "flags",
            )
        )
    if "name_repeated_review" in flags:
        out.append(
            Fact(
                "caution",
                "Same name at 10+ locations with different websites: check it isn't a chain",
                "flags",
            )
        )
    if "generic_name" in flags:
        out.append(
            Fact(
                "caution",
                "Generic listing name: confirm the real business name before calling",
                "flags",
            )
        )
    if "subdomain_platform" in flags:
        out.append(Fact("info", "Website is on a website-builder subdomain", "flags"))
    return [asdict(f) for f in out]


def local_time(tz: str | None, now: datetime | None = None) -> datetime | None:
    if not tz:
        return None
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return (now or datetime.now(tz=ZoneInfo("UTC"))).astimezone(zone)


def callable_now(cat: CategoryConfig | None, tz: str | None, now: datetime | None = None) -> bool:
    lt = local_time(tz, now)
    if cat is None or lt is None:
        return False
    day = lt.strftime("%a").lower()
    t = time(lt.hour, lt.minute, lt.second)
    return any(
        day in {d.value for d in w.days} and w.start <= t <= w.end for w in cat.best_call_windows
    )
