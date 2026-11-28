"""Parsing helpers for listing text. Pure functions."""
from __future__ import annotations

import re

_NUM = re.compile(r"\$?\s*(\d+(?:[.,]\d+)*)\s*(k|m|mil|million)?\b", re.I)
_INDICATIVE = re.compile(r"enquiries over|offers over|from\b|by negotiation|asking|price guide|\b(rv|cv|gv)\b|\bover\b", re.I)
_NO_PRICE = re.compile(r"auction|tender|deadline|negotiation|poa|price on application|contact agent", re.I)


def parse_price(text: str | float | int | None) -> tuple[float | None, bool, str | None]:
    """Return (price, is_indicative, original_text). Never raises.

    "$950,000" -> (950000, False); "Enquiries over $900k" -> (900000, True);
    "Auction" -> (None, False); "" -> (None, False).
    """
    if text is None:
        return None, False, None
    if isinstance(text, (int, float)):
        return (float(text), False, str(text)) if text > 0 else (None, False, str(text))
    raw = str(text).strip()
    if not raw:
        return None, False, None
    indicative = bool(_INDICATIVE.search(raw))
    best = None
    for m in _NUM.finditer(raw):
        n = float(m.group(1).replace(",", ""))
        mult = (m.group(2) or "").lower()
        if mult == "k":
            n *= 1_000
        elif mult in ("m", "mil", "million"):
            n *= 1_000_000
        if n >= 50_000:   # ignore bedroom counts, dates, etc.
            best = n if best is None else max(best, n)
    if best is None:
        return None, False, raw
    return best, indicative, raw


_MULTI = re.compile(
    r"home\s*(and|&|\+)\s*income|two\s+(houses|homes|dwellings)|2\s*(houses|homes|dwellings)|"
    r"granny\s*flat|minor\s+(dwelling|unit)|self[- ]contained\s+(unit|flat|studio)|sleep[- ]?out|"
    r"(separate|second|2nd)\s+(dwelling|unit|flat|house|cottage)|dual\s+(income|living|dwelling)|"
    r"\bstudio\b|\bcottage\b at the rear|income\s+(unit|flat)", re.I)


def dwelling_hint(description: str | None, explicit: int | None) -> tuple[int | None, str]:
    """Return (count or None if unclear, reason)."""
    if explicit is not None:
        return explicit, f"{explicit} dwelling(s) as entered"
    if not description or not description.strip():
        return None, "no listing text to confirm a single existing dwelling"
    m = _MULTI.search(description)
    if m:
        return None, f"listing text mentions '{m.group(0)}': possible second dwelling"
    return 1, "listing text gives no sign of a second dwelling"
