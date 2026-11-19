"""SQL for the explorer, built only from whitelisted fragments plus bound parameters.

Paging is keyset (cursor = the last row's sort values), so page 1,000 costs the
same as page 1 at 3M rows. Counts are exact up to COUNT_CAP and an explicitly
labelled planner estimate beyond it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from psycopg import sql

from leads_engine.config_loader import AppConfig

COUNT_CAP = 50_000
FACET_LIMIT = 60

Direction = Literal["asc", "desc"]


@dataclass(frozen=True)
class Sort:
    label: str
    keys: tuple[str, ...]  # SQL expressions, whitelisted constants only
    direction: Direction


SORTS: dict[str, Sort] = {
    "score": Sort("Score (high first)", ("coalesce(b.score, -1)", "b.id"), "desc"),
    "name": Sort("Name (A-Z)", ("b.name_key", "b.id"), "asc"),
    # Unknown state sorts last ('ZZ' > every state code).
    "place": Sort(
        "State, city", ("coalesce(b.state, 'ZZ')", "coalesce(b.city, '')", "b.id"), "asc"
    ),
    "locations": Sort("Most locations", ("b.location_count", "b.id"), "desc"),
}
DEFAULT_SORT = "score"

# Flags the UI offers as filters, with plain-language labels.
FLAG_LABELS: dict[str, str] = {
    "multiple_locations": "Multiple locations (2-9)",
    "no_website": "No own website",
    "no_phone": "No phone listed",
    "no_usable_phone": "Phone not usable (toll-free/invalid)",
    "shared_phone": "Phone shared with other listings",
    "name_repeated_review": "Name repeated in many places (review)",
    "generic_name": "Generic listing name",
    "subdomain_platform": "Site on a website-builder subdomain",
    # Stage 2 (crawl)
    "uses_answering_service": "Already has answering service / AI (disqualified)",
    "franchise_disclaimer": "Franchise disclaimer on site (disqualified)",
    "website_mismatch": "Website doesn't match the business",
    "phone_not_on_site": "Listed phone not on their website",
    "crawl_blocked": "Website blocked our crawler",
    "robots_disallowed": "robots.txt disallows crawling",
    "website_parked": "Website parked / for sale",
    "website_offsite": "Website redirects elsewhere",
    "thin_homepage": "Homepage nearly empty (JS-only?)",
    "promises_live_answer": "Promises a live person answers (caution)",
}

# Signals the explorer can filter on ("has evidence for"), from the Stage 2 crawl.
SIGNAL_FILTERS: dict[str, str] = {
    "leave_message_text": "Says 'leave a message' / call-back",
    "emergency_247_claim": "Advertises 24/7 / emergency",
    "phone_only_booking": "Phone-only booking",
    "no_online_booking": "No online booking",
    "no_chat_widget": "No chat widget",
    "limited_hours_text": "Limited hours",
    "hiring_receptionist": "Hiring front desk / dispatcher",
    "single_phone_number": "Single phone number",
    "owner_operated": "Owner / family operated",
    "pricing_hints": "Publishes prices",
    "multiple_locations": "Mentions multiple locations",
}

Tri = bool | None


@dataclass(frozen=True)
class Suppressed:
    """The suppression list, read once per request. Small by nature (opt-outs)."""

    business_ids: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class Filters:
    category: tuple[str, ...] = ()
    subcategory: tuple[str, ...] = ()
    state: tuple[str, ...] = ()
    tier: tuple[str, ...] = ()
    q: str | None = None
    has_phone: Tri = None
    has_website: Tri = None
    has_email: Tri = None
    flags_any: tuple[str, ...] = ()
    flags_none: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    callable_now: bool = False
    include_suppressed: bool = False
    # Filled by the API, not by users:
    callable_pairs: tuple[tuple[str, str], ...] = ()  # (category, tz) inside a call window now
    suppressed: Suppressed = Suppressed()

    def without(self, name: str) -> Filters:
        """Same filters minus one facet (facet counts ignore their own selection)."""
        d = {**self.__dict__, name: ()}
        return Filters(**d)


@dataclass
class Where:
    parts: list[sql.Composable] = field(default_factory=list)
    params: list[Any] = field(default_factory=list)

    def add(self, fragment: str, *params: Any) -> None:
        # Fragments are string constants from this module; values only ever go in params.
        self.parts.append(sql.SQL(fragment))
        self.params.extend(params)

    def sql(self) -> sql.Composable:
        if not self.parts:
            return sql.SQL("true")
        return sql.SQL(" and ").join(sql.SQL("({})").format(p) for p in self.parts)


SUPPRESSED_SQL = " or ".join(
    f"exists (select 1 from suppression s where s.match_type = '{mt}' and s.match_value = {col})"  # noqa: S608 (constants)
    for mt, col in (
        ("business_id", "b.id::text"),
        ("phone_e164", "b.phone"),
        ("domain", "b.domain"),
    )
)  # per-row; used only for the handful of rows on a page, never as a filter


# Detail row + suppressed flag. Built from the constant above; the id is a parameter.
DETAIL_SQL = "select b.*, (" + SUPPRESSED_SQL + ") as suppressed from businesses b where b.id = %s"  # noqa: S608


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_where(f: Filters, cfg: AppConfig) -> Where:
    w = Where()
    # A single value is written as "=", not "= any(...)": only then can Postgres use the
    # (state|category, score) indexes to read rows already in sort order.
    for col, values in (
        ("b.category", f.category),
        ("b.subcategory", f.subcategory),
        ("b.state", f.state),
        ("b.tier", f.tier),
    ):
        if len(values) == 1:
            w.add(f"{col} = %s", values[0])
        elif values:
            w.add(f"{col} = any(%s)", list(values))
    if f.q and f.q.strip():
        w.add("b.search_text like %s", f"%{_like_escape(f.q.strip().lower())}%")
    for val, present in (
        (f.has_phone, "b.phone is not null"),
        (f.has_website, "b.domain is not null"),
        (f.has_email, "b.email_status <> 'none'"),
    ):
        if val is True:
            w.add(present)
        elif val is False:
            w.add(f"not ({present})")
    if f.flags_any:
        w.add("b.flags && %s::text[]", list(f.flags_any))
    if f.flags_none:
        w.add("not (b.flags && %s::text[])", list(f.flags_none))
    for name in f.signals:  # each selected signal is required (with evidence)
        w.add(
            "exists (select 1 from signals s"
            " where s.business_id = b.id and s.signal = %s and s.value)",
            name,
        )
    if not f.include_suppressed:
        sp = f.suppressed
        if sp.business_ids:
            w.add("not (b.id = any(%s::uuid[]))", list(sp.business_ids))
        if sp.phones:
            w.add("b.phone is null or not (b.phone = any(%s))", list(sp.phones))
        if sp.domains:
            w.add("b.domain is null or not (b.domain = any(%s))", list(sp.domains))
    if f.callable_now:
        # Pairs are computed in Python (few time zones x few categories), so this is an
        # indexed match instead of a per-row time-zone conversion.
        if not f.callable_pairs:
            w.add("false")
        else:
            w.add(
                "(b.category, b.tz) in (select * from unnest(%s::text[], %s::text[]))",
                [c for c, _ in f.callable_pairs],
                [t for _, t in f.callable_pairs],
            )
    return w


# ------------------------------------------------------------------ cursors


def encode_cursor(values: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, default=str).encode()).decode()


def decode_cursor(cursor: str, sort: Sort) -> list[Any]:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("bad cursor") from exc
    if not isinstance(values, list) or len(values) != len(sort.keys):
        raise ValueError("bad cursor")
    if not all(isinstance(v, str | int | float) for v in values):
        raise ValueError("bad cursor")
    return values


LIST_COLUMNS = (
    "b.id, b.name, b.category, b.subcategory, b.city, b.state, b.tz, b.phone, b.phone_status,"
    " b.website, b.domain, b.email_status, b.location_count, b.flags, b.score, b.tier,"
    " b.source_confidence"
)


def list_query(
    f: Filters, cfg: AppConfig, sort_key: str, cursor: str | None, limit: int
) -> tuple[sql.Composed, list[Any]]:
    sort = SORTS[sort_key]
    w = build_where(f, cfg)
    if cursor:
        values = decode_cursor(cursor, sort)
        op = ">" if sort.direction == "asc" else "<"
        keys = ", ".join(sort.keys)
        marks = ", ".join("%s::uuid" if k == "b.id" else "%s" for k in sort.keys)
        w.add(f"({keys}) {op} ({marks})", *values)
    order = ", ".join(f"{k} {sort.direction}" for k in sort.keys)
    sort_cols = ", ".join(f"{k} as _k{i}" for i, k in enumerate(sort.keys))
    q = sql.SQL(
        "select " + LIST_COLUMNS + ", (" + SUPPRESSED_SQL + ") as suppressed, " + sort_cols  # noqa: S608
        + " from businesses b where {where} order by " + order + " limit %s"
    ).format(where=w.sql())  # fmt: skip
    return q, [*w.params, limit + 1]


def count_query(f: Filters, cfg: AppConfig) -> tuple[sql.Composed, list[Any]]:
    w = build_where(f, cfg)
    q = sql.SQL(
        "select count(*) from (select 1 from businesses b where {where} limit %s) t"
    ).format(where=w.sql())
    return q, [*w.params, COUNT_CAP + 1]


def estimate_query(f: Filters, cfg: AppConfig) -> tuple[sql.Composed, list[Any]]:
    w = build_where(f, cfg)
    q = sql.SQL("explain (format json) select 1 from businesses b where {where}").format(
        where=w.sql()
    )
    return q, w.params


FACET_COLUMNS = {
    "category": "b.category",
    "subcategory": "b.subcategory",
    "state": "b.state",
    "tier": "b.tier",
}


def facet_query(f: Filters, cfg: AppConfig, facet: str) -> tuple[sql.Composed, list[Any]]:
    col = FACET_COLUMNS[facet]
    w = build_where(f.without(facet), cfg)
    q = sql.SQL(
        "select " + col + " as v, count(*) as n from businesses b where {where}"  # noqa: S608
        " group by 1 order by n desc limit %s"
    ).format(where=w.sql())
    return q, [*w.params, FACET_LIMIT]
