"""Google Sheet 'Inbox' tab as a ListingSource.

Reads the tab through Google's CSV export, so the sheet must be viewable by anyone with the link
(Share -> General access -> Anyone with the link -> Viewer). Columns: Address, Price, Bedrooms,
Existing dwellings, Listing URL, Notes / listing text, Added by. The example row is skipped.
"""
from __future__ import annotations

import re
from typing import Iterator

import requests

from ..models import Listing
from .csv_source import CsvSource
from .base import ListingSource

HEADER_MAP = {"listing_url": "url", "notes_/_listing_text": "description", "notes": "description",
              "cv": "cv", "gv": "cv", "cv_/_gv": "cv"}


def export_url(sheet: str, tab: str = "Inbox") -> str:
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", sheet)
    sid = m.group(1) if m else sheet.strip()
    return f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={requests.utils.quote(tab)}"


class GoogleSheetSource(ListingSource):
    name = "gsheet"

    def __init__(self, sheet: str, tab: str = "Inbox", timeout: float = 30):
        super().__init__()
        self.url = export_url(sheet, tab)
        self.timeout = timeout

    def listings(self) -> Iterator[Listing]:
        r = requests.get(self.url, timeout=self.timeout)
        if r.status_code != 200 or r.text.lstrip().startswith("<"):
            raise RuntimeError("Could not read the sheet's Inbox tab. Set sharing to 'Anyone with the link: Viewer'.")
        lines = r.text.splitlines()
        if not lines:
            return
        head = [h.strip().strip('"').lower().replace(" ", "_") for h in lines[0].split(",")]
        head = [HEADER_MAP.get(h, h) for h in head]
        text = ",".join(f'"{h}"' for h in head) + "\n" + "\n".join(lines[1:])
        src = CsvSource(text)
        for l in src.listings():
            if "example row" in (l.description or "").lower():
                continue
            yield l
        self.rejected.extend(src.rejected)
