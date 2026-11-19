"""Environment-based runtime settings.

Everything that differs between machines or runs (database URLs, budget, dry-run,
model overrides) comes from env vars prefixed ``LEADS_`` or from a ``.env`` file.
Business rules (categories, weights, models) live in versioned YAML under
``config/`` and are loaded by :mod:`leads_engine.config_loader`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEADS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql://leads:leads@localhost:5433/frontdesk_leads"
    data_dir: Path = PROJECT_ROOT / "data"
    config_dir: Path = PROJECT_ROOT / "config"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Safety defaults: nothing spends money unless both of these are changed.
    dry_run: bool = True
    run_budget_usd: float = Field(default=0.0, ge=0.0)

    # Lead explorer UI. Binds to localhost unless told otherwise. If ui_token is set,
    # every request must carry it (cookie set via /?token=..., or X-Leads-Token header).
    ui_host: str = "127.0.0.1"
    ui_port: int = Field(default=8765, ge=1, le=65535)
    ui_token: str | None = None

    # Optional overrides for config/models.yaml.
    scoring_model: str | None = None
    opener_model: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
