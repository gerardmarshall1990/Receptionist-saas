"""Pipeline behaviour with fake lookups (no network)."""
import copy

import pytest

from gsf import geometry as geo
from gsf.config import load_config
from gsf.db import Store
from gsf.lookups.council import parse_address
from gsf.lookups.http import LookupFailed
from gsf.lookups.rents import RentResult
from gsf.models import VERIFIED, Fact, Listing
from gsf.pipeline import Pipeline
from gsf.report import ranked, to_csv, to_markdown
from gsf.sources.csv_source import CsvSource
from gsf.sources.paste import PasteSource
from gsf.sources.trademe import TradeMeApiSource, TradeMeNotConfigured

MHS = "Residential - Mixed Housing Suburban Zone"


def square(x0, y0, side):
    # Esri outer ring, clockwise
    return [[[x0, y0], [x0, y0 + side], [x0 + side, y0 + side], [x0 + side, y0], [x0, y0]]]


SITES = {
    # address -> (rings, description, zone, flood level dict)
    "1 good street": (square(1_750_000, 5_910_000, 30), "Lot 1 DP 1000", MHS, {}),
    "2 flood street": (square(1_751_000, 5_910_000, 30), "Lot 2 DP 1000", MHS, {"flood_plain": "core"}),
    "3 xlease street": (square(1_752_000, 5_910_000, 30), "Flat 1 DP 5, 1/2 SH Lot 3 DP 1000", MHS, {}),
    "4 small street": (square(1_753_000, 5_910_000, 20), "Lot 4 DP 1000", MHS, {}),
    "5 timeout street": (square(1_754_000, 5_910_000, 30), "Lot 5 DP 1000", MHS, {}),
}


class FakeCouncil:
    layers = {}

    def __init__(self):
        self.calls = 0

    def _get(self, rings):
        for k, v in SITES.items():
            if v[0] == rings or geo.point_in_polygon(*geo.interior_point(rings), v[0]):
                return k, v
        raise KeyError

    def find_property(self, address):
        self.calls += 1
        k = address.lower().split(",")[0].strip()
        if k not in SITES:
            return None, Fact(None, "unverified", "fake", f"no council property matches '{address}'"), []
        rings, desc, _, _ = SITES[k]
        return ({"attributes": {"PROPERTYID": k, "PROPERTYDESCRIPTION": desc, "FORMATTEDTITLES": "NA1/1",
                                "FORMATTEDADDRESS": k.title(), "OBJECTID": 1}, "geometry": {"rings": rings}},
                Fact(k.title(), VERIFIED, "fake"), [k])

    def zone(self, rings, core):
        return Fact(self._get(rings)[1][2], VERIFIED, "fake"), Fact([], VERIFIED, "fake")

    def hit_level(self, layer, rings, core, where="1=1"):
        k, v = self._get(rings)
        if k == "5 timeout street":
            raise LookupFailed("services1.arcgis.com timed out after 30s (after 4 attempts)")
        return Fact(v[3].get(layer, "none"), VERIFIED, "fake")

    def overlay(self, layer, rings, core):
        return Fact([], VERIFIED, "fake")

    def road_frontage(self, rings, tol):
        return Fact(True, VERIFIED, "fake")

    def near(self, key, rings, d):
        return Fact(False, VERIFIED, "fake")

    def pc120_zone(self, core):
        return Fact(None, VERIFIED, "fake")

    def wgs84_geometry(self, oid):
        return [[[174.7, -36.9], [174.7, -36.8999], [174.7001, -36.8999], [174.7001, -36.9], [174.7, -36.9]]]


class FakeLinz:
    enabled = False


class FakeSa2:
    def neighbours(self, x, y, r):
        return ["136300", "136200"]

    def lookup(self, x, y):
        return Fact({"code": "136300", "name": "Testville"}, VERIFIED, "fake")


class FakeRents:
    def ensure_loaded(self):
        return "fake"

    def lookup(self, code, name, beds, types, stat, neighbours=None):
        if beds is None:
            return RentResult(None, stat, code, name, "?", None, None, None, note="bedroom count unknown")
        w = {2: 550.0, 3: 700.0}.get(beds, 800.0)
        return RentResult(w, stat, code, name, str(beds), types[0], "Q3 2026 (Jul-Sep)", 25)


