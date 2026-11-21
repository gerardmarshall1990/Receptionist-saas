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


# --------------------------------------------- v2 regressions (from the hand review)


def _p(text: str) -> list[Page]:
    return [Page(url="https://x.test/", title="X", text=text, raw_lower=text.lower())]


BIZ = {"name_key": "x", "category": "plumbing"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Contact Us Name Email Leave Us A Message Send Message", False),  # web form label
        ("Leave us a message, and we'll respond shortly. Send", False),
        ("Request a Quote ... Leave us a message... Submit", False),
        ("When we say we will call you back, we do, on the day we said.", False),
        ("If you leave us a voicemail after hours, we'll get back to you", True),
        ("If you have to leave a voicemail because all three lines are busy, we will call", True),
        ("If we miss your call, leave your name and number", True),
        ("Call or leave a voicemail any time.", True),
        ("You will never get voicemail when you call us.", False),
    ],
)
def test_leave_message_is_about_voicemail_not_forms(text: str, expected: bool) -> None:
    assert ("leave_message_text" in extract(_p(text), BIZ, CFG).signals) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your AC unit is working 24/7 to keep you cool.", False),
        ("Maintenance helps reduce expensive emergency repairs.", False),
        ("Plumbers in New Jersey AVAILABLE 24/7 SINCE 1965", True),
        ("24/7 Emergency Service Available", True),
        ("we respond to emergency calls 24/7, 365 days a year", True),
        ("If you need immediate or emergency service, please call 815-893-9276.", True),
    ],
)
def test_emergency_claim_context(text: str, expected: bool) -> None:
    assert ("emergency_247_claim" in extract(_p(text), BIZ, CFG).signals) is expected


@pytest.mark.parametrize(
    ("text", "disqualified", "live_answer_caution"),
    [
        ("No answering service or AI. Call us and someone from our team answers.", False, True),
        ("You will never talk to an answering service or a secretary.", False, True),
        ("Emergency calls reach a technician rather than an answering service.", False, True),
        (
            "No receptionist or answering service here. Fred answers all calls personally.",
            False,
            True,
        ),
        ("After hours please call and our answering service will assist you.", True, False),
        ("24-Hour Answering Service. Competitive pricing.", True, False),
    ],
)
def test_answering_service_respects_negation(
    text: str, disqualified: bool, live_answer_caution: bool
) -> None:
    ex = extract(_p(text), BIZ, CFG)
    assert ("uses_answering_service" in ex.signals) is disqualified
    assert ("promises_live_answer" in ex.flags) is live_answer_caution


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Support Customer Service Report Car Shipping Careers We're Hiring", False),  # "Rep-ort"
        ("We're Hiring For: HVAC Technicians, Lead Installers and Helpers", False),
        ("Now hiring: front desk receptionist, full time.", True),
        (
            "We are always accepting applications for Customer Service, Dispatch, Plumbing jobs. Apply now",
            True,
        ),
    ],
)
def test_hiring_needs_a_front_desk_role_next_to_the_hiring_text(text: str, expected: bool) -> None:
    ex = extract(_p(text), BIZ, CFG)
    assert ("hiring_receptionist" in ex.signals) is expected
    if expected:
        ev = ex.signals["hiring_receptionist"].evidence.lower()
        assert any(w in ev for w in ("hiring", "apply", "applications"))


def test_request_service_button_counts_as_online_booking() -> None:
    text = "Call for Appointment 1-888-797-5225 Request Service About"
    s = extract(_p(text), BIZ, CFG).signals
    assert s["no_online_booking"].value is False
    assert "phone_only_booking" not in s


# ---------------------------------------------- v3 regressions (second hand review)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "I agree to be contacted using an automatic phone dialing system or company voice mail system.",
            False,
        ),
        (
            "These aren't situations where you want to leave a voicemail or wait for a call back.",
            False,
        ),
        (
            "Call your regular dentist first. Many practices offer after-hours lines. Leave a clear voicemail.",
            False,
        ),
        ("Customize reminders with email, text messaging, and/or voicemail.", False),
        (
            "Did not have to wait for a call-back from an automated system, or voice mail. Highly recommend!",
            False,
        ),
        ("Patients of record can also use the practice voicemail for after-hours questions.", True),
        (
            "After hours, please call our main office number and listen to the voicemail instructions.",
            True,
        ),
        ("Left an after hours voicemail when the heater died and they called back.", True),
    ],
)
def test_voicemail_only_when_callers_end_up_there(text: str, expected: bool) -> None:
    assert ("leave_message_text" in extract(_p(text), BIZ, CFG).signals) is expected


