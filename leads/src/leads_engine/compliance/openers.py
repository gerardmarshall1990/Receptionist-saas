"""Rule-based openers ($0): one sentence from a category template, tied to one stored reason.

A template is used only if the lead has a stored reason for its signal. Templates that
need `{evidence}` are skipped: a raw snippet doesn't read as a sentence fragment, and
we never paraphrase evidence. No genuine reason -> None (spec section 5).
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable, Mapping

from leads_engine.config_loader import CategoryConfig

_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|co\.|ltd\.?|pllc|p\.c\.|pc)$",
    re.IGNORECASE,
)


def display_name(name: str) -> str:
    """'ACME PLUMBING, LLC' -> 'Acme Plumbing'. Only trims; never invents words."""
    out = name.strip()
    for _ in range(2):
        out = _LEGAL_SUFFIX.sub("", out).strip(" ,")
    if out.isupper() and len(out) > 4:
        out = string.capwords(out.lower())
    return out or name.strip()


def _fields(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


def pick_opener(
    cat: CategoryConfig | None,
    reason_signals: Iterable[str],
    weights: Mapping[str, float],
    name: str,
    city: str | None,
) -> tuple[str, str] | None:
    """Return (opener, template_id) for the strongest stored signal with a usable template."""
    if cat is None:
        return None
    have = set(reason_signals)
    usable = [
        t
        for t in cat.opener_templates
        if t.requires_signal in have
        and "evidence" not in _fields(t.template)
        and ("city" not in _fields(t.template) or city)
    ]
    if not usable:
        return None
    best = max(usable, key=lambda t: weights.get(t.requires_signal, 0.0))
    text = best.template.format(name=display_name(name), city=city or "")
    return text, best.id
