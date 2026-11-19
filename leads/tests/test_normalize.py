from __future__ import annotations

import pytest

from leads_engine.config_loader import load_config
from leads_engine.universe import normalize as nz
from tests.conftest import REAL_CONFIG

NORM = load_config(REAL_CONFIG).global_.normalization


# ------------------------------------------------------------------ phones


@pytest.mark.parametrize(
    ("raw", "e164"),
    [
        ("+15124741234", "+15124741234"),
        ("(512) 474-1234", "+15124741234"),
        ("512.474.1234", "+15124741234"),
        ("1-512-474-1234", "+15124741234"),
    ],
)
def test_phone_formats_normalize_to_e164(raw: str, e164: str) -> None:
    ph = nz.normalize_phone(raw)
    assert ph is not None
    assert ph.e164 == e164
    assert ph.valid
    assert ph.usable


def test_phone_rejects_and_flags() -> None:
    assert nz.normalize_phone(None) is None
    assert nz.normalize_phone("  ") is None
    bad = nz.normalize_phone("12345")
    assert bad is not None
    assert not bad.valid
    assert not bad.usable
    toll = nz.normalize_phone("+18004445555")
    assert toll is not None
    assert "toll_free" in toll.flags
    assert not toll.usable  # never the primary contact
    fiction = nz.normalize_phone("+15125550123")  # 555-01XX is reserved for fiction
    assert fiction is not None
    assert "suspicious" in fiction.flags
    repeated = nz.normalize_phone("+15127777777")
    assert repeated is not None
    assert "suspicious" in repeated.flags
    garbage = nz.normalize_phone("call us!")
    assert garbage is not None
    assert garbage.e164 is None


def test_phone_type_is_only_a_guess_for_us_numbers() -> None:
    ph = nz.normalize_phone("+15124741234")
    assert ph is not None
    assert ph.number_type == "fixed_line_or_mobile"  # real line type needs the T4 lookup


# ---------------------------------------------------------------- websites


@pytest.mark.parametrize(
    ("raw", "domain"),
    [
        ("https://www.scottsplumbing.net", "scottsplumbing.net"),
        ("scottsplumbing.net", "scottsplumbing.net"),
        ("http://WWW.Example.co.uk/about", "example.co.uk"),
        ("https://shop.joesplumbing.com/x", "joesplumbing.com"),
        ("https://joes.wixsite.com/plumbing", "joes.wixsite.com"),  # hosted: full host
    ],
)
def test_website_domain(raw: str, domain: str) -> None:
    site = nz.normalize_website(raw, NORM)
    assert site is not None
    assert site.domain == domain


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.facebook.com/joesplumbing",
        "https://lakes-mall-dental.hub.biz/",
        "https://www.yelp.com/biz/joes",
        "https://www.demandforce.com/b/drkim",
        "https://www.trane.com/dealer/123",
    ],
)
def test_directory_social_and_platform_pages_are_not_own_sites(raw: str) -> None:
    site = nz.normalize_website(raw, NORM)
    assert site is not None
    assert site.domain is None
    assert site.flag is not None
    assert site.flag.startswith("not_own_site:")


def test_government_page_is_not_own_site_and_not_an_exclusion() -> None:
    site = nz.normalize_website("https://www.cslb.ca.gov/lookup", NORM)
    assert site is not None
    assert site.domain is None
    assert site.flag == "government_page:ca.gov"


def test_empty_and_unparseable_websites() -> None:
    assert nz.normalize_website("", NORM) is None
    assert nz.normalize_website(None, NORM) is None
    bad = nz.normalize_website("not a url", NORM)
    assert bad is not None
    assert bad.domain is None


def test_first_business_site_skips_directories() -> None:
    site, flags = nz.first_business_site(["", "https://facebook.com/x", "joes.com"], NORM)
    assert site is not None
    assert site.domain == "joes.com"
    assert flags == ["not_own_site:facebook.com"]


def test_institution_domains() -> None:
    assert nz.is_institution_domain("ucla.edu")
    assert nz.is_institution_domain("army.mil")
    assert not nz.is_institution_domain("ca.gov")  # handled as a page, not an exclusion
    assert not nz.is_institution_domain("joes.com")
    assert not nz.is_institution_domain(None)


