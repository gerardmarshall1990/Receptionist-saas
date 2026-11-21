"""Durable experiment record: snapshot to files in git, restore onto a fresh database.

The database is the working copy; containers are ephemeral. A snapshot keeps only what
reproduces the experiment: stable source IDs (Overture, pinned release), arm, call order,
seed and params, plus logged call outcomes WITHOUT free-text notes. No names, phones or emails.
Restore maps source IDs back to business IDs and refuses if any lead is missing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import psycopg

ASSIGN_FIELDS = ("call_order", "arm", "source", "source_id", "source_release", "tier_at_assignment",
                 "score_at_assignment")  # fmt: skip
OUTCOME_FIELDS = ("source", "source_id", "called_at", "caller", "disposition", "demo_booked", "trial_started",
                  "paid", "source_system")  # fmt: skip


def snapshot(url: str, name: str, out_root: Path) -> Path:
    with psycopg.connect(url) as c:
        exp = c.execute(
            "select id, name, status, seed, params, config_hash, created_at from experiments where name = %s",
            (name,),
        ).fetchone()
        if exp is None:
            raise ValueError(f"no experiment {name!r}")
        assigns = c.execute(
            "select a.call_order, a.arm, b.source, b.source_id, b.source_release, a.tier_at_assignment,"
            " a.score_at_assignment from experiment_assignments a join businesses b on b.id = a.business_id"
            " where a.experiment_id = %s order by a.call_order",
            (exp[0],),
        ).fetchall()
        outcomes = c.execute(
            "select b.source, b.source_id, o.called_at, o.caller, o.disposition, o.demo_booked, o.trial_started,"
            " o.paid, o.source from outcomes o join businesses b on b.id = o.business_id"
            " where o.experiment_id = %s order by o.called_at, b.source_id",
            (exp[0],),
        ).fetchall()
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    meta = {"name": exp[1], "status": exp[2], "seed": exp[3], "params": exp[4], "config_hash": exp[5],
            "created_at": exp[6].isoformat(), "assignments": len(assigns), "outcomes": len(outcomes)}  # fmt: skip
    (out / "experiment.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for fname, fields, rows in (
        ("assignments.csv", ASSIGN_FIELDS, assigns),
        ("outcomes.csv", OUTCOME_FIELDS, outcomes),
    ):
        with (out / fname).open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(fields)
            for r in rows:
                w.writerow([v.isoformat() if hasattr(v, "isoformat") else v for v in r])
    return out


def restore(url: str, snap_dir: Path) -> dict[str, Any]:
    meta = json.loads((snap_dir / "experiment.json").read_text(encoding="utf-8"))
    with (snap_dir / "assignments.csv").open(encoding="utf-8") as fh:
        assigns = list(csv.DictReader(fh))
    with (snap_dir / "outcomes.csv").open(encoding="utf-8") as fh:
        outcomes = list(csv.DictReader(fh))
    with psycopg.connect(url) as c, c.transaction():
        if c.execute("select 1 from experiments where name = %s", (meta["name"],)).fetchone():
            raise ValueError(f"experiment {meta['name']!r} already exists here; nothing restored")
        keys = {(r["source"], r["source_id"]) for r in assigns}
        ids = {
            (s, sid): bid
            for s, sid, bid in c.execute(
                "select source, source_id, id from businesses where (source, source_id) in"
                " (select * from unnest(%s::text[], %s::text[]))",
                ([k[0] for k in keys], [k[1] for k in keys]),
            )
        }
        missing = sorted(keys - set(ids))
        if missing:
            raise ValueError(
                f"{len(missing)} of {len(keys)} leads are not in this database (load the same Overture release"
                f" and categories first); e.g. {missing[:3]}"
            )
        exp_id = c.execute(
            "insert into experiments (id, name, status, seed, params, config_hash, created_at)"
            " values (gen_random_uuid(), %s, %s, %s, %s, %s, %s) returning id",
            (meta["name"], meta["status"], meta["seed"], json.dumps(meta["params"]), meta["config_hash"],
             meta["created_at"]),
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
        with c.cursor().copy(
            "copy experiment_assignments (experiment_id, business_id, arm, tier_at_assignment,"
            " score_at_assignment, call_order) from stdin"
        ) as cp:
            for r in assigns:
                cp.write_row([exp_id, ids[(r["source"], r["source_id"])], r["arm"], r["tier_at_assignment"] or None,
                              r["score_at_assignment"] or None, int(r["call_order"])])  # fmt: skip
        for o in outcomes:
            c.execute(
                "insert into outcomes (experiment_id, business_id, called_at, caller, disposition, demo_booked,"
                " trial_started, paid, source) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (business_id, called_at) do nothing",
                (exp_id, ids[(o["source"], o["source_id"])], o["called_at"], o["caller"], o["disposition"],
                 o["demo_booked"] == "True", o["trial_started"] == "True", o["paid"] == "True", o["source_system"]),
            )  # fmt: skip
    # Opt-outs logged as call outcomes are re-applied: suppression must survive a rebuild.
    dnc = [
        ids[(o["source"], o["source_id"])] for o in outcomes if o["disposition"] == "do_not_call"
    ]
    added = 0
    if dnc:
        from leads_engine.compliance_commands import add_suppressions

        with psycopg.connect(url) as c:
            phones: dict[Any, str] = {
                bid: ph
                for bid, ph in c.execute(
                    "select id, phone from businesses where id = any(%s)", (dnc,)
                )
                if ph
            }
        rows = [
            ("business_id", str(b), "asked not to be called (call outcome, restored)") for b in dnc
        ]
        rows += [
            ("phone_e164", phones[b], "asked not to be called (call outcome, restored)")
            for b in dnc
            if b in phones
        ]
        added = add_suppressions(url, rows, "experiment-restore")
    return {
        "experiment": meta["name"],
        "assignments": len(assigns),
        "outcomes": len(outcomes),
        "suppression_rows_added": added,
    }
