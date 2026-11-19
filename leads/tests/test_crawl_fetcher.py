"""Politeness and stop rules against a real local HTTP server (no internet).

A fake clock/sleep records every pause, so pacing is verified exactly without
the test actually waiting.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from leads_engine.config_loader import CrawlConfig, load_config
from leads_engine.crawl.fetcher import SiteResult, crawl_site, make_client
from tests.conftest import REAL_CONFIG

FIX = Path(__file__).parent / "fixtures" / "sites"
CRAWL = load_config(REAL_CONFIG).crawl

Route = tuple[int, dict[str, str], bytes]


@dataclass
class Site:
    routes: dict[str, Route] = field(default_factory=dict)
    hits: list[tuple[str, str]] = field(default_factory=list)  # (path, user-agent)
    port: int = 0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def site() -> Iterator[Site]:
    s = Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            s.hits.append((self.path, self.headers.get("User-Agent", "")))
            status, headers, body = s.routes.get(self.path, (404, {}, b"not found"))
            self.send_response(status)
            headers = {"Content-Type": "text/html; charset=utf-8", **headers}
            for k, v in headers.items():
                self.send_header(k, v.replace("{base}", s.base))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    s.port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield s
    server.shutdown()


def html(name: str) -> Route:
    return (200, {}, (FIX / name).read_bytes())


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


def crawl(
    site: Site, cfg: CrawlConfig = CRAWL, start: str | None = None
) -> tuple[SiteResult, FakeClock]:
    clock = FakeClock()

    async def go() -> SiteResult:
        async with make_client(cfg) as client:
            return await crawl_site(
                client, cfg, "x", start or site.base + "/", sleep=clock.sleep, clock=clock.now
            )

    return asyncio.run(go()), clock


def plumber_site(site: Site) -> None:
    site.routes.update({
        "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /about\n"),
        "/": html("plumber_home.html"),
        "/contact-us": html("plumber_contact.html"),
        "/careers": html("plumber_careers.html"),
        "/services": html("plumber_careers.html"),
        "/about": html("plumber_careers.html"),
    })  # fmt: skip


# ------------------------------------------------------------------ happy path


def test_robots_first_then_paced_pages_within_the_site(site: Site) -> None:
    plumber_site(site)
    res, clock = crawl(site)
    assert res.status == "done"
    paths = [p for p, _ in site.hits]
    assert paths[0] == "/robots.txt"
    assert "/about" not in paths  # disallowed by robots.txt
    assert "/brochure.pdf" not in paths
    assert [p.kind for p in res.pages] == ["home", "contact", "careers", "services"]
    # one pause before every request after robots.txt, each >= the configured delay
    assert len(clock.sleeps) == len(res.pages)
    assert all(d >= CRAWL.per_site_delay_s for d in clock.sleeps)
    assert all(ua == CRAWL.user_agent for _, ua in site.hits)


def test_page_cap(site: Site) -> None:
    plumber_site(site)
    site.routes["/robots.txt"] = (404, {}, b"")  # no robots.txt = all allowed
    res, _ = crawl(site, CRAWL.model_copy(update={"max_pages_per_site": 2}))
    assert len(res.pages) == 2


def test_crawl_delay_is_honored(site: Site) -> None:
    plumber_site(site)
    site.routes["/robots.txt"] = (
        200,
        {"Content-Type": "text/plain"},
        b"User-agent: *\nCrawl-delay: 7\n",
    )
    _, clock = crawl(site)
    assert min(clock.sleeps) >= 7


def test_huge_crawl_delay_means_homepage_only(site: Site) -> None:
    plumber_site(site)
    site.routes["/robots.txt"] = (
        200,
        {"Content-Type": "text/plain"},
        b"User-agent: *\nCrawl-delay: 120\n",
    )
    res, _ = crawl(site)
    assert [p.kind for p in res.pages] == ["home"]


# ------------------------------------------------------------------ robots.txt


@pytest.mark.parametrize(
    ("robots", "status", "retryable"),
    [
        (
            (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /\n"),
            "robots_disallowed",
            False,
        ),
        (
            (200, {"Content-Type": "text/plain"}, b"User-agent: FrontdeskLeadsBot\nDisallow: /\n"),
            "robots_disallowed",
            False,
        ),
        ((403, {}, b""), "robots_disallowed", False),
        ((401, {}, b""), "robots_disallowed", False),
        ((503, {}, b""), "error", True),
        ((429, {}, b""), "error", True),
    ],
)
def test_robots_rules(site: Site, robots: Route, status: str, retryable: bool) -> None:
    plumber_site(site)
    site.routes["/robots.txt"] = robots
    res, _ = crawl(site)
    assert res.status == status
    assert res.retryable is retryable
    assert [p for p, _ in site.hits] == ["/robots.txt"]  # nothing else fetched


# ----------------------------------------------------------------- stop rules


@pytest.mark.parametrize("code", [403, 429])
def test_blocks_stop_immediately_and_are_not_retryable(site: Site, code: int) -> None:
    plumber_site(site)
    site.routes["/"] = (code, {}, b"no")
    res, _ = crawl(site)
    assert res.status == "blocked"
    assert res.retryable is False
    assert [p for p, _ in site.hits] == ["/robots.txt", "/"]


def test_challenge_page_mid_crawl_stops(site: Site) -> None:
    plumber_site(site)
    site.routes["/contact-us"] = html("challenge.html")
    res, _ = crawl(site)
    assert res.status == "blocked"
    assert "/careers" not in [p for p, _ in site.hits]


def test_parked_domain(site: Site) -> None:
    site.routes.update({"/robots.txt": (404, {}, b""), "/": html("parked.html")})
    res, _ = crawl(site)
    assert res.status == "parked"


def test_offsite_redirect_recorded_and_not_followed_further(site: Site) -> None:
    plumber_site(site)
    site.routes["/"] = (301, {"Location": f"http://localhost:{site.port}/contact-us"}, b"")
    res, _ = crawl(site)
    assert res.status == "offsite"
    assert res.final_url
    assert "localhost" in res.final_url
    assert len(res.pages) == 1


def test_server_error_on_home_is_retryable_404_is_not(site: Site) -> None:
    site.routes.update({"/robots.txt": (404, {}, b""), "/": (500, {}, b"boom")})
    res, _ = crawl(site)
    assert (res.status, res.retryable) == ("error", True)
    site.routes["/"] = (404, {}, b"gone")
    res, _ = crawl(site)
    assert (res.status, res.retryable) == ("error", False)


def test_non_html_home_is_an_error(site: Site) -> None:
    site.routes.update(
        {"/robots.txt": (404, {}, b""), "/": (200, {"Content-Type": "application/pdf"}, b"%PDF")}
    )
    res, _ = crawl(site)
    assert res.status == "error"


def test_size_cap_truncates(site: Site) -> None:
    big = b"<html><head><title>Big</title></head><body>" + b"x" * 300_000 + b"</body></html>"
    site.routes.update({"/robots.txt": (404, {}, b""), "/": (200, {}, big)})
    res, _ = crawl(site, CRAWL.model_copy(update={"max_bytes": 100_000}))
    assert res.pages[0].truncated
    assert len(res.pages[0].body) == 100_000


def test_unreachable_site_is_retryable() -> None:
    async def go() -> SiteResult:
        async with make_client(CRAWL) as client:
            return await crawl_site(
                client, CRAWL, "x", "http://127.0.0.1:9/", sleep=FakeClock().sleep
            )

    res = asyncio.run(go())
    assert (res.status, res.retryable) == ("error", True)


def test_client_identifies_itself() -> None:
    client: Callable[[CrawlConfig], httpx.AsyncClient] = make_client
    c = client(CRAWL)
    assert c.headers["User-Agent"] == "FrontdeskLeadsBot/0.1"
    asyncio.run(c.aclose())
