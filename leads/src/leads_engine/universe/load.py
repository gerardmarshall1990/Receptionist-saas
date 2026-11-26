"""Load kept businesses from the built Parquet into Postgres (idempotent, set-based).

scope="sample": the stratified pilot sample only (20k).
scope="all":    every kept business (not excluded, not a duplicate).

After a load, Postgres mirrors the chosen scope for this source: rows that are no
longer in it are deleted if no later stage has touched them (crawled_at is null),
otherwise only un-marked. Rows are COPYed into temp tables and merged with a few
set-based statements, so 250k rows take seconds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import duckdb
import psycopg

from leads_engine.universe.build import sample_key
from leads_engine.universe.overture import LICENSE_NOTE

Scope = Literal["sample", "all"]

_BUSINESS_COLS = (
    "source", "source_id", "source_release", "name", "name_key", "category", "subcategory",
    "source_category_code", "country", "state", "city", "address", "postcode", "lat", "lng", "tz",
    "phone", "website", "domain", "location_count", "flags", "source_confidence", "source_datasets",
    "licenses", "attribution", "merged_source_ids", "in_pilot_sample",
)  # fmt: skip

_STAGE_DDL = """
create temp table stage_b (
  source text, source_id text, source_release text, name text, name_key text, category text,
  subcategory text, source_category_code text, country text, state text, city text, address text,
  postcode text, lat double precision, lng double precision, tz text, phone text, website text,
  domain text, location_count integer, flags text[], source_confidence double precision,
  source_datasets text[], licenses text[], attribution text, merged_source_ids text[],
  in_pilot_sample boolean
) on commit drop;
create temp table stage_p (
  source_id text, e164 text, valid boolean, number_type text, flags text[]
) on commit drop;
create temp table stage_e (
  source_id text, email text, syntax_ok boolean, is_role_based boolean, is_freemail boolean,
  domain_matches_site boolean, flags text[]
) on commit drop;
"""

_MERGE = """
insert into businesses ({cols})
select {cols} from stage_b
on conflict (source, source_id) do update set {updates}, updated_at = now();

delete from phones p using businesses b, stage_b s
  where p.business_id = b.id and b.source = s.source and b.source_id = s.source_id;
insert into phones (business_id, e164, sources, agree_count, valid_format, number_type_guess,
                    flags, status)
select distinct on (b.id, sp.e164) b.id, sp.e164, array['overture'], 1, sp.valid, sp.number_type,
       sp.flags, case when sp.valid then 'unverified' else 'invalid' end
from stage_p sp join businesses b on b.source = 'overture' and b.source_id = sp.source_id;

delete from emails e using businesses b, stage_b s
  where e.business_id = b.id and e.origin = 'source_listed'
    and b.source = s.source and b.source_id = s.source_id;
insert into emails (business_id, email, origin, syntax_ok, is_role_based, is_freemail,
                    domain_matches_site, flags)
select distinct on (b.id, se.email) b.id, se.email, 'source_listed', se.syntax_ok,
       se.is_role_based, se.is_freemail, se.domain_matches_site, se.flags
from stage_e se join businesses b on b.source = 'overture' and b.source_id = se.source_id;

-- Denormalized contact status for the explorer (never upgrades past what is stored).
update businesses b set
  phone_status = case
    when b.phone is null then 'none'
    else coalesce(
      (select p.status from phones p where p.business_id = b.id and p.e164 = b.phone), 'none')
  end,
  email_status = case
    when exists (select 1 from emails e where e.business_id = b.id and e.origin = 'business_site')
      then 'site_found'
    when exists (select 1 from emails e where e.business_id = b.id) then 'source_listed'
    else 'none'
  end
