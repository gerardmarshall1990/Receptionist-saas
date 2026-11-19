"""Live Google reviews for one lead, via the official Places API (New).

Policy (Google Maps Platform policies, read 2026-11-19):
- place IDs may be stored indefinitely -> we store `businesses.google_place_id`;
- reviews, rating, names, addresses are Google Maps Content: fetched live, shown with
  attribution (author, "Google Maps", link to the review), never stored or cached;
- deriving stored signals from review content needs counsel first, so nothing here
  feeds scoring.

Cost control: Text Search with an IDs-only field mask, then one Place Details call
(Enterprise + Atmosphere SKU, ~$0.025, first 1,000/month free). Every Details call is
reserved against `api_usage` before it is made; over the monthly cap -> refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
import psycopg

from leads_engine.universe import normalize as nz

BASE = "https://places.googleapis.com/v1"
PROVIDER = "google_places_details"
DETAILS_FIELDS = ",".join(
    ("id", "displayName", "formattedAddress", "internationalPhoneNumber", "rating",
     "userRatingCount", "reviews", "googleMapsUri")
)  # fmt: skip


# Phone-pain phrases highlighted in reviews (plus each category's review_pain_keywords).
GENERIC_PAIN = (
    "never answer", "didn't answer", "did not answer", "no answer", "voicemail", "voice mail",
    "couldn't get through", "could not get through", "hard to reach", "never called back",
    "didn't call back", "never returned", "called several times", "called multiple times",
    "no one answered",
)  # fmt: skip


class CapReachedError(RuntimeError):
    pass


@dataclass
class GoogleResult:
    status: str  # matched | no_match | not_found
    place_id: str | None = None
    detail: str = ""
    place: dict[str, Any] = field(default_factory=dict)  # live content, never stored


def reserve_call(conn: psycopg.Connection[Any], cap: int, today: date | None = None) -> int:
    """Count one Details call for this month; refuse (and count nothing) over the cap."""
    month = (today or date.today()).replace(day=1)
    row = conn.execute(
        "insert into api_usage (provider, month, calls) values (%s, %s, 1)"
        " on conflict (provider, month) do update set calls = api_usage.calls + 1"
        " where api_usage.calls < %s returning calls",
        (PROVIDER, month, cap),
    ).fetchone()
    calls = None if row is None else (row["calls"] if isinstance(row, dict) else row[0])
    if calls is None or calls > cap:
        raise CapReachedError(
            f"monthly Google Places cap reached ({cap} calls);"
            " raise google_places_monthly_cap to allow more"
        )
    return int(calls)


def _words(name: str | None) -> set[str]:
    return {w for w in nz.name_key(name).split() if len(w) >= 3 and w not in nz._GENERIC_WORDS}


def is_same_business(lead: dict[str, Any], place: dict[str, Any]) -> tuple[bool, str]:
    """Phone match is decisive; otherwise name overlap plus ZIP or street number."""
    gphone = nz.normalize_phone(place.get("internationalPhoneNumber"))
    phones = {lead.get("phone")} | set(lead.get("phones") or [])
    if gphone and gphone.e164 and gphone.e164 in phones:
        return True, "phone matches"
    lw, gw = _words(lead.get("name")), _words((place.get("displayName") or {}).get("text"))
    overlap = len(lw & gw) / len(lw) if lw else 0.0
    addr = (place.get("formattedAddress") or "").lower()
    zip_ok = bool(lead.get("postcode")) and str(lead["postcode"]) in addr
    num = re.match(r"\s*(\d+)\b", lead.get("address") or "")
    street_ok = bool(num) and re.search(rf"\b{num.group(1)}\b", addr) is not None if num else False
    if overlap >= 0.5 and (zip_ok or street_ok):
        return True, "name and address match"
    return (
        False,
        f"name overlap {overlap:.0%}, zip {'yes' if zip_ok else 'no'},"
        f" street no. {'yes' if street_ok else 'no'}",
    )


class PlacesClient:
    def __init__(
        self, api_key: str | None = None, transport: httpx.BaseTransport | None = None
    ) -> None:
        headers = {"X-Goog-Api-Key": api_key} if api_key else {}
        self.http = httpx.Client(base_url=BASE, headers=headers, timeout=15.0, transport=transport)

    def close(self) -> None:
        self.http.close()

    def search_id(self, lead: dict[str, Any]) -> str | None:
        query = ", ".join(
            str(x)
            for x in (lead["name"], lead.get("address"), lead.get("city"), lead.get("state"))
            if x
        )
        body: dict[str, Any] = {"textQuery": query, "pageSize": 1}
        if lead.get("lat") is not None and lead.get("lng") is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": lead["lat"], "longitude": lead["lng"]},
                    "radius": 2000.0,
                }
            }
        r = self.http.post(
            "/places:searchText", json=body, headers={"X-Goog-FieldMask": "places.id"}
        )
        r.raise_for_status()
        places = r.json().get("places") or []
        return places[0]["id"] if places else None

    def details(self, place_id: str) -> dict[str, Any]:
        r = self.http.get(f"/places/{place_id}", headers={"X-Goog-FieldMask": DETAILS_FIELDS})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data


def live_reviews(
    conn: psycopg.Connection[Any], client: PlacesClient, lead: dict[str, Any], cap: int
) -> GoogleResult:
    """Find (or reuse) the lead's place ID, fetch details live, verify it's the same business."""
    place_id = lead.get("google_place_id") or client.search_id(lead)
    if not place_id:
        return GoogleResult("not_found", detail="Google found no place for this name and address")
    reserve_call(conn, cap)
    place = client.details(place_id)
    same, why = is_same_business(lead, place)
    if not same:
        if lead.get("google_place_id"):  # a stored ID that no longer matches: forget it
            conn.execute(
                "update businesses set google_place_id = null where id = %s", (lead["id"],)
            )
        return GoogleResult(
            "no_match", detail=f"Top Google result doesn't look like this business ({why})"
        )
    if lead.get("google_place_id") != place_id:
        conn.execute(
            "update businesses set google_place_id = %s where id = %s", (place_id, lead["id"])
        )
    return GoogleResult("matched", place_id=place_id, detail=why, place=place)
