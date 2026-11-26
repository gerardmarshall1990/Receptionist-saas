"""Gate + export (spec section 10). Every export re-runs the gate and re-reads suppression.

Two entry points:
- `check_all`: gate every scored business, store the result on `businesses` (browsing only).
- `run_export`: pick candidates, gate them, write the contract records (Parquet + CSV +
  manifest) and an audit row per candidate in `export_items`.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql
from psycopg.rows import dict_row, tuple_row

from leads_engine.compliance.gate import GateInput, GateResult, evaluate, suppression_hits
from leads_engine.compliance.openers import pick_opener
from leads_engine.config_loader import TIER_RANK, AppConfig, CategoryConfig

# Spec section 10, in order. Tests assert this exact list.
CONTRACT_FIELDS: tuple[str, ...] = (
    "business_id", "name", "category", "country", "state", "city", "tz",
    "phone_e164", "phone_status", "line_type", "email", "email_status", "website",
    "owner_name", "score", "tier", "why_reasons", "opener", "best_call_window",
    "source", "license", "crawled_at", "verified_at", "compliance_status",
)  # fmt: skip

_REASON = pa.struct(
    [("signal", pa.string()), ("evidence", pa.string()), ("source_url", pa.string())]
)
SCHEMA = pa.schema(
    [
        (
            f,
            pa.list_(_REASON)
            if f == "why_reasons"
            else pa.float64()
            if f == "score"
            else pa.string(),
        )
        for f in CONTRACT_FIELDS
    ]
)

# Candidates with everything the gate and the contract need. `{where}` is a Composable
# built from whitelisted fragments (explorer.queries.build_where) with bound parameters.
CANDIDATES = """
select b.id, b.name, b.category, b.country, b.state, b.city, b.tz, b.website, b.domain,
       b.flags, b.source, b.licenses, b.source_datasets, b.crawled_at, b.email_status,
       b.niche_batch, b.subcategory,
       s.total, s.tier, s.dead_reason,
       p.e164, coalesce(p.status, 'none') as phone_status, coalesce(p.flags, '{{}}') as phone_flags,
       p.line_type, p.verified_at,
       e.email, e.origin as email_origin,
       coalesce(r.n, 0) as n_reasons, coalesce(r.reasons, '[]'::jsonb) as reasons
