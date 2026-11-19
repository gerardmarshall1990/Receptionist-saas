from __future__ import annotations

import io
import json

import pytest

from leads_engine.log import bind_run, clear_run, configure_logging, get_logger
from leads_engine.settings import get_settings


def test_safe_defaults_spend_nothing() -> None:
    s = get_settings()
    assert s.dry_run is True
    assert s.run_budget_usd == 0
    assert s.scoring_model is None
    assert s.opener_model is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEADS_DRY_RUN", "false")
    monkeypatch.setenv("LEADS_RUN_BUDGET_USD", "12.5")
    monkeypatch.setenv("LEADS_SCORING_MODEL", "x-model")
    monkeypatch.setenv("LEADS_DATABASE_URL", "postgresql://u:p@h:1/db")
    get_settings.cache_clear()
    s = get_settings()
    assert (s.dry_run, s.run_budget_usd, s.scoring_model) == (False, 12.5, "x-model")
    assert s.database_url == "postgresql://u:p@h:1/db"


def test_negative_budget_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEADS_RUN_BUDGET_USD", "-1")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        get_settings()


def test_json_logs_carry_run_context() -> None:
    buf = io.StringIO()
    configure_logging("INFO", "json", stream=buf)
    try:
        bind_run("run-123", "stage0")
        get_logger("t").info("hello", count=3)
        get_logger("t").debug("filtered_out")
    finally:
        clear_run()
    lines = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert len(lines) == 1
    rec = lines[0]
    assert rec["event"] == "hello"
    assert rec["count"] == 3
    assert rec["run_id"] == "run-123"
    assert rec["stage"] == "stage0"
    assert rec["level"] == "info"
    assert "timestamp" in rec
