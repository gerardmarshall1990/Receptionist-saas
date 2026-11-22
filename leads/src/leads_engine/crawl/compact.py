"""Compact saved pages so the crawl fits on a small disk without changing what extraction sees.

A saved page keeps everything `html.parse` and `extract` read: tags and attributes (links, tel:,
mailto:, classes and ids where widget signatures can sit), the title and the visible text. It drops what
extraction never reads: <style> bodies, and inline <script> bodies, which are replaced by a marker
listing any configured signature they contained (booking, chat and answering-vendor signatures are
matched against the raw HTML). Comments and whitespace are kept: removing them changed results (a
signature inside a comment; digits joined into a phone number).

Trade-off, stated plainly: a signature added to config later can't be found inside inline scripts of
pages compacted before; such a change needs those sites re-crawled. Script `src` URLs are attributes and
are kept, so most widget signatures (domains) still work after any config change.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leads_engine.config_loader import AppConfig

_STYLE = re.compile(r"(<style\b[^>]*>).*?(</style\s*>)", re.I | re.S)
_SCRIPT = re.compile(r"(<script\b[^>]*>)(.*?)(</script\s*>)", re.I | re.S)


@functools.cache
def _sig_re(sig: str) -> re.Pattern[str] | None:
    if "." in sig or "/" in sig:
        return None
    return re.compile(rf"(?<![a-z0-9]){re.escape(sig.lower())}(?![a-z0-9])")


def signature_in(sig: str, text_lower: str) -> bool:
    """The one signature rule (extraction and compaction): word signatures ("text us") match whole
    words, URL-like ones ("calendly.com") match as substrings."""
    rx = _sig_re(sig)
    return bool(rx.search(text_lower)) if rx else sig.lower() in text_lower


def compact_html(html: str, signatures: Iterable[str]) -> str:
    sigs = [s.lower() for s in signatures if s]

    def script(m: re.Match[str]) -> str:
        body = m.group(2).lower()
        hits = [s for s in sigs if signature_in(s, body)]
        marker = f"/*sig: {' '.join(hits)}*/" if hits else ""
        return f"{m.group(1)}{marker}{m.group(3)}"

    # Scripts first: a "<style>" string inside a script would otherwise start a style match that runs
    # on to a later </style> and swallows real page content in between.
    out = _SCRIPT.sub(script, html)
    return _STYLE.sub(r"\1\2", out)


def all_signatures(cfg: AppConfig) -> tuple[str, ...]:
    """Every signature extraction matches against raw HTML."""
    return tuple(
        dict.fromkeys(
            [
                *cfg.crawl.booking_signatures,
                *cfg.crawl.chat_signatures,
                *cfg.global_.answering_vendor_patterns,
            ]
        )
    )


def compact_file(path: Path, signatures: tuple[str, ...]) -> tuple[int, int]:
    """Rewrite one saved-pages Parquet file with compacted HTML (atomic replace; idempotent).
    Returns (bytes before, bytes after)."""
    import gzip

    import pyarrow as pa
    import pyarrow.parquet as pq

    from leads_engine.crawl.fetcher import decode

    before = path.stat().st_size
    table = pq.read_table(path)
    if "compacted" in table.column_names and all(table.column("compacted").to_pylist()):
        return before, before
    rows = table.to_pylist()
    for r in rows:
        if r.get("compacted"):
            continue
        html = decode(gzip.decompress(r["html_gz"]), r["content_type"])
        r["html_gz"] = gzip.compress(compact_html(html, signatures).encode("utf-8"))
        r["content_type"] = "text/html; charset=utf-8"
        r["compacted"] = True
    from leads_engine.crawl.runner import PAGE_SCHEMA

    tmp = path.with_suffix(".tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=PAGE_SCHEMA), tmp, compression="zstd")
    os.replace(tmp, path)
    return before, path.stat().st_size
