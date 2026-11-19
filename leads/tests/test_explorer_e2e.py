"""Real browser against a real server: starts uvicorn on a throwaway DB and runs
tests/e2e/explorer_smoke.cjs with Playwright (Node). Skipped when Node/Playwright
or the test database is unavailable."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from leads_engine import db
from leads_engine.settings import Settings
from leads_engine.universe.build import build
from leads_engine.universe.load import load
from tests.conftest import needs_db
from tests.test_explorer import CFG, _rows

SCRIPT = Path(__file__).parent / "e2e" / "explorer_smoke.cjs"


def _node_env() -> dict[str, str] | None:
    node = shutil.which("node")
    if not node:
        return None
    npm_root = subprocess.run(
        ["npm", "root", "-g"], capture_output=True, text=True, check=False
    ).stdout.strip()
    env = {**os.environ, "NODE_PATH": npm_root}
    ok = subprocess.run(
        [node, "-e", "require('playwright')"], env=env, capture_output=True, check=False
    )
    return env if ok.returncode == 0 else None


NODE_ENV = _node_env()


@pytest.fixture
def server(fresh_db: str, tmp_path: Path) -> Iterator[str]:
    from leads_engine.explorer.app import create_app

    db.migrate(fresh_db)
    out = tmp_path / "built.parquet"
    build(tmp_path / "x", CFG, list(CFG.categories), "2026-11-18.0", out, "seed", rows_in=_rows())
    load(fresh_db, out, "all")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(Settings(database_url=fresh_db), CFG)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    srv.should_exit = True
    thread.join(timeout=10)
    app.state.pool.close()


@pytest.mark.db
@needs_db
@pytest.mark.skipif(NODE_ENV is None, reason="node + playwright not available")
def test_explorer_in_a_real_browser(server: str) -> None:
    shots = os.environ.get("LEADS_E2E_SCREENSHOTS", "")
    assert NODE_ENV is not None
    result = subprocess.run(
        ["node", str(SCRIPT), server, shots],
        env=NODE_ENV, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
