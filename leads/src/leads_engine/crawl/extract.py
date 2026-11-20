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

# v4: competitor context ignored, a form = online channel, franchise wording must be about them.
# v5: "leave your name and number below" (a web form) is not voicemail.
EXTRACTOR_VERSION = (
    "stage2-v6"  # v6: price must follow its keyword; business-left voicemail ignored
)
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


# Callers hit voicemail / a machine / busy lines. NOT web-form labels ("Leave us a message ... Submit"):
# v1 matched those and was ~15% precise on review.
LEAVE_MESSAGE = _rx(
    r"leave (?:us )?a voice ?mail", r"(?:practice|office|our|the office) voice ?mail",
    r"listen to (?:the|our) voice ?mail", r"left (?:an? )?(?:after[- ]hours? )?voice ?mail",
    r"voice ?mail (?:after hours|for after[- ]hours)", r"after[- ]hours voice ?mail", r"answering machine",
    r"leave (?:us )?a message (?:after the (?:tone|beep)|on (?:our|the) (?:voice ?mail|machine))",
    r"leave your name and (?:phone )?number",
    r"(?:all|our|the) (?:phone )?lines are (?:busy|tied up)",
    r"if (?:we(?:'|\u2019)?re|we are|no one is) (?:unable to answer|out on (?:a )?(?:job|call)|on another call|"
    r"busy with (?:a )?customers?|not available)",
    r"if we (?:miss|can(?:'|\u2019)?t (?:get to|answer)|cannot answer|don(?:'|\u2019)?t answer) your call",
)  # fmt: skip
# Reviews that talk about OTHER businesses ("unlike 2 other plumbers I called ...").
COMPETITOR = _rx(
    r"\b(?:last|other|another|previous|prior)\s+(?:company|companies|plumbers?|dentists?|contractors?|"
    r"hvac (?:company|companies)|providers?|offices?|businesses)",
    r"\bunlike\b",
)
# Another online channel next to the "call us" line: not phone-only.
ONLINE_FORM = _rx(
    r"fill out (?:the|our|a|this) (?:contact |online |appointment |request )?form",
    r"form below",
    r"request (?:it )?online",
    r"book online",
    r"(?:email|e-mail) us",
)
# Boilerplate that mentions voicemail without saying callers end up there.
LEAVE_MESSAGE_NOT = _rx(
    # A web form, not a phone line: "leave your name and number below", "Submit".
    r"\bbelow\b",
    # The business called the customer ("Doctor called next day... he left a voice mail").
    r"\b(?:he|she|they|doctor|dr\.?|office|staff) left (?:me |us )?(?:a )?voice ?mail",
    r"\bform\b",
    r"\bsubmit\b",
    r"automatic (?:telephone|phone) dialing",
    r"consent",
    r"reminders?",
    r"many practices",
    r"your regular dentist",
)
LEAVE_MESSAGE_GUARD = _rx(LEAVE_MESSAGE_NOT.pattern, COMPETITOR.pattern)

