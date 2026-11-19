"""Postgres (working/serving data) and DuckDB (bulk Parquet) access."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import psycopg

from leads_engine.settings import PROJECT_ROOT

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
# Arbitrary constant so concurrent `leads db migrate` calls serialize.
_MIGRATION_LOCK_ID = 7_331_001


@contextmanager
def pg_connect(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, connect_timeout=5) as conn:
        yield conn


def check(url: str) -> str:
    with pg_connect(url) as conn:
        row = conn.execute("select version()").fetchone()
    return str(row[0]) if row else "unknown"


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    versions = [f.name[:3] for f in files]
    if len(versions) != len(set(versions)):
        raise ValueError(f"duplicate migration version numbers in {directory}")
    return files


def migrate(url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in order, each in its own transaction.

    Idempotent: re-running applies nothing. Returns the names applied this call.
    """
    applied_now: list[str] = []
    with pg_connect(url) as conn:
        conn.execute("select pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
        try:
            conn.execute(
                "create table if not exists schema_migrations ("
                " name text primary key, applied_at timestamptz not null default now())"
            )
            conn.commit()
            done = {r[0] for r in conn.execute("select name from schema_migrations").fetchall()}
            for path in migration_files(directory):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("insert into schema_migrations(name) values (%s)", (path.name,))
                applied_now.append(path.name)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
            conn.commit()
    return applied_now


def duck_connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """In-memory by default. Bulk files are read in place as Parquet, not imported."""
    return duckdb.connect(str(path) if path else ":memory:")
