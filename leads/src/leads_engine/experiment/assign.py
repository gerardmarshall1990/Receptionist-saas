"""Create a pilot experiment: pick both arms, freeze them, write the blinded call list.

- Scored arm: leads passing the Stage 5 export gate in `scored_tiers`.
- Control arm: a random draw from every lead passing the gate's `control` profile (no score
  information used; safety rules kept), excluding scored-arm picks.
- Both arms get the SAME category quotas (the scored pool's mix), so category can't explain
  a difference. Selection and call order come from sha256(seed + id): reproducible.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from leads_engine.compliance.export import (
    CANDIDATES,
    best_call_window,
    gate_input,
    load_suppression,
)
from leads_engine.compliance.gate import GateInput, evaluate, suppression_hits
from leads_engine.config_loader import AppConfig

CALL_LIST_FIELDS = ("call_order", "lead_ref", "name", "category", "phone_e164", "city", "state",
                    "tz", "best_call_window")  # fmt: skip
# No website column: every scored lead has one but ~1 in 5 control leads don't, so a blank
# website would tell callers which arm a lead is in.
OUTCOME_FIELDS = ("lead_ref", "called_at", "caller", "disposition", "demo_booked", "trial_started",
                  "paid", "notes")  # fmt: skip


@dataclass(frozen=True)
class Lead:
    inp: GateInput
    category: str
    tier: str
    score: float
    row: dict[str, Any]


def _key(seed: str, salt: str, business_id: str) -> str:
    return hashlib.sha256(f"{seed}|{salt}|{business_id}".encode()).hexdigest()


def quotas(pool_counts: Counter[str], per_arm: int) -> dict[str, int]:
    """Split per_arm across categories in proportion to the pool (largest remainder)."""
    total = sum(pool_counts.values())
    if total == 0:
        return {}
    raw = {c: per_arm * n / total for c, n in pool_counts.items()}
    out = {c: int(v) for c, v in raw.items()}
    for c in sorted(raw, key=lambda c: (-(raw[c] - out[c]), c))[: per_arm - sum(out.values())]:
        out[c] += 1
    return out


def _pools(url: str, cfg: AppConfig) -> tuple[list[Lead], list[Lead], dict[str, set[str]]]:
    scored: list[Lead] = []
    control: list[Lead] = []
    tiers = set(cfg.experiment.scored_tiers)
    with psycopg.connect(url) as rconn, psycopg.connect(url) as conn:
        supp = load_suppression(conn)
        cur = rconn.cursor(name="exp_pool", row_factory=dict_row)
        cur.itersize = 5_000
        cur.execute(sql.SQL(CANDIDATES).format(where=sql.SQL("true")))
        keep = ("id", "name", "category", "e164", "city", "state", "tz", "website")
        for row in cur:
            inp = gate_input(row, supp)
            lead = Lead(inp, row["category"], row["tier"], float(row["total"]),
                        {k: row[k] for k in keep})  # fmt: skip
            if row["tier"] in tiers and evaluate(inp, cfg.compliance).status == "pass":
                scored.append(lead)
            if evaluate(inp, cfg.compliance, "control").status == "pass":
                control.append(lead)
        cur.close()
    return scored, control, supp


def _pick(pool: list[Lead], quota: dict[str, int], seed: str, arm: str) -> list[Lead]:
    picked: list[Lead] = []
    for cat, n in sorted(quota.items()):
        ranked = sorted((x for x in pool if x.category == cat),
                        key=lambda x: _key(seed, arm, x.inp.business_id))  # fmt: skip
        if len(ranked) < n:
            raise ValueError(f"{arm} arm: only {len(ranked)} eligible {cat} leads, need {n}")
        picked += ranked[:n]
    return picked


def create(
    url: str, cfg: AppConfig, name: str, seed: str, out_root: Path, per_arm: int | None = None
) -> dict[str, Any]:
    ex = cfg.experiment
    n = per_arm or ex.per_arm
    with psycopg.connect(url) as c:
        if c.execute("select 1 from experiments where name = %s", (name,)).fetchone():
            raise ValueError(f"experiment {name!r} already exists (assignments are frozen)")

    scored_pool, control_pool, _ = _pools(url, cfg)
    q = quotas(Counter(x.category for x in scored_pool), n)
    scored = _pick(scored_pool, q, seed, "scored")
    taken = {x.inp.business_id for x in scored}
    control = _pick([x for x in control_pool if x.inp.business_id not in taken], q, seed, "control")

    arms = [("scored", x) for x in scored] + [("control", x) for x in control]
    arms.sort(key=lambda a: _key(seed, "order", a[1].inp.business_id))
    exp_id = str(uuid.uuid4())
    params = {**ex.model_dump(), "per_arm": n, "quotas": q,
              "compliance_version": cfg.compliance.version,
              "pool_sizes": {"scored": len(scored_pool), "control": len(control_pool)}}  # fmt: skip

    with psycopg.connect(url) as conn, conn.transaction():
        # An opt-out recorded while we drew the arms still wins: nobody suppressed is assigned.
        fresh = load_suppression(conn)
        hits = [lead.inp.business_id for _, lead in arms
                if suppression_hits(replace(lead.inp, suppression=fresh))]  # fmt: skip
        if hits:
            raise ValueError(f"{len(hits)} picked leads were suppressed meanwhile; re-run create")
        conn.execute(
            "insert into experiments (id, name, seed, params, config_hash) values (%s, %s, %s, %s, %s)",
            (exp_id, name, seed, json.dumps(params), cfg.config_hash),
        )
        with conn.cursor().copy(
            "copy experiment_assignments (experiment_id, business_id, arm, tier_at_assignment,"
            " score_at_assignment, call_order) from stdin"
        ) as cp:
            for i, (arm, lead) in enumerate(arms, start=1):
                cp.write_row([exp_id, lead.inp.business_id, arm, lead.tier, lead.score, i])
    path = write_call_list(url, cfg, name, out_root)
    return {
        "experiment_id": exp_id, "per_arm": n, "quotas": q, "path": str(path),
        "pool_scored": len(scored_pool), "pool_control": len(control_pool),
        "scored_tiers": dict(Counter(x.tier for x in scored)),
    }  # fmt: skip


def write_call_list(url: str, cfg: AppConfig, name: str, out_root: Path) -> Path:
    """(Re)write the blinded call list from the frozen assignments, minus anyone suppressed since.

    No arm, tier, score, reason or opener is on the list: callers can't tell the arms apart.
    """
    with psycopg.connect(url) as conn:
        exp = conn.execute("select id, params from experiments where name = %s", (name,)).fetchone()
        if exp is None:
            raise ValueError(f"no experiment {name!r}")
        supp = load_suppression(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "select a.call_order, b.id, b.name, b.category, b.phone, b.city, b.state, b.tz,"
                " b.website, b.domain from experiment_assignments a join businesses b on b.id = a.business_id"
                " where a.experiment_id = %s order by a.call_order",
                (exp[0],),
            )
            rows = cur.fetchall()
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    removed = 0
    with (out / "call_list.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(CALL_LIST_FIELDS)
        for r in rows:
            values = {str(r["id"]): "business_id", r["phone"]: "phone_e164", r["domain"]: "domain"}
            if any(v and v in supp.get(kind, set()) for v, kind in values.items()):
                removed += 1
                continue
            cat = cfg.categories.get(r["category"])
            w.writerow([r["call_order"], r["id"], r["name"], cat.label if cat else r["category"],
                        r["phone"], r["city"], r["state"], r["tz"],
                        best_call_window(cat, r["tz"]) or ""])  # fmt: skip
    with (out / "outcomes_template.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(OUTCOME_FIELDS)
        w.writerow([rows[0]["id"] if rows else "", "2026-12-01T09:15:00-06:00", "alex", "voicemail",
                    "no", "no", "no", "example row: delete before import"])  # fmt: skip
    (out / "PROTOCOL.md").write_text(
        _protocol(name, cfg, len(rows) - removed, removed), encoding="utf-8"
    )
    return out


def _protocol(name: str, cfg: AppConfig, n: int, removed: int) -> str:
    ex = cfg.experiment
    return f"""# Call protocol: {name}

