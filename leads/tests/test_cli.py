from __future__ import annotations

import pytest

from leads_engine.cli import main


def test_config_validate_prints_categories_projection_and_hash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["config", "validate"]) == 0
    out = capsys.readouterr().out
    assert "1. plumbing" in out
    assert "7. auto_repair" in out
    assert "total LLM" in out
    assert "config_hash=" in out


def test_config_validate_fails_cleanly_on_bad_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LEADS_CONFIG_DIR", str(tmp_path))
    from leads_engine.settings import get_settings

    get_settings.cache_clear()
    assert main(["config", "validate"]) == 1
    assert "error:" in capsys.readouterr().err


def test_db_check_reports_unreachable_db(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LEADS_DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")
    from leads_engine.settings import get_settings

    get_settings.cache_clear()
    assert main(["db", "check"]) == 1
    assert "error:" in capsys.readouterr().err
