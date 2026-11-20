"""Parse one HTML page into what the signal rules need. Pure: no network, no I/O."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

import lxml.html
from lxml import etree

_SKIP_EXT = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp4|mp3|ico|css|js)(\?|$)", re.I
)
_WS = re.compile(r"\s+")
# Text-formatting tags that sit inside a word or phrase. Every other element is a
# boundary: without a space, "<div>664-2498</div><div>info@x.com</div>" reads as
# "664-2498info@x.com" (v7 email fix).
_INLINE = frozenset({"b", "strong", "i", "em", "u", "small", "sup", "sub", "mark", "abbr", "font"})


@dataclass(frozen=True)
class Link:
    url: str
    text: str


@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""  # visible text, whitespace-collapsed
    raw_lower: str = ""  # full HTML lowercased: script/iframe signatures live here
    links: list[Link] = field(default_factory=list)
    tel: list[str] = field(default_factory=list)
    mailto: list[str] = field(default_factory=list)


def _host(url: str) -> str:
    try:
        h = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _visible_text(doc: lxml.html.HtmlElement) -> str:
    """Like text_content(), but with a space at every non-inline element boundary.

    Walks the tree instead of editing it: lxml refuses to re-assign text that holds
    control characters, which real pages contain.
    """
    out: list[str] = []
    for event, el in etree.iterwalk(doc, events=("start", "end")):
        boundary = isinstance(el.tag, str) and el.tag not in _INLINE
        if event == "start":
            if boundary:
                out.append(" ")
            if isinstance(el.tag, str) and el.text:  # comments/PIs: skip their content
                out.append(el.text)
        else:
            if boundary:
                out.append(" ")
            if el.tail and el is not doc:
                out.append(el.tail)
    return "".join(out)


def parse(html: str, url: str) -> Page:
    page = Page(url=url, raw_lower=html.lower())
    try:
        doc = lxml.html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return page
    title = doc.find(".//title")
    page.title = _WS.sub(" ", title.text_content()).strip() if title is not None else ""
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        label = _WS.sub(" ", a.text_content() or "").strip()
        low = href.lower()
        if low.startswith("tel:"):
            page.tel.append(urllib.parse.unquote(href[4:]))
        elif low.startswith("mailto:"):
            addr = urllib.parse.unquote(href[7:].split("?")[0]).strip()
            if addr:
                page.mailto.append(addr)
        elif href and not low.startswith(("javascript:", "#", "data:")):
            try:
                absolute = urllib.parse.urljoin(url, href).split("#")[0]
                urllib.parse.urlsplit(absolute).hostname  # noqa: B018 (validates, e.g. bad IPv6)
            except ValueError:
                continue  # malformed link on the page: skip it, keep the page
            if absolute.startswith(("http://", "https://")):
                page.links.append(Link(absolute, label))
    for bad in doc.xpath("//script|//style|//noscript|//template|//svg"):
        bad.drop_tree()
    page.text = _WS.sub(" ", _visible_text(doc)).strip()
    return page


def same_site(url: str, site_host: str) -> bool:
    """Same host or a subdomain of it (www. ignored)."""
    h = _host(url)
    return h == site_host or h.endswith("." + site_host)


def candidate_links(
    page: Page, site_host: str, kinds: dict[str, list[str]], priority: list[str]
) -> list[tuple[str, str]]:
    """Best internal link per kind, in priority order -> [(kind, url)]."""
    best: dict[str, tuple[int, str]] = {}
    for link in page.links:
        if not same_site(link.url, site_host) or _SKIP_EXT.search(link.url):
            continue
        path = urllib.parse.urlsplit(link.url).path.lower()
        text = link.text.lower()
        for kind, words in kinds.items():
            score = 0
            for w in words:
                if w in text:
                    score = max(score, 3)
                if w.replace(" ", "-") in path or w.replace(" ", "") in path:
                    score = max(score, 2)
            if score and (
                kind not in best
                or score > best[kind][0]
                or (score == best[kind][0] and len(link.url) < len(best[kind][1]))
            ):
                best[kind] = (score, link.url)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for kind in priority:
        if (
            kind in best
            and best[kind][1] not in seen
            and best[kind][1].rstrip("/") != page.url.rstrip("/")
        ):
            seen.add(best[kind][1])
            out.append((kind, best[kind][1]))
    return out