{n} leads on `call_list.csv`{f" ({removed} removed: suppressed since assignment)" if removed else ""}.
Two groups are mixed on this list. **Don't try to work out which is which** and don't skip or reorder leads.

1. Call in `call_order`, inside each lead's `best_call_window` (their local time).
2. Up to **{ex.max_attempts} attempts** per lead, each in a different call window or day, until someone answers.
   Stop at the first real conversation or a definite outcome.
3. Same script for every lead. {"No openers in this pilot." if not ex.openers_in_call_list else ""}
4. Log **every attempt** as one row (template: `outcomes_template.csv`):
   - `lead_ref`: copy from the call list
   - `called_at`: date and time **with time zone**, e.g. `2026-12-01T09:15:00-06:00`
   - `caller`: your name or initials
   - `disposition`: one of `no_answer`, `voicemail`, `gatekeeper`, `decision_maker`, `callback`,
     `not_interested`, `has_answering_service`, `wrong_number`, `closed_or_not_business`, `do_not_call`
   - `demo_booked`, `trial_started`, `paid`: yes/no. Later wins (trial, paid) can be logged as a new row.
5. **Anyone who asks not to be called: log `do_not_call`.** Import suppresses them permanently.

Import: `uv run leads outcomes import <file.csv> --experiment {name} --source <dialer/CRM name>`
Readout: `uv run leads experiment readout {name}`
"""
