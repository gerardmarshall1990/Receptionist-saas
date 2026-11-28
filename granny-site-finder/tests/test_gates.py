import pytest

from gsf.config import load_config
from gsf.gates import evaluate
from gsf.models import (CHECK, FAIL, FLAG, INCOMPLETE, INDICATED, PASS, UNVERIFIED, VERIFIED,
                        Fact, Listing, SiteFacts, overall_status)
from gsf.text import dwelling_hint, parse_price
from gsf.titles import (CROSS_LEASE, FREEHOLD, LEASEHOLD, UNIT_TITLE, classify_linz_title,
                        lot_count, shares_access_lot, title_fact_from_council, title_fact_from_linz)

G = load_config()["gates"]
MHS = "Residential - Mixed Housing Suburban Zone"


def V(v):
    return Fact(v, VERIFIED, "test")


def good_site() -> SiteFacts:
    f = SiteFacts(
        matched_address=V("1 Test Street Testville"),
        title_type=V(FREEHOLD),
        land_area_m2=V(750.0),
        zone=V(MHS),
        road_frontage=V(True),
        width_m=V(18.0),
        multi_parcel=V(False),
        rub_nearby=V(False),
        zone_other=V([]),
    )
    f.flood = {k: V("none") for k in ("flood_plain", "flood_prone", "overland_flow_path", "flood_sensitive")}
    f.overlays = {k: V([]) for k in G["overlays"]}
    return f


def good_listing(**kw) -> Listing:
    d = dict(address="1 Test Street", price=900_000, existing_dwellings=1, bedrooms=3)
    d.update(kw)
    return Listing(**d)


def by_gate(results):
    return {r.gate: r for r in results}


def test_all_good_passes():
    res = evaluate(good_site(), good_listing(), G)
    assert overall_status(res) == PASS
    hard = [r for r in res if r.hard]
    assert all(r.status == PASS for r in hard), [r for r in hard if r.status != PASS]


def test_every_result_has_reason():
    for f, l in [(good_site(), good_listing()), (SiteFacts(), Listing(address="x"))]:
        for r in evaluate(f, l, G):
            assert r.reason and r.reason.strip()


def test_single_house_zone_fails_with_reason():
    f = good_site()
    f.zone = V("Residential - Single House Zone")
    r = by_gate(evaluate(f, good_listing(), G))["zone"]
    assert r.status == FAIL and "Single House" in r.reason


def test_mixed_use_zone_is_check_with_consent_reason():
    f = good_site()
    f.zone = V("Business - Mixed Use Zone")
    r = by_gate(evaluate(f, good_listing(), G))["zone"]
    assert r.status == CHECK and "resource consent" in r.reason


def test_unverified_zone_is_check_not_pass():
    f = good_site()
    f.zone = Fact(None, UNVERIFIED, "council", "split zoned: MHS / THAB")
    res = evaluate(f, good_listing(), G)
    assert by_gate(res)["zone"].status == CHECK
    assert overall_status(res) == CHECK


def test_cross_lease_indicated_fails():
    f = good_site()
    f.title_type = Fact(CROSS_LEASE, INDICATED, "Auckland Council property record")
    r = by_gate(evaluate(f, good_listing(), G))["title"]
    assert r.status == FAIL and "Cross-lease" in r.reason


def test_freehold_only_indicated_by_council_is_check():
    f = good_site()
    f.title_type = Fact(FREEHOLD, UNVERIFIED, "council", "consistent with fee simple")
    assert by_gate(evaluate(f, good_listing(), G))["title"].status == CHECK


def test_small_land_fails():
    f = good_site()
    f.land_area_m2 = V(599.0)
    r = by_gate(evaluate(f, good_listing(), G))["land_area"]
    assert r.status == FAIL and "599" in r.reason


def test_land_boundary_600_passes():
    f = good_site()
    f.land_area_m2 = V(600.0)
    assert by_gate(evaluate(f, good_listing(), G))["land_area"].status == PASS


def test_flood_core_fails_partial_flags():
    f = good_site()
    f.flood["overland_flow_path"] = V("core")
    assert by_gate(evaluate(f, good_listing(), G))["flood"].status == FAIL
    f = good_site()
    f.flood["flood_plain"] = V("partial")
    res = by_gate(evaluate(f, good_listing(), G))
    assert res["flood"].status == PASS
    assert res["flood_partial"].status == FLAG and not res["flood_partial"].hard


def test_flood_lookup_failure_is_check():
    f = good_site()
    f.flood["flood_prone"] = Fact(None, UNVERIFIED, "council", "timeout")
    assert by_gate(evaluate(f, good_listing(), G))["flood"].status == CHECK


def test_heritage_blocks_special_character_flags():
    f = good_site()
    f.overlays["heritage_extent"] = V([{"hit_parcel": True, "hit_core": True, "detail": "Sched 14.1 #123"}])
    f.overlays["special_character"] = V([{"hit_parcel": True, "hit_core": True, "detail": "Isthmus B"}])
    res = evaluate(f, good_listing(), G)
    g = by_gate(res)
    assert g["overlays"].status == FAIL and "Heritage" in g["overlays"].reason
    assert g["overlay_special_character"].status == FLAG
    assert overall_status(res) == FAIL


def test_missing_price_is_incomplete_not_crash():
    res = evaluate(good_site(), good_listing(price=None, price_text="Auction"), G)
    assert by_gate(res)["price"].status == INCOMPLETE
    assert overall_status(res) == INCOMPLETE