def test_live_technician_answering_suppresses_the_247_pain_claim() -> None:
    ex = extract(
        _p("24/7 Emergency Service. Emergency calls reach a technician, not a call center."),
        BIZ,
        CFG,
    )
    assert "emergency_247_claim" not in ex.signals
    assert "promises_live_answer" in ex.flags


@pytest.mark.parametrize(
    ("text", "multi", "chain"),
    [
        ("One doctor, two locations, every patient.", True, False),
        ("Serving homes and offices in League City.", False, False),
        ("One of the leading dental offices in Spring, TX.", False, False),
        (
            "In 1999 we became part of the Hajoca family, joining over 200 locations nationwide.",
            True,
            True,
        ),
    ],
)
def test_multi_location_vs_chain_scale(text: str, multi: bool, chain: bool) -> None:
    s = extract(_p(text), BIZ, CFG).signals
    assert ("multiple_locations" in s) is multi
    assert ("is_chain" in s) is chain


def test_malformed_links_are_skipped_not_fatal() -> None:
    html = '<html><body><a href="http://[broken">x</a><a href="/contact">Contact</a></body></html>'
    p = parse(html, "https://ok.test/")
    assert [link.url for link in p.links] == ["https://ok.test/contact"]
    assert candidate_links(p, "ok.test", CFG.crawl.link_kinds, CFG.crawl.link_kind_priority) == [
        ("contact", "https://ok.test/contact")
    ]


# ---------------------------------------------- v4 regressions (third hand review)


def test_reviews_about_other_companies_are_ignored() -> None:
    a = extract(
        _p(
            "Ron answered my emergency call immediately, unlike 2 other plumbers I called and had to leave a voicemail."
        ),
        BIZ,
        CFG,
    )
    assert "leave_message_text" not in a.signals
    b = extract(
        _p("The last company I used had AI receptionists and was over-priced. Royal was great."),
        BIZ,
        CFG,
    )
    assert "uses_answering_service" not in b.signals
    c = extract(_p("Called, very nice answering service. Fast schedule to come out."), BIZ, CFG)
    assert c.signals["uses_answering_service"].value  # about THIS business: still counts


def test_phone_only_not_when_a_form_is_offered() -> None:
    s = extract(
        _p("If you would like to schedule an appointment, call or fill out the contact form."),
        BIZ,
        CFG,
    ).signals
    assert "phone_only_booking" not in s
    s2 = extract(
        _p("Call us today to schedule an appointment. We look forward to it."), BIZ, CFG
    ).signals
    assert s2["phone_only_booking"].value


@pytest.mark.parametrize(
    ("text", "chain"),
    [
        (
            "Licensed at scale, but permit consistency varies by franchise owner and local crew.",
            False,
        ),
        ("Each location is independently owned and operated.", True),
        ("With more than 150 franchise locations globally, our network reaches far.", True),
        ("Call our franchise owner Dave for details.", True),
    ],
)
def test_franchise_wording_must_be_about_them(text: str, chain: bool) -> None:
    assert ("is_chain" in extract(_p(text), BIZ, CFG).signals) is chain


def test_fire_department_is_not_an_emergency_claim_and_saved_is_not_a_price() -> None:
    s = extract(
        _p("Thank You to Watsonville Fire and Emergency Services. He saved me over $300."), BIZ, CFG
    ).signals
    assert "emergency_247_claim" not in s
    assert "pricing_hints" not in s


def test_name_and_number_below_is_a_form_not_voicemail() -> None:
    t = "Give us a call right now or leave your name and number below and we will be in touch with you."
    assert "leave_message_text" not in extract(_p(t), BIZ, CFG).signals
    t2 = "If you reach our voicemail, leave your name and number and we will call you back."
    assert extract(_p(t2), BIZ, CFG).signals["leave_message_text"].value


# ------------------------------------- v6 regressions (Stage 4 hand review of 50 A-tier leads)


