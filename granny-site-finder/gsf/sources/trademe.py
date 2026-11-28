"""Trade Me Property API source: STUB, not wired up.

Hard rule: never scrape trademe.co.nz HTML (Trade Me API terms list scraping as unsupported use).
To enable: register an app at developer.trademe.co.nz (start on Sandbox, then apply for live),
put the consumer key/secret and OAuth tokens in .env (TRADEME_CONSUMER_KEY, TRADEME_CONSUMER_SECRET,
TRADEME_OAUTH_TOKEN, TRADEME_OAUTH_TOKEN_SECRET), then implement `listings()` against the
documented residential search endpoint and map fields with `build_listing("trademe", {...})`.
"""
from __future__ import annotations

import os
from typing import Iterator

from ..models import Listing
from .base import ListingSource


class TradeMeNotConfigured(RuntimeError):
    pass


class TradeMeApiSource(ListingSource):
    name = "trademe"
    ENV = ("TRADEME_CONSUMER_KEY", "TRADEME_CONSUMER_SECRET")

    def __init__(self, search: dict | None = None, sandbox: bool = True):
        super().__init__()
        self.search = search or {}
        self.sandbox = sandbox

    def listings(self) -> Iterator[Listing]:
        missing = [k for k in self.ENV if not os.environ.get(k)]
        if missing:
            raise TradeMeNotConfigured(f"Trade Me API not configured: set {', '.join(missing)} in .env")
        raise NotImplementedError("Trade Me API source is a stub until API access is approved")
        yield  # pragma: no cover