def test_price_over_max_fails():
    r = by_gate(evaluate(good_site(), good_listing(price=G["max_price"] + 1), G))["price"]
    assert r.status == FAIL


def test_dwelling_from_text_is_check():
    l = good_listing(existing_dwellings=None, description="Home and income! Sleepout at rear.")
    assert by_gate(evaluate(good_site(), l, G))["existing_dwellings"].status == CHECK
    l = good_listing(existing_dwellings=None, description="Classic villa on a flat section.")
    assert by_gate(evaluate(good_site(), l, G))["existing_dwellings"].status == CHECK
    l = good_listing(existing_dwellings=2)
    assert by_gate(evaluate(good_site(), l, G))["existing_dwellings"].status == FAIL


def test_empty_facts_never_pass():
    res = evaluate(SiteFacts(), Listing(address="nowhere"), G)
    assert overall_status(res) in (CHECK, INCOMPLETE)
    assert not any(r.status == PASS for r in res if r.gate in ("zone", "title", "land_area", "flood", "overlays"))


# --- titles -----------------------------------------------------------------

@pytest.mark.parametrize("desc,expected,status", [
    ("Lot 10 DP 4165", FREEHOLD, UNVERIFIED),
    ("Flat 1 DP 76677, 1/2 SH Lot 25 DP 16789, Carport 1 DP 76677", CROSS_LEASE, INDICATED),
    ("Unit B UP 167884", UNIT_TITLE, INDICATED),
    ("LOT 1 DP 626148, 1/4 SH LOT 5 DP 626148", FREEHOLD, UNVERIFIED),   # JOAL, not cross-lease
    ("Lot 3 DP 1234 Leasehold", LEASEHOLD, INDICATED),
    # real council descriptions seen during verification (2026-11-28)
    ("LOT 11 DP 23034, FLAT 2 DP 63272", CROSS_LEASE, INDICATED),          # cross-lease without "SH"
    ("PRIN 206 DP 607666, AU 206B DP 607666", UNIT_TITLE, INDICATED),     # principal + accessory unit
    ("UNIT H DP 112081, 1/2 SH AU 1 DP 112081", UNIT_TITLE, INDICATED),
    ("LOT 1 DP 38222", FREEHOLD, UNVERIFIED),
    ("ALLOT 381 SO 47201 PARISH OF MANUREWA", FREEHOLD, UNVERIFIED),
    (None, None, UNVERIFIED),
])
def test_council_title_classification(desc, expected, status):
    f = title_fact_from_council(desc)
    assert (f.value, f.status) == (expected, status)


def test_parent_with_cross_lease_child():
    from gsf.titles import title_fact_from_council_records
    f = title_fact_from_council_records("Lot 130 DP 42694", [
        {"FORMATTEDADDRESS": "20 Roseman Avenue", "PROPERTYDESCRIPTION": "1/2 SH Lot 130 DP 42694, Flat 1 DP 179399"}])
    assert (f.value, f.status) == (CROSS_LEASE, INDICATED) and "20 Roseman" in f.note


def test_joal_and_lot_count():
    assert shares_access_lot("LOT 1 DP 626148, 1/4 SH LOT 5 DP 626148")
    assert not shares_access_lot("Flat 1 DP 76677, 1/2 SH Lot 25 DP 16789")
    assert lot_count("LOT 1 DP 626148, 1/4 SH LOT 5 DP 626148") == 1
    assert lot_count("Lot 1 DP 100, Lot 2 DP 100") == 2


@pytest.mark.parametrize("t,e,expected", [
    ("Freehold", "Fee Simple, 1/1, Lot 10 Deposited Plan 4165, 615 m2", FREEHOLD),
    ("Composite", "Fee Simple, 1/2 Share, Lot 25 DP 16789, Lease Term 999 years", CROSS_LEASE),
    ("Freehold", "Fee Simple, 1/2 Share, Lease Term 999 Years from 1/1/1980", CROSS_LEASE),
    ("Unit Title", "Stratum in Freehold, Unit B Unit Plan 167884", UNIT_TITLE),
    ("Leasehold", "Leasehold, 99 years", LEASEHOLD),
])
def test_linz_title_classification(t, e, expected):
    assert classify_linz_title(t, e) == expected


def test_linz_fact_mixed_titles_takes_worst():
    f = title_fact_from_linz([
        {"title_no": "A", "type": "Freehold", "estate_description": "Fee Simple, 1/1"},
        {"title_no": "B", "type": "Composite", "estate_description": "Fee Simple, 1/2 Share, Lease"},
    ])
    assert f.value == CROSS_LEASE and f.status == VERIFIED


# --- text -------------------------------------------------------------------

@pytest.mark.parametrize("txt,price,ind", [
    ("$950,000", 950_000, False),
    ("Enquiries over $900k", 900_000, True),
    ("Asking price $1.25m", 1_250_000, True),
    ("Auction", None, False),
    ("", None, False),
    (None, None, False),
    (875000, 875_000, False),
    ("3 bedrooms, by negotiation", None, True),
])
def test_parse_price(txt, price, ind):
    p, i, _ = parse_price(txt)
    assert p == price
    if price is not None:
        assert i == ind


def test_dwelling_hint():
    assert dwelling_hint(None, 1) == (1, "1 dwelling(s) as entered")
    assert dwelling_hint("Granny flat out back", None)[0] is None
    assert dwelling_hint("", None)[0] is None
