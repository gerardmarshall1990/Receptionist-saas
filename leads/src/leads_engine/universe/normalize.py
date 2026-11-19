"""Pure normalization functions. No network, no I/O (time zones use an offline map)."""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from functools import cache

import phonenumbers
import tldextract
from phonenumbers import PhoneNumberType
from timezonefinder import TimezoneFinder

from leads_engine.config_loader import Normalization

# ------------------------------------------------------------------ phones

_TYPE_NAMES = {
    PhoneNumberType.FIXED_LINE: "fixed_line",
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.PREMIUM_RATE: "premium_rate",
    PhoneNumberType.SHARED_COST: "shared_cost",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PERSONAL_NUMBER: "personal",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "uan",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}


@dataclass(frozen=True)
class Phone:
    raw: str
    e164: str | None
    valid: bool
    # libphonenumber's guess from the number range. NOT a carrier lookup: for
    # US numbers it is almost always "fixed_line_or_mobile". Real line type is T4.
    number_type: str
    flags: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Good enough to be the primary contact number (still unverified)."""
        return self.valid and not {"toll_free", "premium_rate", "suspicious"} & set(self.flags)


def _suspicious_us(national: str) -> bool:
    """Obvious fakes: 555-01XX fiction range, one repeated digit, straight runs."""
    if len(national) != 10:
        return False
    exchange, line = national[3:6], national[6:]
    if exchange == "555" and line.startswith("01"):
        return True
    if len(set(national[3:])) == 1:
        return True
    return national[3:] in {"1234567", "7654321", "0123456"}


def normalize_phone(raw: str | None, region: str = "US") -> Phone | None:
    if raw is None or not raw.strip():
        return None
    try:
        num = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return Phone(raw, None, False, "unknown", ("unparseable",))
    valid = phonenumbers.is_valid_number(num)
    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164) if valid else None
    ntype = _TYPE_NAMES.get(phonenumbers.number_type(num), "unknown")
    flags: list[str] = []
    if not valid:
        flags.append("invalid")
    if ntype in {"toll_free", "premium_rate", "shared_cost"}:
        flags.append(ntype)
    if num.country_code == 1 and _suspicious_us(str(num.national_number)):
        flags.append("suspicious")
    if valid and phonenumbers.region_code_for_number(num) != region:
        flags.append(f"foreign:{phonenumbers.region_code_for_number(num)}")
    return Phone(raw, e164, valid, ntype, tuple(flags))


# ---------------------------------------------------------------- websites

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # bundled list, offline


@dataclass(frozen=True)
class Website:
    raw: str
    url: str | None
    domain: str | None
    flag: str | None = None  # why it isn't usable as the business's own site
    host: str | None = None


def _registrable(host: str) -> str:
    ext = _EXTRACT(host)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def normalize_website(raw: str | None, cfg: Normalization) -> Website | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return Website(raw, None, None, "unparseable")
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return Website(raw, None, None, "unparseable")
    reg = _registrable(host)
    if reg in cfg.non_business_site_domains:
        return Website(raw, None, None, f"not_own_site:{reg}")
    if is_government_page(reg):
        return Website(raw, None, None, f"government_page:{reg}")
    domain = host if reg in cfg.hosted_site_domains else reg
    scheme = parts.scheme if parts.scheme in {"http", "https"} else "http"
    path = parts.path if parts.path not in {"", "/"} else ""
    return Website(raw, f"{scheme}://{parts.hostname}{path}", domain, None, host)


def first_business_site(
    raws: list[str] | None, cfg: Normalization
) -> tuple[Website | None, list[str]]:
    """First website that is the business's own site, plus flags for the rest."""
    flags: list[str] = []
    for raw in raws or []:
        site = normalize_website(raw, cfg)
        if site is None:
            continue
        if site.domain:
            return site, flags
        if site.flag:
            flags.append(site.flag)
    return None, flags


# .gov is handled separately (is_government_page): private businesses often list
# their state license page. Government bodies are caught by name keywords.
_INSTITUTION_SUFFIXES = ("mil", "edu")


def is_institution_domain(domain: str | None) -> bool:
    """Government, military and university sites: not small-business buyers."""
    if not domain:
        return False
    return _EXTRACT(domain).suffix.split(".")[-1] in _INSTITUTION_SUFFIXES


def is_government_page(domain: str | None) -> bool:
    """A .gov "website" on a private business is usually a license-lookup page."""
    if not domain:
        return False
    return _EXTRACT(domain).suffix.split(".")[-1] == "gov"


# ------------------------------------------------------------------- names

_LEGAL_SUFFIXES = {
    "llc", "inc", "incorporated", "co", "corp", "corporation", "company", "ltd", "llp",
    "pllc", "pc", "pa", "lp", "plc",
}  # fmt: skip