from stage_b s where b.source = s.source and b.source_id = s.source_id;
"""


def attribution(release: str, datasets: list[str]) -> str:
    srcs = ", ".join(datasets) if datasets else "Overture"
    return f"{LICENSE_NOTE}. Release {release}. Contributing datasets: {srcs}."


def kept_rows(built: Path, scope: Scope = "sample") -> list[dict[str, Any]]:
    where = "exclusion is null and dup_of is null" + (" and in_sample" if scope == "sample" else "")
    q = f"select * from read_parquet($p) where {where}"  # noqa: S608 (fixed clauses)
    rows: list[dict[str, Any]] = (
        duckdb.connect().sql(q, params={"p": str(built)}).to_arrow_table().to_pylist()
    )
    return rows


def sample_rows(built: Path) -> list[dict[str, Any]]:
    return kept_rows(built, "sample")


def _merge_sql() -> str:
    cols = ", ".join(_BUSINESS_COLS)
    updates = ", ".join(
        f"{c} = excluded.{c}" for c in _BUSINESS_COLS if c not in {"source", "source_id"}
    )
    return _MERGE.format(cols=cols, updates=updates)


def _stage(cur: psycopg.Cursor[Any], rows: list[dict[str, Any]], counts: dict[str, int]) -> None:
    """COPY rows (and their phones/emails) into the temp staging tables."""
    cur.execute(_STAGE_DDL)
    with cur.copy(f"copy stage_b ({', '.join(_BUSINESS_COLS)}) from stdin") as cp:
        for r in rows:
            v = {c: r.get(c) for c in _BUSINESS_COLS}
            v["source_confidence"] = r["confidence"]
            v["attribution"] = attribution(r["source_release"], r["source_datasets"])
            v["in_pilot_sample"] = bool(r["in_sample"])
            cp.write_row([v[c] for c in _BUSINESS_COLS])
    with cur.copy("copy stage_p from stdin") as cp:
        for r in rows:
            for p in r["phones"]:
                if p["e164"]:  # unparseable/invalid numbers stay in Parquet only
                    cp.write_row(
                        [r["source_id"], p["e164"], p["valid"], p["number_type"], p["flags"]]
                    )
                    counts["phones"] += 1
    with cur.copy("copy stage_e from stdin") as cp:
        for r in rows:
            for e in r["emails"]:
                cp.write_row(
                    [r["source_id"], e["email"], e["syntax_ok"], e["is_role_based"],
                     e["is_freemail"], e["domain_matches_site"], e["flags"]]
                )  # fmt: skip
                counts["emails"] += 1


def load(url: str, built: Path, scope: Scope = "sample") -> dict[str, int]:
    rows = kept_rows(built, scope)
    if any(not r["licenses"] for r in rows):
        raise ValueError("refusing to load rows without a source license")
    counts = {"businesses": len(rows), "phones": 0, "emails": 0}
    with psycopg.connect(url) as conn, conn.transaction(), conn.cursor() as cur:
        _stage(cur, rows, counts)
        # Mirror the scope: drop rows that left it, unless a later stage touched them
        # (crawled, or referenced by an experiment, call outcome, export or suppression).
        cur.execute(
            "delete from businesses b where b.source = 'overture' and b.crawled_at is null"
            " and b.niche_batch is null"
            " and not exists (select 1 from stage_b s where s.source_id = b.source_id)"
            " and not exists (select 1 from experiment_assignments a where a.business_id = b.id)"
            " and not exists (select 1 from outcomes o where o.business_id = b.id)"
            " and not exists (select 1 from export_items x where x.business_id = b.id)"
            " and not exists (select 1 from suppression sp where sp.match_type = 'business_id'"
            "                 and sp.match_value = b.id::text)"
        )
        counts["removed_not_in_scope"] = cur.rowcount
        # Rows kept for history are flagged so the gate never exports them again.
        cur.execute(
            "update businesses b set flags = array_append(b.flags, 'not_in_latest_build')"
            " where b.source = 'overture' and not ('not_in_latest_build' = any(b.flags))"
            " and b.niche_batch is null"
            " and not exists (select 1 from stage_b s where s.source_id = b.source_id)"
        )
        counts["kept_not_in_latest_build"] = cur.rowcount
        cur.execute(
            "update businesses b set in_pilot_sample = false where b.source = 'overture'"
            " and b.in_pilot_sample"
            " and not exists (select 1 from stage_b s where s.source_id = b.source_id)"
        )
        counts["unmarked_not_in_scope"] = cur.rowcount
        cur.execute(_merge_sql())
        cur.execute(
            "update businesses b set flags = array_remove(b.flags, 'not_in_latest_build')"
            " where 'not_in_latest_build' = any(b.flags)"
            " and exists (select 1 from stage_b s where s.source_id = b.source_id)"
        )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("analyze businesses; analyze phones; analyze emails")
    return counts


def additive_rows(
    built: Path,
    existing: tuple[set[str], set[str], set[str]],
    *,
    per_niche: int,
    seed: str,
    subcategories: dict[str, set[str]] | None = None,
    split: set[str] = frozenset(),  # type: ignore[assignment]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The pilot sample for an additive load: kept rows of the built file, minus rows already in
    the database (same source id) or duplicating an existing business (same website domain or
    phone), at most `per_niche` per niche (category, or category:subcategory for `split`),
    in the same stable hash order as the Stage 1 sample."""
    source_ids, domains, phones = existing
    skipped: dict[str, int] = {"already_loaded": 0, "dup_of_existing": 0, "other_subcategory": 0}
    by_niche: dict[str, list[dict[str, Any]]] = {}
    for r in kept_rows(built, "all"):
        allowed = (subcategories or {}).get(r["category"])
        if allowed is not None and r["subcategory"] not in allowed:
            skipped["other_subcategory"] += 1
            continue
        loaded = r["source_id"] in source_ids
        if not loaded and (
            (r["domain"] and r["domain"] in domains) or (r["phone"] and r["phone"] in phones)
        ):
            skipped["dup_of_existing"] += 1
            continue
        key = f"{r['category']}:{r['subcategory']}" if r["category"] in split else r["category"]
        r["_loaded"] = loaded
        by_niche.setdefault(key, []).append(r)
    picked: list[dict[str, Any]] = []
    for key in sorted(by_niche):
        # The same stable sample on every run: re-running never tops the batch up.
        top = sorted(by_niche[key], key=lambda r: sample_key(seed, r["source_id"]))[:per_niche]
        rows = [r for r in top if not r["_loaded"]]
        skipped["already_loaded"] += len(top) - len(rows)
        for r in rows:
            r["in_sample"] = True
        picked += rows
        skipped[f"available:{key}"] = len(by_niche[key])
        skipped[f"picked:{key}"] = len(rows)
    return picked, skipped


