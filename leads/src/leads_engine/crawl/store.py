"""Extraction pass: saved pages (Parquet) -> signals, reasons, emails, phones, flags.

Idempotent: every write replaces the previous Stage 2 output for that business,
so re-running with a better extractor converges (EXTRACTOR_VERSION is stored).
"""

from __future__ import annotations

import gzip
import multiprocessing
import os
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
import psycopg
from psycopg.rows import dict_row

from leads_engine.config_loader import AppConfig
from leads_engine.crawl.extract import EXTRACTOR_VERSION, Extraction, extract
from leads_engine.crawl.fetcher import decode
from leads_engine.crawl.html import parse
from leads_engine.log import get_logger
from leads_engine.signals import SIGNALS
from leads_engine.universe import normalize as nz

# Flags this stage owns (replaced on every run).
STAGE2_FLAGS = {
    "website_mismatch", "phone_not_on_site", "uses_answering_service", "franchise_disclaimer",
    "crawl_blocked", "robots_disallowed", "website_parked", "website_offsite", "crawl_failed",
    "thin_homepage", "promises_live_answer", "name_not_on_site",
}  # fmt: skip
STATUS_FLAG = {
    "blocked": "crawl_blocked", "robots_disallowed": "robots_disallowed", "parked": "website_parked",
    "offsite": "website_offsite", "error": "crawl_failed",
}  # fmt: skip
log = get_logger("extract")
PARALLEL_MIN = 50  # below this many sites a chunk is extracted in-process
TERMINAL = ("done", "blocked", "robots_disallowed", "parked", "offsite", "error")


