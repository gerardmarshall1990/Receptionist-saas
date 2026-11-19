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


def load(url: str, built: Path, scope: Scope = "sample") -> dict[str, int]:
    rows = kept_rows(built, scope)
    if any(not r["licenses"] for r in rows):
        raise ValueError("refusing to load rows without a source license")
    counts = {"businesses": len(rows), "phones": 0, "emails": 0}
    with psycopg.connect(url) as conn, conn.transaction(), conn.cursor() as cur:
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
        # Mirror the scope: drop rows that left it, unless a later stage touched them.
        cur.execute(
            "delete from businesses b where b.source = 'overture' and b.crawled_at is null"
            " and not exists (select 1 from stage_b s where s.source_id = b.source_id)"
        )
        counts["removed_not_in_scope"] = cur.rowcount
        cur.execute(
            "update businesses b set in_pilot_sample = false where b.source = 'overture'"
            " and b.in_pilot_sample"
            " and not exists (select 1 from stage_b s where s.source_id = b.source_id)"
        )
        counts["unmarked_not_in_scope"] = cur.rowcount
        cur.execute(_merge_sql())
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("analyze businesses; analyze phones; analyze emails")
    return counts


def load_sample(url: str, built: Path) -> dict[str, int]:
    return load(url, built, "sample")
