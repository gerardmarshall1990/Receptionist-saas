"""`leads universe ...` commands (Stage 1: T0 universe from Overture)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

import duckdb

from leads_engine.config_loader import AppConfig, load_config
from leads_engine.log import get_logger
from leads_engine.runs import recorded_run
from leads_engine.settings import PROJECT_ROOT, Settings, get_settings
from leads_engine.universe import pipeline
from leads_engine.universe import report as rpt
from leads_engine.universe.load import load_sample, sample_rows

REPORTS_DIR = PROJECT_ROOT / "docs" / "reports"


def _ctx(args: argparse.Namespace) -> tuple[Settings, AppConfig, pipeline.Paths, list[str]]:
    s = get_settings()
    cfg = load_config(s.config_dir)
    paths = pipeline.Paths(s.data_dir, cfg.global_.overture_release, "US")
    cats = (
        args.categories.split(",")
        if getattr(args, "categories", None)
        else list(cfg.global_.pilot_sample)
    )
    for c in cats:
        if c not in cfg.categories:
            raise ValueError(f"unknown category {c!r}")
    return s, cfg, paths, cats


def _run(s: Settings, cfg: AppConfig, stage: str, params: dict[str, Any]) -> Any:
    # Stage 1 spends nothing: open data only. Budget stays whatever is configured.
    return recorded_run(
        s.database_url,
        stage,
        cfg.config_hash,
        dry_run=False,
        budget_usd=s.run_budget_usd,
        params=params,
    )


def cmd_plan(args: argparse.Namespace) -> int:
    _, cfg, paths, cats = _ctx(args)
    print(json.dumps(pipeline.plan(cfg, paths, cats), indent=1))
    return 0


def cmd_census(args: argparse.Namespace) -> int:
    s, cfg, paths, _ = _ctx(args)
    with _run(s, cfg, "stage1.census", {"release": paths.release}) as run:
        primary, alternates = pipeline.run_census(cfg, paths)
        prim, _, total = rpt.census_counts(primary, alternates)
        run.counts = {"places": total, "codes": len(prim)}
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORTS_DIR / f"taxonomy-{paths.release}.md"
        out.write_text(rpt.taxonomy_report(cfg, paths.release, paths.country, primary, alternates))
        missing = rpt.missing_codes(cfg, prim)
    print(f"census: {total:,} places, {len(prim):,} codes -> {out}")
    if missing:
        print(f"error: configured codes not in census: {missing}")
        return 1
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    s, cfg, paths, cats = _ctx(args)
    if args.dry_run:
        return cmd_plan(args)
    with _run(s, cfg, "stage1.extract", {"categories": cats, "release": paths.release}) as run:
        out, secs = pipeline.run_extract(cfg, paths, cats, force=args.force)
        rows = (
            duckdb.connect().execute("select count(*) from read_parquet(?)", [str(out)]).fetchone()
        )
        run.counts = {
            "rows": rows[0] if rows else 0,
            "seconds": round(secs, 1),
            "bytes": out.stat().st_size,
        }
    print(f"extract: {run.counts} -> {out}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    s, cfg, paths, cats = _ctx(args)
    with _run(s, cfg, "stage1.build", {"categories": cats, "seed": pipeline.SAMPLE_SEED}) as run:
        out, stats, reused = pipeline.run_build(cfg, paths, cats, force=args.force)
        run.counts = {
            "raw_rows": stats["raw_rows"],
            "by_category": stats["by_category"],
            "reused": reused,
        }
    how = "reused (same config hash)" if reused else f"built in {stats['build_seconds']:.0f}s"
    print(f"build: {stats['raw_rows']:,} rows, {how} -> {out}")
    for c, v in stats["by_category"].items():
        print(f"  {c}: kept {v.get('kept', 0):,} / raw {v.get('raw', 0):,}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    s, cfg, paths, cats = _ctx(args)
    built = paths.built(cats, cfg.config_hash)
    if not built.exists():
        raise FileNotFoundError(f"built file missing, run `leads universe build`: {built}")
    with _run(s, cfg, "stage1.load", {"built": str(built)}) as run:
        run.counts = load_sample(s.database_url, built)
    print(f"load: {run.counts}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    s, cfg, paths, cats = _ctx(args)
    built = paths.built(cats, cfg.config_hash)
    raw = paths.extract(sorted(cfg.code_map(cats)))
    stats = json.loads(paths.stats(built).read_text())
    meta = raw.with_suffix(".meta.json")
    secs = json.loads(meta.read_text())["seconds"] if meta.exists() else None
    prim, _, _ = rpt.census_counts(
        paths.raw_dir / "census_primary.parquet", paths.raw_dir / "census_alternates.parquet"
    )
    universe = {
        c.slug: sum(prim.get(code, 0) for code in c.included_codes) for c in cfg.categories.values()
    }
    text = rpt.pilot_report(
        cfg, paths.release, stats, built, raw, secs, s.database_url,
        {"pilot_rows_in_parquet": len(sample_rows(built))}, universe,
    )  # fmt: skip
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"stage1-pilot-{paths.release}.md"
    out.write_text(text)
    print(f"report -> {out}")
    return 0


def cmd_pilot(args: argparse.Namespace) -> int:
    if args.dry_run:
        return cmd_plan(args)
    log = get_logger("cli")
    for step in (cmd_census, cmd_extract, cmd_build, cmd_load, cmd_report):
        log.info("pilot_step", step=step.__name__)
        if (rc := step(args)) != 0:
            return rc
    return 0


COMMANDS: dict[tuple[str, str], Callable[[argparse.Namespace], int]] = {
    ("universe", "plan"): cmd_plan,
    ("universe", "census"): cmd_census,
    ("universe", "extract"): cmd_extract,
    ("universe", "build"): cmd_build,
    ("universe", "load"): cmd_load,
    ("universe", "report"): cmd_report,
    ("universe", "pilot"): cmd_pilot,
}


def add_parser(groups: Any) -> None:
    u = groups.add_parser("universe", help="Stage 1: T0 universe from Overture").add_subparsers(
        dest="command", required=True
    )
    helps = {
        "plan": "show what would be read and written (reads only cached file footers)",
        "census": "count every taxonomy code in the US; check configured codes exist",
        "extract": "read matching US places from Overture into data/raw (idempotent)",
        "build": "normalize, exclude, dedupe, sample -> data/derived",
        "load": "upsert the pilot sample into Postgres",
        "report": "write docs/reports/stage1-pilot-<release>.md",
        "pilot": "census + extract + build + load + report",
    }
    for name, h in helps.items():
        sp = u.add_parser(name, help=h)
        sp.add_argument(
            "--categories", help="comma-separated slugs (default: config pilot_sample keys)"
        )
        sp.add_argument("--dry-run", action="store_true", help="print the plan only")
        sp.add_argument("--force", action="store_true", help="re-extract even if the file exists")
