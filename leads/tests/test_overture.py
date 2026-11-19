"""Overture access layer against a local Parquet file with Overture's schema shape.
No network: S3 listing is faked, and extract/census read a fixture file."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import duckdb
import pytest

from leads_engine.universe import overture

FIXTURE_SQL = """
copy (
  select * from (values
    ('p1', 'Joe Plumbing', 'plumbing', ['water_heater_installation_repair'], 'US', 'US-TX', 30.27, -97.74, 'open', ['joesplumbing.com'], ['+15124741234']),
    ('p2', 'Maple Leaf Plumbing', 'plumbing', [], 'CA', 'ON', 43.65, -79.38, 'open', [], ['+14165550100']),
    ('d1', 'Smile Dental', 'dental_clinic', [], 'US', 'CA', 34.05, -118.24, 'open', [], []),
    ('x1', 'Joe Diner', 'restaurant', ['plumbing'], 'US', 'US-TX', 30.27, -97.74, 'open', [], [])
  ) t(id, nm, code, alts, country, region, lat, lng, status, websites, phones)
) to '{path}' (format parquet)
"""

SHAPED = """
select id,
  {{'primary': nm}} as names,
  {{'primary': code, 'hierarchy': [code], 'alternates': alts}} as taxonomy,
  0.9 as confidence, status as operating_status,
  websites::varchar[] as websites, phones::varchar[] as phones,
  null::varchar[] as emails, null::varchar[] as socials,
  [{{'freeform': '1 Main St', 'locality': 'Town', 'postcode': '78701', 'region': region, 'country': country}}] as addresses,
  {{'xmin': lng, 'xmax': lng, 'ymin': lat, 'ymax': lat}} as bbox,
  {{'wikidata': null::varchar, 'names': {{'primary': null::varchar}}}} as brand,
  [{{'property': '', 'dataset': 'meta', 'license': 'CDLA-Permissive-2.0', 'record_id': id, 'update_time': 't'}}] as sources,
  code as basic_category
from read_parquet('{flat}')
"""


@pytest.fixture
def places(tmp_path: Path) -> str:
    flat = tmp_path / "flat.parquet"
    con = duckdb.connect()
    con.execute(FIXTURE_SQL.format(path=flat))
    out = tmp_path / "places.parquet"
    con.execute(f"copy ({SHAPED.format(flat=flat)}) to '{out}' (format parquet)")
    return str(out)


def test_extract_filters_country_and_primary_code(places: str, tmp_path: Path) -> None:
    out = tmp_path / "extract.parquet"
    overture.extract(duckdb.connect(), [places], ["plumbing", "dental_clinic"], "US", out)
    rows = (
        duckdb.connect()
        .sql(f"select * from read_parquet('{out}') order by source_id")
        .to_arrow_table()
        .to_pylist()
    )
    assert [r["source_id"] for r in rows] == ["d1", "p1"]  # Canada and alternates-only excluded
    p1 = rows[1]
    assert p1["name"] == "Joe Plumbing"
    assert p1["region"] == "US-TX"
    assert p1["lat"] == pytest.approx(30.27)
    assert p1["sources"][0]["license"] == "CDLA-Permissive-2.0"
    assert p1["alt_codes"] == ["water_heater_installation_repair"]


def test_extract_is_idempotent(places: str, tmp_path: Path) -> None:
    out = tmp_path / "extract.parquet"
    overture.extract(duckdb.connect(), [places], ["plumbing"], "US", out)
    mtime = out.stat().st_mtime_ns
    overture.extract(duckdb.connect(), [places], ["dental_clinic"], "US", out)  # exists -> skipped
    assert out.stat().st_mtime_ns == mtime
    assert not list(tmp_path.glob("*.tmp.parquet"))


def test_census_counts_primary_and_alternates(places: str, tmp_path: Path) -> None:
    prim, alt = overture.taxonomy_census(duckdb.connect(), [places], "US", tmp_path)
    con = duckdb.connect()
    p = dict(con.execute("select code, n from read_parquet(?)", [str(prim)]).fetchall())
    a = dict(con.execute("select code, n from read_parquet(?)", [str(alt)]).fetchall())
    assert p == {"plumbing": 1, "dental_clinic": 1, "restaurant": 1}
    assert a == {"water_heater_installation_repair": 1, "plumbing": 1}


def test_file_selection_by_bbox() -> None:
    def f(key: str, box: tuple[float, float, float, float]) -> overture.PlaceFile:
        return overture.PlaceFile(key, 1, *box, rows=1)

    files = [
        f("us_west", (-125.0, -100.0, 30.0, 50.0)),
        f("europe", (-1.3, 4.5, 40.8, 60.9)),
        f("aleutians_west", (172.5, 179.0, 51.0, 53.0)),
        f("east_asia", (106.0, 180.0, -84.9, 18.9)),
    ]
    assert [x.key for x in overture.files_for_country(files, "US")] == ["us_west", "aleutians_west"]


def test_release_listing_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        "<r><Contents><Key>a/part-0.parquet</Key><Size>10</Size></Contents>"
        "<Contents><Key>a/_SUCCESS</Key><Size>0</Size></Contents>"
        "<NextContinuationToken>tok</NextContinuationToken></r>",
        "<r><Contents><Key>a/part-1.parquet</Key><Size>20</Size></Contents></r>",
    ]
    seen: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> Any:
        seen.append(url)
        body = pages[len(seen) - 1].encode()
        resp = io.BytesIO(body)
        resp.__enter__ = lambda: resp  # type: ignore[method-assign]
        resp.__exit__ = lambda *a: None  # type: ignore[method-assign]
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert overture.list_release_objects("2026-11-18.0") == [
        ("a/part-0.parquet", 10),
        ("a/part-1.parquet", 20),
    ]
    assert "continuation-token=tok" in seen[1]


def test_release_listing_fails_loudly_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(url: str, timeout: int) -> Any:
        resp = io.BytesIO(b"<r></r>")
        resp.__enter__ = lambda: resp  # type: ignore[method-assign]
        resp.__exit__ = lambda *a: None  # type: ignore[method-assign]
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="no place files"):
        overture.list_release_objects("1999-01-01.0")


def test_report_flags_configured_codes_missing_from_census() -> None:
    from leads_engine.config_loader import load_config
    from leads_engine.universe.report import missing_codes
    from tests.conftest import REAL_CONFIG

    cfg = load_config(REAL_CONFIG)
    all_codes = {
        c: 1 for cat in cfg.categories.values() for s in cat.subcategories for c in s.overture_codes
    }
    assert missing_codes(cfg, all_codes) == {}
    del all_codes["hvac_service"]
    assert missing_codes(cfg, all_codes) == {"hvac": ["hvac_service"]}