# Must read as a claim about their service, not "your AC runs 24/7" or "avoid emergency repairs".
EMERGENCY = _rx(
    r"(?:available|open|on call|service|services|emergenc\w+|answer\w*|respon\w+|here for you|call us)\W{0,25}24 ?[/-] ?7",
    r"24 ?[/-] ?7\W{0,25}(?:emergenc\w+|service|availab\w+|support|response|repairs?|plumb\w*|hvac|ac\b|dentist\w*)",
    r"24 hours? a day", r"24[- ]hours? (?:emergency|service|response)",
    r"emergency (?:service|services|repairs?|calls?|appointments?|plumb\w*|dentist\w*|dental care|response)",
    r"same[- ]day emergenc\w+", r"around the clock", r"round[- ]the[- ]clock", r"day or night",
)  # fmt: skip
EMERGENCY_NOT = _rx(
    r"(?:avoid|prevent|reduce|reducing|costly|expensive|minimi[sz]e)\W+(?:\w+\W+){0,3}$",
    r"(?:running|working|runs|works)\W+$",
    r"(?:fire|police|medical|county|city)\W+(?:and|&)?\W*$",  # "Fire and Emergency Services"
)
# The business already has someone (or something) answering: no 24/7 pain claim then.
COVERAGE = _rx(
    r"live answer\w*", r"answered 24 ?/ ?7", r"(?:a )?real (?:person|human|plumber|technician|tech) (?:always )?answers",
    r"live person answers", r"we always answer", r"live operators?", r"live dispatch\w*",
    r"dispatchers? (?:are )?available 24", r"answers? (?:all|every|our) (?:phone )?calls", r"person to person",
    r"someone (?:from our team )?(?:always )?answers", r"when you call(?: us)?, we answer",
    r"calls? (?:reach|go (?:straight |directly )?to) (?:a|an|our) (?:technician|tech|plumber|person|human|owner)",
    r"we answer the phone",
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
    r"accepting applications",
)  # fmt: skip
FRONT_DESK_ROLES = _rx(
    r"receptionists?\b", r"front desk", r"front office", r"dispatchers?\b", r"dispatch (?:position|role|job)",
    r"customer service rep(?:resentative)?s?\b", r"customer service (?:position|role|job|team member)",
    r"call center (?:agent|rep)", r"scheduling coordinator", r"patient coordinator", r"office assistant",
    r"customer service, dispatch",
)  # fmt: skip
NEGATION = re.compile(
    r"(?:\b(?:no|not|never|without|rather than|instead of)\b|\w+n(?:'|\u2019)t\b)[^.!?]{0,60}$",
    re.I,
)
OWNER_OPERATED = _rx(
    r"family[- ]owned", r"locally[- ]owned", r"owner[- ]operated", r"family[- ]operated", r"owner and operator",
)  # fmt: skip
# "Independently owned and operated" is the standard FRANCHISE disclaimer, not independence.
FRANCHISE = _rx(
    r"(?:each|every) (?:franchise|location|office) is independently owned",
    r"independently owned and operated franchise",
    r"(?:our|this|each|every) franchise (?:location|owner)s?\b", r"\d+\+? franchise locations",
    r"locally owned (?:and operated )?franchise",
    # Scale that only chains/distributors have ("joining over 200 locations nationwide").
    r"(?:over |more than )?\d{2,}\+? (?:locations|offices|branches) (?:nationwide|across (?:the )?(?:us|u\.s\.|country|america))",
)  # fmt: skip
PRICING = _rx(
    # The price must follow the keyword directly ("from $99", "starting at just $150"); v5 allowed 20
    # characters in between and matched stories ("...from that job funded a $500 van").
    r"(?:starting at|starts at|from|only|just|flat[- ]rate|special|coupon|price)[\s:]{0,3}(?:just |only )?\$\s?\d{2,5}(?![\d,])",
    r"\$\s?\d{2,5}(?:\.\d\d)?\s*(?:off|special|flat rate|service call|diagnostic|exam|cleaning)",
)  # fmt: skip
MULTI_LOCATION = _rx(
    r"our locations",
    r"(?:two|three|four|five|\d+) (?:convenient )?locations",
    r"multiple locations",
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


def _negated(text: str, start: int) -> bool:
    """'No answering service', 'you will never talk to an answering service', 'rather than ...'."""
    return bool(NEGATION.search(text[max(0, start - 80) : start]))


def _first(
    rx: re.Pattern[str],
    pages: list[Page],
    *,
    skip_negated: bool = False,
    not_before: re.Pattern[str] | None = None,
    not_near: re.Pattern[str] | None = None,
) -> Hit | None:
    """First match that passes the guards (negation, excluded left context, excluded
    words within ~120 characters either side)."""
    for p in pages:
        for m in rx.finditer(p.text):
            if skip_negated and _negated(p.text, m.start()):
                continue
            if not_before and not_before.search(p.text[max(0, m.start() - 40) : m.start()]):
                continue
            if not_near and not_near.search(p.text[max(0, m.start() - 120) : m.end() + 120]):
                continue
            return Hit(True, snippet(p.text, m.start(), m.end()), p.url)
    return None


def _any_negated(rx: re.Pattern[str], pages: list[Page]) -> Hit | None:
    for p in pages:
        for m in rx.finditer(p.text):
            if _negated(p.text, m.start()):
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
        _first(
            re.compile("|".join(re.escape(k) for k in cat.negative_keywords), re.I),
            pages,
            skip_negated=True,
        )
        if cat
        else None
    )
    answering = _first(ANSWERING, pages, skip_negated=True, not_near=COMPETITOR)
    # "No answering service: the owner answers every call" is the opposite of a disqualifier,
    # but it is a real caution: they sell on live answering.
    if _any_negated(ANSWERING, pages) or _first(COVERAGE, pages):
        ex.flags.append("promises_live_answer")
    if vendor:
        sig["uses_answering_service"] = Hit(True, f"Site references {vendor[0]}", vendor[1])
    elif hit := (negative or answering):
        sig["uses_answering_service"] = hit
    if franchise := _first(FRANCHISE, pages):
        sig["is_chain"] = franchise

    # --- pain --------------------------------------------------------------
    if h := _first(LEAVE_MESSAGE, pages, skip_negated=True, not_near=LEAVE_MESSAGE_GUARD):
        sig["leave_message_text"] = h
    coverage = _first(COVERAGE, pages)
    emergency = _first(EMERGENCY, pages, not_before=EMERGENCY_NOT)
    if emergency and not coverage and "uses_answering_service" not in sig:
        sig["emergency_247_claim"] = emergency
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
    if not booking and (h := _first(PHONE_ONLY, pages, not_near=ONLINE_FORM)):
        sig["phone_only_booking"] = h

    # The role must sit close to the hiring wording, and the evidence must show both.
    for p in pages:
        for m in HIRING_CONTEXT.finditer(p.text):
            lo = max(0, m.start() - 150)
            if role := FRONT_DESK_ROLES.search(p.text[lo : m.end() + 150]):
                a, b = lo + role.start(), lo + role.end()
                start, end = min(a, m.start()), max(b, m.end())
                width = max(SNIPPET, end - start + 20)
                sig["hiring_receptionist"] = Hit(
                    True, snippet(p.text, start, end, width=width), p.url
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
