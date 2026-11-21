"""`leads crawl ...` commands (Stage 2)."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from typing import Any

from leads_engine.config_loader import load_config
from leads_engine.crawl import report as rpt
from leads_engine.crawl import runner
from leads_engine.crawl.store import run_extract
from leads_engine.runs import recorded_run
from leads_engine.settings import PROJECT_ROOT, get_settings

REPORTS_DIR = PROJECT_ROOT / "docs" / "reports"


def cmd_enqueue(args: argparse.Namespace) -> int:
    s = get_settings()
    cfg = load_config(s.config_dir)
    weights = {slug: c.value_weight for slug, c in cfg.categories.items()}
    n = runner.enqueue(s.database_url, "all" if args.all else "pilot", weights)
    print(f"enqueued or re-prioritised {n} sites (best first)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    s = get_settings()
    cfg = load_config(s.config_dir)
    if args.dry_run:
        print(json.dumps(rpt.queue_status(s.database_url) | {
            "would_claim_up_to": args.max_sites, "time_limit_min": args.minutes,
            "concurrency_sites": cfg.crawl.concurrency_sites,
            "per_site_delay_s": cfg.crawl.per_site_delay_s,
            "max_pages_per_site": cfg.crawl.max_pages_per_site, "user_agent": cfg.crawl.user_agent,
            "cost_usd": 0.0,
        }, indent=1))  # fmt: skip
        return 0
    params = {"max_sites": args.max_sites, "minutes": args.minutes}
    with recorded_run(s.database_url, "stage2.crawl", cfg.config_hash, dry_run=False,
                      budget_usd=s.run_budget_usd, params=params) as run:  # fmt: skip
        run.counts = asyncio.run(
            runner.run(s.database_url, cfg.crawl, s.data_dir, max_sites=args.max_sites,
                       time_limit_s=args.minutes * 60, run_id=run.id)
        )  # fmt: skip
    print(f"crawl: {run.counts}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    s = get_settings()
    cfg = load_config(s.config_dir)
    with recorded_run(s.database_url, "stage2.extract", cfg.config_hash, dry_run=False,
                      budget_usd=s.run_budget_usd) as run:  # fmt: skip
        run.counts = run_extract(s.database_url, cfg, s.data_dir, full=args.full)
    print(f"extract: {json.dumps(run.counts, indent=1)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(rpt.queue_status(get_settings().database_url), indent=1))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    s = get_settings()
    cfg = load_config(s.config_dir)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "stage2-crawl-pilot.md"
    out.write_text(rpt.build_report(s.database_url, cfg, s.data_dir))
    print(f"report -> {out}")
    return 0


COMMANDS: dict[tuple[str, str], Callable[[argparse.Namespace], int]] = {
    ("crawl", "enqueue"): cmd_enqueue,
    ("crawl", "run"): cmd_run,
    ("crawl", "extract"): cmd_extract,
    ("crawl", "status"): cmd_status,
    ("crawl", "report"): cmd_report,
}


def add_parser(groups: Any) -> None:
    c = groups.add_parser("crawl", help="Stage 2: polite website crawl + signals").add_subparsers(
        dest="command", required=True
    )
    e = c.add_parser(
        "enqueue", help="queue websites of the pilot sample (or --all loaded businesses)"
    )
    e.add_argument("--all", action="store_true")
    r = c.add_parser("run", help="crawl queued sites (resumable)")
    r.add_argument("--max-sites", type=int, default=1000)
    r.add_argument("--minutes", type=float, default=120)
    r.add_argument("--dry-run", action="store_true")
    ex = c.add_parser(
        "extract", help="signals/emails/phones from saved pages (no network; incremental)"
    )
    ex.add_argument(
        "--full", action="store_true", help="re-extract every finished site, not just new ones"
    )
    c.add_parser("status", help="queue counts")
    c.add_parser("report", help="write docs/reports/stage2-crawl-pilot.md")