@pytest.fixture
def pipe(tmp_path):
    cfg = copy.deepcopy(load_config())
    cfg["storage"]["db_path"] = str(tmp_path / "t.sqlite")
    cfg["gates"]["max_price"] = 2_000_000
    p = Pipeline(cfg, Store(cfg["storage"]["db_path"]), council=FakeCouncil(), linz=FakeLinz(),
                 sa2=FakeSa2(), rents=FakeRents())
    return p


def gates(site):
    return {g["gate"]: g for g in site["gates"]}


def test_good_site_runs_feaso(pipe):
    s = pipe.process(Listing(address="1 Good Street", price=900_000, bedrooms=3, existing_dwellings=1))
    g = gates(s)
    assert g["zone"]["status"] == "pass" and g["land_area"]["status"] == "pass"
    assert g["title"]["status"] == "check"            # no LINZ key: never a guessed pass
    assert s["overall"] == "check"
    assert s["feaso"]["status"] == "ok"
    assert s["feaso"]["report"]["base"]["ltv"] == pytest.approx(0.823701, rel=1e-5)  # hand case 1


def test_missing_price_and_area_does_not_crash(pipe):
    s = pipe.process(Listing(address="1 Good Street", price=None, price_text="Auction", bedrooms=None))
    assert s["overall"] in ("incomplete", "check")
    assert gates(s)["price"]["status"] == "incomplete"
    assert s["feaso"]["status"] == "incomplete" and "price" in s["feaso"]["note"]


def test_no_parcel_match_is_handled(pipe):
    s = pipe.process(Listing(address="99 Nowhere Road", price=800_000))
    g = gates(s)
    assert g["address"]["status"] == "check" and "no council property" in g["address"]["reason"]
    assert g["zone"]["status"] == "check"
    assert g["land_area"]["status"] == "incomplete"


def test_timeout_in_one_lookup_does_not_kill_batch(pipe):
    ls = [Listing(address=a, price=800_000, bedrooms=3) for a in
          ("5 Timeout Street", "1 Good Street", "99 Nowhere Road", "2 Flood Street")]
    out = pipe.run_batch(ls)
    assert len(out) == 4
    t = gates(out[0])
    assert t["flood"]["status"] == "check" and "not checked" in t["flood"]["reason"]
    assert any("timed out" in e for e in out[0]["facts"]["errors"])
    assert gates(out[3])["flood"]["status"] == "fail"


def test_rejections_all_have_reasons(pipe):
    for a in SITES:
        s = pipe.process(Listing(address=a, price=900_000, bedrooms=3, existing_dwellings=1))
        for g in s["gates"]:
            assert g["reason"].strip()
        if s["overall"] == "fail":
            assert any(g["status"] == "fail" and g["reason"] for g in s["gates"])
    assert gates(pipe.process(Listing(address="3 xlease street", price=1)))["title"]["status"] == "fail"
    assert gates(pipe.process(Listing(address="4 small street", price=1)))["land_area"]["status"] == "fail"


def test_rerun_does_not_duplicate(pipe):
    l = Listing(address="1 Good Street", price=900_000, bedrooms=3)
    pipe.process(l)
    c1 = pipe.store.counts()
    pipe.process(l)
    pipe.process(Listing(address="1 good street", price=900_000, bedrooms=3))   # same key, different case
    assert pipe.store.counts() == c1
    # price change re-evaluates the same row
    s = pipe.process(Listing(address="1 Good Street", price=950_000, bedrooms=3))
    assert pipe.store.counts() == c1
    assert s["feaso"]["report"]["inputs"]["price"] == 950_000


def test_report_outputs(pipe):
    for a in SITES:
        pipe.process(Listing(address=a, price=900_000, bedrooms=3, existing_dwellings=1))
    rows = ranked([pipe.store.site(i) for i in pipe.store.all_ids()])
    assert rows[0]["status"] in ("pass", "check")
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    csv_text = to_csv(rows)
    assert csv_text.splitlines()[0].startswith("rank,status,address")
    md = to_markdown(rows, pipe.cfg)
    assert "## Assumptions" in md and "not valuations" in md


# ---- sources -----------------------------------------------------------------

