"""Exclusion, chain, dedupe and sampling rules on synthetic Overture rows."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import duckdb
import pytest

from leads_engine.config_loader import AppConfig, load_config
from leads_engine.universe.build import SCHEMA, BuildStats, build, draw_sample, sample_key
from tests.conftest import REAL_CONFIG

CATS = ["plumbing", "hvac", "dental"]
_phone_seq = itertools.count(1000)
_zip_seq = itertools.count(10001)


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    return load_config(REAL_CONFIG)


def raw(sid: str, name: str | None, **kw: Any) -> dict[str, Any]:
    """One row shaped like the extract (EXTRACT_SQL) output."""
    row: dict[str, Any] = {
        "source_id": sid,
        "name": name,
        "code": "plumbing",
        "alt_codes": None,
        "confidence": 0.9,
        "operating_status": "open",
        "websites": None,
        "phones": [f"+1512474{next(_phone_seq):04d}"],
        "emails": None,
        "socials": None,
        "address": "1 Main St",
        "city": "Austin",
        "postcode": "78701",
        "region": "US-TX",
        "country": "US",
        "lat": 30.27,
        "lng": -97.74,
        "brand_wikidata": None,
        "brand_name": None,
        "sources": [
            {"property": "", "dataset": "meta", "license": "CDLA-Permissive-2.0", "record_id": sid, "update_time": "t"},
            {"property": "/properties/confidence", "dataset": "Overture", "license": "CDLA-Permissive-2.0", "record_id": sid, "update_time": "t"},
        ],
    }  # fmt: skip
    row.update(kw)
    return row


def run(
    cfg: AppConfig, rows: list[dict[str, Any]], tmp_path: Path
) -> tuple[dict[str, dict[str, Any]], BuildStats]:
    out = tmp_path / "built.parquet"
    stats = build(tmp_path / "unused.parquet", cfg, CATS, "2026-11-18.0", out, "seed", rows_in=rows)
    got = duckdb.connect().sql(f"select * from read_parquet('{out}')").to_arrow_table().to_pylist()
    return {r["source_id"]: r for r in got}, stats


def locations(prefix: str, name: str, domain: str, n: int, **kw: Any) -> list[dict[str, Any]]:
    """n listings of one website, each at its own ZIP."""
    return [
        raw(
            f"{prefix}{i}",
            name,
            websites=[f"https://{domain}/loc{i}"],
            postcode=str(next(_zip_seq)),
            **kw,
        )
        for i in range(n)
    ]


# --------------------------------------------------------------- normalize


def test_kept_row_is_normalized_and_attributed(cfg: AppConfig, tmp_path: Path) -> None:
    got, _ = run(cfg, [raw("a", "Joe's Plumbing, LLC", websites=["www.joesplumbing.com"], postcode="78701-1234",
                            phones=["(512) 474-9999"], emails=["Info@JoesPlumbing.com", "joe@gmail.com"])], tmp_path)  # fmt: skip
    r = got["a"]
    assert r["exclusion"] is None
    assert r["dup_of"] is None
    assert (r["category"], r["subcategory"]) == ("plumbing", "plumbing")
    assert r["name_key"] == "joes plumbing"
    assert (r["state"], r["postcode"], r["tz"]) == ("TX", "78701", "America/Chicago")
    assert r["phone"] == "+15124749999"
    assert r["domain"] == "joesplumbing.com"
    assert r["licenses"] == ["CDLA-Permissive-2.0"]
    assert r["source_datasets"] == ["meta"]  # record-level sources only
    by_email = {e["email"]: e for e in r["emails"]}
    assert by_email["info@joesplumbing.com"]["domain_matches_site"]
    assert by_email["joe@gmail.com"]["is_freemail"]


def test_contact_flags(cfg: AppConfig, tmp_path: Path) -> None:
    got, _ = run(cfg, [
        raw("nophone", "A Plumbing", phones=None),
        raw("tollfree", "B Plumbing", phones=["+18004445555"]),
        raw("nosite", "C Plumbing", websites=["https://facebook.com/c"]),
    ], tmp_path)  # fmt: skip
    assert "no_phone" in got["nophone"]["flags"]
    assert got["tollfree"]["phone"] is None
    assert "no_usable_phone" in got["tollfree"]["flags"]
    assert got["nosite"]["domain"] is None
    assert "not_own_site:facebook.com" in got["nosite"]["flags"]
    assert all(r["exclusion"] is None for r in got.values())  # flagged, not dropped


# -------------------------------------------------------------- exclusions


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (raw("x", None), "no_name"),
        (raw("x", "Joe Plumbing", operating_status="permanently_closed"), "closed"),
        (raw("x", "Joe Plumbing", operating_status="temporarily_closed"), "closed"),
        (
            raw(
                "x",
                "UCLA Venice Dental Center",
                code="dental_clinic",
                websites=["https://dentistry.ucla.edu"],
            ),
            "institution",
        ),
        (raw("x", "Emergency Dentist 24/7", code="dental_clinic"), "referral_listing"),
        (raw("x", "Some Brand Plumbing", brand_wikidata="Q123"), "brand_wikidata"),
        (raw("x", "Aspen Dental - Springfield", code="dental_clinic"), "chain_name_pattern"),
        (raw("x", "Roto-Rooter Plumbing & Water Cleanup"), "chain_name_pattern"),
        (raw("x", "Barlow Service Experts", code="hvac_service"), "chain_name_pattern"),
        (raw("x", "Lafayette Dental Laboratory", code="dental_clinic"), "exclusion_keyword"),
        (raw("x", "Homes Plumbing Supply Inc"), "exclusion_keyword"),
        (raw("x", "VA Medical Center Dental", code="dental_clinic"), "exclusion_keyword"),
        (raw("x", "Smith.ai Plumbing Partner"), "answering_vendor"),
    ],
)
def test_exclusion_reasons(
    cfg: AppConfig, tmp_path: Path, row: dict[str, Any], reason: str
) -> None:
    got, stats = run(cfg, [row], tmp_path)
    assert got["x"]["exclusion"] == reason
    assert stats.raw_rows == 1


@pytest.mark.parametrize(
    "name",
    [
        "Twin Aspen Dental Center",
        "New Castle Dental",
        "Tim Ferguson Plumbing",
        "Gentle Dental Care of Smithtown",
    ],
)
def test_independents_that_share_words_with_chains_are_kept(
    cfg: AppConfig, tmp_path: Path, name: str
) -> None:
    code = "plumbing" if "Plumbing" in name else "dental_clinic"
    got, _ = run(cfg, [raw("x", name, code=code)], tmp_path)
    assert got["x"]["exclusion"] is None


def test_gov_license_page_does_not_exclude_a_real_plumber(cfg: AppConfig, tmp_path: Path) -> None:
    got, _ = run(
        cfg, [raw("x", "Pacific Plumbing", websites=["https://www.cslb.ca.gov/x"])], tmp_path
    )
    assert got["x"]["exclusion"] is None
    assert got["x"]["domain"] is None
    assert "government_page:ca.gov" in got["x"]["flags"]


# --------------------------------------------------------- location rules


def test_domain_at_many_zips_is_a_chain_even_in_one_city(cfg: AppConfig, tmp_path: Path) -> None:
    rows = locations(
        "c", "Absolute Smiles", "absolutesmiles.com", 10, code="dental_clinic"
    )  # all Austin
    got, stats = run(cfg, rows, tmp_path)
    assert {r["exclusion"] for r in got.values()} == {"chain_multi_location"}
    assert stats.chain_domains[0][:2] == ("absolutesmiles.com", 10)


def test_small_multi_location_business_is_kept_and_flagged(cfg: AppConfig, tmp_path: Path) -> None:
    got, _ = run(
        cfg,
        locations("m", "Kadan Orthodontics", "kadanortho.com", 3, code="orthodontics"),
        tmp_path,
    )
    for r in got.values():
        assert r["exclusion"] is None
        assert r["dup_of"] is None  # separate locations are not duplicates
        assert r["location_count"] == 3
        assert "multiple_locations" in r["flags"]


def test_unknown_subdomain_platform_is_split_by_host(cfg: AppConfig, tmp_path: Path) -> None:
    names = ["Alpha Plumbing", "Bravo Drains", "Charlie Pipes", "Delta Rooter", "Echo Water", "Fox Leaks",
             "Golf Plumbing", "Hotel Drains", "India Pipes", "Juliet Plumbing", "Kilo Rooter"]  # fmt: skip
    rows = [
        raw(
            f"p{i}",
            n,
            websites=[f"https://{n.split()[0].lower()}.someplatform.site"],
            postcode=str(next(_zip_seq)),
        )
        for i, n in enumerate(names)
    ]
    got, stats = run(cfg, rows, tmp_path)
    assert all(r["exclusion"] is None for r in got.values())
    assert got["p0"]["domain"] == "alpha.someplatform.site"
    assert stats.subdomain_platforms == [("someplatform.site", 11)]


def test_franchise_subdomains_still_count_as_one_chain(cfg: AppConfig, tmp_path: Path) -> None:
    rows = [
        raw(f"f{i}", f"Zap Air of City{i}", code="hvac_service",
            websites=[f"https://city{i}.zapair.com"], postcode=str(next(_zip_seq)))
        for i in range(10)
    ]  # fmt: skip
    got, _ = run(cfg, rows, tmp_path)
    assert {r["exclusion"] for r in got.values()} == {"chain_multi_location"}


def test_name_network_vs_generic_name(cfg: AppConfig, tmp_path: Path) -> None:
    network = [raw(f"n{i}", "Mr Fixit Plumbing", websites=["https://mrfixit.pro"] if i < 4 else None,
                   postcode=str(next(_zip_seq))) for i in range(6)]  # fmt: skip
    network += [
        raw(f"n{i}", "Mr Fixit Plumbing", postcode=str(next(_zip_seq))) for i in range(6, 10)
    ]
    generic = [raw(f"g{i}", "Main Street Plumbing", websites=[f"https://mainst{i}.com"],
                   postcode=str(next(_zip_seq))) for i in range(10)]  # fmt: skip
    got, _ = run(cfg, network + generic, tmp_path)
    # mrfixit.pro is at only 4 locations (not a domain chain), but 4/4 listings with
    # a site share it and the name is at 10 locations -> listing network.
    assert {got[f"n{i}"]["exclusion"] for i in range(10)} == {"chain_name_network"}
    for i in range(10):
        assert got[f"g{i}"]["exclusion"] is None
        assert "name_repeated_review" in got[f"g{i}"]["flags"]


# ------------------------------------------------------------------ dedupe


def test_same_phone_merges_and_keeps_the_record_with_a_website(
    cfg: AppConfig, tmp_path: Path
) -> None:
    got, stats = run(cfg, [
        raw("a", "Joe Plumbing", phones=["+15124740001"], postcode="78702"),
        raw("b", "Joe's Plumbing LLC", phones=["+15124740001", "+15124740002"], postcode="78703",
            websites=["joesplumbing.com"]),
    ], tmp_path)  # fmt: skip
    assert got["b"]["dup_of"] is None
    assert got["a"]["dup_of"] == "b"
    assert got["b"]["merged_source_ids"] == ["a"]
    phones = {p["e164"]: p["source_records"] for p in got["b"]["phones"]}
    assert phones == {"+15124740001": 2, "+15124740002": 1}
    assert stats.merges_by_key["phone"] == 1


def test_shared_phone_is_not_a_merge_key(cfg: AppConfig, tmp_path: Path) -> None:
    rows = [raw(f"s{i}", f"Practice {name}", code="dental_clinic", phones=["+15124740100"],
                postcode=str(next(_zip_seq))) for i, name in enumerate("ABCD")]  # fmt: skip
    got, stats = run(cfg, rows, tmp_path)
    assert all(r["dup_of"] is None for r in got.values())
    assert all("shared_phone" in r["flags"] for r in got.values())
    assert stats.shared_phones == 1


def test_same_name_and_zip_merges_but_generic_names_do_not(cfg: AppConfig, tmp_path: Path) -> None:
    got, _ = run(cfg, [
        raw("a", "Joe Plumbing", postcode="78701"), raw("b", "JOE PLUMBING", postcode="78701"),
        raw("c", "Dentist", code="dental_clinic", postcode="78701"),
        raw("d", "Dentist", code="dental_clinic", postcode="78701"),
    ], tmp_path)  # fmt: skip
    assert {got["a"]["dup_of"], got["b"]["dup_of"]} == {None, "a"} or {
        got["a"]["dup_of"],
        got["b"]["dup_of"],
    } == {None, "b"}
    assert got["c"]["dup_of"] is None
    assert got["d"]["dup_of"] is None


def test_merges_are_transitive(cfg: AppConfig, tmp_path: Path) -> None:
    got, _ = run(cfg, [
        raw("a", "Alpha Dental", code="dental_clinic", phones=["+15124740201"], postcode="78701"),
        raw("b", "Dr. Smith DDS", code="dental_clinic", phones=["+15124740201"], postcode="78701",
            websites=["alphadental.com"]),
        raw("c", "Alpha Dental Group", code="dental_clinic", phones=["+15124740202"], postcode="78701",
            websites=["alphadental.com/about"]),
    ], tmp_path)  # fmt: skip
    survivors = [r for r in got.values() if r["dup_of"] is None]
    assert len(survivors) == 1
    assert sorted(survivors[0]["merged_source_ids"]) == sorted(
        set("abc") - {survivors[0]["source_id"]}
    )


# ------------------------------------------------------ sample and output


def test_sample_is_deterministic_and_only_from_kept(cfg: AppConfig) -> None:
    rows = [{"source_id": f"r{i}", "category": "plumbing", "exclusion": None, "dup_of": None, "in_sample": False}
            for i in range(50)]  # fmt: skip
    rows += [
        {
            "source_id": "ex",
            "category": "plumbing",
            "exclusion": "closed",
            "dup_of": None,
            "in_sample": False,
        }
    ]
    draw_sample(rows, {"plumbing": 10}, "seed")
    picked = sorted(str(r["source_id"]) for r in rows if r["in_sample"])
    assert len(picked) == 10
    assert "ex" not in picked
    expected = sorted(
        sorted((f"r{i}" for i in range(50)), key=lambda s: sample_key("seed", s))[:10]
    )
    assert picked == expected


def test_every_input_row_is_accounted_for(cfg: AppConfig, tmp_path: Path) -> None:
    rows = [
        raw("k", "Keep Plumbing"),
        raw("x", "Aspen Dental", code="dental_clinic"),
        raw("d1", "Dup Plumbing", phones=["+15124740300"], postcode="78705"),
        raw("d2", "Dup Plumbing", phones=["+15124740300"], postcode="78705"),
    ]
    got, stats = run(cfg, rows, tmp_path)
    assert set(got) == {"k", "x", "d1", "d2"}
    for cat, c in stats.by_category.items():
        assert c["raw"] == c["excluded"] + c["duplicate"] + c["kept"], cat
    out = tmp_path / "built.parquet"
    schema = (
        duckdb.connect().sql(f"select * from read_parquet('{out}') limit 0").to_arrow_table().schema
    )
    assert schema.names == SCHEMA.names
