"""Archive state survives real SQLite backup/spool import, including owner remap."""

import hashlib
import sqlite3
from contextlib import closing

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup.chats import capture_chat_tables, restore_preserved_chats
from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.thread_messages import ThreadMessageInput, ThreadMessageRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.paths import PathLayout


def _seed(pool, remap=False):
    users = UserRepo(pool)
    if remap:
        users.create(username="keeper", password_hash="synthetic", role="user")
    uid = users.create(username="owner", password_hash="synthetic", role="user")
    AgentRepo(pool).create(agent_id="expert", user_id=uid, name="Expert")
    return uid


def _thread(pool, uid, tid="thread"):
    ThreadRepo(pool).insert(
        thread_id=tid,
        agent_id="expert",
        user_id=uid,
        channel_type="dashboard",
        session_key=f"expert:dashboard:{uid}:dm",
        title="Straße",
    )
    SessionRepo(pool).bind_dashboard_owner(
        session_key=f"expert:dashboard:{uid}:dm", agent_id="expert", user_id=uid, thread_id=tid
    )
    ThreadMessageRepo(pool).append_if_ready(
        tid, [ThreadMessageInput("m", "user", '{"text":"owned history"}', 10)]
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_preserved_chat_archive_old_or_current_spool_with_remap(tmp_path, legacy):
    source = SqlitePool(tmp_path / "source.sqlite")
    target = SqlitePool(tmp_path / "target.sqlite")
    spool_path = tmp_path / "spool.sqlite"
    try:
        for pool in (source, target):
            migrate.run_migrations(pool)
        _thread(source, _seed(source))
        _seed(target, remap=True)
        ThreadRepo(source).set_archive_owned(
            thread_id="thread", user_id=1, actor_is_admin=False, archived=True, now=100
        )
        assert ThreadRepo(source).list_by_agent_user(agent_id="expert", user_id=1) == []
        capture_chat_tables(source, spool_path)
        with closing(sqlite3.connect(spool_path)) as spool:
            assert spool.execute("SELECT archived_at FROM threads").fetchone()[0] == 100
            if legacy:
                spool.execute("ALTER TABLE threads DROP COLUMN archived_at")
                spool.commit()
        inserted, skipped = restore_preserved_chats(target, spool_path)
        assert inserted > 0 and skipped == 0
        restored = ThreadRepo(target).get("thread")
        assert restored.user_id == 2 and restored.session_key == "expert:dashboard:2:dm"
        assert restored.archived_at == (None if legacy else 100)
        assert SessionRepo(target).get("expert:dashboard:2:dm").thread_id == "thread"
        assert (
            ThreadMessageRepo(target).range_rows("thread", 0, 10)[0].message_json
            == '{"text":"owned history"}'
        )
        spool_path.unlink()
    finally:
        source.close()
        target.close()


def test_new_spool_old_target_rejects_without_silent_drop_or_partial_import(tmp_path, monkeypatch):
    source = SqlitePool(tmp_path / "source.sqlite")
    target = SqlitePool(tmp_path / "old.sqlite")
    discover = migrate._discover
    spool_path = tmp_path / "spool.sqlite"
    try:
        migrate.run_migrations(source)
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 37],
            )
            migrate.run_migrations(target)
        _thread(source, _seed(source))
        _thread(target, _seed(target), tid="existing")
        ThreadRepo(source).set_archive_owned(
            thread_id="thread", user_id=1, actor_is_admin=False, archived=True, now=100
        )
        capture_chat_tables(source, spool_path)
        with target.connect() as conn:
            before = {
                table: [tuple(row) for row in conn.execute("SELECT * FROM " + table)]
                for table in ("threads", "sessions", "thread_messages")
            }
        with pytest.raises(sqlite3.OperationalError, match="archived_at"):
            restore_preserved_chats(target, spool_path)
        with target.connect() as conn:
            after = {
                table: [tuple(row) for row in conn.execute("SELECT * FROM " + table)]
                for table in before
            }
            assert not conn.in_transaction
        assert after == before
        spool_path.unlink()
    finally:
        source.close()
        target.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_full_backup_upgrades_37_or_preserves_38(tmp_path, monkeypatch, legacy):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    src = PathLayout(tmp_path / "source")
    dst = PathLayout(tmp_path / "target")
    source = SqlitePool(src.db)
    target = SqlitePool(dst.db)
    try:
        if legacy:
            discover = migrate._discover
            with monkeypatch.context() as patch:
                patch.setattr(
                    migrate,
                    "_discover",
                    lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 37],
                )
                migrate.run_migrations(source)
        else:
            migrate.run_migrations(source)
        _thread(source, _seed(source))
        if not legacy:
            ThreadRepo(source).set_archive_owned(
                thread_id="thread", user_id=1, actor_is_admin=False, archived=True, now=100
            )
        archive = tmp_path / "full.tar.gz"
        create_system_backup(
            paths=src,
            agent_rows=[],
            pool=source,
            db_config=DatabaseConfig(),
            dest=archive,
            include_chats=True,
        )
        migrate.run_migrations(target)
        restore_system_backup(archive, paths=dst, pool=target, db_config=DatabaseConfig())
        assert ThreadRepo(target).get("thread").archived_at == (None if legacy else 100)
        assert SessionRepo(target).get("expert:dashboard:1:dm").thread_id == "thread"
        assert (
            ThreadMessageRepo(target).range_rows("thread", 0, 10)[0].message_json
            == '{"text":"owned history"}'
        )
    finally:
        source.close()
        target.close()


def test_no_chat_backup_restore_preserves_live_archived_state_and_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    layout = PathLayout(tmp_path / "home")
    pool = SqlitePool(layout.db)
    try:
        migrate.run_migrations(pool)
        _thread(pool, _seed(pool))
        ThreadRepo(pool).set_archive_owned(
            thread_id="thread", user_id=1, actor_is_admin=False, archived=True, now=100
        )
        workspace = layout.agent_workspace("expert")
        history = workspace / "conversation_history" / "owned.json"
        history.parent.mkdir(parents=True)
        history.write_bytes(b"owned history bytes")
        before = hashlib.sha256(history.read_bytes()).hexdigest()
        archive = tmp_path / "no-chats.tar.gz"
        create_system_backup(
            paths=layout,
            agent_rows=[AgentRepo(pool).get("expert")],
            pool=pool,
            db_config=DatabaseConfig(),
            dest=archive,
            include_chats=False,
        )
        restore_system_backup(archive, paths=layout, pool=pool, db_config=DatabaseConfig())
        assert ThreadRepo(pool).get("thread").archived_at == 100
        assert SessionRepo(pool).get("expert:dashboard:1:dm").thread_id == "thread"
        assert hashlib.sha256(history.read_bytes()).hexdigest() == before
    finally:
        pool.close()