def test_paste_source():
    src = PasteSource("# comment\n12 Sandringham Rd, Kingsland | $1,050,000 | 3 | 1\n\n45 Example St\n | 100")
    ls = list(src.listings())
    assert [l.address for l in ls] == ["12 Sandringham Rd, Kingsland", "45 Example St"]
    assert ls[0].price == 1_050_000 and ls[0].bedrooms == 3 and ls[0].existing_dwellings == 1
    assert ls[1].price is None
    assert len(src.rejected) == 1


def test_csv_source():
    text = "Address,Price,Beds,Land,Notes\n1 A St,Enquiries over $900k,3,650 m2,Home and income\n,5,,,\n"
    src = CsvSource(text)
    ls = list(src.listings())
    assert len(ls) == 1 and ls[0].price == 900_000 and ls[0].price_is_indicative
    assert ls[0].land_area_m2 == 650 and ls[0].description == "Home and income"
    assert len(src.rejected) == 1


def test_trademe_stub_refuses_without_keys(monkeypatch):
    monkeypatch.delenv("TRADEME_CONSUMER_KEY", raising=False)
    with pytest.raises(TradeMeNotConfigured):
        list(TradeMeApiSource().listings())


# ---- geometry / address --------------------------------------------------------

def test_geometry():
    sq = square(0, 0, 10)
    assert geo.polygon_area(sq) == pytest.approx(100)
    assert geo.polygon_area(geo.scaled(sq, 0.6)) == pytest.approx(36)
    w, d = geo.min_rect_dims([[[0, 0], [0, 40], [15, 40], [15, 0], [0, 0]]])
    assert (round(w), round(d)) == (15, 40)
    # L-shape: centroid outside polygon, interior_point must be inside
    L = [[[0, 0], [0, 10], [2, 10], [2, 2], [10, 2], [10, 0], [0, 0]]]
    assert geo.point_in_polygon(*geo.interior_point(L), L)
    # tiny polygon in lon/lat degrees: centroid must be in the middle, not on an edge
    d = [[[174.7, -36.9], [174.7, -36.8998], [174.7002, -36.8998], [174.7002, -36.9], [174.7, -36.9]]]
    cx, cy = geo.centroid(d[0])
    assert cx == pytest.approx(174.7001, abs=1e-9) and cy == pytest.approx(-36.8999, abs=1e-9)


@pytest.mark.parametrize("raw,num,street,suburb", [
    ("12 Sandringham Rd, Kingsland", "12", "Sandringham Road", "Kingsland"),
    ("12 Sandringham Road Kingsland Auckland 1024", "12", "Sandringham Road", "Kingsland"),
    ("1/304a Sandringham Road", "1/304A", "Sandringham Road", None),
    ("Flat 2 / 45 Mt Albert Rd, Mount Albert, Auckland", "2/45", "Mt Albert Road", "Mount Albert"),
    ("7 O'Neills Ave, Takapuna", "7", "O'Neills Avenue", "Takapuna"),
    ("G09/39 Thomas Road MANGERE 2022", "G09/39", "Thomas Road", "MANGERE"),
    ("31-33 Neilson Street Onehunga Auckland 1061", "31-33", "Neilson Street", "Onehunga"),
])
def test_parse_address(raw, num, street, suburb):
    pa = parse_address(raw)
    assert (pa.number, pa.street, pa.suburb) == (num, street, suburb)


def test_parse_address_rejects_garbage():
    assert parse_address("Sandringham Road") is None


def test_gsheet_source_parses_inbox(monkeypatch):
    from gsf.sources import gsheet

    class R:
        status_code = 200
        text = ('"Address","Price","Bedrooms","Existing dwellings","Listing URL","Notes / listing text","Added by"\n'
                '"14 Example Street, Suburb","950000","3","1","","Example row: delete me",""\n'
                '"10 The Crest, Sunnyhills","1,050,000","3","1","https://x/1","Flat section","G"\n')
    monkeypatch.setattr(gsheet.requests, "get", lambda *a, **k: R())
    src = gsheet.GoogleSheetSource("https://docs.google.com/spreadsheets/d/1vasGaXW6TTa_BC4JyNVGBlev7WTS-6Z3z38odIkvkqo/edit")
    ls = list(src.listings())
    assert [l.address for l in ls] == ["10 The Crest, Sunnyhills"]
    assert ls[0].price == 1_050_000 and ls[0].existing_dwellings == 1 and ls[0].url == "https://x/1"
    assert "gviz/tq?tqx=out:csv&sheet=Inbox" in src.url
