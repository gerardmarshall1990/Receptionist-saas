"""CSV import source.

Required column: address. Optional: price, price_text, bedrooms, bathrooms, land_area_m2,
property_type, listing_date, url, description, existing_dwellings, listing_id,
existing_rent_override, unit_rent_override. Header names are case-insensitive; spaces/dashes ok.
"""
from __future__ import annotations

import csv
import io
from typing import Iterator

from ..models import Listing
from .base import ListingSource, build_listing

ALIASES = {"land": "land_area_m2", "land_area": "land_area_m2", "land_m2": "land_area_m2", "beds": "bedrooms",
           "baths": "bathrooms", "id": "listing_id", "notes": "description", "dwellings": "existing_dwellings", "capital_value": "cv", "gv": "cv", "rv": "cv"}


class CsvSource(ListingSource):
    name = "csv"

    def __init__(self, text: str | None = None, path: str | None = None):
        super().__init__()
        if path:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                text = fh.read()
        self.text = text or ""

    def listings(self) -> Iterator[Listing]:
        reader = csv.DictReader(io.StringIO(self.text.lstrip("﻿")))
        for n, row in enumerate(reader, start=2):
            fields = {}
            for k, v in row.items():
                if k is None:
                    continue
                key = k.strip().lower().replace(" ", "_").replace("-", "_")
                fields[ALIASES.get(key, key)] = (v or "").strip()
            try:
                yield build_listing(self.name, fields)
            except ValueError as e:
                self.rejected.append((f"line {n}: {row}", str(e)))