from businesses b
join scores s on s.business_id = b.id
left join phones p on p.business_id = b.id and p.e164 = b.phone
left join lateral (
  select count(*) as n,
         jsonb_agg(jsonb_build_object('signal', x.signal, 'evidence', x.evidence, 'source_url', x.source_url)
                   order by x.weight desc, x.id) as reasons
  from reasons x
  where x.business_id = b.id and x.weight > 0 and coalesce(x.evidence, '') <> '' and x.source_url is not null
) r on true
left join lateral (
  select m.email, m.origin from emails m
  where m.business_id = b.id and m.origin = 'business_site' and m.syntax_ok
  order by m.domain_matches_site desc, m.is_role_based desc, m.email
  limit 1
) e on true
where {where}
"""

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def best_call_window(cat: CategoryConfig | None, tz: str | None) -> str | None:
    """'Mon-Fri 08:00-09:00, 16:00-18:00 (America/Chicago)' from the category config."""
    if cat is None or tz is None:
        return None
    parts = []
    for w in cat.best_call_windows:
        idx = sorted(_DAYS.index(d.value) for d in w.days)
        contiguous = idx == list(range(idx[0], idx[-1] + 1))
        days = (
            f"{_DAYS[idx[0]].title()}-{_DAYS[idx[-1]].title()}"
            if contiguous and len(idx) > 1
            else ",".join(_DAYS[i].title() for i in idx)
        )
        parts.append((days, f"{w.start:%H:%M}-{w.end:%H:%M}"))
    grouped: dict[str, list[str]] = {}
    for days, span in parts:
        grouped.setdefault(days, []).append(span)
    text = "; ".join(f"{d} {', '.join(spans)}" for d, spans in grouped.items())
    return f"{text} ({tz})"


def load_suppression(conn: psycopg.Connection[Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {
        "business_id": set(),
        "phone_e164": set(),
        "email": set(),
        "domain": set(),
    }
    with conn.cursor(row_factory=tuple_row) as cur:
        for mt, mv in cur.execute("select match_type, match_value from suppression"):
            out.setdefault(mt, set()).add(mv.lower() if mt in {"email", "domain"} else mv)
    return out


def gate_input(row: dict[str, Any], suppression: dict[str, set[str]]) -> GateInput:
    return GateInput(
        business_id=str(row["id"]),
        country=row["country"],
        tier=row["tier"],
        dead_reason=row["dead_reason"],
        phone_e164=row["e164"],
        phone_status=row["phone_status"],
        phone_flags=tuple(row["phone_flags"] or ()),
        line_type=row["line_type"],
        business_flags=tuple(row["flags"] or ()),
        source=row["source"],
        licenses=tuple(row["licenses"] or ()),
        n_reasons=row["n_reasons"],
        domain=row["domain"],
        email=row["email"],
        email_origin=row["email_origin"],
        niche_batch=row.get("niche_batch"),
        suppression=suppression,
    )


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def contract_record(row: dict[str, Any], cfg: AppConfig) -> dict[str, Any]:
    """The exported record. Only called for rows that passed the gate."""
    cat = cfg.categories.get(row["category"])
    reasons = [dict(r) for r in row["reasons"][: cfg.compliance.export.max_reasons]]
    # No opener when the listing's name isn't on the website: the caller may reach a firm with
    # another name, and an opener that names the wrong business is worse than none.
    opener = (
        None
        if "name_not_on_site" in (row["flags"] or ())
        else pick_opener(
            cat,
            (r["signal"] for r in row["reasons"]),
            cfg.scoring.signal_weights,
            row["name"],
            row["city"],
        )
    )
    return {
        "business_id": str(row["id"]),
        "name": row["name"],
        "category": row["category"],
        "country": row["country"],
        "state": row["state"],
        "city": row["city"],
        "tz": row["tz"],
        "phone_e164": row["e164"],
        "phone_status": row["phone_status"],
        "line_type": row["line_type"] or "unknown",
        "email": row["email"],
        "email_status": row["email_status"] if row["email"] else "none",
        "website": row["website"],
        "owner_name": None,  # not collected: no public business-role source yet
        "score": float(row["total"]),
        "tier": row["tier"],
        "why_reasons": reasons,
        "opener": opener[0] if opener else None,
        "best_call_window": best_call_window(cat, row["tz"]),
        "source": row["source"],
        "license": ", ".join(row["licenses"]),
        "crawled_at": _iso(row["crawled_at"]),
        "verified_at": _iso(row["verified_at"]),
        "compliance_status": "pass",
    }


def _stream(
    conn: psycopg.Connection[Any], where: sql.Composable, params: list[Any], tail: str = ""
) -> psycopg.ServerCursor[dict[str, Any]]:
    cur = conn.cursor(name=f"gate_{uuid.uuid4().hex[:8]}", row_factory=dict_row)
    cur.itersize = 5_000
    cur.execute(sql.SQL(CANDIDATES).format(where=where) + sql.SQL(tail), params)
    return cur


def gate_one(
    conn: psycopg.Connection[Any], business_id: Any, cfg: AppConfig
) -> dict[str, Any] | None:
    """Live gate for one lead (explorer detail). None if the lead isn't scored."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql.SQL(CANDIDATES).format(where=sql.SQL("b.id = %s")), [business_id])
        row = cur.fetchone()
        if row is None:
            return None
        values = [str(row["id"]), row["e164"], (row["email"] or "").lower(), row["domain"]]
        supp: dict[str, set[str]] = {}
        with conn.cursor(row_factory=tuple_row) as sc:  # callers' pools may default to dict rows
            sc.execute(
                "select match_type, match_value from suppression where match_value = any(%s)",
                (values,),
            )
            for mt, mv in sc:
                supp.setdefault(mt, set()).add(mv)
    res = evaluate(gate_input(row, supp), cfg.compliance)
    opener = contract_record(row, cfg)["opener"] if res.status in ("pass", "pending") else None
    return {
        "status": res.status,
        "blocks": list(res.blocks),
        "warnings": list(res.warnings),
        "opener": opener,
    }


# ---------------------------------------------------------------- check_all


