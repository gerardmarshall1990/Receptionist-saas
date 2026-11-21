"""Score every business in Postgres (set-based, idempotent). $0: rules only."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import psycopg

from leads_engine.config_loader import AppConfig
from leads_engine.scoring.prescore import score

SELECT = """
select b.id, b.category, b.flags, b.is_chain, b.phone_status, b.email_status, b.in_pilot_sample,
       b.crawled_at is not null as crawled,
       coalesce(array_agg(s.signal) filter (where s.value), '{}') as signals
from businesses b left join signals s on s.business_id = b.id
group by b.id
"""

STAGE = """
create temp table stage_scores (
  business_id uuid, pain numeric, value numeric, reachability numeric, fit numeric,
  total numeric, tier char(1), dead_reason text, components jsonb
) on commit drop
"""

MERGE = """
insert into scores (business_id, pain, value, reachability, fit, total, tier, dead_reason,
                    components, model_version, scored_at)
select business_id, pain, value, reachability, fit, total, tier, dead_reason, components,
       %(mv)s, now()
from stage_scores
on conflict (business_id) do update set
  pain = excluded.pain, value = excluded.value, reachability = excluded.reachability, fit = excluded.fit,
  total = excluded.total, tier = excluded.tier, dead_reason = excluded.dead_reason,
  components = excluded.components, model_version = excluded.model_version, scored_at = now()
-- Unchanged scores are left alone: rewriting all 726k rows every refresh only bloats the table.
where (scores.total, scores.tier, scores.dead_reason, scores.components, scores.model_version)
  is distinct from (excluded.total, excluded.tier, excluded.dead_reason, excluded.components, excluded.model_version)
"""

DENORMALIZE = """
update businesses b set score = s.total, tier = s.tier, updated_at = now()
from stage_scores s where s.business_id = b.id
  and (b.score is distinct from s.total or b.tier is distinct from s.tier)
"""


def model_version(cfg: AppConfig) -> str:
    return f"rules-v{cfg.scoring.version}-{cfg.config_hash}"


def run(url: str, cfg: AppConfig) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    # Reader and writer need separate connections: Postgres can't FETCH from a cursor
    # while a COPY is running on the same connection.
    with psycopg.connect(url) as rconn, psycopg.connect(url) as conn, conn.transaction():
        cur = conn.cursor()
        cur.execute(STAGE)
        read = rconn.cursor(name="score_stream")  # server-side cursor: constant memory at 3M
        read.itersize = 20_000
        read.execute(SELECT)
        cols = [d.name for d in read.description or []]
        with cur.copy(
            "copy stage_scores (business_id, pain, value, reachability, fit, total, tier, dead_reason, components)"
            " from stdin"
        ) as cp:
            for row in read:
                b = dict(zip(cols, row, strict=True))
                s = score(b, set(b["signals"]), cfg)
                cp.write_row([b["id"], s.pain, s.value, s.reachability, s.fit, s.total, s.tier,
                              s.dead_reason, json.dumps(s.components)])  # fmt: skip
                counts["scored"] += 1
                counts[f"tier_{s.tier}"] += 1
                if b["in_pilot_sample"] and b["crawled"]:
                    counts[f"crawled_tier_{s.tier}"] += 1
                if s.dead_reason:
                    counts["dead"] += 1
        read.close()
        cur.execute(MERGE, {"mv": model_version(cfg)})
        cur.execute(DENORMALIZE)
        # "Why this lead" order follows the current weights.
        weights = list(cfg.scoring.signal_weights.items())
        cur.execute(
            "update reasons r set weight = w.weight from (select * from unnest(%s::text[], %s::numeric[])"
            " as t(signal, weight)) w where r.signal = w.signal",
            ([k for k, _ in weights], [v for _, v in weights]),
        )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("analyze scores; analyze businesses")
    return dict(counts)
