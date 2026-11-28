"""Load config.yaml (thresholds/assumptions) and .env (secrets)."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .feaso import FeasoInputs, StressSpec

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"

REQUIRED = {
    "gates": ["allowed_zones", "min_land_m2", "max_price", "required_existing_dwellings",
              "building_core_scale", "overlays", "flood_layers"],
    "feaso": ["interest_rate", "cap_months", "draw_factor", "target_yield", "yield_basis",
              "opex_ratio", "ltv_cap", "units", "unit_cost_installed", "misc", "stress"],
    "rents": ["source", "bond_csv_url", "min_bonds", "unit_bedrooms"],
    "sources": ["council_base", "council_layers", "sa2_layer"],
    "storage": ["db_path"],
}


class ConfigError(ValueError):
    pass


def load_env(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars win."""
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("GSF_CONFIG") or DEFAULT_CONFIG)
    cfg = yaml.safe_load(p.read_text())
    for section, keys in REQUIRED.items():
        if section not in cfg:
            raise ConfigError(f"config.yaml missing section '{section}'")
        missing = [k for k in keys if k not in cfg[section]]
        if missing:
            raise ConfigError(f"config.yaml [{section}] missing: {', '.join(missing)}")
    if cfg["feaso"]["yield_basis"] not in ("gross", "net"):
        raise ConfigError("feaso.yield_basis must be gross or net")
    for k, mode in cfg["gates"]["overlays"].items():
        if mode not in ("block", "flag"):
            raise ConfigError(f"gates.overlays.{k} must be block or flag")
    # storage paths relative to project root
    for k in ("db_path", "reports_dir"):
        if k in cfg["storage"] and not Path(cfg["storage"][k]).is_absolute():
            cfg["storage"][k] = str(ROOT / cfg["storage"][k])
    cfg["_path"] = str(p)
    return cfg


FEASO_KEYS = ("interest_rate", "cap_months", "draw_factor", "target_yield", "yield_basis",
              "opex_ratio", "ltv_cap", "units", "unit_cost_installed", "misc", "min_io_cover")


def feaso_inputs(cfg: dict, price: float, existing_rent: float, unit_rent: float,
                 overrides: dict | None = None) -> FeasoInputs:
    f = copy.deepcopy(cfg["feaso"])
    f.update({k: v for k, v in (overrides or {}).items() if k in FEASO_KEYS and v is not None})
    return FeasoInputs(price=price, existing_rent_pw=existing_rent, unit_rent_pw=unit_rent,
                       **{k: f[k] for k in FEASO_KEYS if k in f})


def stress_spec(cfg: dict) -> StressSpec:
    return StressSpec(**cfg["feaso"]["stress"])