def _pages_by_domain(
    pages_dir: Path, domains: list[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Latest copy of each page, for the given domains only (constant memory per batch)."""
    files = sorted(pages_dir.glob("*.parquet"))
    if not files:
        return {}
    where = "where domain in (select unnest($domains))" if domains is not None else ""
    # `where` is one of two constants; domains and files are bound parameters.
    q = f"""
    select domain, url, final_url, kind, content_type, html_gz from (
      select *, row_number() over (partition by domain, url order by fetched_at desc) as rn
      from read_parquet($files, union_by_name = true) {where}
    ) where rn = 1
    """
    params: dict[str, Any] = {"files": [str(f) for f in files]}
    if domains is not None:
        params["domains"] = domains
    rows = duckdb.connect().sql(q, params=params).to_arrow_table().to_pylist()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order = {"home": 0, "contact": 1, "booking": 2, "careers": 3, "about": 4, "services": 5}
    for r in rows:
        out[r["domain"]].append(r)
    for v in out.values():
        v.sort(key=lambda r: order.get(r["kind"], 9))
    return out


def _write(
    cur: psycopg.Cursor[Any], b: dict[str, Any], ex: Extraction, cfg: AppConfig, status: str
) -> None:
    bid = b["id"]
    cur.execute("delete from signals where business_id = %s", (bid,))
    cur.execute("delete from reasons where business_id = %s and stage = 'stage2'", (bid,))
    cur.execute("delete from emails where business_id = %s and origin = 'business_site'", (bid,))
    weights = cfg.scoring.signal_weights
    for name, hit in ex.signals.items():
        cur.execute(
            "insert into signals (business_id, signal, value, count, evidence, source_url, extractor_version)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            (bid, name, hit.value, hit.count, hit.evidence, hit.url, EXTRACTOR_VERSION),
        )
        meta = SIGNALS[name]
        # Reasons = evidence-backed, positive, non-disqualifier signals that say something
        # about need or value. Plain contact facts are not reasons.
        if hit.value and not meta.disqualifier and meta.dimension.value in {"pain", "value"}:
            cur.execute(
                "insert into reasons (business_id, signal, dimension, evidence, source_url, weight, stage)"
                " values (%s, %s, %s, %s, %s, %s, 'stage2') on conflict do nothing",
                (bid, name, meta.dimension.value, hit.evidence, hit.url, weights.get(name, 0)),
            )
    for email, url in ex.emails:
        e = nz.classify_email(email, b["domain"], cfg.global_.normalization)
        if not e.syntax_ok:
            continue
        cur.execute(
            "insert into emails (business_id, email, origin, source_url, syntax_ok, is_role_based,"
            " is_freemail, domain_matches_site, flags) values (%s, %s, 'business_site', %s, %s, %s, %s, %s, %s)"
            " on conflict do nothing",
            (
                bid,
                e.email,
                url,
                e.syntax_ok,
                e.is_role_based,
                e.is_freemail,
                e.domain_matches_site,
                list(e.flags),
            ),
        )
    # Phone corroboration: the website is an independent source (spec section 6).
    for e164 in ex.phones:
        ph = nz.normalize_phone(e164)
        if ph is None or not ph.valid:
            continue
        cur.execute(
            "insert into phones (business_id, e164, sources, agree_count, valid_format, number_type_guess,"
            " flags, status) values (%s, %s, array['website'], 1, true, %s, %s, 'unverified')"
            " on conflict (business_id, e164) do update set"
            "   sources = (select array_agg(distinct s) from unnest(phones.sources || array['website']) s),"
            "   agree_count = (select count(distinct s) from unnest(phones.sources || array['website']) s)",
            (bid, ph.e164, ph.number_type, list(ph.flags)),
        )
    cur.execute(
        "update phones set status = 'likely' where business_id = %s and agree_count >= 2 and status = 'unverified'",
        (bid,),
    )
    flags = [f for f in (b["flags"] or []) if f not in STAGE2_FLAGS] + ex.flags
    if status in STATUS_FLAG:
        flags.append(STATUS_FLAG[status])
    if (
        ex.signals.get("uses_answering_service", None)
        and ex.signals["uses_answering_service"].value
    ):
        flags.append("uses_answering_service")
    # Recomputed from this run's evidence (Stage 1 never stores chains: it excludes them).
    is_chain = bool(ex.signals.get("is_chain"))
    if ex.signals.get("is_chain"):
        flags.append("franchise_disclaimer")
    first_usable = next((p for p in ex.phones if (n := nz.normalize_phone(p)) and n.usable), None)
    cur.execute(
        "update businesses set flags = %s, is_chain = %s, crawled_at = now(), updated_at = now(),"
        " phone = coalesce(phone, %s) where id = %s",
        (sorted(set(flags)), is_chain, first_usable, bid),
    )
    cur.execute(
        "update businesses b set"
        " phone_status = case when b.phone is null then 'none' else coalesce("
        "   (select p.status from phones p where p.business_id = b.id and p.e164 = b.phone), 'none') end,"
        " email_status = case"
        "   when exists (select 1 from emails e where e.business_id = b.id and e.origin = 'business_site')"
        "     then 'site_found'"
        "   when exists (select 1 from emails e where e.business_id = b.id) then 'source_listed'"
        "   else 'none' end"
        " where b.id = %s",
        (bid,),
    )


def run_extract(
    url: str,
    cfg: AppConfig,
    data_dir: Path,
    full: bool = False,
    batch: int = 8_000,
    workers: int | None = None,
) -> dict[str, int]:
    """Extract signals for finished sites. Incremental by default: only sites crawled since
    their last extraction, or extracted by an older EXTRACTOR_VERSION. Batches of `batch`
    domains keep memory flat however many sites have been crawled."""
    counts: dict[str, int] = defaultdict(int)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        sites = conn.execute(
            "select domain, status from crawl_sites where status = any(%s)"
            " and not (status = 'error' and attempts < %s)"
            " and (%s or extracted_version is distinct from %s or extracted_at is null"
            "      or extracted_at < updated_at)"
            " order by domain",
            (list(TERMINAL), cfg.crawl.max_attempts, full, EXTRACTOR_VERSION),
        ).fetchall()
        counts["sites_to_extract"] = len(sites)
        for i in range(0, len(sites), batch):
            chunk = sites[i : i + batch]
            pages = _pages_by_domain(data_dir / "crawl" / "pages", [s["domain"] for s in chunk])
            failed = _extract_chunk(conn, chunk, pages, cfg, counts, workers)
            # A site that failed stays unmarked, so the next incremental pass retries it.
            conn.execute(
                "update crawl_sites set extracted_version = %s, extracted_at = now()"
                " where domain = any(%s)",
                (EXTRACTOR_VERSION, [s["domain"] for s in chunk if s["domain"] not in failed]),
            )
            conn.commit()
            log.info("extract_progress", done=min(i + batch, len(sites)), total=len(sites))
        conn.execute("analyze signals; analyze reasons")
    return dict(counts)


def _work(
    task: tuple[str, str, list[dict[str, Any]], list[dict[str, Any]], AppConfig],
) -> tuple[str, Any]:
    """Parse + extract one site (runs in a worker process). Returns extractions or an error."""
    domain, status, pages, businesses, cfg = task
    try:
        parsed = [
            parse(decode(gzip.decompress(p["html_gz"]), p["content_type"]), p["final_url"])
            for p in pages
        ]
        return domain, [
            extract(parsed, b, cfg) if status in {"done", "offsite"} else Extraction()
            for b in businesses
        ]
    except Exception as exc:  # one odd page must not stop the pass; it is counted
        return domain, f"{type(exc).__name__}: {exc}"


def _extract_chunk(
    conn: psycopg.Connection[dict[str, Any]],
    sites: list[dict[str, Any]],
    pages: dict[str, list[dict[str, Any]]],
    cfg: AppConfig,
    counts: dict[str, int],
    workers: int | None = None,
) -> set[str]:
    """Parsing is CPU-bound: worker processes parse and extract, this process writes to Postgres."""
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in conn.execute(
        "select id, name, name_key, category, phone, domain, flags, is_chain from businesses"
        " where domain = any(%s)",
        ([s["domain"] for s in sites],),
    ):
        by_domain[b["domain"]].append(b)
    status_of = {s["domain"]: s["status"] for s in sites}
    tasks = [(d, status_of[d], pages.get(d, []), by_domain.get(d, []), cfg) for d in status_of]
    n = workers if workers is not None else max(1, (os.cpu_count() or 2))
    failed: set[str] = set()

    def results() -> Iterator[tuple[str, Any]]:
        if n <= 1 or len(tasks) < PARALLEL_MIN:
            yield from map(_work, tasks)
            return
        with ProcessPoolExecutor(
            max_workers=n, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            yield from pool.map(_work, tasks, chunksize=25)

    for domain, out in results():
        if isinstance(out, str):
            log.error("extract_failed", domain=domain, error=out)
            counts["extract_errors"] += 1
            failed.add(domain)
            continue
        status = status_of[domain]
        with conn.transaction(), conn.cursor() as cur:
            for b, ex in zip(by_domain.get(domain, []), out, strict=True):
                _write(cur, b, ex, cfg, status)
                counts["businesses"] += 1
                counts[f"site_{status}"] += 1
                for name, hit in ex.signals.items():
                    if hit.value:
                        counts[f"signal:{name}"] += 1
                counts["with_site_email"] += bool(ex.emails)
    return failed
