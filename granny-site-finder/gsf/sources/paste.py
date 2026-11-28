"""Paste-addresses source (CLI stdin / web form textarea).

One listing per line. Either just an address, or pipe-separated fields:
    address | price | bedrooms | existing dwellings | CV | notes
e.g.
    12 Sandringham Rd, Kingsland | $1,050,000 | 3 | 1
    45 Example Street, Mt Roskill | Enquiries over 950k | 3
Blank lines and lines starting with # are ignored.
"""
from __future__ import annotations

from typing import Iterator

from ..models import Listing
from .base import ListingSource, build_listing

FIELDS = ["address", "price", "bedrooms", "existing_dwellings", "cv", "description"]


class PasteSource(ListingSource):
    name = "paste"

    def __init__(self, text: str):
        super().__init__()
        self.text = text or ""

    def listings(self) -> Iterator[Listing]:
        from .results_text import ResultsTextSource, looks_like_results_page
        if looks_like_results_page(self.text):          # a copied results page, not one-per-line input
            src = ResultsTextSource(self.text)
            yield from src.listings()
            self.rejected.extend(src.rejected)
            return
        for raw in self.text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            fields = dict(zip(FIELDS, parts))
            try:
                yield build_listing(self.name, fields)
            except ValueError as e:
                self.rejected.append((raw, str(e)))
