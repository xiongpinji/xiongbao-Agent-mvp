"""Canonical SQLite v36 upgrade, explicit repair, rollback and no-rescan reentry."""

from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.project_plan_keys import normalize_project_plan_key

MIGRATIONS = Path(migrate.__file__).parent / "migrations"


def _legacy(pool: SqlitePool, monkeypatch: pytest.MonkeyPatch) -> None:
    discovered = migrate._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migrate,
            "_discover",
            lambda dialect="sqlite": [item for item in discovered(dialect) if item[0] <= 35],
        )
        migrate.run_migrations(pool)
    UserRepo(pool).create(username="owner", password_hash="synthetic", role="user")
    AgentRepo(pool).create(agent_id="expert", user_id=1, name="Expert")
    with pool.transaction() as conn:
        for index, title in enumerate((None, "", "Ｓｔｒａße", "old long title " * 10)):
            conn.execute(
                "INSERT INTO threads(thread_id,agent_id,user_id,channel_type,session_key,title,last_active,created_at) VALUES (?, 'expert',1,'dashboard','owned',?,1,1)",
                (str(index), title),
            )


def test_upgrade_full_values_and_normal_reentry_no_title_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SqlitePool(tmp_path / "upgrade.sqlite")
    try:
        _legacy(pool, monkeypatch)
        with pool.connect() as conn:
            before = [tuple(r) for r in conn.execute("SELECT * FROM threads ORDER BY id")]
        migrate.run_migrations(pool)
        trace: list[str] = []
        with pool.connect() as conn:
            rows = conn.execute("SELECT * FROM threads ORDER BY id").fetchall()
            assert [tuple(r)[:-1] for r in rows] == before
            assert [r["title_search_key"] for r in rows] == [
                normalize_project_plan_key(r["title"] or "") for r in rows
            ]
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == migrate._max_discovered_version("sqlite")
            conn.set_trace_callback(trace.append)
        try:
            migrate.run_migrations(pool)
        finally:
            with pool.connect() as conn:
                conn.set_trace_callback(None)
        assert not any(
            "selectid,title,title_search_key" in sql.lower().replace(" ", "") for sql in trace
        )
        assert not any("FROM threads ORDER BY id" in sql for sql in trace)
    finally:
        pool.close()


@pytest.mark.parametrize("partial", [False, True])
def test_explicit_repair_and_higher_watermark_do_not_clamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    pool = SqlitePool(tmp_path / "repair.sqlite")
    try:
        _legacy(pool, monkeypatch)
        # Keep later activated foundations valid while testing only v36 repair.
        migrate.run_migrations(pool)
        with pool.connect() as conn:
            if partial:
                conn.execute("UPDATE threads SET title_search_key=''")
            else:
                conn.execute("ALTER TABLE threads DROP COLUMN title_search_key")
            conn.execute("UPDATE _schema_version SET version=99")
        path = MIGRATIONS / "036_thread_title_search_key.sql"
        migrate.run_migrations(pool)
        if not partial:
            with pool.connect() as conn:
                assert (
                    conn.execute(
                        "SELECT title_search_key FROM threads WHERE thread_id='2'"
                    ).fetchone()[0]
                    == "strasse"
                )
        migrate._ensure_thread_title_search_key_v36(pool, path)
        migrate._ensure_thread_title_search_key_v36(pool, path)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 99
            assert (
                conn.execute("SELECT title_search_key FROM threads WHERE thread_id='2'").fetchone()[
                    0
                ]
                == "strasse"
            )
    finally:
        pool.close()


def test_pre_key_v1_title_repair_precedes_final_search_key(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "v1.sqlite")
    try:
        with pool.connect() as conn:
            conn.executescript((MIGRATIONS / "001_initial.sql").read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES ('owner','synthetic','user',1)"
            )
            conn.execute(
                "INSERT INTO agents(agent_id,user_id,name,created_at,updated_at) VALUES ('expert',1,'Expert',1,1)"
            )
            conn.execute(
                "INSERT INTO threads(thread_id,agent_id,user_id,channel_type,session_key,title,last_active,created_at) VALUES ('legacy','expert',1,'dashboard','owned',?,1,1)",
                ("Ａ" * 40,),
            )
        migrate.run_migrations(pool)
        with pool.connect() as conn:
            row = conn.execute("SELECT title,title_search_key FROM threads").fetchone()
            assert tuple(row) == ("Ａ" * 39 + "…", "a" * 39 + "...")
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        pool.close()


@pytest.mark.parametrize("stage", ["backfill", "watermark"])
def test_failed_upgrade_rolls_back_column_keys_and_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    pool = SqlitePool(tmp_path / "rollback.sqlite")
    try:
        _legacy(pool, monkeypatch)
        with pool.connect() as conn:
            table = "threads" if stage == "backfill" else "_schema_version"
            conn.execute(
                f"CREATE TRIGGER owned_failure BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'owned failure'); END"
            )
        with pytest.raises(Exception, match="owned failure"):
            migrate._ensure_thread_title_search_key_v36(
                pool, MIGRATIONS / "036_thread_title_search_key.sql"
            )
        with pool.connect() as conn:
            assert "title_search_key" not in {
                r["name"] for r in conn.execute("PRAGMA table_info(threads)")
            }
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 35
            assert not conn.in_transaction
    finally:
        pool.close()
