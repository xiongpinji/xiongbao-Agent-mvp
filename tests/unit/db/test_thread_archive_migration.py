"""Archive schema is a real fresh/upgrade/reentry contract, not a DTO fallback."""

import sqlite3
from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool


def test_fresh_archive_schema_is_checked_nullable_and_reentry_is_idempotent(tmp_path):
    pool = SqlitePool(tmp_path / "archive.db")
    try:
        migrate.run_migrations(pool)
        with pool.connect() as conn:
            columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(threads)")}
            assert columns["archived_at"]["type"] == "INTEGER"
            assert columns["archived_at"]["notnull"] == 0
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] >= 38
        migrate.run_migrations(pool)
    finally:
        pool.close()


@pytest.mark.parametrize("prior", [36, 37])
def test_real_pre_archive_upgrade_keeps_rows_and_binding(tmp_path, monkeypatch, prior):
    pool = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect: [(v, p) for v, p in discover(dialect) if v <= prior],
            )
            migrate.run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES ('owner','synthetic','user',1)"
            )
            conn.execute(
                "INSERT INTO agents(agent_id,user_id,name,created_at,updated_at) VALUES ('a',1,'a',1,1)"
            )
            conn.execute(
                "INSERT INTO threads(thread_id,agent_id,user_id,channel_type,session_key,last_active,created_at) VALUES ('t','a',1,'dashboard','a:dashboard:1:dm',0,1)"
            )
            conn.execute(
                "INSERT INTO sessions(session_key,agent_id,user_id,channel_type,chat_type,thread_id,updated_at) VALUES ('a:dashboard:1:dm','a',1,'dashboard','dm','t',1)"
            )
        migrate.run_migrations(pool)
        with pool.connect() as conn:
            row = conn.execute("SELECT * FROM threads WHERE thread_id='t'").fetchone()
            assert row["archived_at"] is None and row["session_key"] == "a:dashboard:1:dm"
            assert conn.execute("SELECT thread_id FROM sessions").fetchone()[0] == "t"
            for value in (0, -1, 9007199254740992):
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute("UPDATE threads SET archived_at=? WHERE thread_id='t'", (value,))
    finally:
        pool.close()


def test_recorded_current_missing_archive_index_is_not_repaired(tmp_path):
    pool = SqlitePool(tmp_path / "bad.db")
    try:
        migrate.run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute("DROP INDEX idx_threads_user_archive")
        with pytest.raises(RuntimeError, match="archive"):
            migrate.run_migrations(pool)
        with pool.connect() as conn:
            assert (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='idx_threads_user_archive'"
                ).fetchone()
                is None
            )
    finally:
        pool.close()


@pytest.mark.parametrize(
    "column", ["archived_at INTEGER", "archived_at TEXT", "archived_at INTEGER NOT NULL DEFAULT 0"]
)
def test_incompatible_manual_column_at_37_fails_without_rebuild(tmp_path, monkeypatch, column):
    pool = SqlitePool(tmp_path / "manual.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 37],
            )
            migrate.run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute("ALTER TABLE threads ADD COLUMN " + column)
        with pool.connect() as conn:
            before = conn.execute("SELECT sql FROM sqlite_master WHERE name='threads'").fetchone()[
                0
            ]
        with pytest.raises(RuntimeError, match="archive"):
            migrate.run_migrations(pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 37
            assert (
                conn.execute("SELECT sql FROM sqlite_master WHERE name='threads'").fetchone()[0]
                == before
            )
    finally:
        pool.close()


@pytest.mark.parametrize("damage", ["column", "index"])
def test_recorded_38_missing_or_wrong_schema_fails(tmp_path, monkeypatch, damage):
    pool = SqlitePool(tmp_path / "damaged.db")
    discover = migrate._discover
    try:
        if damage == "column":
            with monkeypatch.context() as patch:
                patch.setattr(
                    migrate,
                    "_discover",
                    lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 37],
                )
                migrate.run_migrations(pool)
            with pool.transaction() as conn:
                conn.execute("UPDATE _schema_version SET version=38")
        else:
            with monkeypatch.context() as patch:
                patch.setattr(
                    migrate,
                    "_discover",
                    lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 38],
                )
                migrate.run_migrations(pool)
            with pool.transaction() as conn:
                conn.execute("DROP INDEX idx_threads_user_archive")
                conn.execute("CREATE INDEX idx_threads_user_archive ON threads(user_id,thread_id)")
        with pytest.raises(RuntimeError, match="archive"):
            migrate.run_migrations(pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 38
    finally:
        pool.close()


def test_complete_current_schema_keeps_higher_watermark(tmp_path):
    pool = SqlitePool(tmp_path / "high.db")
    try:
        migrate.run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute("UPDATE _schema_version SET version=99")
        migrate.run_migrations(pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 99
    finally:
        pool.close()


@pytest.mark.parametrize("prior", [37, 38, 99])
def test_archive_users_only_legacy_advances_and_reenters_without_history(tmp_path, prior):
    pool = SqlitePool(tmp_path / "users-only.db")
    path = Path(migrate.__file__).parent / "migrations/038_thread_archive.sql"
    try:
        with pool.transaction() as conn:
            conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT)")
            conn.execute("INSERT INTO users VALUES (1, 'legacy')")
            conn.execute("CREATE TABLE _schema_version(version INTEGER NOT NULL)")
            conn.execute("INSERT INTO _schema_version VALUES (?)", (prior,))
        migrate._ensure_thread_archive_v38(pool, path)
        migrate._ensure_thread_archive_v38(pool, path)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == max(
                prior, 38
            )
            assert tuple(conn.execute("SELECT * FROM users").fetchone()) == (1, "legacy")
            assert (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name IN ('agents','threads','idx_threads_user_archive')"
                ).fetchall()
                == []
            )
    finally:
        pool.close()


def test_archive_missing_threads_with_agents_is_not_users_only_legacy(tmp_path):
    pool = SqlitePool(tmp_path / "broken-history.db")
    path = Path(migrate.__file__).parent / "migrations/038_thread_archive.sql"
    try:
        with pool.transaction() as conn:
            conn.execute("CREATE TABLE agents(id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE _schema_version(version INTEGER NOT NULL)")
            conn.execute("INSERT INTO _schema_version VALUES (37)")
        with pytest.raises(RuntimeError, match="archive"):
            migrate._ensure_thread_archive_v38(pool, path)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 37
    finally:
        pool.close()
