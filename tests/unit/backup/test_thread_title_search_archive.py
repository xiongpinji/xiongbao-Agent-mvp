"""Owned real SQLite archives and preserved-chat spools, with closed resources."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup.chats import capture_chat_tables, restore_preserved_chats
from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.project_plan_keys import normalize_project_plan_key


def _seed(pool: SqlitePool, *, remap: bool = False) -> None:
    users = UserRepo(pool)
    if remap:
        users.create(username="keeper", password_hash="synthetic", role="user")
    uid = users.create(username="owner", password_hash="synthetic", role="user")
    AgentRepo(pool).create(agent_id="expert", user_id=uid, name="Expert")


@pytest.mark.parametrize("legacy_spool", [False, True])
def test_preserved_spool_recomputes_key_after_user_mapping(
    tmp_path: Path, legacy_spool: bool
) -> None:
    source = SqlitePool(tmp_path / "source.sqlite")
    target = SqlitePool(tmp_path / "target.sqlite")
    spool_path = tmp_path / "owned-spool.sqlite"
    try:
        for pool in (source, target):
            migrate.run_migrations(pool)
        _seed(source)
        _seed(target, remap=True)
        ThreadRepo(source).insert(
            thread_id="thread",
            agent_id="expert",
            user_id=1,
            channel_type="dashboard",
            session_key="expert:dashboard:1:dm",
            title="Ｓｔｒａße",
        )
        capture_chat_tables(source, spool_path)
        with closing(sqlite3.connect(spool_path)) as spool:
            if legacy_spool:
                spool.execute("ALTER TABLE threads DROP COLUMN title_search_key")
            else:
                spool.execute("UPDATE threads SET title_search_key='stale'")
            spool.commit()
        inserted, skipped = restore_preserved_chats(target, spool_path)
        assert inserted > 0 and skipped == 0
        with target.connect() as conn:
            row = conn.execute(
                "SELECT user_id,title,title_search_key,session_key FROM threads"
            ).fetchone()
        assert tuple(row) == (2, "Ｓｔｒａße", "strasse", "expert:dashboard:2:dm")
        assert [
            r.thread_id
            for r in ThreadRepo(target).list_by_agent_user(
                agent_id="expert", user_id=2, q="STRASSE"
            )
        ] == ["thread"]
        spool_path.unlink()  # Windows proves the captured/imported spool handles are closed.
    finally:
        source.close()
        target.close()


def test_spool_failure_rolls_back_existing_chats_and_closes_spool(tmp_path: Path) -> None:
    source = SqlitePool(tmp_path / "source.sqlite")
    target = SqlitePool(tmp_path / "target.sqlite")
    spool_path = tmp_path / "failure-spool.sqlite"
    try:
        for pool in (source, target):
            migrate.run_migrations(pool)
            _seed(pool)
        for pool, tid in ((source, "new"), (target, "existing")):
            ThreadRepo(pool).insert(
                thread_id=tid,
                agent_id="expert",
                user_id=1,
                channel_type="dashboard",
                session_key="owned",
                title="Straße",
            )
        capture_chat_tables(source, spool_path)
        with target.connect() as conn:
            conn.execute(
                "CREATE TRIGGER owned_import_failure BEFORE INSERT ON threads BEGIN SELECT RAISE(ABORT,'owned failure'); END"
            )
        with pytest.raises(Exception, match="owned failure"):
            restore_preserved_chats(target, spool_path)
        assert [
            r.thread_id
            for r in ThreadRepo(target).list_by_agent_user(
                agent_id="expert", user_id=1, q="strasse"
            )
        ] == ["existing"]
        spool_path.unlink()
    finally:
        source.close()
        target.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_real_full_archive_restores_old_or_new_title_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    src_layout = PathLayout(tmp_path / "source")
    dst_layout = PathLayout(tmp_path / "target")
    source = SqlitePool(src_layout.db)
    target = SqlitePool(dst_layout.db)
    try:
        if legacy:
            discovered = migrate._discover
            with monkeypatch.context() as patch:
                patch.setattr(
                    migrate,
                    "_discover",
                    lambda dialect="sqlite": [
                        item for item in discovered(dialect) if item[0] <= 35
                    ],
                )
                migrate.run_migrations(source)
        else:
            migrate.run_migrations(source)
        _seed(source)
        title = "old full-width Ａ " * 5
        with source.transaction() as conn:
            if legacy:
                conn.execute(
                    "INSERT INTO threads(thread_id,agent_id,user_id,channel_type,session_key,title,last_active,created_at) VALUES ('archived','expert',1,'dashboard','owned',?,1,1)",
                    (title,),
                )
            else:
                conn.execute(
                    "INSERT INTO threads(thread_id,agent_id,user_id,channel_type,session_key,title,title_search_key,last_active,created_at) VALUES ('archived','expert',1,'dashboard','owned',?,?,1,1)",
                    (title, normalize_project_plan_key(title)),
                )
        archive = tmp_path / "owned.tar.gz"
        create_system_backup(
            paths=src_layout,
            agent_rows=[],
            pool=source,
            db_config=DatabaseConfig(),
            dest=archive,
            include_chats=True,
        )
        migrate.run_migrations(target)
        restore_system_backup(archive, paths=dst_layout, pool=target, db_config=DatabaseConfig())
        with target.connect() as conn:
            row = conn.execute("SELECT title,title_search_key FROM threads").fetchone()
        assert tuple(row) == (title, normalize_project_plan_key(title))
    finally:
        source.close()
        target.close()


def test_legacy_target_without_key_keeps_original_spool_contract(tmp_path: Path) -> None:
    source = SqlitePool(tmp_path / "source.sqlite")
    target = SqlitePool(tmp_path / "target.sqlite")
    spool_path = tmp_path / "legacy.sqlite"
    try:
        for pool in (source, target):
            migrate.run_migrations(pool)
            _seed(pool)
        ThreadRepo(source).insert(
            thread_id="old",
            agent_id="expert",
            user_id=1,
            channel_type="dashboard",
            session_key="owned",
            title="old",
        )
        capture_chat_tables(source, spool_path)
        with closing(sqlite3.connect(spool_path)) as spool:
            spool.execute("ALTER TABLE threads DROP COLUMN title_search_key")
            spool.commit()
        with target.connect() as conn:
            conn.execute("ALTER TABLE threads DROP COLUMN title_search_key")
        assert restore_preserved_chats(target, spool_path)[1] == 0
        assert ThreadRepo(target).get("old").title == "old"
    finally:
        source.close()
        target.close()
