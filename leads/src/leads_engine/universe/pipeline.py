"""Stage 1 orchestration: census -> extract -> build -> load -> report.

Paths are content-addressed so reruns are cheap and correct:
- raw extract file name includes a hash of the Overture codes it covers;
- built file name includes the config hash, so any config change rebuilds.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leads_engine.config_loader import AppConfig
from leads_engine.universe import overture
from leads_engine.universe.build import build

SAMPLE_SEED = "stage1-v1"


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    release: str
    country: str

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / "overture" / self.release

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived" / "stage1" / self.release

    def extract(self, codes: list[str]) -> Path:
        h = hashlib.sha256(",".join(sorted(codes)).encode()).hexdigest()[:10]
        return self.raw_dir / f"extract_{self.country}_{h}.parquet"

    def built(self, categories: list[str], config_hash: str) -> Path:
        tag = "-".join(sorted(categories))
        return self.derived_dir / f"built_{self.country}_{tag}_{config_hash}.parquet"

    def stats(self, built: Path) -> Path:
        return built.with_suffix(".stats.json")


def selected_files(cfg: AppConfig, paths: Paths) -> list[overture.PlaceFile]:
    con = overture.connect()
    files = overture.build_manifest(con, paths.release, paths.raw_dir)
    return overture.files_for_country(files, paths.country)


def plan(cfg: AppConfig, paths: Paths, categories: list[str]) -> dict[str, Any]:
    """What a run would do, without reading any place data (only file footers, cached)."""
    files = selected_files(cfg, paths)
    codes = sorted(cfg.code_map(categories))
    extract_path = paths.extract(codes)
    return {
        "release": paths.release,
        "country": paths.country,
        "categories": categories,
        "overture_codes": codes,
        "files_selected": len(files),
        "files_bytes_upper_bound": sum(f.size_bytes for f in files),
        "rows_in_selected_files": sum(f.rows for f in files),
        "extract_path": str(extract_path),
        "extract_exists": extract_path.exists(),
        "built_path": str(paths.built(categories, cfg.config_hash)),
        "cost_usd": 0.0,
        "note": "Only needed columns/row groups are read; transfer is far below the bound.",
    }


def run_census(cfg: AppConfig, paths: Paths) -> tuple[Path, Path]:
    con = overture.connect()
    urls = [f.url for f in selected_files(cfg, paths)]
    return overture.taxonomy_census(con, urls, paths.country, paths.raw_dir)


def run_extract(
    cfg: AppConfig, paths: Paths, categories: list[str], force: bool = False
) -> tuple[Path, float]:
    codes = sorted(cfg.code_map(categories))
    out = paths.extract(codes)
    if force:
        out.unlink(missing_ok=True)
    meta = out.with_suffix(".meta.json")
    if out.exists() and meta.exists():
        return out, float(json.loads(meta.read_text())["seconds"])
    t0 = time.monotonic()
    con = overture.connect()
    urls = [f.url for f in selected_files(cfg, paths)]
    overture.extract(con, urls, codes, paths.country, out)
    seconds = time.monotonic() - t0
    meta.write_text(json.dumps({"codes": codes, "seconds": seconds, "files": urls}, indent=1))
    return out, seconds


def run_build(
    cfg: AppConfig, paths: Paths, categories: list[str], force: bool = False
) -> tuple[Path, dict[str, Any], bool]:
    """Returns (built path, stats, reused). The file name carries the config hash, so an
    existing file was built from exactly this config and extract: reuse it."""
    raw = paths.extract(sorted(cfg.code_map(categories)))
    if not raw.exists():
        raise FileNotFoundError(f"extract missing, run `leads universe extract` first: {raw}")
    out = paths.built(categories, cfg.config_hash)
    stats_path = paths.stats(out)
    if out.exists() and stats_path.exists() and not force:
        return out, json.loads(stats_path.read_text()), True
    t0 = time.monotonic()
    stats = build(raw, cfg, categories, paths.release, out, SAMPLE_SEED)
    data = stats.as_dict() | {"build_seconds": time.monotonic() - t0}
    stats_path.write_text(json.dumps(data, indent=1))
    return out, data, False
