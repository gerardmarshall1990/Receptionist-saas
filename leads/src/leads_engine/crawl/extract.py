"""Rule-based signals from a crawled site (spec section 4). Pure: no network, no DB.

Every positive signal carries a quoted snippet and the URL of the page it came
from. "Absence" signals (no online booking, no chat) record which pages were
checked, so "not found" is auditable and distinguishable from "not crawled".
Nothing is inferred beyond the rule that fired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import phonenumbers

from leads_engine.config_loader import AppConfig, CategoryConfig
from leads_engine.crawl.html import Page
from leads_engine.universe import normalize as nz

EXTRACTOR_VERSION = "stage2-v1"
SNIPPET = 300


@dataclass
class Hit:
    value: bool
    evidence: str
    url: str | None
    count: int | None = None


@dataclass
class Extraction:
    signals: dict[str, Hit] = field(default_factory=dict)
    phones: list[str] = field(default_factory=list)  # E.164 found on the site
    emails: list[tuple[str, str]] = field(default_factory=list)  # (email, source_url)
    flags: list[str] = field(default_factory=list)


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


LEAVE_MESSAGE = _rx(
    r"leave (?:us )?a (?:message|voicemail)", r"leave your name and (?:phone )?number",
    r"we(?:'|\u2019)?ll call you back", r"we will (?:call you back|return your call)",
    r"(?:return|returns) (?:your|all) calls?", r"call you back (?:as soon as|within|shortly)",
)  # fmt: skip
EMERGENCY = _rx(
    r"24 ?/ ?7", r"24-7", r"24 hours? a day", r"24[- ]hour (?:emergency|service)",
    r"emergency (?:service|services|repairs?|calls?|appointments?|plumb\w*|dentist\w*)",
    r"same[- ]day emergenc\w+", r"around the clock", r"day or night", r"after[- ]hours (?:service|emergenc\w+)",
)  # fmt: skip
# The business already has someone (or something) answering: no 24/7 pain claim then.
COVERAGE = _rx(
    r"live answer\w*", r"answered 24 ?/ ?7", r"(?:a )?real (?:person|human) (?:always )?answers",
    r"live person answers", r"we always answer", r"live operators?", r"live dispatch\w*",
    r"dispatchers? (?:are )?available 24",
)  # fmt: skip
ANSWERING = _rx(
    r"answering service",
    r"virtual receptionist",
    r"ai receptionist",
    r"ai phone (?:agent|assistant)",
)
LIMITED_HOURS = _rx(
    r"closed (?:on )?(?:saturdays?|sundays?|weekends?)", r"(?:sat|sun)(?:urday|day)?s?\s*[:\-\u2013]?\s*closed",
    r"closed (?:for|during) lunch", r"by appointment only", r"closed (?:on )?(?:fridays?|mondays?)",
)  # fmt: skip
PHONE_ONLY = _rx(
    r"call (?:us )?(?:today |now )?(?:to|for) (?:schedule|book|make|set up|request)(?: an| your| a)? "
    r"(?:appointment|service|estimate|consultation|visit)",
    r"call (?:us )?(?:today |now )?for (?:an |your |a )?(?:appointment|estimate|consultation)",
    r"to (?:schedule|book|make) an appointment,? (?:please )?call", r"please call to schedule",
)  # fmt: skip
HIRING_CONTEXT = _rx(
    r"now hiring", r"we(?:'|\u2019)?re hiring", r"we are hiring", r"join our team", r"career opportunit\w+",
    r"job openings?", r"open positions?", r"employment opportunit\w+", r"apply (?:now|today)",
)  # fmt: skip
FRONT_DESK_ROLES = _rx(
    r"receptionist", r"front desk", r"front office", r"dispatcher", r"customer service rep\w*",
    r"call center", r"scheduling coordinator", r"scheduler", r"patient coordinator", r"office assistant",
)  # fmt: skip
OWNER_OPERATED = _rx(
    r"family[- ]owned", r"locally[- ]owned", r"owner[- ]operated", r"family[- ]operated", r"owner and operator",
)  # fmt: skip
# "Independently owned and operated" is the standard FRANCHISE disclaimer, not independence.
FRANCHISE = _rx(
    r"(?:each|every) (?:franchise|location|office) is independently owned",
    r"independently owned and operated franchise", r"franchise (?:location|owner)s?\b",
    r"locally owned (?:and operated )?franchise",
)  # fmt: skip
PRICING = _rx(
    r"(?:starting at|starts at|from|only|just|flat[- ]rate|special|coupon|save|price)\D{0,20}\$\s?\d{2,5}",
    r"\$\s?\d{2,5}(?:\.\d\d)?\s*(?:off|special|flat rate|service call|diagnostic|exam|cleaning)",
)  # fmt: skip
MULTI_LOCATION = _rx(
    r"our locations", r"(?:two|three|four|five|\d+) locations", r"multiple locations", r"offices in"
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
_JUNK_EMAIL_DOMAINS = (
    "example.com", "domain.com", "email.com", "yourdomain.com", "yoursite.com", "sentry.io",
    "wixpress.com", "sentry-next.wixpress.com", "godaddy.com", "mysite.com", "test.com",
)  # fmt: skip
_IMAGE_SUFFIX = re.compile(r"\.(png|jpe?g|gif|webp|svg)$", re.I)


def snippet(text: str, start: int, end: int, width: int = SNIPPET) -> str:
    """The sentence-ish window around a match, trimmed to whole words."""
    pad = max(0, (width - (end - start)) // 2)
    a, b = max(0, start - pad), min(len(text), end + pad)
    while a > 0 and not text[a - 1].isspace() and start - a < pad + 20:
        a -= 1
    while b < len(text) and not text[b].isspace() and b - end < pad + 20:
        b += 1
    out = text[a:b].strip()
    return ("…" if a > 0 else "") + out[:width] + ("…" if b < len(text) else "")


def _first(rx: re.Pattern[str], pages: list[Page]) -> Hit | None:
    for p in pages:
        if m := rx.search(p.text):
            return Hit(True, snippet(p.text, m.start(), m.end()), p.url)
    return None


def _signature(sigs: list[str], pages: list[Page]) -> tuple[str, str] | None:
    for p in pages:
        for s in sigs:
            if s in p.raw_lower:
                return s, p.url
    return None


def _site_phones(pages: list[Page]) -> list[str]:
    found: list[str] = []
    for p in pages:
        candidates = list(p.tel)
        candidates += [
            m.raw_string for m in phonenumbers.PhoneNumberMatcher(p.text[:200_000], "US")
        ]
        for raw in candidates:
            ph = nz.normalize_phone(raw)
            if ph and ph.e164 and ph.e164 not in found:
                found.append(ph.e164)
    return found


def _site_emails(pages: list[Page]) -> list[tuple[str, str]]:
    out: dict[str, str] = {}
    for p in pages:
        for raw in [*p.mailto, *_EMAIL_RE.findall(p.text)]:
            e = raw.strip().strip(".,;:").lower()
            dom = e.rpartition("@")[2]
            if not e or _IMAGE_SUFFIX.search(e) or dom.endswith(_JUNK_EMAIL_DOMAINS):
                continue
            out.setdefault(e, p.url)
    return list(out.items())


def _checked(pages: list[Page]) -> str:
    return "Checked " + ", ".join(p.url for p in pages)


def extract(pages: list[Page], business: dict[str, Any], cfg: AppConfig) -> Extraction:
    """Signals for one business from its site's pages (home first)."""
    ex = Extraction()
    if not pages:
        return ex
    cat: CategoryConfig | None = cfg.categories.get(business.get("category", ""))
    crawl = cfg.crawl
    sig = ex.signals
    home = pages[0]
    sig["website_works"] = Hit(True, f"Homepage loaded: {home.title or home.url}", home.url)

    # --- disqualifiers -----------------------------------------------------
    vendor = _signature([v for v in cfg.global_.answering_vendor_patterns], pages)
    negative = (
        _first(re.compile("|".join(re.escape(k) for k in cat.negative_keywords), re.I), pages)
        if cat
        else None
    )
    answering = _first(ANSWERING, pages)
    if vendor:
        sig["uses_answering_service"] = Hit(True, f"Site references {vendor[0]}", vendor[1])
    elif hit := (negative or answering):
        sig["uses_answering_service"] = hit
    if franchise := _first(FRANCHISE, pages):
        sig["is_chain"] = franchise

    # --- pain --------------------------------------------------------------
    if h := _first(LEAVE_MESSAGE, pages):
        sig["leave_message_text"] = h
    coverage = _first(COVERAGE, pages)
    if (h := _first(EMERGENCY, pages)) and not coverage and "uses_answering_service" not in sig:
        sig["emergency_247_claim"] = h
    if h := _first(LIMITED_HOURS, pages):
        sig["limited_hours_text"] = h

    booking = _signature(crawl.booking_signatures, pages)
    sig["no_online_booking"] = (
        Hit(False, f"Online booking found: {booking[0]}", booking[1])
        if booking
        else Hit(True, _checked(pages) + ": no online booking widget or link found", home.url)
    )
    chat = _signature(crawl.chat_signatures, pages)
    sig["no_chat_widget"] = (
        Hit(False, f"Chat/text widget found: {chat[0]}", chat[1])
        if chat
        else Hit(True, _checked(pages) + ": no chat or text widget found", home.url)
    )
    if not booking and (h := _first(PHONE_ONLY, pages)):
        sig["phone_only_booking"] = h

    for p in pages:
        for m in HIRING_CONTEXT.finditer(p.text):
            window = p.text[max(0, m.start() - 250) : m.end() + 250]
            if role := FRONT_DESK_ROLES.search(window):
                a = max(0, m.start() - 250) + role.start()
                sig["hiring_receptionist"] = Hit(
                    True, snippet(p.text, min(a, m.start()), max(a, m.end())), p.url
                )
                break
        if "hiring_receptionist" in sig:
            break

    # --- value / fit -------------------------------------------------------
    if h := _first(PRICING, pages):
        sig["pricing_hints"] = h
    if h := _first(MULTI_LOCATION, pages):
        sig["multiple_locations"] = h
    if "is_chain" not in sig and (h := _first(OWNER_OPERATED, pages)):
        sig["owner_operated"] = h

    # --- contacts ----------------------------------------------------------
    ex.phones = _site_phones(pages)
    usable = [p for p in ex.phones if (ph := nz.normalize_phone(p)) and ph.usable]
    if usable:
        sig["single_phone_number"] = Hit(
            len(usable) == 1,
            f"Phone numbers on site: {', '.join(usable[:5])}",
            home.url,
            len(usable),
        )
    ex.emails = _site_emails(pages)

    # --- does this site belong to this business? ---------------------------
    listed = business.get("phone")
    words = [
        w
        for w in (business.get("name_key") or "").split()
        if len(w) >= 3 and w not in nz._GENERIC_WORDS
    ]
    site_text = " ".join((p.title + " " + p.text).lower() for p in pages)
    name_match = any(re.search(rf"\b{re.escape(w)}\b", site_text) for w in words) if words else None
    phone_match = bool(listed and listed in ex.phones)
    if listed and ex.phones and not phone_match:
        ex.flags.append("phone_not_on_site")
    if name_match is False and not phone_match:
        ex.flags.append("website_mismatch")
    if len(home.text) < 300:
        ex.flags.append("thin_homepage")  # likely JS-rendered: signals may be missing
    return ex
