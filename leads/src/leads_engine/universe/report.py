"""Markdown reports for Stage 1 (committed under docs/reports/)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import psycopg

from leads_engine.config_loader import AppConfig
from leads_engine.universe.build import EXCLUSION_ORDER


def _pct(n: float, d: float) -> str:
    return f"{100 * n / d:.1f}%" if d else "-"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


# --------------------------------------------------------------- taxonomy


def census_counts(primary: Path, alternates: Path) -> tuple[dict[str, int], dict[str, int], int]:
    con = duckdb.connect()
    prim = dict(
        con.execute(
            "select code, sum(n)::bigint from read_parquet(?) group by 1", [str(primary)]
        ).fetchall()
    )
    alt = dict(
        con.execute(
            "select code, sum(n)::bigint from read_parquet(?) group by 1", [str(alternates)]
        ).fetchall()
    )
    total = int(sum(prim.values()))
    return prim, alt, total


def missing_codes(cfg: AppConfig, prim: dict[str, int]) -> dict[str, list[str]]:
    """Configured codes that the census never saw as a primary code."""
    out: dict[str, list[str]] = {}
    for cat in cfg.categories.values():
        miss = sorted(c for s in cat.subcategories for c in s.overture_codes if c not in prim)
        if miss:
            out[cat.slug] = miss
    return out


def taxonomy_report(
    cfg: AppConfig, release: str, country: str, primary: Path, alternates: Path
) -> str:
    prim, alt, total = census_counts(primary, alternates)
    lines = [
        f"# Overture taxonomy census: {country}, release {release}",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by `leads universe census`.",
        f"{total:,} places in {country} (`addresses[1].country = '{country}'`), {len(prim):,} distinct primary codes.",
        "",
        "Counts are by `taxonomy.primary`. *As alternate* is how often the code appears in `taxonomy.alternates`"
        " of any place; those places are **not** extracted (primary code only), so this is the ceiling on what"
        " matching alternates could add.",
        "",
    ]
    est_rows = []
    for cat in sorted(cfg.categories.values(), key=lambda c: c.rank):
        lines += [f"## {cat.rank}. {cat.label} (`{cat.slug}`)", ""]
        rows = []
        for s in cat.subcategories:
            for code in s.overture_codes:
                rows.append(
                    [
                        f"`{code}`",
                        s.slug,
                        "yes" if s.include else "no",
                        f"{prim.get(code, 0):,}",
                        f"{alt.get(code, 0):,}",
                    ]
                )
        lines += [_table(["code", "subcategory", "include", "primary", "as alternate"], rows), ""]
        included = sum(prim.get(c, 0) for c in cat.included_codes)
        est_rows.append([cat.slug, f"{included:,}"])
    miss = missing_codes(cfg, prim)
    lines += [
        "## Included places per category (before exclusions and dedupe)",
        "",
        _table(["category", f"{country} places"], est_rows),
        "",
        f"Total: {sum(sum(prim.get(c, 0) for c in cat.included_codes) for cat in cfg.categories.values()):,}",
        "",
        "## Check",
        "",
        "All configured codes were found." if not miss else f"**Missing codes:** {miss}",
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------- pilot


def _pg_sizes(url: str) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        out = {}
        for t in ("businesses", "phones", "emails"):
            size = conn.execute(f"select pg_total_relation_size('{t}')").fetchone()
            count = conn.execute(f"select count(*) from {t}").fetchone()  # noqa: S608
            out[f"{t}_bytes"] = int(size[0]) if size else 0
            out[f"{t}_rows"] = int(count[0]) if count else 0
        return out


def _sample_rows(url: str, n: int = 20) -> list[list[Any]]:
    q = """
    select name, category || '/' || subcategory, coalesce(city, '') || ', ' || coalesce(state, ''),
           tz, phone, coalesce(domain, '-'), location_count,
           (select count(*) from emails e where e.business_id = b.id),
           array_to_string(flags, ' ')
    from businesses b where in_pilot_sample
    order by md5(source_id) limit %s
    """
    with psycopg.connect(url) as conn:
        return [list(r) for r in conn.execute(q, (n,)).fetchall()]


def pilot_report(
    cfg: AppConfig,
    release: str,
    stats: dict[str, Any],
    built: Path,
    raw: Path,
    extract_seconds: float | None,
    db_url: str,
    load_counts: dict[str, int],
    universe_by_category: dict[str, int],
) -> str:
    con = duckdb.connect()
    b = str(built)
    cats = sorted(stats["by_category"], key=lambda c: cfg.categories[c].rank)
    L: list[str] = [
        f"# Stage 1 pilot report: US, {', '.join(cats)}",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC}. Overture release `{release}`, config hash"
        f" `{cfg.config_hash}`, sample seed `stage1-v1`.",
        "",
        "## 1. Counts",
        "",
    ]
    rows = []
    for c in cats:
        s = stats["by_category"][c]
        rows.append(
            [
                c,
                f"{s.get('raw', 0):,}",
                f"{s.get('excluded', 0):,}",
                f"{s.get('duplicate', 0):,}",
                f"{s.get('kept', 0):,}",
                f"{s.get('sampled', 0):,}",
            ]
        )
    tot = {
        k: sum(stats["by_category"][c].get(k, 0) for c in cats)
        for k in ("raw", "excluded", "duplicate", "kept", "sampled")
    }
    rows.append(
        [
            "**total**",
            *(f"**{tot[k]:,}**" for k in ("raw", "excluded", "duplicate", "kept", "sampled")),
        ]
    )
    L += [
        _table(
            [
                "category",
                "raw",
                "excluded",
                "duplicates merged",
                "kept",
                "in pilot sample (Postgres)",
            ],
            rows,
        ),
        "",
    ]

    L += [
        "## 2. Exclusions by reason",
        "",
        "First matching reason wins, in this order: " + ", ".join(EXCLUSION_ORDER) + ".",
        "",
    ]
    reasons = sorted({r for c in cats for r in stats["exclusions"].get(c, {})})
    L += [
        _table(
            ["reason", *cats],
            [
                [r, *(f"{stats['exclusions'].get(c, {}).get(r, 0):,}" for c in cats)]
                for r in reasons
            ],
        ),
        "",
    ]

    L += [
        "## 3. Contact coverage (kept businesses)",
        "",
        "Phone = a valid, non-toll-free, non-suspicious number (still **unverified**: verification is Stage 3)."
        " Website = the business's own site, not a directory or social page. Emails here are **source-listed**"
        " (Overture), not yet seen on the business's own site, so none of them is exportable yet.",
        "",
    ]
    metrics = [
        ("usable phone", "kept_usable_phone"), ("own website", "kept_website"),
        ("any source-listed email", "kept_any_email"), ("email on the site's own domain", "kept_email_on_site_domain"),
        ("only free-mail emails (gmail etc.)", "kept_freemail_only"), ("multiple locations (2-9)", "kept_multi_location"),
        ("phone shared with >3 records", "kept_shared_phone"), ("name repeated at 10+ locations (review)", "kept_name_repeated_review"),
        ("generic name (e.g. 'Dentist')", "kept_generic_name"),
        ("state unknown", "kept_no_state"),
    ]  # fmt: skip
    L += [
        _table(
            ["metric", *cats],
            [
                [
                    m,
                    *(
                        _pct(
                            stats["by_category"][c].get(k, 0),
                            stats["by_category"][c].get("kept", 0),
                        )
                        for c in cats
                    ),
                ]
                for m, k in metrics
            ],
        ),
        "",
    ]
    both = con.execute(
        "select category, avg((phone is not null)::int), avg((phone is not null and domain is not null)::int)"
        " from read_parquet(?) where exclusion is null and dup_of is null group by 1", [b]
    ).fetchall()  # fmt: skip
    L += [
        _table(
            ["category", "phone", "phone + website (crawlable)"],
            [[c, _pct(p, 1), _pct(pw, 1)] for c, p, pw in sorted(both)],
        ),
        "",
    ]

    L += [
        "## 4. Dedupe",
        "",
        f"Merges by matching key (union-find, so chains of matches collapse): {stats['merges_by_key']}.",
        f" Phones listed by more than {cfg.global_.chain_detection.shared_phone_max} records are never used to merge"
        f" (call centers, tracking numbers, franchise lines): {stats['shared_phones']:,} such numbers.",
        "",
    ]

    cd = cfg.global_.chain_detection
    L += [
        "## 5. Chain, platform and multi-location review",
        "",
        f"A website domain at >= {cd.chain_min_locations} locations (distinct ZIPs) is excluded as a chain."
        " *Top name* = share of its listings with the most common name; *label in name* = share whose name"
        " contains the domain's label. Real chains score high on one of the two; **low on both usually means"
        " a platform, directory or manufacturer dealer page** (add it to `normalization.non_business_site_domains`)"
        " or a DSO that keeps practice names (correctly excluded).",
        "",
    ]
    L += [
        _table(
            ["domain", "locations", "top name", "label in name"],
            [list(x) for x in stats["chain_domains_top"][:40]],
        ),
        "",
    ]
    L += [
        "Detected automatically as subdomain platforms (each business has its own subdomain; keyed by full host):",
        "",
    ]
    L += [
        _table(
            ["platform domain", "listings"], [list(x) for x in stats["subdomain_platforms"][:15]]
        ),
        "",
    ]
    L += [
        f"Multi-location businesses kept ({cd.multi_location_min}-{cd.chain_min_locations - 1} locations; value signal, check decision-maker). Top:",
        "",
    ]
    L += [
        _table(
            ["domain", "locations"], [list(x) for x in stats["multi_location_domains_top"][:15]]
        ),
        "",
    ]
    L += [
        f"Names at >= {cd.name_repeat_review_cities} locations. *excluded* = at least"
        f" {cd.name_network_min_domain_share:.0%} of the listings share one website (chain or listing network);"
        " *review* = different websites (usually a generic name; add real chains to `chain_name_patterns`):",
        "",
    ]
    L += [
        _table(
            ["category", "name", "locations", "top domain", "result"],
            [list(x) for x in stats["repeated_names_top"][:30]],
        ),
        "",
    ]

    states = con.execute(
        "select state, count(*) n from read_parquet(?) where exclusion is null and dup_of is null"
        " group by 1 order by n desc limit 12", [b]
    ).fetchall()  # fmt: skip
    L += [
        "## 6. Top states (kept)",
        "",
        _table(["state", "businesses"], [[s or "unknown", f"{n:,}"] for s, n in states]),
        "",
    ]

    L += [
        "## 7. Twenty sample rows (from Postgres, pilot sample)",
        "",
        "Email addresses are not printed here (some are personal); the count is shown.",
        "",
    ]
    L += [
        _table(
            ["name", "category", "city", "tz", "phone", "domain", "locations", "emails", "flags"],
            _sample_rows(db_url),
        ),
        "",
    ]

    sizes = _pg_sizes(db_url)
    raw_rows = stats["raw_rows"]
    raw_bpr = raw.stat().st_size / raw_rows if raw_rows else 0
    built_bpr = built.stat().st_size / raw_rows if raw_rows else 0
    pg_rows = sizes["businesses_rows"] or 1
    pg_bpr = (sizes["businesses_bytes"] + sizes["phones_bytes"] + sizes["emails_bytes"]) / pg_rows
    n = cfg.global_.universe_target
    us_total = sum(universe_by_category.values())
    L += [
        "## 8. Projection to the full universe",
        "",
        _table(
            ["measure", "pilot", f"at {n:,} businesses"],
            [
                [
                    "raw extract Parquet",
                    f"{raw.stat().st_size / 1e6:.1f} MB ({raw_bpr:.0f} B/row)",
                    f"{raw_bpr * n / 1e9:.2f} GB",
                ],
                [
                    "built Parquet",
                    f"{built.stat().st_size / 1e6:.1f} MB ({built_bpr:.0f} B/row)",
                    f"{built_bpr * n / 1e9:.2f} GB",
                ],
                [
                    "Postgres (businesses+phones+emails, with indexes)",
                    f"{pg_bpr:.0f} B/business",
                    f"{pg_bpr * n / 1e9:.2f} GB",
                ],
                [
                    "extract time (remote read)",
                    f"{extract_seconds:.0f} s" if extract_seconds else "cached",
                    "scales with files read, not rows: all US categories ~ same",
                ],
                [
                    "build time",
                    f"{stats.get('build_seconds', 0):.0f} s for {raw_rows:,} rows",
                    f"~{stats.get('build_seconds', 0) / max(raw_rows, 1) * n / 60:.0f} min",
                ],
                [
                    "cost",
                    "$0",
                    "$0 (open data; LLM/paid steps only on the top slices, see `leads config validate`)",
                ],
            ],
        ),
        "",
        f"US places in all 7 configured categories (primary code, before exclusions/dedupe): **{us_total:,}**"
        f" ({', '.join(f'{k} {v:,}' for k, v in universe_by_category.items())}).",
        " Applying this pilot's kept rate gives the US share of the 3M target; CA/AU/UK fill the rest in Stage 7.",
        "",
        f"Load: {load_counts}.",
        "",
    ]
    return "\n".join(L)
