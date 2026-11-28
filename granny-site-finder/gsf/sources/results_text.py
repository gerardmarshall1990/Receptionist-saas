"""Listings from text the user copied off a property results page (select-all, copy, paste).

The user copies what their own browser shows; nothing is fetched or scraped. The text is messy, so
this pulls out every line that looks like a street address and attaches the price, bedroom count and
land area found in the lines that follow it (until the next address). Unknown bits are left blank,
so those sites show 'incomplete' rather than guessed.
"""
from __future__ import annotations

import re
from typing import Iterator

from ..lookups.council import parse_address
from ..models import Listing
from ..text import parse_price
from .base import ListingSource, build_listing

_ADDR_LINE = re.compile(r"^\s*(?:[A-Za-z]?\d+[A-Za-z]?\s*/\s*)?\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?\s+[A-Za-z][A-Za-z' .-]+"
                        r"\b(road|rd|street|st|avenue|ave|crescent|cres|drive|dr|place|pl|terrace|tce|lane|ln|grove|"
                        r"close|court|way|parade|highway|hwy|rise|heights|mews|view|circle|boulevard|esplanade|"
                        r"square|glade|green|walk|loop|track|quay)\b", re.I)
_ADDR_COMMA = re.compile(r"^\s*(?:[A-Za-z]?\d+[A-Za-z]?\s*/\s*)?\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?\s+[A-Za-z][A-Za-z' .-]{2,40},\s*[A-Za-z]")
_NOT_ADDR = re.compile(r"\b(bed|bath|m²|m2|sqm|car|garage|price|\$)", re.I)


def _is_addr(line: str) -> bool:
    return bool((_ADDR_LINE.match(line) or _ADDR_COMMA.match(line)) and not _NOT_ADDR.search(line)
                and parse_address(line))


_PRICE = re.compile(r"(\$\s?\d[\d,.]*\s*(k|m)?)|auction|tender|deadline|negotiation|enquiries over|price by|poa", re.I)
_BEDS = re.compile(r"\b(\d+)\s*(bed|bedroom|bdrm|br)s?\b", re.I)
_LAND = re.compile(r"(?:land(?:\s*area)?[:\s]*)?(\d[\d,.]*)\s*(m²|m2|sqm|square metres)", re.I)
_URL = re.compile(r"https?://\S+")
_SKIP = re.compile(r"^(featured|new listing|open home|premium|sponsored|save|share|photos?)\b", re.I)


class ResultsTextSource(ListingSource):
    name = "paste"

    def __init__(self, text: str):
        super().__init__()
        self.text = text or ""

    def listings(self) -> Iterator[Listing]:
        lines = [l.strip() for l in self.text.splitlines()]
        blocks: list[tuple[str, list[str]]] = []
        for l in lines:
            if not l or _SKIP.match(l):
                continue
            if _is_addr(l):
                blocks.append((l, []))
            elif blocks:
                blocks[-1][1].append(l)
        seen = set()
        for addr, rest in blocks:
            key = re.sub(r"\W+", " ", addr.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            body = "\n".join(rest)
            price_line = next((r for r in rest if _PRICE.search(r)), None)
            price, indicative, ptxt = parse_price(price_line)
            beds = _BEDS.search(body)
            land = None
            for m in _LAND.finditer(body):
                v = float(m.group(1).replace(",", ""))
                if v >= 100:          # ignore floor areas < 100 m² mis-read; prefer the largest (land) figure
                    land = max(land or 0, v)
            url = _URL.search(body)
            try:
                yield build_listing(self.name, {
                    "address": addr, "price": ptxt or "", "price_text": ptxt or "",
                    "bedrooms": beds.group(1) if beds else "", "land_area_m2": land or "",
                    "url": url.group(0) if url else "", "description": body[:1000],
                })
            except ValueError as e:
                self.rejected.append((addr, str(e)))


def looks_like_results_page(text: str) -> bool:
    """True when pasted text is a results page rather than one-listing-per-line input."""
    n = sum(1 for l in text.splitlines() if _is_addr(l.strip()))
    return "|" not in text and n >= 2 and len([l for l in text.splitlines() if l.strip()]) > 2 * n