def load_additive(
    url: str,
    built: Path,
    batch: str,
    *,
    per_niche: int,
    seed: str,
    subcategories: dict[str, set[str]] | None = None,
    split: set[str] = frozenset(),  # type: ignore[assignment]
) -> dict[str, int]:
    """Add a new niche batch WITHOUT touching anything already loaded: no deletes, no
    `not_in_latest_build` flags, no updates to existing rows (conflicts are skipped)."""
    with psycopg.connect(url) as conn:
        ids = {r[0] for r in conn.execute("select source_id from businesses")}
        doms = {
            r[0] for r in conn.execute("select domain from businesses where domain is not null")
        }
        phs = {r[0] for r in conn.execute("select phone from businesses where phone is not null")}
    rows, counts = additive_rows(
        built, (ids, doms, phs), per_niche=per_niche, seed=seed,
        subcategories=subcategories, split=split,
    )  # fmt: skip
    if any(not r["licenses"] for r in rows):
        raise ValueError("refusing to load rows without a source license")
    counts.update({"businesses": len(rows), "phones": 0, "emails": 0})
    cols = ", ".join(_BUSINESS_COLS)
    with psycopg.connect(url) as conn, conn.transaction(), conn.cursor() as cur:
        _stage(cur, rows, counts)
        cur.execute(
            f"insert into businesses ({cols}, niche_batch) select {cols}, %s from stage_b"  # noqa: S608
            " on conflict (source, source_id) do nothing",
            (batch,),
        )
        counts["inserted"] = cur.rowcount
        cur.execute(
            "insert into phones (business_id, e164, sources, agree_count, valid_format,"
            " number_type_guess, flags, status)"
            " select distinct on (b.id, sp.e164) b.id, sp.e164, array['overture'], 1, sp.valid,"
            " sp.number_type, sp.flags, case when sp.valid then 'unverified' else 'invalid' end"
            " from stage_p sp join businesses b"
            " on b.source = 'overture' and b.source_id = sp.source_id where b.niche_batch = %s"
            " and not exists (select 1 from phones x where x.business_id = b.id)",
            (batch,),
        )
        cur.execute(
            "insert into emails (business_id, email, origin, syntax_ok, is_role_based, is_freemail,"
            " domain_matches_site, flags)"
            " select distinct on (b.id, se.email) b.id, se.email, 'source_listed', se.syntax_ok,"
            " se.is_role_based, se.is_freemail, se.domain_matches_site, se.flags"
            " from stage_e se join businesses b"
            " on b.source = 'overture' and b.source_id = se.source_id where b.niche_batch = %s"
            " and not exists (select 1 from emails x where x.business_id = b.id)",
            (batch,),
        )
        cur.execute(
            "update businesses b set phone_status = case when b.phone is null then 'none' else"
            " coalesce((select p.status from phones p"
            " where p.business_id = b.id and p.e164 = b.phone), 'none') end,"
            " email_status = case when exists"
            " (select 1 from emails e where e.business_id = b.id)"
            " then 'source_listed' else 'none' end"
            " from stage_b s where b.source = s.source and b.source_id = s.source_id"
            " and b.niche_batch = %s and b.crawled_at is null",
            (batch,),
        )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("analyze businesses; analyze phones; analyze emails")
    return counts


def load_sample(url: str, built: Path) -> dict[str, int]:
    return load(url, built, "sample")
