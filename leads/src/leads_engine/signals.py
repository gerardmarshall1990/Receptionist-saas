"""Catalog of signals (spec section 4).

This is the single list of signal names the system knows. Category configs,
opener templates, scoring and the "why this lead" reasons may only reference
names from here, so a reason or opener can never point at a signal that the
crawler does not actually extract and store.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Dimension(StrEnum):
    PAIN = "pain"
    VALUE = "value"
    REACHABILITY = "reachability"
    FIT = "fit"


@dataclass(frozen=True)
class Signal:
    name: str
    dimension: Dimension
    description: str
    # A disqualifier removes the lead regardless of score (e.g. already has an
    # answering service, is a chain).
    disqualifier: bool = False
    # True if this can be the basis of an opener sentence. Disqualifiers and
    # plain contact facts cannot.
    opener_eligible: bool = True
    # Which funnel tier first produces it (spec section 2).
    tier: str = "T1"


_SIGNALS: tuple[Signal, ...] = (
    # Pain / need
    Signal(
        "phone_only_booking", Dimension.PAIN, "Site asks visitors to call to book; no other way."
    ),
    Signal("no_online_booking", Dimension.PAIN, "No online booking or scheduling widget found."),
    Signal("no_chat_widget", Dimension.PAIN, "No live chat or chatbot on the site."),
    Signal("single_phone_number", Dimension.PAIN, "Only one phone number published."),
    Signal(
        "leave_message_text", Dimension.PAIN, "Site says 'leave a message' / 'we'll call you back'."
    ),
    Signal(
        "limited_hours_text",
        Dimension.PAIN,
        "Published hours are limited (e.g. closed evenings/weekends).",
    ),
    Signal(
        "emergency_247_claim",
        Dimension.PAIN,
        "Advertises 24/7 or emergency service with no evidence of after-hours answering.",
    ),
    Signal(
        "hiring_receptionist",
        Dimension.PAIN,
        "Job post for receptionist / front desk / dispatcher.",
    ),
    Signal(
        "review_hard_to_reach",
        Dimension.PAIN,
        "Reviews mention not answering, voicemail, or not getting through.",
        tier="T3",
    ),
    # Value
    Signal("pricing_hints", Dimension.VALUE, "Published prices or ticket sizes for services."),
    Signal(
        "multiple_locations",
        Dimension.VALUE,
        "More than one location (more value; check decision-maker).",
    ),
    # Fit
    Signal(
        "website_works", Dimension.FIT, "Website returns a working page.", opener_eligible=False
    ),
    Signal(
        "owner_operated",
        Dimension.FIT,
        "Evidence the owner runs the business day to day.",
        opener_eligible=False,
    ),
    Signal(
        "staff_in_range",
        Dimension.FIT,
        "Staff estimate inside the category's target range.",
        opener_eligible=False,
    ),
    # Reachability
    Signal(
        "phone_verified",
        Dimension.REACHABILITY,
        "Phone status is verified.",
        opener_eligible=False,
        tier="T2",
    ),
    Signal(
        "email_valid",
        Dimension.REACHABILITY,
        "Public business email that verified valid.",
        opener_eligible=False,
        tier="T2",
    ),
    Signal(
        "owner_name_public",
        Dimension.REACHABILITY,
        "Owner/decision-maker name published in a business role.",
        opener_eligible=False,
    ),
    # Disqualifiers
    Signal(
        "uses_answering_service",
        Dimension.PAIN,
        "Already uses an answering service or AI receptionist.",
        disqualifier=True,
        opener_eligible=False,
    ),
    Signal(
        "is_chain",
        Dimension.FIT,
        "Chain or franchise location.",
        disqualifier=True,
        opener_eligible=False,
    ),
    Signal(
        "staff_too_large",
        Dimension.FIT,
        "Staff estimate well above the category's target range.",
        disqualifier=True,
        opener_eligible=False,
    ),
)

SIGNALS: dict[str, Signal] = {s.name: s for s in _SIGNALS}


def get_signal(name: str) -> Signal:
    try:
        return SIGNALS[name]
    except KeyError as exc:
        raise KeyError(f"unknown signal {name!r}; known: {sorted(SIGNALS)}") from exc
