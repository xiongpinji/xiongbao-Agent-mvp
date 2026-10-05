"""Owned archive changes only personal visibility, using current SQL qualification."""

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def archive_db(tmp_path):
    pool = SqlitePool(tmp_path / "owned.db")
    try:
        run_migrations(pool)
        users = UserRepo(pool)
        for name in ("one", "two", "admin"):
            users.create(username=name, password_hash="synthetic", role="user")
        AgentRepo(pool).create(agent_id="a", user_id=1, name="Stopped")
        repo = ThreadRepo(pool)
        for tid, owner, title in (("one", 1, "Straße %_"), ("two", 2, "Foreign")):
            repo.insert(
                thread_id=tid,
                agent_id="a",
                user_id=owner,
                channel_type="dashboard",
                session_key=f"a:dashboard:{owner}:dm",
                title=title,
                last_active=0,
            )
        yield pool, repo
    finally:
        pool.close()


def test_narrow_idempotent_archive_and_restore_preserve_other_columns(archive_db):
    pool, repo = archive_db
    with pool.connect() as conn:
        before = dict(conn.execute("SELECT * FROM threads WHERE thread_id='one'").fetchone())
    receipt = repo.set_archive_owned(
        thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100
    )
    assert receipt.archived_at == 100
    assert (
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=200
        ).archived_at
        == 100
    )
    with pool.connect() as conn:
        after = dict(conn.execute("SELECT * FROM threads WHERE thread_id='one'").fetchone())
    assert after == before | {"archived_at": 100}
    assert repo.list_by_agent_user(agent_id="a", user_id=1) == []
    assert [
        r.thread_id for r in repo.list_by_agent_user(agent_id="a", user_id=1, archived=True)
    ] == ["one"]
    assert repo.get("one") is not None and len(repo.list_by_agent(agent_id="a")) == 2
    assert (
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=False, now=300
        ).archived_at
        is None
    )


def test_fresh_agent_access_and_real_owner_are_required(archive_db):
    pool, repo = archive_db
    assert (
        repo.set_archive_owned(
            thread_id="one", user_id=3, actor_is_admin=True, archived=True, now=100
        )
        is None
    )
    assert (
        repo.set_archive_owned(
            thread_id="two", user_id=2, actor_is_admin=False, archived=True, now=100
        )
        is None
    )
    with pool.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared=1 WHERE agent_id='a'")
    assert (
        repo.set_archive_owned(
            thread_id="two", user_id=2, actor_is_admin=False, archived=True, now=100
        ).archived_at
        == 100
    )
    with pool.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared=0 WHERE agent_id='a'")
    assert repo.list_archived_by_user(user_id=2, actor_is_admin=False) == []
    assert (
        repo.set_archive_owned(
            thread_id="two", user_id=2, actor_is_admin=False, archived=False, now=100
        )
        is None
    )
    assert repo.get("two").archived_at == 100


def test_global_literal_search_and_qualification_precede_pagination(archive_db):
    _, repo = archive_db
    assert repo.set_archive_owned(
        thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100
    )
    assert [
        r.thread_id
        for r in repo.list_archived_by_user(
            user_id=1, actor_is_admin=False, q="STRASSE %_", limit=1
        )
    ] == ["one"]
    assert repo.list_archived_by_user(user_id=1, actor_is_admin=False, q="not present") == []


def test_files_exact_qualification_does_not_require_project_or_current_binding(archive_db):
    pool, repo = archive_db
    with pool.transaction() as conn:
        conn.execute("UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='a'")
        conn.execute("DELETE FROM threads WHERE thread_id='two'")
    assert (
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100
        ).archived_at
        == 100
    )
    repo.insert(
        thread_id="extra",
        agent_id="a",
        user_id=1,
        channel_type="dashboard",
        session_key="a:dashboard:1:dm",
    )
    assert (
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=False, now=100
        )
        is None
    )
    assert repo.get("one").archived_at == 100


@pytest.mark.parametrize("change", ["owner", "channel", "session"])
def test_invalid_files_identity_refuses_both_states_with_zero_write(archive_db, change):
    pool, repo = archive_db
    with pool.transaction() as conn:
        conn.execute("UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='a'")
        conn.execute("DELETE FROM threads WHERE thread_id='two'")
    assert repo.set_archive_owned(
        thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100
    )
    sql = {
        "owner": "UPDATE agents SET user_id=2 WHERE agent_id='a'",
        "channel": "UPDATE threads SET channel_type='telegram' WHERE thread_id='one'",
        "session": "UPDATE threads SET session_key='a:dashboard:1:group' WHERE thread_id='one'",
    }[change]
    with pool.transaction() as conn:
        conn.execute(sql)
    with pool.connect() as conn:
        before = dict(conn.execute("SELECT * FROM threads WHERE thread_id='one'").fetchone())
    for value in (True, False):
        assert (
            repo.set_archive_owned(
                thread_id="one", user_id=1, actor_is_admin=True, archived=value, now=200
            )
            is None
        )
    with pool.connect() as conn:
        assert (
            dict(conn.execute("SELECT * FROM threads WHERE thread_id='one'").fetchone()) == before
        )


def test_archive_order_and_normal_pin_order_are_independent(archive_db):
    _, repo = archive_db
    repo.insert(
        thread_id="older",
        agent_id="a",
        user_id=1,
        channel_type="dashboard",
        session_key="a:dashboard:1:dm",
        last_active=10,
    )
    repo.set_pinned("older", True)
    for tid, stamp in (("older", 100), ("one", 200)):
        repo.set_archive_owned(
            thread_id=tid, user_id=1, actor_is_admin=False, archived=True, now=stamp
        )
    assert [
        r.thread_id for r in repo.list_by_agent_user(agent_id="a", user_id=1, archived=True)
    ] == ["one", "older"]
    for tid in ("older", "one"):
        repo.set_archive_owned(
            thread_id=tid, user_id=1, actor_is_admin=False, archived=False, now=300
        )
    assert [r.thread_id for r in repo.list_by_agent_user(agent_id="a", user_id=1)] == [
        "older",
        "one",
    ]


def test_seventy_row_literal_match_is_filtered_before_prefix_limit(archive_db):
    _, repo = archive_db
    for index in range(70):
        tid = f"row{index:03}"
        repo.insert(
            thread_id=tid,
            agent_id="a",
            user_id=1,
            channel_type="dashboard",
            session_key="a:dashboard:1:dm",
            title="Ｓｔｒａße %_ needle" if index == 56 else f"ordinary {index}",
            last_active=70 - index,
        )
    assert [
        row.thread_id
        for row in repo.list_by_agent_user(agent_id="a", user_id=1, q="STRASSE %_ NEEDLE", limit=1)
    ] == ["row056"]
    for index in range(70):
        repo.set_archive_owned(
            thread_id=f"row{index:03}",
            user_id=1,
            actor_is_admin=False,
            archived=True,
            now=100 + index,
        )
    assert [
        row.thread_id
        for row in repo.list_by_agent_user(
            agent_id="a", user_id=1, q="STRASSE %_ NEEDLE", archived=True, limit=1
        )
    ] == ["row056"]
    assert [
        row.thread_id
        for row in repo.list_archived_by_user(
            user_id=1, actor_is_admin=False, q="STRASSE %_ NEEDLE", limit=1
        )
    ] == ["row056"]
    assert (
        len(repo.list_archived_by_user(user_id=1, actor_is_admin=False, limit=20, offset=60)) == 10
    )
