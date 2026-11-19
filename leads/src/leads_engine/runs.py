"""Every job records a row in `runs`: stage, config hash, budget, cost, counts.

Budget is a hard stop: `add_cost` raises before the cap would be exceeded, and
the run ends as `stopped_budget`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg

from leads_engine.log import bind_run, clear_run, get_logger


class BudgetExceededError(RuntimeError):
    pass


@dataclass
class Run:
    id: str
    stage: str
    budget_usd: float
    dry_run: bool
    cost_usd: float = 0.0
    counts: dict[str, Any] = field(default_factory=dict)

    def add_cost(self, usd: float) -> None:
        if usd < 0:
            raise ValueError("cost must be >= 0")
        if self.cost_usd + usd > self.budget_usd + 1e-9:
            total = self.cost_usd + usd
            raise BudgetExceededError(
                f"run {self.id} would spend ${total:.4f} > budget ${self.budget_usd:.4f}"
            )
        self.cost_usd += usd


def _json(v: Any) -> str:
    return json.dumps(v, default=str, sort_keys=True)


@contextmanager
def recorded_run(
    url: str,
    stage: str,
    config_hash: str,
    *,
    dry_run: bool,
    budget_usd: float,
    params: dict[str, Any] | None = None,
) -> Iterator[Run]:
    run = Run(str(uuid.uuid4()), stage, budget_usd, dry_run)
    log = get_logger("runs")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into runs (id, stage, status, dry_run, config_hash, budget_usd, params)"
            " values (%s, %s, 'running', %s, %s, %s, %s)",
            (run.id, stage, dry_run, config_hash, budget_usd, _json(params or {})),
        )
        bind_run(run.id, stage)
        log.info("run_started", params=params or {})
        status, error = "succeeded", None
        try:
            yield run
        except BudgetExceededError as exc:
            status, error = "stopped_budget", str(exc)
            raise
        except BaseException as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            conn.execute(
                "update runs set status = %s, cost_usd = %s, counts = %s, error = %s,"
                " finished_at = now() where id = %s",
                (status, run.cost_usd, _json(run.counts), error, run.id),
            )
            log.info("run_finished", status=status, cost_usd=run.cost_usd, error=error)
            clear_run()
