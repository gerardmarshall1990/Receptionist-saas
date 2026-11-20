"""`leads compliance|export|suppress ...` (Stage 5). $0: no paid API, no LLM."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from leads_engine.config_loader import load_config
from leads_engine.settings import get_settings
from leads_engine.universe.normalize import normalize_phone

MATCH_TYPES = ("phone_e164", "email", "domain", "business_id")


def normalize_match(match_type: str, value: str) -> str:
    """Store suppression values in the same form the gate compares against."""
    v = value.strip()
    if match_type == "phone_e164":
        ph = normalize_phone(v)
        if ph is None or ph.e164 is None:
            raise ValueError(f"not a valid phone number: {value!r}")
        return ph.e164
    if match_type == "email":
        if "@" not in v:
            raise ValueError(f"not an email: {value!r}")
        return v.lower()
    if match_type == "domain":
        v = v.lower().removeprefix("http://").removeprefix("https://").split("/")[0]
        return v.removeprefix("www.")
    if match_type == "business_id":
        import uuid

        return str(uuid.UUID(v))
    raise ValueError(f"unknown match type {match_type!r}; use one of {MATCH_TYPES}")


def add_suppressions(url: str, rows: list[tuple[str, str, str]], source: str) -> int:
    """Insert (match_type, value, reason) rows. Existing rows are left alone (permanent)."""
    added = 0
    with psycopg.connect(url) as c, c.transaction():
        for mt, mv, reason in rows:
            cur = c.execute(
                "insert into suppression (match_type, match_value, reason, source)"
                " values (%s, %s, %s, %s) on conflict do nothing",
                (mt, normalize_match(mt, mv), reason, source),
            )
            added += cur.rowcount
        # Browsing status follows at once; exports re-check suppression regardless.
        c.execute(
            "update businesses b set compliance_status = 'blocked',"
            " compliance_blocks = array_append(compliance_blocks, 'suppressed')"
            " where compliance_status = 'pass' and exists (select 1 from suppression s where"
            " (s.match_type = 'business_id' and s.match_value = b.id::text)"
            " or (s.match_type = 'phone_e164' and s.match_value = b.phone)"
            " or (s.match_type = 'domain' and s.match_value = b.domain))"
        )
    return added


def _suppress_add(args: argparse.Namespace) -> int:
    n = add_suppressions(
        get_settings().database_url, [(args.type, args.value, args.reason)], args.source
    )
    print(f"suppression rows added: {n}")
    return 0


def _suppress_import(args: argparse.Namespace) -> int:
    """CSV with columns match_type,match_value[,reason]. Bad rows stop the import."""
    rows: list[tuple[str, str, str]] = []
    with Path(args.file).open(newline="", encoding="utf-8") as fh:
        for i, rec in enumerate(csv.DictReader(fh), start=2):
            mt, mv = (rec.get("match_type") or "").strip(), (rec.get("match_value") or "").strip()
            if mt not in MATCH_TYPES or not mv:
                raise ValueError(f"line {i}: need match_type in {MATCH_TYPES} and match_value")
            normalize_match(mt, mv)  # validate before writing anything
            rows.append((mt, mv, (rec.get("reason") or args.reason).strip()))
    n = add_suppressions(get_settings().database_url, rows, args.source)
    print(f"read {len(rows)} rows, added {n} (others already suppressed)")
    return 0


def _compliance_check(args: argparse.Namespace) -> int:
    from leads_engine.compliance.export import check_all
    from leads_engine.runs import recorded_run

    s = get_settings()
    cfg = load_config(s.config_dir)
    with recorded_run(
        s.database_url, "stage5.compliance", cfg.config_hash,
        dry_run=False, budget_usd=s.run_budget_usd, params={"version": cfg.compliance.version},
    ) as run:  # fmt: skip
        run.counts = check_all(s.database_url, cfg)
    for k, v in sorted(run.counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<45} {v:>8,}")
    return 0


def export_where(categories: list[str], states: list[str]) -> tuple[Any, list[Any]]:
    from leads_engine.explorer import queries as qx

    s = get_settings()
    cfg = load_config(s.config_dir)
    unknown = sorted(set(categories) - set(cfg.categories))
    if unknown:
        raise ValueError(f"unknown categories: {unknown}")
    w = qx.build_where(qx.Filters(category=tuple(categories), state=tuple(states)), cfg)
    return w.sql(), w.params


def _export_run(args: argparse.Namespace) -> int:
    from leads_engine.compliance.export import run_export
    from leads_engine.runs import recorded_run

    s = get_settings()
    cfg = load_config(s.config_dir)
    cats = [c for c in (args.categories or "").split(",") if c]
    states = [x.upper() for x in (args.states or "").split(",") if x]
    where, params = export_where(cats, states)
    request = {"categories": cats, "states": states, "limit": args.limit}
    with recorded_run(
        s.database_url, "stage5.export", cfg.config_hash,
        dry_run=args.dry_run, budget_usd=s.run_budget_usd, params=request,
    ) as run:  # fmt: skip
        res = run_export(
            s.database_url, cfg, where=where, params=params, origin="cli",
            out_root=s.data_dir / "exports", run_id=run.id, limit=args.limit,
            dry_run=args.dry_run, request=request,
        )  # fmt: skip
        run.counts = {**res.counts, "export_id": res.export_id}
    print(f"export {res.export_id}{' (dry run, nothing written)' if args.dry_run else ''}")
    for k, v in sorted(res.counts.items()):
        print(f"  {k:<30} {v:>8,}")
    print("blocked by:")
    for k, v in sorted(res.blocked_by.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<45} {v:>8,}")
    if res.path:
        print(f"files: {res.path}")
    return 0


def _export_list(args: argparse.Namespace) -> int:
    with psycopg.connect(get_settings().database_url) as c:
        rows = c.execute(
            "select id, origin, created_at, n_candidates, n_passed, n_blocked, path from exports"
            " order by created_at desc limit 20"
        ).fetchall()
    for r in rows:
        print(
            f"{r[2]:%Y-%m-%d %H:%M} {r[1]:<8} {r[0]} cand={r[3]} pass={r[4]} blocked={r[5]} {r[6]}"
        )
    return 0


COMMANDS: dict[tuple[str, str], Callable[[argparse.Namespace], int]] = {
    ("compliance", "check"): _compliance_check,
    ("export", "run"): _export_run,
    ("export", "list"): _export_list,
    ("suppress", "add"): _suppress_add,
    ("suppress", "import"): _suppress_import,
}


def add_parser(groups: Any) -> None:
    cp = groups.add_parser("compliance", help="Stage 5: compliance gate").add_subparsers(
        dest="command", required=True
    )
    cp.add_parser("check", help="gate every scored business; store status for the explorer")
    ep = groups.add_parser("export", help="Stage 5: gated export").add_subparsers(
        dest="command", required=True
    )
    run = ep.add_parser("run", help="gate candidates (tier >= compliance min_tier) and export")
    run.add_argument("--categories", help="comma-separated category slugs")
    run.add_argument("--states", help="comma-separated state codes")
    run.add_argument("--limit", type=int, help="top N candidates by score")
    run.add_argument("--dry-run", action="store_true", help="gate and count; write nothing")
    ep.add_parser("list", help="recent exports")
    sp = groups.add_parser("suppress", help="permanent suppression list").add_subparsers(
        dest="command", required=True
    )
    add = sp.add_parser("add", help="suppress one phone/email/domain/business id")
    add.add_argument("--type", required=True, choices=MATCH_TYPES)
    add.add_argument("--value", required=True)
    add.add_argument("--reason", required=True)
    add.add_argument("--source", default="cli")
    imp = sp.add_parser("import", help="CSV: match_type,match_value[,reason]")
    imp.add_argument("file")
    imp.add_argument("--reason", default="opt-out list")
    imp.add_argument("--source", required=True, help="where the list came from")
