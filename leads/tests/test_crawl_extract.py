"""Signal extraction on saved HTML fixtures (hand-written, tests/fixtures/sites)."""

from __future__ import annotations

from pathlib import Path

import pytest

from leads_engine.config_loader import load_config
from leads_engine.crawl.extract import extract, snippet
from leads_engine.crawl.fetcher import decode, looks_blocked, looks_parked
from leads_engine.crawl.html import Page, candidate_links, parse
from tests.conftest import REAL_CONFIG

CFG = load_config(REAL_CONFIG)
FIX = Path(__file__).parent / "fixtures" / "sites"


def page(name: str, url: str) -> Page:
    return parse((FIX / name).read_text(), url)


PLUMBER = {
    "name_key": "joes plumbing",
    "category": "plumbing",
    "phone": "+15124741234",
    "domain": "joesplumbing.com",
}


@pytest.fixture
def plumber_pages() -> list[Page]:
    return [
        page("plumber_home.html", "https://joesplumbing.com/"),
        page("plumber_contact.html", "https://joesplumbing.com/contact-us"),
        page("plumber_careers.html", "https://joesplumbing.com/careers"),
    ]


# ------------------------------------------------------------------- html


def test_parse_strips_scripts_and_collects_links() -> None:
    p = page("plumber_home.html", "https://joesplumbing.com/")
    assert p.title.startswith("Joe's Plumbing")
    assert "googletagmanager" not in p.text
    assert "googletagmanager" in p.raw_lower  # signatures still visible
    assert p.tel == ["+1-512-474-1234"]
    assert any(link.url == "https://joesplumbing.com/contact-us" for link in p.links)


def test_candidate_links_pick_one_internal_page_per_kind() -> None:
    p = page("plumber_home.html", "https://joesplumbing.com/")
    got = candidate_links(p, "joesplumbing.com", CFG.crawl.link_kinds, CFG.crawl.link_kind_priority)
    assert got == [
        ("contact", "https://joesplumbing.com/contact-us"),
        ("careers", "https://joesplumbing.com/careers"),
        ("about", "https://joesplumbing.com/about"),
        ("services", "https://joesplumbing.com/services"),
    ]  # no facebook, no pdf, no home


def test_block_and_parked_detection() -> None:
    assert looks_blocked(page("challenge.html", "https://x.com/"), CFG.crawl.block_markers)
    # A normal page with reCAPTCHA in a form is NOT a block.
    assert (
        looks_blocked(page("plumber_home.html", "https://x.com/"), CFG.crawl.block_markers) is None
    )
    assert looks_parked(page("parked.html", "https://x.com/"), CFG.crawl.parked_markers)
    assert (
        looks_parked(page("plumber_home.html", "https://x.com/"), CFG.crawl.parked_markers) is None
    )


def test_decode_uses_declared_charset() -> None:
    body = "Café".encode("latin-1")
    assert decode(body, "text/html; charset=ISO-8859-1") == "Café"
    assert decode("Café".encode(), "text/html") == "Café"
    assert decode(b"x", "text/html; charset=not-a-charset") == "x"


# ---------------------------------------------------------------- signals


def test_plumber_signals_with_evidence(plumber_pages: list[Page]) -> None:
    ex = extract(plumber_pages, PLUMBER, CFG)
    s = ex.signals
    assert s["leave_message_text"].value
    assert "leave a message" in s["leave_message_text"].evidence
    assert s["leave_message_text"].url == "https://joesplumbing.com/contact-us"
    assert "24/7" in s["emergency_247_claim"].evidence
    assert "Sat: Closed" in s["limited_hours_text"].evidence
    assert "schedule an appointment" in s["phone_only_booking"].evidence
    assert s["no_online_booking"].value
    assert s["no_chat_widget"].value
    assert "receptionist" in s["hiring_receptionist"].evidence
    assert s["owner_operated"].value
    assert "$89" in s["pricing_hints"].evidence
    assert s["single_phone_number"].value
    assert s["single_phone_number"].count == 1
    assert "uses_answering_service" not in s
    assert "is_chain" not in s
    assert ex.flags == []  # name and phone both match this site


def test_every_positive_signal_has_evidence_and_a_page(plumber_pages: list[Page]) -> None:
    for name, hit in extract(plumber_pages, PLUMBER, CFG).signals.items():
        assert hit.evidence, name
        assert hit.url, name
        assert hit.url.startswith("https://joesplumbing.com"), name
        assert len(hit.evidence) <= 302, name


def test_emails_only_published_ones_and_no_junk(plumber_pages: list[Page]) -> None:
    emails = dict(extract(plumber_pages, PLUMBER, CFG).emails)
    assert set(emails) == {"office@joesplumbing.com", "joe.smith1998@gmail.com"}
    assert emails["office@joesplumbing.com"] == "https://joesplumbing.com/contact-us"
    # never invented: no info@/contact@ guesses for a domain that didn't publish them
    assert not any(e.startswith(("info@", "contact@")) for e in emails)


def test_booking_chat_and_answering_service_detected() -> None:
    p = [page("dental_booking.html", "https://echodental.com/")]
    biz = {
        "name_key": "echo family dental",
        "category": "dental",
        "phone": "+15184362200",
        "domain": "echodental.com",
    }
    s = extract(p, biz, CFG).signals
    assert s["no_online_booking"].value is False
    assert "nexhealth" in s["no_online_booking"].evidence
    assert s["no_chat_widget"].value is False
    assert s["uses_answering_service"].value  # disqualifier
    assert "emergency_247_claim" not in s
    assert "phone_only_booking" not in s  # they have online booking
    # 555-01XX is fiction: not counted as a usable number
    assert s["single_phone_number"].count == 2


def test_franchise_disclaimer_is_a_chain_not_owner_operated() -> None:
    p = [page("franchise.html", "https://mrfixit.com/dallas")]
    s = extract(
        p, {"name_key": "mr fixit of dallas", "category": "plumbing", "domain": "mrfixit.com"}, CFG
    ).signals
    assert s["is_chain"].value
    assert "owner_operated" not in s
    assert "emergency_247_claim" not in s  # a live dispatcher answers: no pain claim


def test_website_mismatch_and_phone_not_on_site(plumber_pages: list[Page]) -> None:
    other = {"name_key": "colgan hydro jetting", "category": "plumbing", "phone": "+18323059716"}
    ex = extract(plumber_pages, other, CFG)
    assert "website_mismatch" in ex.flags
    assert "phone_not_on_site" in ex.flags


def test_thin_js_homepage_is_flagged_not_guessed() -> None:
    ex = extract(
        [page("thin_spa.html", "https://spa.com/")], {"name_key": "spa", "category": "dental"}, CFG
    )
    assert "thin_homepage" in ex.flags
    assert ex.signals["website_works"].value
    assert "leave_message_text" not in ex.signals


def test_no_pages_means_no_signals() -> None:
    ex = extract([], PLUMBER, CFG)
    assert ex.signals == {}
    assert ex.flags == []


def test_snippet_window() -> None:
    text = "a " * 400 + "LEAVE A MESSAGE" + " b" * 400
    i = text.index("LEAVE")
    out = snippet(text, i, i + 15)
    assert "LEAVE A MESSAGE" in out
    assert out.startswith("…")
    assert out.endswith("…")
    assert len(out) <= 302
