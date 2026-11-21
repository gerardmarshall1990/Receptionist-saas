"""Import call outcomes (one row per attempt) from a dialer/CRM CSV.

Every row is validated before anything is written; a bad row stops the whole import.
Re-importing the same file changes nothing (unique lead + called_at). `do_not_call`
adds a permanent suppression for the business and its phone.
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from leads_engine.compliance_commands import add_suppressions
from leads_engine.experiment.assign import OUTCOME_FIELDS
from leads_engine.experiment.stats import DISPOSITIONS

_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0", ""}


def _bool(v: str | None, line: int, field: str) -> bool:
    s = (v or "").strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"line {line}: {field} must be yes/no, got {v!r}")


def parse(path: Path, valid_refs: dict[str, str]) -> list[dict[str, Any]]:
    """Validate the CSV against the experiment's leads. valid_refs: business_id -> phone."""
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [f for f in OUTCOME_FIELDS if f not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"missing columns: {missing} (template: outcomes_template.csv)")
        for line, rec in enumerate(reader, start=2):
            ref = (rec["lead_ref"] or "").strip()
            if ref not in valid_refs:
                raise ValueError(f"line {line}: lead_ref {ref!r} is not in this experiment")
            try:
                called = datetime.fromisoformat((rec["called_at"] or "").strip())
            except ValueError:
                raise ValueError(
                    f"line {line}: called_at {rec['called_at']!r} is not ISO date-time"
                ) from None
            if called.tzinfo is None:
                raise ValueError(
                    f"line {line}: called_at needs a time zone, e.g. 2026-12-01T09:15:00-06:00"
                )
            disp = (rec["disposition"] or "").strip().lower()
            if disp not in DISPOSITIONS:
                raise ValueError(
                    f"line {line}: disposition {disp!r} not one of {sorted(DISPOSITIONS)}"
                )
            rows.append({
                "business_id": ref, "called_at": called, "caller": (rec["caller"] or "").strip(),
                "disposition": disp,
                "demo_booked": _bool(rec["demo_booked"], line, "demo_booked"),
                "trial_started": _bool(rec["trial_started"], line, "trial_started"),
                "paid": _bool(rec["paid"], line, "paid"),
                "notes": (rec["notes"] or "").strip() or None,
            })  # fmt: skip
    return rows


def import_csv(
    url: str, experiment: str, path: Path, source: str, max_attempts: int
) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        exp = conn.execute("select id from experiments where name = %s", (experiment,)).fetchone()
        if exp is None:
            raise ValueError(f"no experiment {experiment!r}")
        refs = {
            str(bid): phone
            for bid, phone in conn.execute(
                "select a.business_id, b.phone from experiment_assignments a"
                " join businesses b on b.id = a.business_id where a.experiment_id = %s",
                (exp[0],),
            )
        }
    rows = parse(path, refs)
    counts: Counter[str] = Counter(rows=len(rows))
    with psycopg.connect(url) as conn, conn.transaction():
        for r in rows:
            cur = conn.execute(
                "insert into outcomes (experiment_id, business_id, called_at, caller, disposition,"
                " demo_booked, trial_started, paid, notes, source)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (business_id, called_at) do nothing",
                (exp[0], r["business_id"], r["called_at"], r["caller"], r["disposition"],
                 r["demo_booked"], r["trial_started"], r["paid"], r["notes"], source),
            )  # fmt: skip
            counts["inserted" if cur.rowcount else "already_imported"] += 1
        # Protocol check: more unanswered attempts than allowed means the arms weren't dialled alike.
        over = conn.execute(
            "select count(*) from (select business_id from outcomes where experiment_id = %s"
            " group by business_id having count(*) filter (where disposition in ('no_answer', 'voicemail')) > %s) x",
            (exp[0], max_attempts),
        ).fetchone()
        counts["leads_over_max_attempts"] = over[0] if over else 0
    dnc = [r for r in rows if r["disposition"] == "do_not_call"]
    if dnc:
        sup = [
            ("business_id", r["business_id"], "asked not to be called (call outcome)") for r in dnc
        ]
        sup += [("phone_e164", refs[r["business_id"]], "asked not to be called (call outcome)")
                for r in dnc if refs[r["business_id"]]]  # fmt: skip
        counts["suppression_rows_added"] = add_suppressions(url, sup, f"outcomes:{source}")
    return dict(counts)
