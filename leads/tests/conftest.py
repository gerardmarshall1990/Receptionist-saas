from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from leads_engine.log import clear_run, configure_logging
from leads_engine.settings import PROJECT_ROOT, get_settings

REAL_CONFIG = PROJECT_ROOT / "config"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test sees settings built from its own env, never a developer's .env."""
    for key in list(os.environ):
        if key.startswith("LEADS_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(PROJECT_ROOT / "tests")  # no .env here
    get_settings.cache_clear()
    configure_logging("INFO", "json")  # fresh sink: a test may have pointed logs at a buffer
    yield
    clear_run()
    get_settings.cache_clear()


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(REAL_CONFIG, dst)
    return dst


Mutator = Callable[[str, Callable[[dict[str, Any]], None]], None]


@pytest.fixture
def mutate(config_copy: Path) -> Mutator:
    """mutate("categories/plumbing.yaml", lambda d: d.update(...))"""

    def _apply(rel: str, fn: Callable[[dict[str, Any]], None]) -> None:
        path = config_copy / rel
        data = yaml.safe_load(path.read_text())
        fn(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    return _apply


TEST_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """A throwaway database per test, dropped afterwards."""
    assert TEST_URL
    name = f"leads_t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(TEST_URL, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    base, _, _ = TEST_URL.rpartition("/")
    try:
        yield f"{base}/{name}"
    finally:
        with psycopg.connect(TEST_URL, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')
