"""Stage 2 report: crawl outcome, politeness evidence, signal coverage, a random
sample of signal hits for manual precision review, and projections."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from leads_engine.config_loader import AppConfig
from leads_engine.signals import SIGNALS


def _pct(n: float, d: float) -> str:
    return f"{100 * n / d:.1f}%" if d else "-"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        cells = ["" if v is None else str(v).replace("|", "\\|").replace("\n", " ") for v in r]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def queue_status(url: str) -> dict[str, Any]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "select status, count(*) from crawl_sites group by 1 order by 1"
        ).fetchall()
        pages = conn.execute("select count(*) from pages").fetchone()
    return {"sites": {s: n for s, n in rows}, "pages": pages[0] if pages else 0}


def build_report(url: str, cfg: AppConfig, data_dir: Path) -> str:
    with psycopg.connect(url, row_factory=dict_row) as c:
        sites = c.execute(
            "select status, count(*) n from crawl_sites group by 1 order by n desc"
        ).fetchall()
        total_sites = sum(r["n"] for r in sites)
        attempted = c.execute(
            "select count(*) n, sum(pages_fetched) p from crawl_sites where status <> 'pending'"
        ).fetchone()
        details = c.execute(
            "select status, detail, count(*) n from crawl_sites where status not in ('done', 'pending')"
            " group by 1, 2 order by n desc limit 15"
        ).fetchall()
        # Politeness evidence. Idle gap = next request sent - previous response finished.
        # Pages fetched before requested_at existed (the first pilot run) can't be measured
        # this way: they were paced start-to-start in code (>= delay between request starts).
        gap = c.execute(
            "select min(g) as min_gap, percentile_cont(0.5) within group (order by g) as med_gap,"
            " count(*) as pairs from ("
            " select extract(epoch from requested_at - lag(fetched_at) over (partition by domain order by fetched_at)) g"
            " from pages) t where g is not null"
        ).fetchone()
        legacy = c.execute("select count(*) as n from pages where requested_at is null").fetchone()
        per_site = c.execute(
            "select max(n) as max_pages from (select count(*) n from pages group by domain) t"
        ).fetchone()
        kinds = c.execute(
            "select kind, count(*) n from pages group by 1 order by n desc"
        ).fetchall()
        size = c.execute(
            "select avg(bytes)::int as avg_bytes, sum(bytes) as total from pages"
        ).fetchone()
        runs = c.execute(
            "select extract(epoch from sum(finished_at - started_at)) s from runs"
            " where stage = 'stage2.crawl' and status = 'succeeded'"
        ).fetchone()

        crawled = c.execute(
            "select category, count(*) n,"
            " count(*) filter (where 'crawl_blocked' = any(flags)) blocked,"
            " count(*) filter (where 'thin_homepage' = any(flags)) thin,"
            " count(*) filter (where 'website_mismatch' = any(flags)) mismatch,"
            " count(*) filter (where 'phone_not_on_site' = any(flags)) phone_off,"
            " count(*) filter (where 'uses_answering_service' = any(flags)) answering,"
            " count(*) filter (where is_chain) franchise,"
            " count(*) filter (where email_status = 'site_found') site_email,"
            " count(*) filter (where phone_status = 'likely') phone_likely"
            " from businesses where crawled_at is not null group by 1 order by 1"
        ).fetchall()
        sig = c.execute(
            "select b.category, s.signal, count(*) filter (where s.value) n"
            " from signals s join businesses b on b.id = s.business_id group by 1, 2"
        ).fetchall()
        with_signals = c.execute(
            "select b.category, count(distinct s.business_id) n from signals s join businesses b on b.id = s.business_id"
            " where s.signal = 'website_works' group by 1"
        ).fetchall()
        reasons = c.execute(
            "select b.category, count(distinct r.business_id) n from reasons r join businesses b on b.id = r.business_id"
            " where r.dimension = 'pain' group by 1"
        ).fetchall()
        sample = c.execute(
            "select s.signal, b.name, b.category, s.evidence, s.source_url from signals s"
            " join businesses b on b.id = s.business_id"
            " where s.value and s.signal not in ('website_works', 'no_online_booking', 'no_chat_widget', 'single_phone_number')"
            " order by md5(s.business_id::text || s.signal) limit 100"
        ).fetchall()

    L = [
        "# Stage 2 pilot report: crawl + signals",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by `leads crawl report`. Config hash `{cfg.config_hash}`,"
        f" user agent `{cfg.crawl.user_agent}`.",
        "",
        "## 1. Crawl outcome (per website)",
        "",
        _table(
            ["status", "sites", "share"],
            [[r["status"], f"{r['n']:,}", _pct(r["n"], total_sites)] for r in sites],
        ),
        "",
        "Top reasons for not-done sites:",
        "",
        _table(
            ["status", "detail", "sites"], [[r["status"], r["detail"], r["n"]] for r in details]
        ),
        "",
        "## 2. Politeness (measured from stored fetch times)",
        "",
        _table(
            ["check", "rule", "measured"],
            [
                [
                    "idle gap: response finished -> next request to the same site",
                    f">= {cfg.crawl.per_site_delay_s:.1f} s",
                    f"min {gap['min_gap']:.2f} s, median {gap['med_gap']:.2f} s over {gap['pairs']:,} pairs"
                    if gap and gap["min_gap"] is not None
                    else "not measurable for these pages (see next row)",
                ],
                [
                    "pages fetched before request times were recorded",
                    "paced start-to-start in code: >= delay between request starts",
                    f"{legacy['n']:,} pages" if legacy else "-",
                ],
                [
                    "max pages fetched from one site",
                    f"<= {cfg.crawl.max_pages_per_site}",
                    per_site["max_pages"] if per_site else "-",
                ],
                ["robots.txt", "fetched first; 401/403 = disallow all", "enforced in code + tests"],
                [
                    "blocks (403/429/challenge)",
                    "stop, never retry",
                    f"{next((r['n'] for r in sites if r['status'] == 'blocked'), 0)} sites",
                ],
            ],
        ),
        "",
        "Pages by kind: " + ", ".join(f"{r['kind']} {r['n']:,}" for r in kinds) + ".",
        "",
        "## 3. Coverage per category (crawled businesses)",
        "",
    ]
    rows = []
    for r in crawled:
        n = r["n"]
        rows.append([r["category"], f"{n:,}", _pct(r["site_email"], n), _pct(r["phone_likely"], n),
                     _pct(r["mismatch"], n), _pct(r["phone_off"], n), _pct(r["answering"], n),
                     _pct(r["franchise"], n), _pct(r["blocked"], n), _pct(r["thin"], n)])  # fmt: skip
    L += [
        _table(
            [
                "category",
                "crawled",
                "email on own site",
                "phone confirmed by site",
                "site mismatch",
                "listed phone not on site",
                "answering service (DQ)",
                "franchise (DQ)",
                "blocked",
                "thin homepage (JS?)",
            ],
            rows,
        ),
        "",
        "## 4. Signal coverage (share of businesses whose site loaded)",
        "",
    ]
    base = {r["category"]: r["n"] for r in with_signals}
    cats = sorted(base)
    names = [n for n in SIGNALS if any(s["signal"] == n for s in sig)]
    by = {(s["category"], s["signal"]): s["n"] for s in sig}
    L += [
        _table(
            ["signal", *cats],
            [[n, *(_pct(by.get((c, n), 0), base[c]) for c in cats)] for n in names],
        ),
        "",
    ]
    rs = {r["category"]: r["n"] for r in reasons}
    L += ["Businesses with at least one evidence-backed **pain** reason: "
          + ", ".join(f"{c} {_pct(rs.get(c, 0), base[c])}" for c in cats) + ".", ""]  # fmt: skip
    L += [
        "## 5. Precision review sample (100 random positive hits)",
        "",
        "Reviewed by hand in `docs/reports/stage2-precision.md`. Absence signals and phone counts are excluded"
        " here (their evidence is the list of pages checked).",
        "",
        _table(
            ["#", "signal", "business", "evidence", "page"],
            [
                [i + 1, s["signal"], s["name"], (s["evidence"] or "")[:220], s["source_url"]]
                for i, s in enumerate(sample)
            ],
        ),
        "",
    ]
    secs = float(runs["s"]) if runs and runs["s"] else 0.0
    total_bytes = int(size["total"] or 0) if size else 0
    avg_bytes = int(size["avg_bytes"] or 0) if size else 0
    done_sites = attempted["n"] if attempted else 0
    n3 = cfg.global_.universe_target
    site_share = 0.78  # share of businesses with their own website (Stage 1 report)
    L += [
        "## 6. Projection",
        "",
        _table(
            ["measure", "pilot", f"at {n3:,} businesses (~{int(n3 * site_share):,} sites)"],
            [
                [
                    "wall time",
                    f"{secs / 60:.0f} min for {done_sites:,} sites",
                    f"~{secs / max(done_sites, 1) * n3 * site_share / 3600:.0f} h at the same concurrency",
                ],
                [
                    "download",
                    f"{total_bytes / 1e9:.2f} GB ({avg_bytes:,} B/page)",
                    f"~{total_bytes / max(done_sites, 1) * n3 * site_share / 1e12:.1f} TB",
                ],
                ["cost", "$0", "$0 in fees; compute + bandwidth only"],
            ],
        ),
        "",
        f"Saved HTML: `{data_dir / 'crawl' / 'pages'}` (gzipped Parquet, gitignored).",
        "",
    ]
    return "\n".join(L)
