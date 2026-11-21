"""Readout: scored vs control on reach, conversation, demo, trial, paid. Aggregates only.

The verdict is mechanical: no winner below `min_dialed_per_arm`, and a winner only when the
primary metric differs at p < alpha. Everything else is labelled descriptive.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from leads_engine.config_loader import AppConfig
from leads_engine.experiment.stats import (
    Attempt,
    lead_outcome,
    required_n,
    two_proportion_p,
    wilson,
)

ROWS = ("reached", "conversation", "demo", "trial", "paid")
LABELS = {
    "reached": "Reached a person", "conversation": "Real conversation", "demo": "Demo booked",
    "trial": "Trial started", "paid": "Paid", "bad_data": "Wrong number / closed",
    "has_service": "Already has answering service", "do_not_call": "Asked not to be called",
}  # fmt: skip


@dataclass
class ArmStats:
    assigned: int = 0
    dialed: int = 0
    attempts: int = 0
    hits: Counter[str] | None = None

    def rate(self, metric: str) -> float:
        return (self.hits or Counter())[metric] / self.dialed if self.dialed else 0.0

    def k(self, metric: str) -> int:
        return (self.hits or Counter())[metric]


def load(url: str, name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with psycopg.connect(url) as conn:
        exp = conn.execute(
            "select id, name, seed, params, created_at from experiments where name = %s", (name,)
        ).fetchone()
        if exp is None:
            raise ValueError(f"no experiment {name!r}")
        leads = conn.execute(
            "select a.business_id, a.arm, a.tier_at_assignment, b.category,"
            " coalesce((select array_agg(distinct r.signal) from reasons r where r.business_id = a.business_id"
            "           and r.weight > 0), '{}') as signals,"
            " coalesce((select json_agg(json_build_object('d', o.disposition, 'demo', o.demo_booked,"
            "           'trial', o.trial_started, 'paid', o.paid)) from outcomes o"
            "           where o.experiment_id = a.experiment_id and o.business_id = a.business_id), '[]') as att"
            " from experiment_assignments a join businesses b on b.id = a.business_id"
            " where a.experiment_id = %s",
            (exp[0],),
        ).fetchall()
    meta = {
        "id": str(exp[0]),
        "name": exp[1],
        "seed": exp[2],
        "params": exp[3],
        "created_at": exp[4],
    }
    out = []
    for bid, arm, tier, cat, signals, att in leads:
        attempts = [Attempt(a["d"], a["demo"], a["trial"], a["paid"]) for a in att]
        out.append({"id": str(bid), "arm": arm, "tier": tier, "category": cat, "signals": list(signals),
                    "n_attempts": len(attempts), **lead_outcome(attempts)})  # fmt: skip
    return meta, out


def arm_stats(leads: list[dict[str, Any]]) -> ArmStats:
    s = ArmStats(hits=Counter())
    for x in leads:
        s.assigned += 1
        if x["dialed"]:
            s.dialed += 1
            s.attempts += x["n_attempts"]
            for m in LABELS:
                if x[m]:
                    s.hits[m] += 1  # type: ignore[index]
    return s


def verdict(s: ArmStats, c: ArmStats, cfg: AppConfig) -> tuple[str, str]:
    ex = cfg.experiment
    m = ex.primary_metric
    if min(s.dialed, c.dialed) < ex.min_dialed_per_arm:
        return ("not_enough_data",
                f"Not enough data yet: {s.dialed} scored and {c.dialed} control leads dialled; "
                f"the readout names a winner only after {ex.min_dialed_per_arm} per arm.")  # fmt: skip
    p = two_proportion_p(s.k(m), s.dialed, c.k(m), c.dialed)
    if p < ex.alpha and s.rate(m) > c.rate(m):
        return (
            "scored_wins",
            f"Scored leads beat the random list on {LABELS[m].lower()} "
            f"({s.rate(m):.1%} vs {c.rate(m):.1%}, p = {p:.3f}). Recommend scale-out (Stage 7).",
        )
    if p < ex.alpha:
        return (
            "control_wins",
            f"The random list beat scored leads on {LABELS[m].lower()} "
            f"({c.rate(m):.1%} vs {s.rate(m):.1%}, p = {p:.3f}). Stop and review the scoring.",
        )
    need = required_n(c.rate(m), s.rate(m), ex.alpha, ex.power)
    more = f" Detecting the observed gap reliably needs ~{need:,} dialled per arm." if need else ""
    return (
        "no_difference_yet",
        f"No significant difference on {LABELS[m].lower()} yet "
        f"({s.rate(m):.1%} vs {c.rate(m):.1%}, p = {p:.2f}).{more}",
    )


def _pct(k: int, n: int, alpha: float) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson(k, n, alpha)
    return f"{k / n:.1%} ({k}/{n}; {lo:.1%}-{hi:.1%})"


def render(
    meta: dict[str, Any], leads: list[dict[str, Any]], cfg: AppConfig
) -> tuple[str, dict[str, Any]]:
    ex = cfg.experiment
    by_arm = {a: [x for x in leads if x["arm"] == a] for a in ("scored", "control")}
    s, c = arm_stats(by_arm["scored"]), arm_stats(by_arm["control"])
    code, text = verdict(s, c, cfg)
    pm = ex.primary_metric
    lines = [
        f"# Stage 6 readout: {meta['name']}",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Experiment created {meta['created_at']:%Y-%m-%d}, "
        f"seed `{meta['seed']}`. Scored = tiers {', '.join(meta['params']['scored_tiers'])} passing the export gate; "
        "control = random leads passing the safety rules only (no score information).",
        "",
        f"**Verdict ({code}):** {text}",
        "",
        f"Rates are per lead dialled, with 95% ranges. Primary metric: **{LABELS[pm].lower()}**.",
        "",
        "| | Scored | Control | Lift | p |",
        "|---|---|---|---:|---:|",
        f"| Leads assigned | {s.assigned:,} | {c.assigned:,} | | |",
        f"| Leads dialled | {s.dialed:,} | {c.dialed:,} | | |",
        f"| Attempts per dialled lead | {s.attempts / s.dialed if s.dialed else 0:.1f} | "
        f"{c.attempts / c.dialed if c.dialed else 0:.1f} | | |",
    ]
    result: dict[str, Any] = {"verdict": code, "scored": {}, "control": {}}
    for m in (*ROWS, "bad_data", "has_service", "do_not_call"):
        lift = f"x{s.rate(m) / c.rate(m):.2f}" if c.rate(m) > 0 else "-"
        p = two_proportion_p(s.k(m), s.dialed, c.k(m), c.dialed)
        bold = "**" if m == pm else ""
        lines.append(f"| {bold}{LABELS[m]}{bold} | {_pct(s.k(m), s.dialed, ex.alpha)} | "
                     f"{_pct(c.k(m), c.dialed, ex.alpha)} | {lift} | {f'{p:.3f}' if s.dialed and c.dialed else '-'} |")  # fmt: skip
        result["scored"][m], result["control"][m] = s.k(m), c.k(m)
    result.update(scored_dialed=s.dialed, control_dialed=c.dialed)

    # Secondary, descriptive only.
    lines += ["", "## Secondary (descriptive, not a verdict)", "", "### Scored arm by tier", "",
              "| Tier | Dialled | Reached | Demo |", "|---|---:|---:|---:|"]  # fmt: skip
    for t in sorted({x["tier"] for x in by_arm["scored"]}):
        st = arm_stats([x for x in by_arm["scored"] if x["tier"] == t])
        lines.append(f"| {t} | {st.dialed} | {_pct(st.k('reached'), st.dialed, ex.alpha)} | "
                     f"{_pct(st.k('demo'), st.dialed, ex.alpha)} |")  # fmt: skip
    lines += ["", "### By category", "", f"| Category | Scored {LABELS[pm].lower()} | Control {LABELS[pm].lower()} |",
              "|---|---|---|"]  # fmt: skip
    for cat in sorted({x["category"] for x in leads}):
        a = arm_stats([x for x in by_arm["scored"] if x["category"] == cat])
        b = arm_stats([x for x in by_arm["control"] if x["category"] == cat])
        lines.append(
            f"| {cat} | {_pct(a.k(pm), a.dialed, ex.alpha)} | {_pct(b.k(pm), b.dialed, ex.alpha)} |"
        )
    lines += ["", f"### Signals and {LABELS[pm].lower()} (scored arm, signals with >= 20 dialled leads)", "",
              "| Signal | With signal | Without |", "|---|---|---|"]  # fmt: skip
    dialed_scored = [x for x in by_arm["scored"] if x["dialed"]]
    sig_counts: dict[str, int] = defaultdict(int)
    for x in dialed_scored:
        for sg in x["signals"]:
            sig_counts[sg] += 1
    for sg in sorted(sig_counts, key=lambda k: -sig_counts[k]):
        if sig_counts[sg] < 20:
            continue
        w = [x for x in dialed_scored if sg in x["signals"]]
        wo = [x for x in dialed_scored if sg not in x["signals"]]
        lines.append(f"| {sg} | {_pct(sum(x[pm] for x in w), len(w), ex.alpha)} | "
                     f"{_pct(sum(x[pm] for x in wo), len(wo), ex.alpha)} |")  # fmt: skip
    lines += ["", "Signals are the current stored reasons (they may have been refreshed since assignment). "
              "Use them to pick hypotheses for Stage 8, not to re-weight on small numbers.", ""]  # fmt: skip
    return "\n".join(lines), result


def readout(url: str, cfg: AppConfig, name: str, out_dir: Path) -> tuple[Path, dict[str, Any]]:
    meta, leads = load(url, name)
    text, result = render(meta, leads, cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"stage6-readout-{name}.md"
    path.write_text(text, encoding="utf-8")
    return path, result
