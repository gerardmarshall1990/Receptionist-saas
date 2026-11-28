"""ListingSource: the single interface every ingestion route implements, so sources can be swapped."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator

from ..models import Listing, normalise_key
from ..text import parse_price


class ListingSource(ABC):
    name: str = "abstract"

    @abstractmethod
    def listings(self) -> Iterator[Listing]:
        """Yield listings. Must not raise for one bad record; skip it and record it in self.rejected."""

    def __init__(self) -> None:
        self.rejected: list[tuple[str, str]] = []   # (raw input, reason)


def _int(v: Any) -> int | None:
    try:
        return int(float(str(v).strip())) if str(v).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        s = str(v).replace(",", "").replace("m2", "").replace("m²", "").replace("sqm", "").strip()
        return float(s) if s not in ("", "None") else None
    except (TypeError, ValueError):
        return None


def build_listing(source: str, fields: dict[str, Any]) -> Listing:
    """Normalise a dict of loosely-typed fields (from CSV, form or paste) into a Listing."""
    address = (fields.get("address") or "").strip()
    if not address:
        raise ValueError("address is required")
    price_raw = fields.get("price")
    price, indicative, ptxt = parse_price(price_raw if price_raw not in (None, "") else fields.get("price_text"))
    return Listing(
        address=address,
        source=source,
        source_id=(fields.get("source_id") or fields.get("listing_id") or None) or normalise_key(address),
        url=(fields.get("url") or None),
        price=price,
        price_text=ptxt or (fields.get("price_text") or None),
        price_is_indicative=indicative,
        bedrooms=_int(fields.get("bedrooms")),
        bathrooms=_float(fields.get("bathrooms")),
        land_area_m2=_float(fields.get("land_area_m2")),
        property_type=(fields.get("property_type") or None),
        listing_date=(fields.get("listing_date") or None),
        description=(fields.get("description") or None),
        existing_dwellings=_int(fields.get("existing_dwellings")),
        cv=parse_price(fields.get("cv") or fields.get("gv") or fields.get("capital_value") or None)[0],
        existing_rent_override=_float(fields.get("existing_rent_override")),
        unit_rent_override=_float(fields.get("unit_rent_override")),
    )
