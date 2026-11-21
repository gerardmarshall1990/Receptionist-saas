"""Pure statistics for the readout: no I/O. Checked against hand-computed values in tests."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import NormalDist

# Dispositions (migration 009). Lead-level outcomes are "any attempt reached X".
REACHED = frozenset({"gatekeeper", "decision_maker", "callback", "not_interested",
                     "has_answering_service", "do_not_call"})  # fmt: skip
CONVERSATION = frozenset({"decision_maker", "callback", "not_interested", "has_answering_service"})
BAD_DATA = frozenset({"wrong_number", "closed_or_not_business"})
DISPOSITIONS = frozenset({"no_answer", "voicemail"} | REACHED | CONVERSATION | BAD_DATA)

METRICS = (
    "reached",
    "conversation",
    "demo",
    "trial",
    "paid",
    "bad_data",
    "has_service",
    "do_not_call",
)


@dataclass(frozen=True)
class Attempt:
    disposition: str
    demo_booked: bool = False
    trial_started: bool = False
    paid: bool = False


def lead_outcome(attempts: Iterable[Attempt]) -> dict[str, bool]:
    """Collapse one lead's call attempts into lead-level yes/no outcomes."""
    att = list(attempts)
    d = {a.disposition for a in att}
    return {
        "dialed": bool(att),
        "reached": bool(d & REACHED) or any(a.demo_booked for a in att),
        "conversation": bool(d & CONVERSATION) or any(a.demo_booked for a in att),
        "demo": any(a.demo_booked for a in att),
        "trial": any(a.trial_started for a in att),
        "paid": any(a.paid for a in att),
        "bad_data": bool(d & BAD_DATA),
        "has_service": "has_answering_service" in d,
        "do_not_call": "do_not_call" in d,
    }


def wilson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a proportion (good at small k)."""
    if n == 0:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(1 - alpha / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def two_proportion_p(k1: int, n1: int, k0: int, n0: int) -> float:
    """Two-sided p-value, pooled two-proportion z-test. 1.0 when undefined."""
    if n1 == 0 or n0 == 0:
        return 1.0
    pooled = (k1 + k0) / (n1 + n0)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n0))
    if se == 0:
        return 1.0
    z = (k1 / n1 - k0 / n0) / se
    return 2 * (1 - NormalDist().cdf(abs(z)))


def required_n(p0: float, p1: float, alpha: float = 0.05, power: float = 0.80) -> int | None:
    """Leads per arm to detect p0 -> p1 (two-sided). None if the rates are equal or degenerate."""
    if not (0 < p0 < 1 and 0 < p1 < 1) or p0 == p1:
        return None
    za = NormalDist().inv_cdf(1 - alpha / 2)
    zb = NormalDist().inv_cdf(power)
    pbar = (p0 + p1) / 2
    num = za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1))
    return math.ceil(num * num / (p1 - p0) ** 2)
