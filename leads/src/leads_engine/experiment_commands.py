"""`leads experiment|outcomes ...` (Stage 6). $0: no paid API, no LLM."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from leads_engine.config_loader import load_config
from leads_engine.settings import PROJECT_ROOT, get_settings


def _create(args: argparse.Namespace) -> int:
    from leads_engine.experiment.assign import create
    from leads_engine.runs import recorded_run

    s = get_settings()
    cfg = load_config(s.config_dir)
    params = {
        "name": args.name,
        "seed": args.seed,
        "per_arm": args.per_arm or cfg.experiment.per_arm,
    }
    with recorded_run(
        s.database_url, "stage6.experiment", cfg.config_hash,
        dry_run=False, budget_usd=s.run_budget_usd, params=params,
    ) as run:  # fmt: skip
        res = create(
            s.database_url, cfg, args.name, args.seed, s.data_dir / "experiments", args.per_arm
        )
        run.counts = {k: v for k, v in res.items() if k != "path"}
    print(f"experiment {args.name} locked: {res['per_arm']} per arm, quotas {res['quotas']}")
    print(
        f"pools: scored {res['pool_scored']:,}, control {res['pool_control']:,}; scored tiers {res['scored_tiers']}"
    )
    print(f"call list: {res['path']}/call_list.csv (protocol: PROTOCOL.md)")
    return 0


def _call_list(args: argparse.Namespace) -> int:
    from leads_engine.experiment.assign import write_call_list

    s = get_settings()
    path = write_call_list(
        s.database_url, load_config(s.config_dir), args.name, s.data_dir / "experiments"
    )
    print(f"call list rewritten (suppressed leads removed): {path}/call_list.csv")
    return 0


def _import(args: argparse.Namespace) -> int:
    from leads_engine.experiment.outcomes import import_csv

    s = get_settings()
    cfg = load_config(s.config_dir)
    counts = import_csv(
        s.database_url, args.experiment, Path(args.file), args.source, cfg.experiment.max_attempts
    )
    for k, v in counts.items():
        print(f"  {k:<28} {v:>7,}")
    return 0


def _readout(args: argparse.Namespace) -> int:
    from leads_engine.experiment.readout import readout

    s = get_settings()
    path, res = readout(
        s.database_url, load_config(s.config_dir), args.name, PROJECT_ROOT / "docs" / "reports"
    )
    print(
        f"verdict: {res['verdict']} (dialled: scored {res['scored_dialed']}, control {res['control_dialed']})"
    )
    print(f"report: {path}")
    return 0


def _list(args: argparse.Namespace) -> int:
    with psycopg.connect(get_settings().database_url) as c:
        for name, status, created, n, o in c.execute(
            "select e.name, e.status, e.created_at,"
            " (select count(*) from experiment_assignments a where a.experiment_id = e.id),"
            " (select count(*) from outcomes o where o.experiment_id = e.id)"
            " from experiments e order by e.created_at desc"
        ):
            print(f"{created:%Y-%m-%d} {name:<16} {status:<7} leads={n} attempts_logged={o}")
    return 0


COMMANDS: dict[tuple[str, str], Callable[[argparse.Namespace], int]] = {
    ("experiment", "create"): _create,
    ("experiment", "call-list"): _call_list,
    ("experiment", "readout"): _readout,
    ("experiment", "list"): _list,
    ("outcomes", "import"): _import,
}


def add_parser(groups: Any) -> None:
    ep = groups.add_parser("experiment", help="Stage 6: pilot experiment").add_subparsers(
        dest="command", required=True
    )
    cr = ep.add_parser("create", help="pick + freeze both arms, write the blinded call list")
    cr.add_argument("name")
    cr.add_argument("--seed", required=True, help="any string; same seed + data = same arms")
    cr.add_argument("--per-arm", type=int, help="default: config/experiment.yaml per_arm")
    cl = ep.add_parser("call-list", help="rewrite the call list (drops anyone suppressed since)")
    cl.add_argument("name")
    ro = ep.add_parser("readout", help="write docs/reports/stage6-readout-<name>.md")
    ro.add_argument("name")
    ep.add_parser("list", help="experiments and logged attempts")
    op = groups.add_parser("outcomes", help="Stage 6: call outcomes").add_subparsers(
        dest="command", required=True
    )
    im = op.add_parser("import", help="CSV, one row per call attempt (see outcomes_template.csv)")
    im.add_argument("file")
    im.add_argument("--experiment", required=True)
    im.add_argument("--source", required=True, help="dialer/CRM the file came from")
