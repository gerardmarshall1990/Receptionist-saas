"""Explorer API end to end: real pipeline data in a throwaway Postgres."""

from __future__ import annotations

import csv
import io
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from leads_engine import db
from leads_engine.compliance.export import check_all
from leads_engine.config_loader import AppConfig, load_config
from leads_engine.explorer import facts as fx
from leads_engine.settings import Settings
from leads_engine.universe.build import build
from leads_engine.universe.load import load
from tests.conftest import REAL_CONFIG, needs_db
from tests.test_build import raw

warnings.filterwarnings("ignore", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

from leads_engine.explorer.app import create_app  # noqa: E402

CFG = load_config(REAL_CONFIG)


def _rows() -> list[dict[str, Any]]:
    rows = [
        raw("p1", "Alpha Plumbing", websites=["alphaplumbing.com"], emails=["info@alphaplumbing.com"],
            region="US-TX", city="Austin", postcode="78701"),
        raw("p2", "Bravo 100% Rooter_Co", region="US-TX", city="Dallas", postcode="75201", lat=32.78, lng=-96.8),
        raw("p3", "Charlie Pipes", region="US-FL", city="Tampa", postcode="33601", lat=27.95, lng=-82.46,
            websites=["https://facebook.com/charlie"]),
        raw("h1", "Delta Heating & Air", code="hvac_service", region="US-CA", city="Fresno", postcode="93701",
            lat=36.74, lng=-119.78, websites=["deltaair.com"]),
        raw("d1", "Echo Family Dental", code="general_dentistry", region="US-NY", city="Albany", postcode="12207",
            lat=42.65, lng=-73.75, websites=["https://echodental.com"]),
        raw("d2", "Foxtrot Orthodontics", code="orthodontics", region="US-NY", city="Troy", postcode="12180",
            lat=42.73, lng=-73.69, phones=None),
    ]  # fmt: skip
    # three locations of one small group -> multiple_locations
    rows += [raw(f"m{i}", "Golf Smiles", code="dental_clinic", region="US-WA", city="Seattle",
                 postcode=f"9810{i}", lat=47.6, lng=-122.3, websites=[f"golfsmiles.com/loc{i}"])
             for i in range(3)]  # fmt: skip
    return rows


@pytest.fixture
def client(fresh_db: str, tmp_path: Path) -> Iterator[TestClient]:
    db.migrate(fresh_db)
    out = tmp_path / "built.parquet"
    build(tmp_path / "x", CFG, list(CFG.categories), "2026-11-18.0", out, "seed", rows_in=_rows())
    load(fresh_db, out, "all")
    app = create_app(Settings(database_url=fresh_db, data_dir=tmp_path), CFG)
    with TestClient(app) as c:
        yield c
    app.state.pool.close()


def names(resp: Any) -> list[str]:
    assert resp.status_code == 200, resp.text
    return sorted(i["name"] for i in resp.json()["items"])


pytestmark = [pytest.mark.db, needs_db]


# ------------------------------------------------------------------- list


def test_meta_and_default_list(client: TestClient) -> None:
    meta = client.get("/api/meta").json()
    assert meta["total"] == 9
    assert meta["export"]["enabled"] is True
    assert [c["slug"] for c in meta["categories"]][:2] == ["plumbing", "hvac"]
    body = client.get("/api/leads").json()
    assert len(body["items"]) == 9
    assert body["next_cursor"] is None
    item = next(i for i in body["items"] if i["name"] == "Alpha Plumbing")
    assert item["phone_status"] == "unverified"
    assert item["email_status"] == "source_listed"
    assert item["local_time"]
    assert item["suppressed"] is False


@pytest.mark.parametrize("sort", ["score", "name", "place", "locations"])
def test_cursor_paging_returns_every_row_exactly_once(client: TestClient, sort: str) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        url = f"/api/leads?sort={sort}&limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 9
    assert len(set(seen)) == 9


def test_name_and_place_sort_order(client: TestClient) -> None:
    by_name = [i["name"] for i in client.get("/api/leads?sort=name").json()["items"]]
    assert by_name[0] == "Alpha Plumbing"
    by_place = [i["state"] for i in client.get("/api/leads?sort=place").json()["items"]]
    assert by_place == sorted(by_place)


def test_filters(client: TestClient) -> None:
    assert names(client.get("/api/leads?category=hvac")) == ["Delta Heating & Air"]
    assert names(client.get("/api/leads?state=TX")) == ["Alpha Plumbing", "Bravo 100% Rooter_Co"]
    assert names(client.get("/api/leads?category=dental&subcategory=orthodontics")) == [
        "Foxtrot Orthodontics"
    ]
    assert "Charlie Pipes" in names(
        client.get("/api/leads?has_website=no")
    )  # facebook page != own site
    assert "Charlie Pipes" not in names(client.get("/api/leads?has_website=yes"))
    assert names(client.get("/api/leads?has_phone=no")) == ["Foxtrot Orthodontics"]
    assert names(client.get("/api/leads?has_email=yes")) == ["Alpha Plumbing"]
    assert names(client.get("/api/leads?flag=multiple_locations")) == ["Golf Smiles"] * 3
    assert "Golf Smiles" not in names(client.get("/api/leads?not_flag=multiple_locations"))


def test_search_is_literal_not_a_pattern(client: TestClient) -> None:
    assert names(client.get("/api/leads?q=echodental")) == ["Echo Family Dental"]  # domain
    assert names(client.get("/api/leads?q=tampa")) == ["Charlie Pipes"]  # city
    assert names(client.get("/api/leads?q=100%25")) == ["Bravo 100% Rooter_Co"]  # literal %
    assert names(client.get("/api/leads?q=r_co")) == ["Bravo 100% Rooter_Co"]  # literal _
    # As a LIKE wildcard, "a_p" would match "Alp(ha)"; literally it matches nothing.
    assert names(client.get("/api/leads?q=a_p")) == []
    assert names(client.get("/api/leads?q=%25")) == ["Bravo 100% Rooter_Co"]


def test_injection_attempts_are_inert(client: TestClient) -> None:
    r = client.get("/api/leads", params={"q": "'; drop table businesses; --"})
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert (
        client.get("/api/leads", params={"sort": "name; drop table businesses"}).status_code == 422
    )
    assert client.get("/api/leads", params={"category": "x' or '1'='1"}).status_code == 422
    assert client.get("/api/meta").json()["total"] == 9


@pytest.mark.parametrize(
    "url",
    [
        "/api/leads?category=vets",
        "/api/leads?state=ZZ",
        "/api/leads?flag=made_up",
        "/api/leads?tier=E",
        "/api/leads?sort=random",
        "/api/leads?limit=500",
        "/api/leads?cursor=not-base64!!",
        "/api/leads?cursor=WzFd",  # valid base64, wrong arity
        "/api/leads?has_phone=maybe",
        "/api/facets?facet=name",
    ],
)
def test_bad_input_is_rejected(client: TestClient, url: str) -> None:
    assert client.get(url).status_code == 422


def test_query_length_cap(client: TestClient) -> None:
    assert client.get("/api/leads", params={"q": "x" * 101}).status_code == 422


def test_count_and_facets(client: TestClient) -> None:
    assert client.get("/api/count?category=dental").json() == {"value": 5, "exact": True}
    f = client.get("/api/facets?category=dental&facet=category&facet=state").json()
    cats = {x["value"]: x["count"] for x in f["category"]}
    assert cats == {"dental": 5, "plumbing": 3, "hvac": 1}  # own selection ignored
    states = {x["value"]: x["count"] for x in f["state"]}
    assert states == {"NY": 2, "WA": 3}  # other filters applied


def test_callable_now_matches_python_rule(client: TestClient) -> None:
    got = {i["name"] for i in client.get("/api/leads?callable_now=true").json()["items"]}
    expected = {
        i["name"]
        for i in client.get("/api/leads?limit=200").json()["items"]
        if fx.callable_now(CFG.categories[i["category"]], i["tz"])
    }
    assert got == expected


# ---------------------------------------------------------------- detail


def _id(client: TestClient, name: str) -> str:
    items = client.get("/api/leads?limit=200").json()["items"]
    return str(next(i["id"] for i in items if i["name"] == name))


def test_detail_has_facts_and_evidence_reasons(client: TestClient, fresh_db: str) -> None:
    lead = _id(client, "Alpha Plumbing")
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute(
            "insert into reasons (business_id, signal, dimension, evidence, source_url, weight, stage)"
            " values (%s, 'leave_message_text', 'pain', 'Please leave a message and we will call you back',"
            " 'https://alphaplumbing.com/contact', 3, 'stage2')",
            (lead,),
        )
    d = client.get(f"/api/leads/{lead}").json()
    assert d["business"]["name"] == "Alpha Plumbing"
    assert "search_text" not in d["business"]
    assert d["reasons"][0]["evidence"].startswith("Please leave a message")
    assert d["emails"][0]["origin"] == "source_listed"
    texts = [f["text"] for f in d["facts"]]
    assert "Own website: alphaplumbing.com" in texts
    assert any("not exportable" in t for t in texts)


def test_detail_404_and_bad_id(client: TestClient) -> None:
    assert client.get("/api/leads/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/leads/not-a-uuid").status_code == 422


# -------------------------------------------------------------- suppress


def test_suppress_is_permanent_and_hides_the_lead(client: TestClient, fresh_db: str) -> None:
    lead = _id(client, "Alpha Plumbing")
    # Cross-site form trick: JSON body sent as text/plain must not be accepted.
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        r = client.post(
            f"/api/leads/{lead}/suppress",
            content='{"reason": "asked"}',
            headers={"content-type": ctype},
        )
        assert r.status_code == 415
    assert client.get(f"/api/leads/{lead}").json()["suppression"] == []
    assert (
        client.post(f"/api/leads/{lead}/suppress", json={"reason": "x"}).status_code == 422
    )  # too short
    r = client.post(f"/api/leads/{lead}/suppress", json={"reason": "Owner asked not to be called"})
    assert r.status_code == 200
    assert r.json()["rows_added"] == 3  # business id + phone + email
    again = client.post(f"/api/leads/{lead}/suppress", json={"reason": "again"}).json()
    assert again["rows_added"] == 0  # idempotent
    assert "Alpha Plumbing" not in names(client.get("/api/leads"))
    shown = client.get("/api/leads?include_suppressed=true&q=alpha").json()["items"]
    assert shown[0]["suppressed"] is True
    assert client.get(f"/api/leads/{lead}").json()["suppression"]
    with (
        psycopg.connect(fresh_db, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        conn.execute("delete from suppression")


def test_suppress_domain_hides_every_location(client: TestClient) -> None:
    lead = _id(client, "Golf Smiles")
    r = client.post(
        f"/api/leads/{lead}/suppress", json={"reason": "group opted out", "include_domain": True}
    )
    assert r.status_code == 200
    assert "Golf Smiles" not in names(client.get("/api/leads"))


# ------------------------------------------------------------ auth/headers


def test_security_headers(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_token_required_when_configured(fresh_db: str) -> None:
    db.migrate(fresh_db)
    app = create_app(Settings(database_url=fresh_db, ui_token="s3cret-token"), CFG)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200  # liveness stays open
        assert c.get("/api/meta").status_code == 401
        assert c.get("/").status_code == 401
        assert c.get("/?token=wrong").status_code == 401
        assert c.get("/api/meta", headers={"x-leads-token": "s3cret-token"}).status_code == 200
        r = c.get("/?token=s3cret-token", follow_redirects=False)
        assert r.status_code == 303
        assert "httponly" in r.headers["set-cookie"].lower()
        assert "samesite=strict" in r.headers["set-cookie"].lower()
        assert c.get("/api/meta").status_code == 200  # cookie now set
    app.state.pool.close()


# ------------------------------------------------------------- pure logic


def _cfg() -> AppConfig:
    return CFG


def test_callable_now_window_logic() -> None:
    plumbing = _cfg().categories["plumbing"]  # mon-fri 08:00-09:00 and 16:00-18:00
    tue_830_chicago = datetime(2026, 11, 17, 14, 30, tzinfo=UTC)  # 08:30 CST
    assert fx.callable_now(plumbing, "America/Chicago", tue_830_chicago)
    assert not fx.callable_now(plumbing, "America/Los_Angeles", tue_830_chicago)  # 06:30 PST
    sat = datetime(2026, 11, 21, 14, 30, tzinfo=UTC)
    assert not fx.callable_now(plumbing, "America/Chicago", sat)
    assert not fx.callable_now(plumbing, None, tue_830_chicago)
    assert not fx.callable_now(plumbing, "Not/AZone", tue_830_chicago)
    assert not fx.callable_now(None, "America/Chicago", tue_830_chicago)


def test_facts_only_restate_stored_fields() -> None:
    b = {"flags": ["multiple_locations", "shared_phone", "not_own_site:yelp.com"], "location_count": 3,
         "domain": "x.com", "phone": "+15124741234", "phone_status": "unverified"}  # fmt: skip
    phones = [{"status": "unverified"}]
    out = fx.facts_for(b, phones, [])
    texts = [f["text"] for f in out]
    assert "3 locations share x.com" in texts
    assert any("shared" in t.lower() for t in texts)
    assert any("yelp.com" in t for t in texts)
    assert {f["kind"] for f in out} <= {"value", "reachability", "caution", "info"}


# ------------------------------------------------------------------ Stage 5 export


def _make_exportable(fresh_db: str, lead: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute("update phones set status = 'likely' where business_id = %s", (lead,))
        conn.execute("update businesses set phone_status = 'likely' where id = %s", (lead,))
        conn.execute(
            "insert into scores (business_id, pain, value, reachability, fit, total, tier, components,"
            " model_version) select id, 50, 50, 50, 50, case when id = %s then 80 else 30 end,"
            " case when id = %s then 'A' else 'D' end, '[]', 'test' from businesses",
            (lead, lead),
        )
        conn.execute(
            "insert into reasons (business_id, signal, dimension, evidence, source_url, weight, stage)"
            " values (%s, 'phone_only_booking', 'pain', 'Call us to book your appointment',"
            " 'https://alphaplumbing.com/contact', 6, 'stage2')",
            (lead,),
        )
    check_all(fresh_db, CFG)


def test_export_downloads_only_gated_leads(client: TestClient, fresh_db: str) -> None:
    lead = _id(client, "Alpha Plumbing")
    _make_exportable(fresh_db, lead)

    assert names(client.get("/api/leads?compliance=pass")) == ["Alpha Plumbing"]
    assert "Alpha Plumbing" not in names(client.get("/api/leads?compliance=blocked&limit=200"))
    assert client.get("/api/leads?compliance=maybe").status_code == 422
    assert client.get("/api/meta").json()["export"]["enabled"] is True

    d = client.get(f"/api/leads/{lead}").json()
    assert d["compliance"]["status"] == "pass"
    assert d["compliance"]["opener"].startswith("It looks like the only way to book Alpha Plumbing")
    other = client.get(f"/api/leads/{_id(client, 'Charlie Pipes')}").json()
    assert other["compliance"]["status"] == "blocked"

    # JSON-only POST (CSRF guard), then a CSV of exactly the passing leads.
    assert client.post(
        "/api/export", content="x", headers={"content-type": "text/plain"}
    ).status_code in (403, 415)
    r = client.post("/api/export", json={})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["x-export-passed"] == "1"
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [row["name"] for row in rows] == ["Alpha Plumbing"]
    assert rows[0]["compliance_status"] == "pass"

    # Suppressing the lead blocks it everywhere at once.
    client.post(f"/api/leads/{lead}/suppress", json={"reason": "asked not to be called"})
    assert names(client.get("/api/leads?compliance=pass&include_suppressed=true")) == []
    assert client.post("/api/export", json={}).headers["x-export-passed"] == "0"
    with psycopg.connect(fresh_db) as conn:
        assert conn.execute(
            "select origin, n_passed from exports order by created_at"
        ).fetchall() == [
            ("explorer", 1),
            ("explorer", 0),
        ]
