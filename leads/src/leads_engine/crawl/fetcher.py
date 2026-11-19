"""Crawl ONE website politely. No database here: the runner persists results.

Rules (spec section 7), enforced in this order:
- robots.txt first. 401/403 on robots.txt = disallow all. 5xx/429/network error on
  robots.txt = try later, never crawl blind. Crawl-delay is honored.
- At most one request in flight per site, at least per_site_delay_s apart.
- 403 / 429 / a challenge page = "blocked": stop now, never retry, never evade.
- Same site only, HTML only, size-capped, a handful of pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.parse
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from leads_engine.config_loader import CrawlConfig
from leads_engine.crawl.html import Page, candidate_links, parse, same_site

Sleep = Callable[[float], Awaitable[None]]
MAX_CRAWL_DELAY_S = 30.0  # a site asking for more gets its homepage only
# Our own client ran out of connections: not the site's fault, must not use up its attempts.
LOCAL_ERROR = "local: connection pool timeout"


@dataclass
class Fetched:
    url: str
    final_url: str
    kind: str
    status: int
    content_type: str
    body: bytes
    truncated: bool = False
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def text(self) -> str:
        return decode(self.body, self.content_type)


def decode(body: bytes, content_type: str | None) -> str:
    """Decode with the charset the server declared, falling back to UTF-8."""
    charset = "utf-8"
    for part in (content_type or "").split(";")[1:]:
        k, _, v = part.strip().partition("=")
        if k.lower() == "charset" and v:
            charset = v.strip("\"' ").lower()
    try:
        return body.decode(charset, errors="replace")
    except LookupError:  # unknown charset name
        return body.decode("utf-8", errors="replace")


@dataclass
class SiteResult:
    domain: str
    status: str = "pending"  # done | blocked | robots_disallowed | error | parked | offsite
    pages: list[Fetched] = field(default_factory=list)
    final_url: str | None = None
    detail: str | None = None
    retryable: bool = False


class _Pacer:
    """Enforces the minimum gap between two requests to one site."""

    def __init__(self, delay: float, sleep: Sleep, clock: Callable[[], float]) -> None:
        self.delay, self.sleep, self.clock = delay, sleep, clock
        self.last: float | None = None

    async def wait(self) -> None:
        if self.last is not None:
            gap = self.delay - (self.clock() - self.last)
            if gap > 0:
                await self.sleep(gap)
        self.last = self.clock()


def _site_host(url: str) -> str:
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _is_html(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct in {"text/html", "application/xhtml+xml"} or ct == ""


def looks_blocked(page: Page, markers: list[str]) -> str | None:
    """A challenge page, not a real page that merely embeds reCAPTCHA in a form.

    A marker in the <title> counts. A marker in the body counts only when the page
    is nearly empty, which is what interstitial challenge pages look like; small
    business homepages are often short, so a 1-2k character page is NOT enough."""
    title = page.title.lower()
    for m in markers:
        if m in title:
            return m
    if len(page.text) < 300:
        low = page.text.lower()
        for m in markers:
            if m in low:
                return m
    return None


def looks_parked(page: Page, markers: list[str]) -> str | None:
    low = (page.title + " " + page.text[:5000]).lower()
    return next((m for m in markers if m in low), None)


async def _get(client: httpx.AsyncClient, url: str, cfg: CrawlConfig, kind: str) -> Fetched:
    async with client.stream("GET", url) as resp:
        chunks: list[bytes] = []
        size = 0
        truncated = False
        if resp.status_code == 200 and _is_html(resp.headers.get("content-type", "")):
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > cfg.max_bytes:
                    truncated = True
                    break
        body = b"".join(chunks)[: cfg.max_bytes]
        return Fetched(url, str(resp.url), kind, resp.status_code,
                       resp.headers.get("content-type", ""), body, truncated)  # fmt: skip


async def _robots(
    client: httpx.AsyncClient, base: str, cfg: CrawlConfig
) -> tuple[urllib.robotparser.RobotFileParser | None, str | None, bool]:
    """(parser, problem, retryable). parser None + problem = don't crawl."""
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = await client.get(urllib.parse.urljoin(base, "/robots.txt"))
    except httpx.PoolTimeout:
        return None, LOCAL_ERROR, True
    except httpx.HTTPError as exc:
        return None, f"robots.txt unreachable: {type(exc).__name__}", True
    if resp.status_code in {401, 403}:
        return None, f"robots.txt {resp.status_code}: treated as disallow-all", False
    if resp.status_code == 429 or resp.status_code >= 500:
        return None, f"robots.txt {resp.status_code}", True
    if resp.status_code >= 400:
        rp.parse([])  # no robots.txt = everything allowed
    else:
        rp.parse(resp.text.splitlines())
    return rp, None, False


