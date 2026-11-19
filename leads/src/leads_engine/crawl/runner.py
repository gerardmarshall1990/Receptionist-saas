"""Crawl queue (Postgres) + runner.

Durability rule: a site's row only becomes terminal (done/blocked/...) in the same
step that its pages are written to Parquet and Postgres. If the process dies, the
site stays 'running' and is re-claimed once its lock is older than LOCK_STALE.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from leads_engine.config_loader import CrawlConfig
from leads_engine.crawl.fetcher import Fetched, SiteResult, crawl_site, make_client
from leads_engine.log import get_logger

LOCK_STALE = "15 minutes"
FLUSH_SITES = 50
PAGE_SCHEMA = pa.schema(
    [
        ("domain", pa.string()),
        ("url", pa.string()),
        ("final_url", pa.string()),
        ("kind", pa.string()),
        ("http_status", pa.int32()),
        ("content_type", pa.string()),
        ("bytes", pa.int32()),
        ("truncated", pa.bool_()),
        ("content_hash", pa.string()),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
        ("html_gz", pa.binary()),
    ]
)

log = get_logger("crawl")


def enqueue(url: str, scope: Literal["pilot", "all"] = "pilot") -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(
            "insert into crawl_sites (domain, start_url)"
            " select domain, min(website) from businesses"
            " where domain is not null and website is not null and (in_pilot_sample or %s)"
            " group by domain on conflict (domain) do nothing",
            (scope == "all",),
        )
        return cur.rowcount


def claim(
    conn: psycopg.Connection[Any], worker: str, n: int, max_attempts: int
) -> list[tuple[str, str]]:
    rows = conn.execute(
        "update crawl_sites set status = 'running', locked_by = %s, locked_at = now(),"
        " attempts = attempts + 1, updated_at = now()"
        " where domain in (select domain from crawl_sites"
        "   where (status = 'pending')"
        "      or (status = 'error' and attempts < %s and next_attempt_at <= now())"
        "      or (status = 'running' and locked_at < now() - %s::interval)"
        "   order by domain for update skip locked limit %s)"
        " returning domain, start_url",
        (worker, max_attempts, LOCK_STALE, n),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _page_row(r: SiteResult, p: Fetched) -> dict[str, Any]:
    return {
        "domain": r.domain,
        "url": p.url,
        "final_url": p.final_url,
        "kind": p.kind,
        "http_status": p.status,
        "content_type": p.content_type,
        "bytes": len(p.body),
        "truncated": p.truncated,
        "content_hash": p.content_hash,
        "fetched_at": p.fetched_at,
        "html_gz": gzip.compress(p.body),
    }


_INSERT_PAGE = (
    "insert into pages (domain, url, final_url, kind, http_status, content_type,"
    " bytes, content_hash, fetched_at, parquet_file)"
    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


@dataclass
class Buffer:
    out_dir: Path
    run_id: str
    results: list[SiteResult] = field(default_factory=list)
    seq: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, r: SiteResult) -> None:
        self.results.append(r)
        self.counts[r.status] = self.counts.get(r.status, 0) + 1
        self.counts["pages"] = self.counts.get("pages", 0) + len(r.pages)

    def flush(self, conn: psycopg.Connection[Any], max_attempts: int) -> None:
        if not self.results:
            return
        rows = [_page_row(r, p) for r in self.results for p in r.pages]
        path: Path | None = None
        if rows:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{self.run_id}-{self.seq:05d}.parquet"
            tmp = path.with_suffix(".tmp")
            pq.write_table(pa.Table.from_pylist(rows, schema=PAGE_SCHEMA), tmp, compression="zstd")
            os.replace(tmp, path)
            self.seq += 1
        with conn.transaction():
            for r in self.results:
                conn.execute("delete from pages where domain = %s", (r.domain,))
                for p in r.pages:
                    conn.execute(
                        _INSERT_PAGE,
                        (r.domain, p.url, p.final_url, p.kind, p.status, p.content_type,
                         len(p.body), p.content_hash, p.fetched_at, str(path) if path else None),
                    )  # fmt: skip
                status = r.status
                if status == "error" and r.retryable:
                    conn.execute(
                        "update crawl_sites set status = 'error', detail = %s, locked_by = null,"
                        " next_attempt_at = now() + interval '10 minutes' * attempts,"
                        " updated_at = now(), run_id = %s where domain = %s",
                        (r.detail, self.run_id, r.domain),
                    )
                    continue
                if status == "error":
                    # Not retryable: burn the remaining attempts so it is never re-claimed.
                    conn.execute(
                        "update crawl_sites set attempts = %s where domain = %s",
                        (max_attempts, r.domain),
                    )
                conn.execute(
                    "update crawl_sites set status = %s, detail = %s, final_url = %s,"
                    " pages_fetched = %s, locked_by = null, updated_at = now(), run_id = %s"
                    " where domain = %s",
                    (status, r.detail, r.final_url, len(r.pages), self.run_id, r.domain),
                )
        self.results.clear()


async def run(
    url: str,
    cfg: CrawlConfig,
    data_dir: Path,
    *,
    max_sites: int,
    time_limit_s: float,
    run_id: str | None = None,
) -> dict[str, int]:
    run_id = run_id or str(uuid.uuid4())
    worker = f"{socket.gethostname()}:{os.getpid()}"
    buf = Buffer(data_dir / "crawl" / "pages", run_id)
    deadline = time.monotonic() + time_limit_s
    claimed = 0
    sem = asyncio.Semaphore(cfg.concurrency_sites)
    with psycopg.connect(url, autocommit=True) as conn:
        async with make_client(cfg) as client:

            async def one(domain: str, start: str) -> None:
                async with sem:
                    try:
                        res = await crawl_site(client, cfg, domain, start)
                    except Exception as exc:  # a bug in one site must not stop the run
                        res = SiteResult(
                            domain,
                            "error",
                            detail=f"crawler error: {type(exc).__name__}: {exc}"[:500],
                        )
                    buf.add(res)

            tasks: set[asyncio.Task[None]] = set()
            while True:
                room = cfg.concurrency_sites * 2 - len(tasks)
                if room > 0 and claimed < max_sites and time.monotonic() < deadline:
                    batch = claim(conn, worker, min(room, max_sites - claimed), cfg.max_attempts)
                    claimed += len(batch)
                    for domain, start in batch:
                        tasks.add(asyncio.create_task(one(domain, start)))
                    if not batch and not tasks:
                        break
                elif not tasks:
                    break
                done, tasks = await asyncio.wait(
                    tasks, timeout=5, return_when=asyncio.FIRST_COMPLETED
                )
                if len(buf.results) >= FLUSH_SITES:
                    buf.flush(conn, cfg.max_attempts)
                    log.info("crawl_progress", claimed=claimed, **buf.counts)
                _ = done
            buf.flush(conn, cfg.max_attempts)
    log.info("crawl_finished", claimed=claimed, **buf.counts)
    return {"claimed": claimed, **buf.counts}