def check_all(url: str, cfg: AppConfig) -> dict[str, Any]:
    """Gate every scored business and store status + blocks on `businesses`."""
    counts: Counter[str] = Counter()
    with psycopg.connect(url) as rconn, psycopg.connect(url) as conn, conn.transaction():
        supp = load_suppression(conn)
        conn.execute(
            "create temp table stage_gate (business_id uuid, status text, blocks text[]) on commit drop"
        )
        cur = _stream(rconn, sql.SQL("true"), [])
        with conn.cursor().copy("copy stage_gate (business_id, status, blocks) from stdin") as cp:
            for row in cur:
                res = evaluate(gate_input(row, supp), cfg.compliance)
                cp.write_row([row["id"], res.status, list(res.blocks)])
                counts[res.status] += 1
                if res.status == "pass":
                    counts[f"pass_tier_{row['tier']}"] += 1
                for b in res.blocks:
                    counts[
                        f"block:{b.split(':')[0] if b.startswith('phone_flag:foreign') else b}"
                    ] += 1
        cur.close()
        conn.execute(
            "update businesses b set compliance_status = g.status, compliance_blocks = g.blocks,"
            " compliance_checked_at = now() from stage_gate g where g.business_id = b.id"
            # Only rows whose result changed: rewriting all 726k rows every refresh bloated the table.
            " and (b.compliance_status is distinct from g.status"
            "      or b.compliance_blocks is distinct from g.blocks)"
        )
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("analyze businesses")
    return dict(counts)


# ---------------------------------------------------------------- export


@dataclass
class ExportResult:
    export_id: str
    counts: dict[str, int]
    blocked_by: dict[str, int]
    path: Path | None
    records: list[dict[str, Any]] = field(default_factory=list)


def tiers_at_or_above(min_tier: str) -> list[str]:
    return [t for t, r in TIER_RANK.items() if r >= TIER_RANK[min_tier]]


def to_csv(records: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(CONTRACT_FIELDS), lineterminator="\n")
    w.writeheader()
    for r in records:
        w.writerow({k: json.dumps(v) if k == "why_reasons" else v for k, v in r.items()})
    return buf.getvalue()


def run_export(
    url: str,
    cfg: AppConfig,
    *,
    where: sql.Composable,
    params: list[Any],
    origin: str,
    out_root: Path,
    run_id: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    request: dict[str, Any] | None = None,
) -> ExportResult:
    """Gate the candidates matched by `where` (alias `b` = businesses) and export the passes.

    Candidates are limited to tiers >= compliance min_tier; the gate checks the tier again.
    """
    export_id = str(uuid.uuid4())
    tiers = tiers_at_or_above(cfg.compliance.export.min_tier)
    full_where = sql.SQL("({}) and s.tier = any(%s)").format(where)
    tail = " order by s.total desc, b.id" + (" limit %s" if limit else "")
    qparams = [*params, tiers, *([limit] if limit else [])]
    counts: Counter[str] = Counter()
    blocked_by: Counter[str] = Counter()
    warned: Counter[str] = Counter()
    attributions: set[str] = set()
    items: list[tuple[GateInput, GateResult, dict[str, Any] | None]] = []

    with psycopg.connect(url) as rconn, psycopg.connect(url) as conn:
        supp = load_suppression(conn)
        cur = _stream(rconn, full_where, qparams, tail)
        for row in cur:
            inp = gate_input(row, supp)
            res = evaluate(inp, cfg.compliance)
            rec = None
            if res.status == "pass":
                rec = contract_record(row, cfg)
                attributions.update(row["source_datasets"] or ())
            items.append((inp, res, rec))
        cur.close()

        with conn.transaction():
            # Re-read suppression at write time: an opt-out recorded while we gated still wins.
            fresh = load_suppression(conn)
            final: list[tuple[str, GateResult, dict[str, Any] | None]] = []
            for inp, res, rec in items:
                bid = inp.business_id
                if rec is not None:
                    hits = suppression_hits(replace(inp, suppression=fresh))
                    if hits:
                        res, rec = GateResult("blocked", tuple(hits), res.warnings), None
                final.append((bid, res, rec))
                counts["candidates"] += 1
                counts[res.status] += 1
                for b in res.blocks:
                    blocked_by[
                        "phone_flag:foreign" if b.startswith("phone_flag:foreign") else b
                    ] += 1
                if rec is not None:
                    warned.update(res.warnings)
            records = [rec for _, _, rec in final if rec is not None]
            path: Path | None = None
            if not dry_run:
                path = _write_files(
                    out_root,
                    export_id,
                    records,
                    cfg,
                    counts,
                    blocked_by,
                    warned,
                    attributions,
                    request or {},
                )
                conn.execute(
                    "insert into exports (id, run_id, origin, params, config_hash, compliance_version,"
                    " n_candidates, n_passed, n_blocked, path) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (export_id, run_id, origin, json.dumps(request or {}, default=str), cfg.config_hash,
                     cfg.compliance.version, counts["candidates"], counts["pass"], counts["blocked"],
                     str(path)),
                )  # fmt: skip
                with conn.cursor().copy(
                    "copy export_items (export_id, business_id, status, blocks, warnings, row) from stdin"
                ) as cp:
                    for bid, res, rec in final:
                        cp.write_row([export_id, bid, res.status, list(res.blocks), list(res.warnings),
                                      json.dumps(rec) if rec else None])  # fmt: skip
    counts.update({f"warning:{k}": v for k, v in warned.items()})
    return ExportResult(export_id, dict(counts), dict(blocked_by), path, records)