def name_key(name: str | None) -> str:
    """Comparison key: lowercase ASCII, '&'->'and', no punctuation or legal suffixes."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace("'", "")
    words = re.findall(r"[a-z0-9]+", text)
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


_GENERIC_WORDS = frozenset(
    [
        "dds",
        "dmd",
        "dentist",
        "dentists",
        "dental",
        "dentistry",
        "office",
        "clinic",
        "center",
        "plumber",
        "plumbers",
        "plumbing",
        "hvac",
        "heating",
        "cooling",
        "air",
        "conditioning",
        "and",
        "repair",
        "service",
        "services",
        "orthodontist",
        "orthodontics",
        "family",
        "general",
        "cosmetic",
        "pediatric",
        "my",
    ]
)


def is_generic_name(key: str) -> bool:
    """'Dentist', 'Dental Office', 'DDS': not a business name we can match or open with."""
    words = key.split()
    return bool(words) and all(w in _GENERIC_WORDS for w in words) and len(words) <= 3


_REFERRAL_MARKERS = ("24 7", "24 hour", "24 hours", "near me", "open now")
_REFERRAL_WORDS = frozenset(["24", "7", "hour", "hours", "near", "me", "open", "now", "emergency"])


def is_referral_listing(key: str) -> bool:
    """'Emergency Dentist 24/7', 'Plumber Near Me': lead-gen/referral listings.

    True when the name has a referral marker AND nothing but generic trade words
    besides it. '24 Hour Plumbing Pros' has a real name part ('pros'), so it stays."""
    padded = f" {key} "
    if not any(f" {m} " in padded for m in _REFERRAL_MARKERS):
        return False
    rest = [w for w in key.split() if w not in _REFERRAL_WORDS]
    return all(w in _GENERIC_WORDS for w in rest)


def norm_text(text: str) -> str:
    """Matching form for names and patterns alike: ASCII lowercase, '&'->'and',
    apostrophes dropped, everything else non-alphanumeric -> single space."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " and ").replace("'", "")
    return " ".join(re.findall(r"[a-z0-9]+", t))


@cache
def _pattern_re(patterns: tuple[str, ...], prefix: bool) -> re.Pattern[str] | None:
    normed = sorted({n for p in patterns if (n := norm_text(p))}, key=len, reverse=True)
    if not normed:
        return None
    alts = "|".join(re.escape(p) for p in normed)
    start = "^" if prefix else r"(?<![a-z0-9])"
    return re.compile(rf"{start}(?:{alts})(?![a-z0-9])")


def find_pattern(
    text: str | None, patterns: tuple[str, ...], *, prefix: bool = False
) -> str | None:
    """First whole-word pattern in text (or at its start, if prefix), else None.
    Both sides go through norm_text: "Mr. Rooter" ~ "mr rooter", "My Kid's" ~ "my kids"."""
    rx = _pattern_re(patterns, prefix)
    if not text or rx is None:
        return None
    m = rx.search(norm_text(text))
    return m.group(0) if m else None


# --------------------------------------------------------------- addresses

US_STATES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
    ]
)


def state_code(region: str | None, country: str = "US") -> str | None:
    """'US-FL' or 'FL' -> 'FL'. Anything that isn't a real state -> None."""
    if not region:
        return None
    code = region.strip().upper()
    if code.startswith(f"{country}-"):
        code = code[len(country) + 1 :]
    return code if country == "US" and code in US_STATES else None


def zip5(postcode: str | None) -> str | None:
    if not postcode:
        return None
    m = re.match(r"\s*(\d{5})(?:-?\d{4})?\s*$", postcode)
    return m.group(1) if m else None


def clean_city(city: str | None) -> str | None:
    if not city or not city.strip():
        return None
    return " ".join(city.split())


# ------------------------------------------------------------------ emails

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


@dataclass(frozen=True)
class Email:
    email: str
    syntax_ok: bool
    is_role_based: bool
    is_freemail: bool
    domain_matches_site: bool
    flags: tuple[str, ...] = field(default=())


def classify_email(raw: str, site_domain: str | None, cfg: Normalization) -> Email:
    email = raw.strip().lower()
    ok = bool(_EMAIL_RE.match(email)) and len(email) <= 254
    local, _, dom = email.partition("@")
    reg = _registrable(dom) if ok else ""
    freemail = reg in cfg.freemail_domains
    role = local.split("+")[0] in cfg.role_email_prefixes
    matches = bool(site_domain) and (dom == site_domain or reg == site_domain)
    flags = []
    if not ok:
        flags.append("bad_syntax")
    if freemail:
        flags.append("freemail")
    if ok and not freemail and site_domain and not matches:
        flags.append("domain_mismatch")
    return Email(email, ok, role, freemail, matches, tuple(flags))


# -------------------------------------------------------------- time zones


@cache
def _tf() -> TimezoneFinder:
    return TimezoneFinder(in_memory=True)


def timezone_for(lat: float | None, lng: float | None) -> str | None:
    if lat is None or lng is None:
        return None
    return _tf().timezone_at(lat=lat, lng=lng)