async def crawl_site(
    client: httpx.AsyncClient,
    cfg: CrawlConfig,
    domain: str,
    start_url: str,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SiteResult:
    if "://" not in start_url:
        start_url = "http://" + start_url
    parts = urllib.parse.urlsplit(start_url)
    base = f"{parts.scheme}://{parts.netloc}/"
    site = _site_host(start_url)
    result = SiteResult(domain)

    rp, problem, retryable = await _robots(client, base, cfg)
    if rp is None:
        result.status = "error" if retryable else "robots_disallowed"
        result.detail, result.retryable = problem, retryable
        return result
    agent = cfg.robots_agent
    crawl_delay = float(rp.crawl_delay(agent) or 0)
    pacer = _Pacer(max(cfg.per_site_delay_s, min(crawl_delay, MAX_CRAWL_DELAY_S)), sleep, clock)
    pacer.last = clock()  # the robots.txt request counts
    max_pages = 1 if crawl_delay > MAX_CRAWL_DELAY_S else cfg.max_pages_per_site
    if not rp.can_fetch(agent, start_url):
        result.status, result.detail = "robots_disallowed", "homepage disallowed by robots.txt"
        return result

    queue: list[tuple[str, str]] = [("home", start_url)]
    seen: set[str] = set()
    while queue and len(result.pages) < max_pages:
        kind, url = queue.pop(0)
        if url in seen or not rp.can_fetch(agent, url):
            continue
        seen.add(url)
        await pacer.wait()
        try:
            f = await _get(client, url, cfg, kind)
        except httpx.PoolTimeout:
            result.status, result.detail, result.retryable = "error", LOCAL_ERROR, True
            return result
        except httpx.HTTPError as exc:
            if kind == "home":
                result.status, result.detail, result.retryable = (
                    "error",
                    f"{type(exc).__name__}",
                    True,
                )
                return result
            continue  # an inner page failing doesn't sink the site
        # 403/429 and any other non-404/410 4xx (401, 406, 409, 451, vendor codes like 466)
        # plus 202 "accepted" pages are how WAFs answer bots: treat as a block, never retry.
        if f.status in {202, 403, 429} or (400 <= f.status < 500 and f.status not in {404, 410}):
            result.status, result.detail = "blocked", f"HTTP {f.status} on {kind}"
            return result
        if f.status != 200 or not f.body:
            if kind == "home":
                result.status = "error"
                result.detail = f"HTTP {f.status}" if f.status != 200 else "not HTML"
                result.retryable = f.status >= 500
                return result
            continue
        if not same_site(f.final_url, site):
            if kind == "home":
                result.pages.append(f)
                result.status, result.final_url = "offsite", f.final_url
                result.detail = f"redirects to {_site_host(f.final_url)}"
                return result
            continue
        page = parse(f.text(), f.final_url)
        if marker := looks_blocked(page, cfg.block_markers):
            result.status, result.detail = "blocked", f"challenge page ({marker}) on {kind}"
            return result
        result.pages.append(f)
        if kind == "home":
            result.final_url = f.final_url
            if marker := looks_parked(page, cfg.parked_markers):
                result.status, result.detail = "parked", f"parked domain ({marker})"
                return result
            queue += candidate_links(page, site, cfg.link_kinds, cfg.link_kind_priority)
    result.status = "done"
    return result


def make_client(cfg: CrawlConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": cfg.user_agent, "Accept": "text/html,application/xhtml+xml"},
        timeout=httpx.Timeout(cfg.timeout_s, pool=60.0),
        follow_redirects=True,
        max_redirects=cfg.max_redirects,
        # One client per site: at most one request in flight per site anyway.
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=5.0),
    )
