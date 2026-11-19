"""Live Google reviews: matching, policy (store place ID only), monthly cap, errors.
Google is replaced by an httpx.MockTransport: no network, no key, no cost."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from leads_engine import db
from leads_engine.explorer import google as gx
from leads_engine.explorer.app import create_app
from leads_engine.settings import Settings
from leads_engine.universe.build import build
from leads_engine.universe.load import load
from tests.conftest import needs_db
from tests.test_explorer import CFG, _id, _rows

REVIEW_TEXT = (
    "Called three times and it went to voicemail every time. Great work once they showed up."
)


class FakeGoogle:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.search_result: list[dict[str, Any]] = [{"id": "PLACE_ALPHA"}]
        self.details_status = 200
        self.place: dict[str, Any] = {
            "id": "PLACE_ALPHA",
            "displayName": {"text": "Alpha Plumbing LLC"},
            "formattedAddress": "1 Main St, Austin, TX 78701, USA",
            "internationalPhoneNumber": "+1 512-474-1000",
            "rating": 4.2,
            "userRatingCount": 87,
            "googleMapsUri": "https://maps.google.com/?cid=1",
            "reviews": [
                {
                    "rating": 3,
                    "relativePublishTimeDescription": "2 months ago",
                    "text": {"text": REVIEW_TEXT},
                    "authorAttribution": {"displayName": "Pat Q.", "uri": "https://www.google.com/maps/contrib/1",
                                          "photoUri": "https://lh3.googleusercontent.com/a/x"},
                    "googleMapsUri": "https://www.google.com/maps/reviews/1",
                }
            ],
        }  # fmt: skip

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.path} [{req.headers.get('X-Goog-FieldMask')}]")
        if req.url.path.endswith(":searchText"):
            assert req.headers["X-Goog-FieldMask"] == "places.id"  # IDs-only search
            body = json.loads(req.content)
            assert "Alpha Plumbing" in body["textQuery"] or "Bravo" in body["textQuery"]
            return httpx.Response(200, json={"places": self.search_result})
        if self.details_status != 200:
            return httpx.Response(
                self.details_status, json={"error": {"status": "PERMISSION_DENIED"}}
            )
        return httpx.Response(200, json=self.place)


@pytest.fixture
def setup(fresh_db: str, tmp_path: Path) -> Iterator[tuple[TestClient, FakeGoogle, str]]:
    db.migrate(fresh_db)
    rows = _rows()
    rows[0]["phones"] = ["+15124741000"]  # Alpha Plumbing's listed phone
    out = tmp_path / "b.parquet"
    build(tmp_path / "x", CFG, list(CFG.categories), "2026-11-18.0", out, "s", rows_in=rows)
    load(fresh_db, out, "all")
    fake = FakeGoogle()
    client = gx.PlacesClient(transport=httpx.MockTransport(fake.handler))
    app = create_app(Settings(database_url=fresh_db), CFG, places_client=client)
    with TestClient(app) as c:
        yield c, fake, fresh_db
    app.state.pool.close()


pytestmark = [pytest.mark.db, needs_db]


def post(c: TestClient, lead: str) -> Any:
    return c.post(f"/api/leads/{lead}/google", json={})


def test_match_by_phone_shows_reviews_and_stores_only_the_place_id(
    setup: tuple[TestClient, FakeGoogle, str],
) -> None:
    c, _, url = setup
    lead = _id(c, "Alpha Plumbing")
    r = post(c, lead)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "matched"
    assert d["detail"] == "phone matches"
    assert d["place"]["rating"] == 4.2
    rv = d["place"]["reviews"][0]
    assert rv["text"] == REVIEW_TEXT
    assert rv["author"] == "Pat Q."
    assert rv["url"].startswith("https://www.google.com/maps/")
    assert "voicemail" in d["highlight"]
    assert "not stored" in d["notice"]
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "select google_place_id from businesses where id = %s", (lead,)
        ).fetchone() == ("PLACE_ALPHA",)
        # Google content is not stored anywhere in the database.
        dump = conn.execute(
            "select string_agg(t::text, ' ') from (select * from businesses) t"
        ).fetchone()
        assert dump is not None
        assert "voicemail every time" not in (dump[0] or "")
        assert "Pat Q." not in (dump[0] or "")
        for table in ("signals", "reasons"):
            assert conn.execute(f"select count(*) from {table}").fetchone() == (0,)
        assert conn.execute("select calls from api_usage").fetchone() == (1,)


def test_stored_place_id_is_reused_without_a_new_search(
    setup: tuple[TestClient, FakeGoogle, str],
) -> None:
    c, fake, _ = setup
    lead = _id(c, "Alpha Plumbing")
    post(c, lead)
    fake.calls.clear()
    post(c, lead)
    assert [x.split(" [")[0] for x in fake.calls] == ["GET /v1/places/PLACE_ALPHA"]


def test_a_different_business_is_rejected_and_nothing_is_stored(
    setup: tuple[TestClient, FakeGoogle, str],
) -> None:
    c, fake, url = setup
    fake.place.update({"displayName": {"text": "Zeta Roofing"}, "internationalPhoneNumber": "+1 512-555-9999",
                       "formattedAddress": "99 Oak Ave, Dallas, TX 75001"})  # fmt: skip
    lead = _id(c, "Alpha Plumbing")
    d = post(c, lead).json()
    assert d["status"] == "no_match"
    assert d["place"] is None
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "select google_place_id from businesses where id = %s", (lead,)
        ).fetchone() == (None,)


def test_not_found_costs_nothing(setup: tuple[TestClient, FakeGoogle, str]) -> None:
    c, fake, url = setup
    fake.search_result = []
    d = post(c, _id(c, "Alpha Plumbing")).json()
    assert d["status"] == "not_found"
    assert all(":searchText" in x for x in fake.calls)  # no Details call
    with psycopg.connect(url) as conn:
        assert conn.execute("select count(*) from api_usage").fetchone() == (0,)


def test_monthly_cap_is_enforced(setup: tuple[TestClient, FakeGoogle, str], fresh_db: str) -> None:
    c, fake, url = setup
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into api_usage (provider, month, calls) values (%s, date_trunc('month', now())::date, %s)",
            (gx.PROVIDER, CFG.global_.google_places_monthly_cap),
        )
    r = post(c, _id(c, "Alpha Plumbing"))
    assert r.status_code == 429
    assert "cap reached" in r.json()["detail"]
    assert not any(
        "/places/" in x and "searchText" not in x for x in fake.calls
    )  # Details never called


def test_google_errors_map_to_clear_messages(setup: tuple[TestClient, FakeGoogle, str]) -> None:
    c, fake, _ = setup
    fake.details_status = 403
    r = post(c, _id(c, "Alpha Plumbing"))
    assert r.status_code == 503
    assert "key" in r.json()["detail"]


def test_requires_json_post(setup: tuple[TestClient, FakeGoogle, str]) -> None:
    c, _, _ = setup
    lead = _id(c, "Alpha Plumbing")
    assert c.get(f"/api/leads/{lead}/google").status_code == 405
    assert (
        c.post(
            f"/api/leads/{lead}/google", content="{}", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )


# ----------------------------------------------------------------- matching


@pytest.mark.parametrize(
    ("place", "expected"),
    [
        ({"internationalPhoneNumber": "+1 512-474-1000"}, True),  # phone decides
        (
            {
                "displayName": {"text": "Alpha Plumbing Co"},
                "formattedAddress": "1 Main St, Austin, TX 78701",
            },
            True,
        ),
        (
            {
                "displayName": {"text": "Alpha Plumbing Co"},
                "formattedAddress": "500 Elm, Houston, TX 77002",
            },
            False,
        ),
        (
            {
                "displayName": {"text": "Beta Drains"},
                "formattedAddress": "1 Main St, Austin, TX 78701",
            },
            False,
        ),
    ],
)
def test_same_business_rules(place: dict[str, Any], expected: bool) -> None:
    lead = {
        "name": "Alpha Plumbing",
        "phone": "+15124741000",
        "phones": [],
        "postcode": "78701",
        "address": "1 Main St",
    }
    assert gx.is_same_business(lead, place)[0] is expected
