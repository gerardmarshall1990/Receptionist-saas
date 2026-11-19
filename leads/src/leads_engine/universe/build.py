"""Raw Overture extract -> normalized, excluded, deduped businesses + pilot sample.

Nothing is silently dropped. Every input row is written to the output with
either `exclusion` (why it can't be a lead), `dup_of` (the record it was merged
into), or neither (a kept business). Stats explain every count.

Order of operations (each documented in docs/reports/stage1-*.md):
 1. Row-level: normalize; exclude no_name / closed / brand_wikidata /
    chain_name_pattern / exclusion_keyword / answering_vendor.
 2. Cross-row: a website domain seen in >= chain_min_locations distinct cities
    is a chain; 2.. is a multi-location business (value signal). A name seen
    in many cities is only flagged for review (generic names are common).
 3. Dedupe the survivors on: same phone (unless shared by many records), same
    domain + city, same name + ZIP. Union-find, so A~B and B~C merge all three.
 4. Stratified random pilot sample per category (deterministic seed).
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from leads_engine.config_loader import AppConfig
from leads_engine.universe import normalize as nz

EXCLUSION_ORDER = (
    "no_name",
    "closed",
    "institution",
    "referral_listing",
    "brand_wikidata",
    "chain_name_pattern",
    "answering_vendor",
    "exclusion_keyword",
    "chain_multi_location",
    "chain_name_network",
)

PHONE_T = pa.struct(
    [
        ("e164", pa.string()),
        ("raw", pa.string()),
        ("valid", pa.bool_()),
        ("number_type", pa.string()),
        ("flags", pa.list_(pa.string())),
        ("source_records", pa.int32()),
    ]
)
EMAIL_T = pa.struct(
    [
        ("email", pa.string()),
        ("syntax_ok", pa.bool_()),
        ("is_role_based", pa.bool_()),
        ("is_freemail", pa.bool_()),
        ("domain_matches_site", pa.bool_()),
        ("flags", pa.list_(pa.string())),
    ]
)
SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_id", pa.string()),
        ("source_release", pa.string()),
        ("name", pa.string()),
        ("name_key", pa.string()),
        ("category", pa.string()),
        ("subcategory", pa.string()),
        ("source_category_code", pa.string()),
        ("confidence", pa.float64()),
        ("operating_status", pa.string()),
        ("country", pa.string()),
        ("state", pa.string()),
        ("city", pa.string()),
        ("address", pa.string()),
        ("postcode", pa.string()),
        ("lat", pa.float64()),
        ("lng", pa.float64()),
        ("tz", pa.string()),
        ("phone", pa.string()),
        ("phones", pa.list_(PHONE_T)),
        ("website", pa.string()),
        ("domain", pa.string()),
        ("site_host", pa.string()),
        ("emails", pa.list_(EMAIL_T)),
        ("brand_wikidata", pa.string()),
        ("source_datasets", pa.list_(pa.string())),
        ("licenses", pa.list_(pa.string())),
        ("flags", pa.list_(pa.string())),
        ("exclusion", pa.string()),
        ("exclusion_detail", pa.string()),
        ("dup_of", pa.string()),
        ("merged_source_ids", pa.list_(pa.string())),
        ("location_count", pa.int32()),
        ("in_sample", pa.bool_()),
    ]
)


@dataclass
class BuildStats:
    raw_rows: int = 0
    by_category: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    exclusions: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    merges_by_key: Counter[str] = field(default_factory=Counter)
    shared_phones: int = 0
    chain_domains: list[tuple[str, int, float, float]] = field(default_factory=list)
    subdomain_platforms: list[tuple[str, int]] = field(default_factory=list)
    multi_location_domains: list[tuple[str, int]] = field(default_factory=list)
    repeated_names: list[tuple[str, str, int, str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_rows": self.raw_rows,
            "by_category": {k: dict(v) for k, v in self.by_category.items()},
            "exclusions": {k: dict(v) for k, v in self.exclusions.items()},
            "merges_by_key": dict(self.merges_by_key),
            "shared_phones": self.shared_phones,
            "chain_domains_top": self.chain_domains[:40],
            "subdomain_platforms": self.subdomain_platforms[:25],
            "multi_location_domains_top": self.multi_location_domains[:25],
            "repeated_names_top": self.repeated_names[:40],
        }


# ----------------------------------------------------------------- step 1


def _iter_raw(path: Path, batch: int = 50_000) -> Iterator[dict[str, Any]]:
    con = duckdb.connect()
    reader = con.sql("select * from read_parquet($p)", params={"p": str(path)}).to_arrow_reader(
        batch
    )
    for rb in reader:
        yield from rb.to_pylist()


def normalize_row(
    raw: dict[str, Any], cfg: AppConfig, code_map: dict[str, tuple[str, str]], release: str
) -> dict[str, Any]:
    norm = cfg.global_.normalization
    category, subcategory = code_map[raw["code"]]
    cat = cfg.categories[category]
    flags: list[str] = []

    phones: dict[str, dict[str, Any]] = {}
    for p in raw.get("phones") or []:
        ph = nz.normalize_phone(p)
        if ph is None:
            continue
        key = ph.e164 or f"raw:{ph.raw}"
        if key not in phones:
            phones[key] = {
                "e164": ph.e164, "raw": ph.raw, "valid": ph.valid, "number_type": ph.number_type,
                "flags": list(ph.flags), "source_records": 1, "_usable": ph.usable,
            }  # fmt: skip
    primary = next((p["e164"] for p in phones.values() if p["_usable"]), None)
    if not phones:
        flags.append("no_phone")
    elif primary is None:
        flags.append("no_usable_phone")

    site, site_flags = nz.first_business_site(raw.get("websites"), norm)
    flags.extend(site_flags)
    if site is None:
        flags.append("no_website")

    emails = [
        nz.classify_email(e, site.domain if site else None, norm) for e in raw.get("emails") or []
    ]
    sources = raw.get("sources") or []
    datasets = sorted(
        {s["dataset"] for s in sources if s and s.get("dataset") and not s.get("property")}
    )
    licenses = sorted({s["license"] for s in sources if s and s.get("license")})

    name = (raw.get("name") or "").strip()
    row: dict[str, Any] = {
        "source": "overture",
        "source_id": raw["source_id"],
        "source_release": release,
        "name": name,
        "name_key": nz.name_key(name),
        "category": category,
        "subcategory": subcategory,
        "source_category_code": raw["code"],
        "confidence": raw.get("confidence"),
        "operating_status": raw.get("operating_status"),
        "country": raw.get("country"),
        "state": nz.state_code(raw.get("region")),
        "city": nz.clean_city(raw.get("city")),
        "address": (raw.get("address") or "").strip() or None,
        "postcode": nz.zip5(raw.get("postcode")),
        "lat": raw.get("lat"),
        "lng": raw.get("lng"),
        "tz": nz.timezone_for(raw.get("lat"), raw.get("lng")),
        "phone": primary,
        "phones": [{k: v for k, v in p.items() if not k.startswith("_")} for p in phones.values()],
        "website": site.url if site else None,
        "domain": site.domain if site else None,
        "site_host": site.host if site else None,
        "emails": [
            {
                "email": e.email,
                "syntax_ok": e.syntax_ok,
                "is_role_based": e.is_role_based,
                "is_freemail": e.is_freemail,
                "domain_matches_site": e.domain_matches_site,
                "flags": list(e.flags),
            }
            for e in emails
        ],
        "brand_wikidata": raw.get("brand_wikidata"),
        "source_datasets": datasets,
        "licenses": licenses,
        "flags": flags,
        "exclusion": None,
        "exclusion_detail": None,
        "dup_of": None,
        "merged_source_ids": [],
        "location_count": 1,
        "in_sample": False,
    }
    if row["state"] is None:
        flags.append("no_state")

    # Row-level exclusions, first match wins (EXCLUSION_ORDER).
    def exclude(reason: str, detail: str | None = None) -> None:
        if row["exclusion"] is None:
            row["exclusion"], row["exclusion_detail"] = reason, detail

    if not row["name_key"]:
        exclude("no_name")
    if row["operating_status"] in {"permanently_closed", "temporarily_closed"}:
        exclude("closed", row["operating_status"])
    if site and nz.is_institution_domain(site.domain):
        exclude("institution", site.domain)
    if nz.is_referral_listing(row["name_key"]):
        exclude("referral_listing", name)
    if nz.is_generic_name(row["name_key"]):
        flags.append("generic_name")
    if cfg.global_.chain_detection.use_brand_wikidata and row["brand_wikidata"]:
        exclude("brand_wikidata", row["brand_wikidata"])
    if hit := nz.find_pattern(name, tuple(cat.exclusions.chain_name_patterns), prefix=True):
        exclude("chain_name_pattern", hit)
    if hit := nz.find_pattern(name, tuple(cat.exclusions.chain_name_contains)):
        exclude("chain_name_pattern", hit)
    if hit := nz.find_pattern(name, tuple(cfg.global_.answering_vendor_patterns)):
        exclude("answering_vendor", hit)
    keywords = tuple(cat.exclusions.keywords) + tuple(cfg.global_.global_exclusion_keywords)
    if hit := nz.find_pattern(name, keywords):
        exclude("exclusion_keyword", hit)
    return row


# ----------------------------------------------------------------- step 2


def _place(row: dict[str, Any]) -> tuple[str, str] | None:
    """A location: ZIP if known, else city+state. Used to count locations, not cities,
    so a chain with 40 sites in one metro is still a chain."""
    if row["postcode"]:
        return ("zip", row["postcode"])
    if row["city"] and row["state"]:
        return (row["city"].lower(), row["state"])
    return None


def _split_subdomain_platforms(rows: list[dict[str, Any]], stats: BuildStats) -> None:
    """Website builders we don't know by name: most records on the domain have their
    own subdomain (biz1.platform.site, biz2.platform.site). Key those by full host."""
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["domain"] and r["site_host"] and r["site_host"] != r["domain"]:
            by_domain[r["domain"]].append(r)
    for domain, recs in by_domain.items():
        hosts = {r["site_host"] for r in recs}
        # A franchise also gives each location a subdomain (city.aireserv.com), but its
        # listings carry the brand name. A platform's listings don't.
        _, label_share = _name_agreement(recs, domain)
        if len(recs) >= 3 and len(hosts) >= 0.8 * len(recs) and label_share < 0.3:
            stats.subdomain_platforms.append((domain, len(recs)))
            for r in recs:
                r["domain"] = r["site_host"]
                r["flags"].append("subdomain_platform")
    stats.subdomain_platforms.sort(key=lambda x: -x[1])


def _name_agreement(recs: list[dict[str, Any]], domain: str) -> tuple[float, float]:
    """(share of the most common name, share of names containing the domain label)."""
    keys = [r["name_key"] for r in recs]
    top = Counter(keys).most_common(1)[0][1] / len(keys)
    label = "".join(ch for ch in domain.split(".")[0] if ch.isalnum())
    hit = sum(
        1 for k in keys if label and (label in k.replace(" ", "") or k.replace(" ", "") in label)
    )
    return top, hit / len(keys)


def apply_location_rules(rows: list[dict[str, Any]], cfg: AppConfig, stats: BuildStats) -> None:
    cd = cfg.global_.chain_detection
    eligible = [r for r in rows if r["exclusion"] not in {"no_name", "closed", "institution"}]
    _split_subdomain_platforms(eligible, stats)

    places_by_domain: dict[str, set[tuple[str, str]]] = defaultdict(set)
    recs_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        if r["domain"] and (pl := _place(r)):
            places_by_domain[r["domain"]].add(pl)
            recs_by_domain[r["domain"]].append(r)
    counts = {d: len(c) for d, c in places_by_domain.items()}
    for d, n in counts.items():
        if n >= cd.chain_min_locations:
            top, lbl = _name_agreement(recs_by_domain[d], d)
            stats.chain_domains.append((d, n, round(top, 2), round(lbl, 2)))
        elif n >= cd.multi_location_min:
            stats.multi_location_domains.append((d, n))
    stats.chain_domains.sort(key=lambda x: -x[1])
    stats.multi_location_domains.sort(key=lambda x: -x[1])
    for r in eligible:
        n = counts.get(r["domain"] or "", 1)
        r["location_count"] = n
        if n >= cd.chain_min_locations:
            if r["exclusion"] is None:
                r["exclusion"], r["exclusion_detail"] = (
                    "chain_multi_location",
                    f"{r['domain']} at {n} locations",
                )
        elif n >= cd.multi_location_min:
            r["flags"].append("multiple_locations")

    # Same name in many places. If most of those listings share one website it is a
    # chain or a listing network (exclude); otherwise it's a generic name (flag only).
    by_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        if r["exclusion"] is None and not r["flags"].count("generic_name"):
            by_name[(r["category"], r["name_key"])].append(r)
    for (category, key), recs in by_name.items():
        places = {pl for r in recs if (pl := _place(r))}
        if len(places) < cd.name_repeat_review_cities:
            continue
        domains = Counter(r["domain"] for r in recs if r["domain"])
        top_domain, top_n = domains.most_common(1)[0] if domains else (None, 0)
        with_domain = sum(domains.values())
        network = (
            top_n >= 3 and with_domain and top_n / with_domain >= cd.name_network_min_domain_share
        )
        stats.repeated_names.append(
            (category, key, len(places), top_domain or "-", "excluded" if network else "review")
        )
        for r in recs:
            if network:
                r["exclusion"], r["exclusion_detail"] = (
                    "chain_name_network",
                    f"{key} / {top_domain}",
                )
            else:
                r["flags"].append("name_repeated_review")
    stats.repeated_names.sort(key=lambda x: -x[2])


# ----------------------------------------------------------------- step 3


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[max(ra, rb)] = min(ra, rb)
        return True


def _survivor_rank(r: dict[str, Any]) -> tuple[Any, ...]:
    # Prefer: usable phone, own website, higher source confidence, more sources; stable on id.
    return (
        r["phone"] is not None,
        r["domain"] is not None,
        r["confidence"] or 0.0,
        len(r["source_datasets"]),
        _neg(r["source_id"]),
    )


def _neg(s: str) -> tuple[int, ...]:
    return tuple(-ord(c) for c in s)


def dedupe(rows: list[dict[str, Any]], cfg: AppConfig, stats: BuildStats) -> None:
    live = [r for r in rows if r["exclusion"] is None]
    phone_use = Counter(p["e164"] for r in live for p in r["phones"] if p["e164"])
    shared = {e for e, n in phone_use.items() if n > cfg.global_.chain_detection.shared_phone_max}
    stats.shared_phones = len(shared)

    uf = _UnionFind(len(live))
    first_seen: dict[tuple[str, ...], int] = {}

    def link(key: tuple[str, ...], i: int) -> None:
        j = first_seen.setdefault(key, i)
        if j != i and uf.union(i, j):
            stats.merges_by_key[key[0]] += 1

    for i, r in enumerate(live):
        for p in r["phones"]:
            if p["e164"] in shared:
                if "shared_phone" not in r["flags"]:
                    r["flags"].append("shared_phone")
            elif p["e164"]:
                link(("phone", p["e164"]), i)
        # Same site at the same ZIP = same location. (Same site in the same *city* is
        # not: multi-location businesses have several sites per city.)
        if r["domain"] and r["postcode"]:
            link(("domain_zip", r["domain"], r["postcode"]), i)
        if r["name_key"] and r["postcode"] and "generic_name" not in r["flags"]:
            link(("name_zip", r["name_key"], r["postcode"]), i)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(live)):
        groups[uf.find(i)].append(i)
    for members in groups.values():
        if len(members) == 1:
            continue
        recs = [live[i] for i in members]
        keep = max(recs, key=_survivor_rank)
        _merge_into(keep, [r for r in recs if r is not keep])


def _merge_into(keep: dict[str, Any], others: list[dict[str, Any]]) -> None:
    by_phone = {p["e164"] or p["raw"]: p for p in keep["phones"]}
    by_email = {e["email"]: e for e in keep["emails"]}
    for o in others:
        o["dup_of"] = keep["source_id"]
        keep["merged_source_ids"].append(o["source_id"])
        for p in o["phones"]:
            k = p["e164"] or p["raw"]
            if k in by_phone:
                by_phone[k]["source_records"] += 1
            else:
                by_phone[k] = dict(p)
        for e in o["emails"]:
            by_email.setdefault(e["email"], e)
        keep["source_datasets"] = sorted(set(keep["source_datasets"]) | set(o["source_datasets"]))
        keep["licenses"] = sorted(set(keep["licenses"]) | set(o["licenses"]))
        if keep["domain"] is None and o["domain"]:
            keep["website"], keep["domain"] = o["website"], o["domain"]
        if keep["phone"] is None and o["phone"]:
            keep["phone"] = o["phone"]
        keep["location_count"] = max(keep["location_count"], o["location_count"])
        for f in o["flags"]:
            if (
                f
                in {
                    "multiple_locations",
                    "shared_phone",
                    "name_repeated_review",
                    "subdomain_platform",
                }
                and f not in keep["flags"]
            ):
                keep["flags"].append(f)
    keep["phones"] = list(by_phone.values())
    keep["emails"] = list(by_email.values())
    keep["merged_source_ids"].sort()
    # Contact flags describe the merged record, so recompute them.
    keep["flags"] = [
        f for f in keep["flags"] if f not in {"no_phone", "no_usable_phone", "no_website"}
    ]
    if not keep["phones"]:
        keep["flags"].append("no_phone")
    elif keep["phone"] is None:
        keep["flags"].append("no_usable_phone")
    if keep["domain"] is None:
        keep["flags"].append("no_website")


# ----------------------------------------------------------------- step 4


def sample_key(seed: str, source_id: str) -> str:
    return hashlib.sha256(f"{seed}:{source_id}".encode()).hexdigest()


def draw_sample(rows: list[dict[str, Any]], sizes: dict[str, int], seed: str) -> None:
    for category, n in sizes.items():
        kept = [
            r
            for r in rows
            if r["category"] == category and r["exclusion"] is None and r["dup_of"] is None
        ]
        kept.sort(key=lambda r: sample_key(seed, r["source_id"]))
        for r in kept[:n]:
            r["in_sample"] = True


# -------------------------------------------------------------- pipeline


def build(
    raw_path: Path,
    cfg: AppConfig,
    categories: list[str],
    release: str,
    out_path: Path,
    seed: str,
    rows_in: Iterable[dict[str, Any]] | None = None,
) -> BuildStats:
    code_map = cfg.code_map(categories)
    stats = BuildStats()
    rows = [
        normalize_row(r, cfg, code_map, release)
        for r in (rows_in if rows_in is not None else _iter_raw(raw_path))
    ]
    stats.raw_rows = len(rows)
    apply_location_rules(rows, cfg, stats)
    dedupe(rows, cfg, stats)
    sizes = {c: n for c, n in cfg.global_.pilot_sample.items() if c in categories}
    draw_sample(rows, sizes, seed)

    for r in rows:
        c = stats.by_category[r["category"]]
        c["raw"] += 1
        if r["exclusion"]:
            c["excluded"] += 1
            stats.exclusions[r["category"]][r["exclusion"]] += 1
        elif r["dup_of"]:
            c["duplicate"] += 1
        else:
            c["kept"] += 1
            c["kept_usable_phone"] += r["phone"] is not None
            c["kept_website"] += r["domain"] is not None
            c["kept_any_email"] += bool(r["emails"])
            c["kept_email_on_site_domain"] += any(e["domain_matches_site"] for e in r["emails"])
            c["kept_freemail_only"] += bool(r["emails"]) and all(
                e["is_freemail"] for e in r["emails"]
            )
            c["kept_multi_location"] += "multiple_locations" in r["flags"]
            c["kept_shared_phone"] += "shared_phone" in r["flags"]
            c["kept_name_repeated_review"] += "name_repeated_review" in r["flags"]
            c["kept_no_state"] += r["state"] is None
            c["kept_generic_name"] += "generic_name" in r["flags"]
            c["sampled"] += r["in_sample"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.parquet")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    tmp.replace(out_path)
    return stats
