"""Command line entry point: ``leads <group> <command>``.

Groups: config, db, universe (Stage 1). Nothing here crawls websites or
calls a paid API; `universe` reads open Overture data.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from leads_engine import db, universe_commands
from leads_engine.config_loader import AppConfig, load_config, resolve_model
from leads_engine.log import configure_logging, get_logger
from leads_engine.settings import get_settings


def _config_validate(args: argparse.Namespace) -> int:
    s = get_settings()
    cfg = load_config(s.config_dir)
    log = get_logger("cli")
    log.info(
        "config_valid",
        config_hash=cfg.config_hash,
        categories=sorted(cfg.categories),
        pilot_a=[c.slug for c in cfg.pilot_categories("a")],
        pilot_b=[c.slug for c in cfg.pilot_categories("b")],
        scoring_model=resolve_model(cfg, "scoring", s.scoring_model),
        opener_model=resolve_model(cfg, "opener", s.opener_model),
        dry_run=s.dry_run,
        run_budget_usd=s.run_budget_usd,
    )
    for cat in sorted(cfg.categories.values(), key=lambda c: c.rank):
        print(
            f"{cat.rank}. {cat.slug:<20} value={cat.value_weight:.2f} "
            f"pilot={cat.pilot_phase or '-'} pain_kw={len(cat.pain_keywords)} "
            f"openers={len(cat.opener_templates)} open_q={len(cat.open_questions)} "
            f"source_codes={cat.source_codes.status}"
        )
    for line in cost_projection_lines(cfg):
        print(line)
    print(f"config_hash={cfg.config_hash}")
    return 0


def cost_projection_lines(cfg: AppConfig) -> list[str]:
    """LLM spend for a full universe, from config only (no API calls)."""
    n = cfg.global_.universe_target
    sc, op = cfg.models.scoring, cfg.models.opener
    score_calls = int(n * cfg.scoring.llm_top_fraction)
    opener_calls = int(n * cfg.scoring.opener_top_fraction)
    s_cost, o_cost = sc.projected_cost_usd(score_calls), op.projected_cost_usd(opener_calls)
    return [
        f"projection @ universe={n:,} (LLM only; paid phone/email verification priced in Stage 3):",
        f"  T3 scoring  {sc.model:<18} {score_calls:>9,} calls x ${sc.est_cost_per_call_usd:.5f}"
        f" = ${s_cost:,.2f}",
        f"  T5 openers  {op.model:<18} {opener_calls:>9,} calls x ${op.est_cost_per_call_usd:.5f}"
        f" = ${o_cost:,.2f}",
        f"  total LLM   ${s_cost + o_cost:,.2f}",
    ]


def _db_check(args: argparse.Namespace) -> int:
    print(db.check(get_settings().database_url))
    return 0


def _db_migrate(args: argparse.Namespace) -> int:
    applied = db.migrate(get_settings().database_url)
    get_logger("cli").info("migrations_applied", applied=applied)
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


COMMANDS: dict[tuple[str, str], Callable[[argparse.Namespace], int]] = {
    ("config", "validate"): _config_validate,
    ("db", "check"): _db_check,
    ("db", "migrate"): _db_migrate,
    **universe_commands.COMMANDS,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leads")
    groups = p.add_subparsers(dest="group", required=True)
    cfg = groups.add_parser("config").add_subparsers(dest="command", required=True)
    cfg.add_parser("validate", help="validate all YAML config and print its hash")
    dbp = groups.add_parser("db").add_subparsers(dest="command", required=True)
    dbp.add_parser("check", help="connect to LEADS_DATABASE_URL")
    dbp.add_parser("migrate", help="apply pending migrations")
    universe_commands.add_parser(groups)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    try:
        return COMMANDS[(args.group, args.command)](args)
    except Exception as exc:
        get_logger("cli").error("command_failed", error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
