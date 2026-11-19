from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from leads_engine import db
from tests.conftest import needs_db


def test_migration_files_are_numbered_and_unique() -> None:
    files = db.migration_files()
    assert files, "no migrations found"
    assert [f.name for f in files] == sorted(f.name for f in files)
    assert files[0].name == "001_init.sql"


def test_duckdb_reads_parquet_in_place(tmp_path: Path) -> None:
    con = db.duck_connect()
    f = tmp_path / "x.parquet"
    con.execute(
        f"copy (select 1 as id, 'a' as name union all select 2, 'b') to '{f}' (format parquet)"
    )
    assert con.execute("select count(*), max(name) from read_parquet(?)", [str(f)]).fetchone() == (
        2,
        "b",
    )


@pytest.mark.db
@needs_db
def test_migrate_is_idempotent(fresh_db: str) -> None:
    assert db.migrate(fresh_db) == [f.name for f in db.migration_files()]
    assert db.migrate(fresh_db) == []
    with psycopg.connect(fresh_db) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "select table_name from information_schema.tables where table_schema='public'"
            )
        }
    assert {"runs", "suppression", "schema_migrations", "businesses", "phones", "emails"} <= tables


@pytest.mark.db
@needs_db
def test_suppression_rows_are_permanent(fresh_db: str) -> None:
    db.migrate(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute(
            "insert into suppression(match_type, match_value, reason, source)"
            " values ('phone_e164', '+15555550100', 'opt-out', 'test')"
        )
        with pytest.raises(psycopg.errors.RaiseException, match="permanent"):
            conn.execute("delete from suppression")
        with pytest.raises(psycopg.errors.RaiseException, match="permanent"):
            conn.execute("update suppression set reason = 'x'")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "insert into suppression(match_type, match_value, reason, source)"
                " values ('phone_e164', '+15555550100', 'again', 'test')"
            )
        row = conn.execute("select count(*) from suppression").fetchone()
    assert row == (1,)


@pytest.mark.db
@needs_db
def test_runs_table_rejects_bad_status_and_negative_cost(fresh_db: str) -> None:
    db.migrate(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into runs(id, stage, status, dry_run, config_hash, budget_usd)"
                " values (gen_random_uuid(), 's', 'weird', true, 'h', 0)"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into runs(id, stage, status, dry_run, config_hash, budget_usd, cost_usd)"
                " values (gen_random_uuid(), 's', 'running', true, 'h', 0, -1)"
            )