def _write_files(
    out_root: Path,
    export_id: str,
    records: list[dict[str, Any]],
    cfg: AppConfig,
    counts: Counter[str],
    blocked_by: Counter[str],
    warned: Counter[str],
    attributions: set[str],
    request: dict[str, Any],
) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = out_root / f"{stamp}-{export_id[:8]}"
    out.mkdir(parents=True, exist_ok=False)
    pq.write_table(pa.Table.from_pylist(records, schema=SCHEMA), out / "leads.parquet")
    (out / "leads.csv").write_text(to_csv(records), encoding="utf-8")
    manifest = {
        "export_id": export_id,
        "created_at": datetime.now(UTC).isoformat(),
        "contract": list(CONTRACT_FIELDS),
        "counts": dict(counts),
        "blocked_by": dict(blocked_by),
        "request": request,
        "config_hash": cfg.config_hash,
        "compliance_version": cfg.compliance.version,
        "calling_mode": cfg.compliance.calling_mode,
        "warnings_on_passed_rows": dict(warned),
        "needs_counsel": cfg.compliance.needs_counsel,
        "attribution": (
            f"Overture Maps Foundation (overturemaps.org), release {cfg.global_.overture_release}."
            f" Contributing datasets: {', '.join(sorted(attributions)) or 'n/a'}."
            " Per-record licenses are in the `license` column."
        ),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    return out


def prune_exports(url: str, keep: int) -> dict[str, int]:
    """Drop items and data files of CLI exports older than the newest `keep`. Explorer exports
    (a person downloaded them) are never pruned; header rows and manifest.json always stay."""
    with psycopg.connect(url, row_factory=tuple_row) as conn:
        old = conn.execute(
            "select id, path from exports where origin = 'cli' and pruned_at is null"
            " and not protected and id not in"
            " (select id from exports where origin = 'cli' order by created_at desc limit %s)",
            (max(keep, 1),),
        ).fetchall()
        files = items = 0
        for export_id, path in old:
            items += conn.execute(
                "delete from export_items where export_id = %s", (export_id,)
            ).rowcount
            for name in ("leads.parquet", "leads.csv"):
                f = Path(path) / name if path else None
                if f and f.exists():
                    f.unlink()
                    files += 1
            conn.execute("update exports set pruned_at = now() where id = %s", (export_id,))
    return {"exports": len(old), "items": items, "files": files}


# ---------------------------------------------------------------- merge

MERGE_FIELDS: tuple[str, ...] = (*CONTRACT_FIELDS, "niche_batch", "subcategory")
MERGE_SCHEMA = pa.schema(
    [*SCHEMA, pa.field("niche_batch", pa.string()), pa.field("subcategory", pa.string())]
)
BASE_BATCH = "core-7"


@dataclass
class MergeResult:
    export_id: str
    path: Path
    counts: dict[str, int]


def merge_export(
    url: str, cfg: AppConfig, *, base: Path, batch: str, out_root: Path, run_id: str | None = None
) -> MergeResult:
    """One file in the export contract (+ `niche_batch`, `subcategory`): the base export's rows
    exactly as exported, plus the batch's gated rows. Batch rows are `pending` (never `pass`)
    while the batch is in compliance `pending_batches`. Suppression is re-read at write time and
    always wins, for base rows too."""
    base_manifest = json.loads((base / "manifest.json").read_text())
    base_rows = pq.read_table(base / "leads.parquet").to_pylist()
    if any(r["compliance_status"] != "pass" for r in base_rows):
        raise ValueError("base export holds rows that are not 'pass'")
    tiers = tiers_at_or_above(cfg.compliance.export.min_tier)
    counts: Counter[str] = Counter()
    blocked_by: Counter[str] = Counter()
    batch_items: list[tuple[str, GateResult, dict[str, Any] | None]] = []
    attributions: set[str] = set()
    with psycopg.connect(url) as rconn, psycopg.connect(url) as conn:
        supp = load_suppression(conn)
        cur = _stream(
            rconn,
            sql.SQL("b.niche_batch = %s and s.tier = any(%s)"),
            [batch, tiers],
            " order by s.total desc, b.id",
        )
        for row in cur:
            res = evaluate(gate_input(row, supp), cfg.compliance)
            rec = None
            if res.status in ("pass", "pending"):
                rec = {**contract_record(row, cfg), "compliance_status": res.status,
                       "niche_batch": batch, "subcategory": row["subcategory"]}  # fmt: skip
                attributions.update(row["source_datasets"] or ())
            for b in res.blocks:
                blocked_by[b] += 1
            counts[f"batch_{res.status}"] += 1
            batch_items.append((str(row["id"]), res, rec))
        cur.close()

        export_id = str(uuid.uuid4())
        with conn.transaction():
            fresh = load_suppression(conn)

            def suppressed(r: dict[str, Any]) -> bool:
                return bool(suppression_hits(GateInput(
                    business_id=r["business_id"], country=r["country"], tier=r["tier"],
                    dead_reason=None, phone_e164=r["phone_e164"], phone_status=r["phone_status"],
                    phone_flags=(), line_type=r["line_type"], business_flags=(), source=r["source"],
                    licenses=(), n_reasons=0, domain=None, email=r["email"], suppression=fresh,
                )))  # fmt: skip

            records: list[dict[str, Any]] = []
            for r in base_rows:
                if suppressed(r):
                    counts["base_dropped_suppressed"] += 1
                    continue
                records.append({**r, "niche_batch": BASE_BATCH, "subcategory": None})
                counts["base_rows"] += 1
            final: list[tuple[str, GateResult, dict[str, Any] | None]] = []
            for bid, res, rec in batch_items:
                if rec is not None and suppressed(rec):
                    res, rec = GateResult("blocked", ("suppressed",), res.warnings), None
                    counts["batch_dropped_suppressed"] += 1
                if rec is not None:
                    records.append(rec)
                    counts["batch_rows"] += 1
                final.append((bid, res, rec))
            counts["total_rows"] = len(records)

            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            out = out_root / f"{stamp}-{export_id[:8]}"
            out.mkdir(parents=True, exist_ok=False)
            pq.write_table(
                pa.Table.from_pylist(records, schema=MERGE_SCHEMA), out / "leads.parquet"
            )
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(MERGE_FIELDS), lineterminator="\n")
            w.writeheader()
            for r in records:
                w.writerow(
                    {k: json.dumps(r[k]) if k == "why_reasons" else r[k] for k in MERGE_FIELDS}
                )
            (out / "leads.csv").write_text(buf.getvalue(), encoding="utf-8")
            manifest = {
                "export_id": export_id,
                "created_at": datetime.now(UTC).isoformat(),
                "kind": "merge",
                "base_export_id": base_manifest["export_id"],
                "batch": batch,
                "contract": list(MERGE_FIELDS),
                "counts": dict(counts),
                "blocked_by": dict(blocked_by),
                "config_hash": cfg.config_hash,
                "compliance_version": cfg.compliance.version,
                "calling_mode": cfg.compliance.calling_mode,
                "pending_means": "passed every current gate check; DNC, line type and country rules"
                " not checked yet for this batch. Not cleared for calling.",
                "needs_counsel": cfg.compliance.needs_counsel,
                "attribution": base_manifest["attribution"]
                + (
                    f" Batch {batch} datasets: {', '.join(sorted(attributions))}."
                    if attributions
                    else ""
                ),
            }
            (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
            conn.execute(
                "insert into exports (id, run_id, origin, params, config_hash, compliance_version,"
                " n_candidates, n_passed, n_blocked, path, protected)"
                " values (%s,%s,'cli',%s,%s,%s,%s,%s,%s,%s,true)",
                (export_id, run_id, json.dumps({"merge": True, "base": base_manifest["export_id"],
                 "batch": batch}), cfg.config_hash, cfg.compliance.version, len(batch_items),
                 counts["base_rows"], counts["batch_blocked"], str(out)),
            )  # fmt: skip
            conn.execute(
                "update exports set protected = true where id = %s", (base_manifest["export_id"],)
            )
            with conn.cursor().copy(
                "copy export_items (export_id, business_id, status, blocks, warnings, row) from stdin"
            ) as cp:
                for bid, res, rec in final:
                    cp.write_row([export_id, bid, res.status, list(res.blocks), list(res.warnings),
                                  json.dumps(rec) if rec else None])  # fmt: skip
    return MergeResult(export_id, out, dict(counts))
