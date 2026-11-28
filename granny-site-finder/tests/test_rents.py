import pytest

from gsf.config import load_config
from gsf.lookups.http import HttpClient
from gsf.lookups.rents import BondRents

HDR = ("TimeFrame,Location Id,Dwelling Type,Number Of Beds,Total Bonds,Active Bonds,Closed Bonds,Median Rent,"
       "Geometric Mean Rent,Upper Quartile Rent,Lower Quartile Rent,Log Std Dev Weekly Rent\n")
ROWS = [
    # latest quarter: SA2 A has 12 x 3-bed House -> direct hit
    "1/07/2026,100001,House,3,12,100,10,700,700,760,640,0.2",
    # SA2 A 2-bed House thin in latest quarter, but 4 quarters pool to 12 bonds (bond-weighted)
    "1/07/2026,100001,House,2,4,30,3,600,600,640,560,0.2",
    "1/04/2026,100001,House,2,4,30,3,580,580,620,540,0.2",
    "1/01/2026,100001,House,2,4,30,3,560,560,600,520,0.2",
    # 2-bed ALL latest quarter is big, but House must win (like-for-like first)
    "1/07/2026,100001,ALL,2,40,300,30,450,450,500,400,0.2",
    # 1-bed: only in neighbour SA2 B
    "1/07/2026,100002,House,1,15,50,5,480,480,520,440,0.2",
    # suppressed cell
    "1/07/2026,100001,House,4,2,10,1,NULL,NULL,NULL,NULL,NULL",
    "1/07/2026,-99,House,3,9999,9999,999,650,650,700,600,0.2",
]


@pytest.fixture
def rents(tmp_path):
    cfg = load_config()
    db = str(tmp_path / "r.sqlite")
    r = BondRents(HttpClient(db), cfg, db)
    p = tmp_path / "b.csv"
    p.write_text(HDR + "\n".join(ROWS) + "\n", encoding="utf-8")
    r.load_csv(str(p), "test")
    return r


def test_direct_latest_quarter(rents):
    r = rents.lookup("100001", "A", 3, ["House", "ALL"], "median")
    assert r.weekly == 700 and r.period == "Q3 2026 (Jul-Sep)" and r.bonds == 12


def test_pooled_quarters_bond_weighted_and_type_first(rents):
    r = rents.lookup("100001", "A", 2, ["House", "ALL"], "median")
    assert r.dwelling_type == "House" and r.weekly == 580 and r.bonds == 12 and "pooled" in r.period
    assert rents.lookup("100001", "A", 2, ["House", "ALL"], "lower_quartile").weekly == 540


def test_neighbour_pool(rents):
    assert not rents.lookup("100001", "A", 1, ["House"], "median").ok
    r = rents.lookup("100001", "A", 1, ["House"], "median", neighbours=["100002"])
    assert r.weekly == 480 and "2 SA2s within" in r.label()


def test_thin_and_unknown(rents):
    r = rents.lookup("100001", "A", 4, ["House"], "median")
    assert not r.ok and "manual rent" in r.note
    assert not rents.lookup("100001", "A", None, ["House"], "median").ok
