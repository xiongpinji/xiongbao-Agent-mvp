import sqlite3
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db import migrate
from octop.infra.db.migrate import _max_discovered_version, run_migrations
from octop.infra.db.pool import SqlitePool


def test_fresh_schema_and_reentry(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "public.db")
    try:
        run_migrations(pool)
        with pool.connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_spaces)")}
            assert "public_connectors_revision" in columns
            assert (
                conn.execute(
                    "SELECT initialized FROM project_public_connector_key_state"
                ).fetchone()[0]
                == 0
            )
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == _max_discovered_version("sqlite")
        run_migrations(pool)
    finally:
        pool.close()


def upgrade_base(pool: SqlitePool, monkeypatch: pytest.MonkeyPatch) -> None:
    discover = migrate._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migrate,
            "_discover",
            lambda dialect: [
                (version, path) for version, path in discover(dialect) if version <= 38
            ],
        )
        run_migrations(pool)


def test_real_038_upgrade_and_preserving_reentry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SqlitePool(tmp_path / "upgrade.db")
    try:
        upgrade_base(pool, monkeypatch)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 38
            before = list(conn.execute("SELECT * FROM users"))
        run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute("INSERT INTO project_public_connector_ids VALUES('retained-id',1)")
            conn.execute("UPDATE project_public_connector_key_state SET initialized=1")
            conn.execute("UPDATE _schema_version SET version=99")
        run_migrations(pool)
        with pool.connect() as conn:
            assert list(conn.execute("SELECT * FROM users")) == before
            assert (
                conn.execute("SELECT connector_id FROM project_public_connector_ids").fetchone()[0]
                == "retained-id"
            )
            assert (
                conn.execute(
                    "SELECT initialized FROM project_public_connector_key_state"
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 99
    finally:
        pool.close()


@pytest.mark.parametrize("corruption", ["table", "index", "guard", "definition", "unique_index"])
def test_recorded_high_watermark_rejects_missing_or_wrong_schema(
    tmp_path: Path, corruption: str
) -> None:
    pool = SqlitePool(tmp_path / "bad.db")
    try:
        run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute("UPDATE _schema_version SET version=99")
            if corruption == "table":
                conn.execute("DROP TABLE project_public_connectors")
            elif corruption == "index":
                conn.execute("DROP INDEX idx_project_public_connectors_project")
            elif corruption == "guard":
                conn.execute("DELETE FROM project_public_connector_key_state")
            elif corruption == "unique_index":
                conn.execute(
                    "CREATE UNIQUE INDEX unexpected_name_unique ON project_public_connectors(display_name)"
                )
            else:
                conn.execute("DROP TABLE project_public_connector_key_state")
                conn.execute(
                    "CREATE TABLE project_public_connector_key_state(id INTEGER PRIMARY KEY,purpose TEXT,initialized INTEGER)"
                )
                conn.execute(
                    "INSERT INTO project_public_connector_key_state VALUES(1,'octop.project_public_connector.credentials.v1',0)"
                )
        with pool.connect() as conn:
            before = "\n".join(conn.iterdump())
        with pytest.raises(RuntimeError, match="public connector"):
            run_migrations(pool)
        with pool.connect() as conn:
            assert "\n".join(conn.iterdump()) == before
            assert not conn.in_transaction
    finally:
        pool.close()


def test_partial_upgrade_never_fills_missing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SqlitePool(tmp_path / "partial.db")
    try:
        upgrade_base(pool, monkeypatch)
        with pool.transaction() as conn:
            conn.execute(
                "ALTER TABLE project_spaces ADD COLUMN public_connectors_revision INTEGER NOT NULL DEFAULT 1"
            )
        with pool.connect() as conn:
            before = "\n".join(conn.iterdump())
        with pytest.raises(RuntimeError, match="revision definition"):
            run_migrations(pool)
        with pool.connect() as conn:
            assert "\n".join(conn.iterdump()) == before
    finally:
        pool.close()


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_039_ddl_guard_and_watermark_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    pool = SqlitePool(tmp_path / "atomic.db")
    try:
        upgrade_base(pool, monkeypatch)
        with pool.connect() as conn:
            before = "\n".join(conn.iterdump())
        primary = error("synthetic migration failure")

        def fail(conn: Any, statements: Any, *, dialect: str) -> None:
            assert conn.in_transaction
            assert (
                conn.execute(
                    "SELECT initialized FROM project_public_connector_key_state"
                ).fetchone()[0]
                == 0
            )
            raise primary

        monkeypatch.setattr(migrate, "_validate_public_connector_v39", fail)
        with pytest.raises(error) as exc:
            run_migrations(pool)
        assert exc.value is primary
        with pool.connect() as conn:
            assert "\n".join(conn.iterdump()) == before
            assert not conn.in_transaction
    finally:
        pool.close()


def test_sqlite_check_foreign_keys_and_postgres_pair_equivalence(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "ddl.db")
    try:
        run_migrations(pool)
        with pool.connect() as conn:
            fks = list(conn.execute("PRAGMA foreign_key_list(project_public_connectors)"))
            assert {(row["from"], row["table"], row["on_delete"]) for row in fks} == {
                ("connector_id", "project_public_connector_ids", "RESTRICT"),
                ("project_id", "project_spaces", "CASCADE"),
                ("creator_user_id", "users", "SET NULL"),
                ("updated_by", "users", "SET NULL"),
                ("revoked_by", "users", "SET NULL"),
            }
        suffix = Path(migrate.__file__).parent / "migrations"
        sql = (suffix / "039_project_public_connectors.sql").read_text()
        pg = (suffix / "039_project_public_connectors.pg.sql").read_text()
        normalized_pg = (
            pg.replace(
                "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
                "INTEGER PRIMARY KEY AUTOINCREMENT",
            )
            .replace("BIGINT", "INTEGER")
            .replace("BYTEA", "BLOB")
            .replace("octet_length", "length")
        )
        assert migrate._d1_sql_normalized(sql) == migrate._d1_sql_normalized(normalized_pg)
        with pool.transaction() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO project_public_connector_ids VALUES('',1)")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE project_public_connector_key_state SET initialized=2")
    finally:
        pool.close()
