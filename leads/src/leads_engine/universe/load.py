"""Load the pilot sample from the built Parquet into Postgres (idempotent upsert)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import psycopg
from psycopg import sql

from leads_engine.universe.overture import LICENSE_NOTE

_BUSINESS_COLS = (
    "source", "source_id", "source_release", "name", "name_key", "category", "subcategory",
    "source_category_code", "country", "state", "city", "address", "postcode", "lat", "lng", "tz",
    "phone", "website", "domain", "location_count", "flags", "source_confidence", "source_datasets",
    "licenses", "attribution", "merged_source_ids", "in_pilot_sample",
)  # fmt: skip


def attribution(release: str, datasets: list[str]) -> str:
    srcs = ", ".join(datasets) if datasets else "Overture"
    return f"{LICENSE_NOTE}. Release {release}. Contributing datasets: {srcs}."


def sample_rows(built: Path) -> list[dict[str, Any]]:
    con = duckdb.connect()
    q = "select * from read_parquet($p) where in_sample and exclusion is null and dup_of is null"
    rows: list[dict[str, Any]] = con.sql(q, params={"p": str(built)}).to_arrow_table().to_pylist()
    return rows


def _business_values(r: dict[str, Any]) -> dict[str, Any]:
    vals = {c: r.get(c) for c in _BUSINESS_COLS}
    vals["source_confidence"] = r["confidence"]
    vals["attribution"] = attribution(r["source_release"], r["source_datasets"])
    vals["in_pilot_sample"] = True
    return vals


_UPSERT = sql.SQL(
    "insert into businesses ({cols}) values ({vals})"
    " on conflict (source, source_id) do update set {updates}, updated_at = now()"
    " returning id"
).format(
    cols=sql.SQL(", ").join(map(sql.Identifier, _BUSINESS_COLS)),
    vals=sql.SQL(", ").join(map(sql.Placeholder, _BUSINESS_COLS)),
    updates=sql.SQL(", ").join(
        sql.SQL("{c} = excluded.{c}").format(c=sql.Identifier(c))
        for c in _BUSINESS_COLS
        if c not in {"source", "source_id"}
    ),
)
_INSERT_PHONE = (
    "insert into phones (business_id, e164, sources, agree_count, valid_format,"
    " number_type_guess, flags, status) values (%s, %s, %s, 1, %s, %s, %s, %s)"
    " on conflict do nothing"
)
_INSERT_EMAIL = (
    "insert into emails (business_id, email, origin, syntax_ok, is_role_based,"
    " is_freemail, domain_matches_site, flags)"
    " values (%s, %s, 'source_listed', %s, %s, %s, %s, %s) on conflict do nothing"
)


def load_sample(url: str, built: Path) -> dict[str, int]:
    rows = sample_rows(built)
    if any(not r["licenses"] for r in rows):
        raise ValueError("refusing to load rows without a source license")
    counts = {"businesses": 0, "phones": 0, "emails": 0}
    with psycopg.connect(url) as conn, conn.transaction(), conn.cursor() as cur:
        # Rows that left the sample (e.g. a config change now excludes them): delete
        # them if no later stage has touched them, otherwise just un-mark them.
        ids = [r["source_id"] for r in rows]
        cur.execute(
            "delete from businesses where in_pilot_sample and crawled_at is null"
            " and not (source_id = any(%s))",
            (ids,),
        )
        counts["removed_from_previous_sample"] = cur.rowcount
        cur.execute(
            "update businesses set in_pilot_sample = false"
            " where in_pilot_sample and not (source_id = any(%s))",
            (ids,),
        )
        counts["unmarked_from_previous_sample"] = cur.rowcount
        for r in rows:
            cur.execute(_UPSERT, _business_values(r))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"upsert returned no id for {r['source_id']}")
            bid = row[0]
            counts["businesses"] += 1
            # Contacts are replaced wholesale so a rerun converges to the Parquet.
            cur.execute("delete from phones where business_id = %s", (bid,))
            cur.execute(
                "delete from emails where business_id = %s and origin = 'source_listed'", (bid,)
            )
            for p in r["phones"]:
                if not p["e164"]:
                    continue  # unparseable/invalid numbers stay in Parquet only
                status = "unverified" if p["valid"] else "invalid"
                cur.execute(
                    _INSERT_PHONE,
                    (
                        bid,
                        p["e164"],
                        ["overture"],
                        p["valid"],
                        p["number_type"],
                        p["flags"],
                        status,
                    ),
                )
                counts["phones"] += 1
            for e in r["emails"]:
                cur.execute(
                    _INSERT_EMAIL,
                    (bid, e["email"], e["syntax_ok"], e["is_role_based"], e["is_freemail"],
                     e["domain_matches_site"], e["flags"]),
                )  # fmt: skip
                counts["emails"] += 1
    return counts