def test_price_must_follow_its_keyword() -> None:
    story = "KC completed a $400,000 project. The profit from that job funded a $500 van and tools."
    assert "pricing_hints" not in extract(_p(story), BIZ, CFG).signals
    for text in (
        "Drain cleaning from $99 this month.",
        "Tune-ups starting at just $150.",
        "AC check for only $89.00.",
    ):
        assert extract(_p(text), BIZ, CFG).signals["pricing_hints"].value, text


def test_voicemail_left_by_the_business_is_not_caller_voicemail() -> None:
    review = "Procedure was painless. Doctor called next day to make sure everything was good. Missed call but he left a voice mail."
    assert "leave_message_text" not in extract(_p(review), BIZ, CFG).signals
    caller = (
        "Left an after hours voicemail when the AC quit in the evening and they called back fast."
    )
    assert extract(_p(caller), BIZ, CFG).signals["leave_message_text"].value


# v7: emails glued to neighbouring text (found in the Stage 5 export hand check).
@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # Adjacent block elements: the page text must keep them apart.
        ("<div>(704) 664-2498</div><div>ThompsonPlumbing@Windstream.net</div>",
         {"thompsonplumbing@windstream.net"}),
        ("<p>IL 60007</p><span>Email</span><span>info@oralsurgeryil.com</span><p>Office</p>",
         {"info@oralsurgeryil.com"}),
        # Glued inside one text node: dropped, never repaired.
        ("<p>Call 776-5691jason@briwv.comwv today</p>", set()),
        ("<p>info@cougarmechanical.comoffice</p>", set()),
        ("<p>Write to 60007emailinfo@x-dental.com</p>", set()),
        # Ordinary text emails still work, including ones that start with digits.
        ("<p>Email: 247plumbing@gmail.com, or office@joesplumbing.com.</p>",
         {"247plumbing@gmail.com", "office@joesplumbing.com"}),
        # mailto links are trusted as written.
        ('<a href="mailto:Front.Desk@SmileCo.com">write</a>', {"front.desk@smileco.com"}),
    ],
)  # fmt: skip
def test_site_emails_are_whole_tokens(html: str, expected: set[str]) -> None:
    pages = [
        parse(f"<html><title>t</title><body>{html}</body></html>", "https://joesplumbing.com/")
    ]
    assert set(dict(extract(pages, PLUMBER, CFG).emails)) == expected


def test_page_text_keeps_element_boundaries_but_not_inside_words() -> None:
    p = parse(
        "<html><body><div>Mon-Fri</div><div>8-5</div><p>Emer<b>gency</b> service</p></body></html>",
        "https://x.com/",
    )
    assert "Mon-Fri 8-5" in p.text
    assert "Emergency service" in p.text


def test_page_text_survives_control_characters_and_skips_comments() -> None:
    p = parse(
        "<html><body><p>Call\x0bus</p><!-- hidden note --><div>today</div></body></html>",
        "https://x.com/",
    )
    assert "hidden note" not in p.text
    assert p.text.endswith("today")


# v8: false positives from the first law / roofing / electrical / auto crawl sample.
@pytest.mark.parametrize(
    ("text", "signal", "expected"),
    [
        ("Call, chat, or submit the form. Our Tampa line is answered 24 hours a day.", "emergency_247_claim", False),
        ("GET A FREE CASE REVIEW CALL US 24/7 AT (904) 777-5646", "emergency_247_claim", True),
        ("If you have been arrested, we are available 24 hours a day, 7 days a week.", "emergency_247_claim", True),
        ("So nice to have someone answer the phone, especially in a world of automation, answering machines, "
         "and voicemail.", "leave_message_text", False),
        ("I called and left voicemails for many attorneys looking for help. Peter called back.", "leave_message_text", False),
        ("AFTER HOURS EMERGENCY: call and leave a voicemail with your contact information.", "leave_message_text", True),
        ("You may also email or call us to make an appointment.", "phone_only_booking", False),
        ("Submit your legal inquiry, and we'll call you back. Call for an appointment.", "phone_only_booking", False),
        ("Injury cases for over 30 years. Call us for an appointment.", "phone_only_booking", True),
        ("Our clients range from family-owned businesses to multinational corporations.", "owner_operated", False),
        ("27 Years of Combined Experience | Locally Owned | Responsive", "owner_operated", True),
    ],
)  # fmt: skip
def test_v8_new_category_false_positives(text: str, signal: str, expected: bool) -> None:
    assert (
        signal in extract(_p(text), {"name_key": "x", "category": "law_small"}, CFG).signals
    ) is expected