# ------------------------------------------------------------------- names


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Joe's Plumbing, LLC", "joes plumbing"),
        ("The Plumbing Connection Inc.", "plumbing connection"),
        ("Smith & Sons Heating Co", "smith and sons heating"),
        ("Café Dental P.C.", "cafe dental p c"),
        ("", ""),
        (None, ""),
    ],
)
def test_name_key(name: str | None, key: str) -> None:
    assert nz.name_key(name) == key


def test_generic_and_referral_names() -> None:
    assert nz.is_generic_name(nz.name_key("Dentist"))
    assert nz.is_generic_name(nz.name_key("Family Dentistry"))
    assert not nz.is_generic_name(nz.name_key("Tuskawilla Family Dentistry"))
    assert nz.is_referral_listing(nz.name_key("Emergency Dentist 24/7"))
    assert nz.is_referral_listing(nz.name_key("Plumber Near Me"))
    assert not nz.is_referral_listing(nz.name_key("24 Hour Plumbing Pros"))  # has a real name part
    assert not nz.is_referral_listing(nz.name_key("Family Dentistry"))


def test_patterns_match_normalized_whole_words() -> None:
    assert (
        nz.find_pattern("My Kid's Dentist & Orthodontics", ("my kids dentist",))
        == "my kids dentist"
    )
    assert nz.find_pattern("Mr. Rooter of Dallas", ("mr. rooter",)) == "mr rooter"
    assert nz.find_pattern("ROTO-ROOTER Plumbing", ("roto-rooter",)) == "roto rooter"
    assert nz.find_pattern("Stars Plumbing", ("ars",)) is None  # whole words only
    assert nz.find_pattern("Joe's Plumbing", ()) is None
    assert nz.find_pattern(None, ("x",)) is None


def test_prefix_patterns_ignore_mid_name_matches() -> None:
    pats = ("aspen dental", "castle dental")
    assert nz.find_pattern("Aspen Dental - Springfield", pats, prefix=True) == "aspen dental"
    assert nz.find_pattern("Twin Aspen Dental Center", pats, prefix=True) is None
    assert nz.find_pattern("New Castle Dental", pats, prefix=True) is None
    assert nz.find_pattern("New Castle Dental", pats) == "castle dental"  # contains mode


# --------------------------------------------------------------- addresses


def test_state_zip_city() -> None:
    assert nz.state_code("US-FL") == "FL"
    assert nz.state_code("ca") == "CA"
    assert nz.state_code("ON") is None  # not a US state
    assert nz.state_code(None) is None
    assert nz.zip5("33772") == "33772"
    assert nz.zip5("91206-3562") == "91206"
    assert nz.zip5("912063562") == "91206"
    assert nz.zip5("K1A 0B1") is None
    assert nz.clean_city("  San   Dimas ") == "San Dimas"
    assert nz.clean_city("") is None


def test_timezone_offline() -> None:
    assert nz.timezone_for(27.84, -82.79) == "America/New_York"  # Seminole, FL
    assert nz.timezone_for(19.72, -155.08) == "Pacific/Honolulu"  # Hilo, HI
    assert nz.timezone_for(None, None) is None


# ------------------------------------------------------------------ emails


def test_email_classification() -> None:
    on_site = nz.classify_email("Info@JoesPlumbing.com", "joesplumbing.com", NORM)
    assert on_site.email == "info@joesplumbing.com"
    assert on_site.syntax_ok
    assert on_site.is_role_based
    assert on_site.domain_matches_site
    assert on_site.flags == ()

    free = nz.classify_email("joe.smith@gmail.com", "joesplumbing.com", NORM)
    assert free.is_freemail
    assert not free.is_role_based
    assert "freemail" in free.flags

    other = nz.classify_email("office@someagency.com", "joesplumbing.com", NORM)
    assert "domain_mismatch" in other.flags

    bad = nz.classify_email("not-an-email", None, NORM)
    assert not bad.syntax_ok
    assert "bad_syntax" in bad.flags


def test_website_keeps_port_and_rejects_bad_port() -> None:
    site = nz.normalize_website("http://shop.example.com:8080/x", NORM)
    assert site is not None
    assert site.url == "http://shop.example.com:8080/x"
    bad = nz.normalize_website("http://example.com:99999/", NORM)
    assert bad is not None
    assert bad.domain is None
