"""Real SQLite matching and all current thread-title write primitives."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.threads import (
    ThreadRepo,
    clip_thread_title,
    repair_all_legacy_thread_titles,
)
from octop.infra.db.repos.users import UserRepo
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.utils.project_plan_keys import normalize_project_plan_key


@pytest.fixture
def search_db(tmp_path: Path) -> Iterator[tuple[SqlitePool, ThreadRepo]]:
    pool = SqlitePool(tmp_path / "threads.sqlite")
    try:
        run_migrations(pool)
        UserRepo(pool).create(username="owner", password_hash="synthetic", role="user")
        AgentRepo(pool).create(agent_id="expert", user_id=1, name="Expert")
        yield pool, ThreadRepo(pool)
    finally:
        pool.close()


def _insert(repo: ThreadRepo, tid: str, title: str | None, activity: int = 1) -> None:
    repo.insert(
        thread_id=tid,
        agent_id="expert",
        user_id=1,
        channel_type="dashboard",
        session_key="owned",
        title=title,
        last_active=activity,
    )


@pytest.mark.parametrize(
    "title,q,matched",
    [
        ("Straße", "STRASSE", True),
        ("ＡＢＣ", "abc", True),
        ("é", "e\u0301", True),
        ("Σ σ ς", "ς", True),
        ("İ", "i\u0307", True),
        ("I", "ı", False),
        ("café", "cafe", False),
        ("中文😀", "😀", True),
        ("10%_done", "%_", True),
        ("100_done", "%_", False),
        ("quote'back\\?", "'back\\?", True),
        (None, "unnamed", False),
        ("", "unnamed", False),
    ],
)
def test_literal_normalized_matching(search_db, title: str | None, q: str, matched: bool) -> None:
    _, repo = search_db
    _insert(repo, "one", title)
    result = repo.list_by_agent_user(agent_id="expert", user_id=1, q=q)
    assert [row.thread_id for row in result] == (["one"] if matched else [])


def test_prefix_order_and_filter_before_limit(search_db) -> None:
    pool, repo = search_db
    for tid, title, activity in (
        ("a", "hit", 2),
        ("b", "hit", 2),
        ("c", "miss", 99),
        ("d", "hit", 0),
    ):
        _insert(repo, tid, title, activity)
    repo.set_pinned("a", True)
    with pool.transaction() as conn:
        conn.execute("UPDATE threads SET created_at=3 WHERE thread_id='d'")
    assert [
        r.thread_id for r in repo.list_by_agent_user(agent_id="expert", user_id=1, q="hit", limit=1)
    ] == ["a"]
    assert [
        r.thread_id
        for r in repo.list_by_agent_user(agent_id="expert", user_id=1, q="hit", limit=101)
    ] == ["a", "d", "b"]
    assert repo.list_by_agent_user(agent_id="expert", user_id=1, q="  ") == repo.list_by_agent_user(
        agent_id="expert", user_id=1
    )


def test_writers_compute_key_from_final_title_and_if_null_noop(search_db) -> None:
    pool, repo = search_db
    _insert(repo, "one", "a" * 40 + "unsearchable-tail")
    assert repo.list_by_agent_user(agent_id="expert", user_id=1, q="tail") == []
    repo.update_title("one", "  Straße\nＦＯＯ  ")
    repo.set_title_if_null("one", "must not replace")
    _insert(repo, "two", None)
    repo.set_title_if_null("two", "ＦＯＯ")
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT title,title_search_key FROM threads ORDER BY thread_id"
        ).fetchall()
    assert [(r["title"], r["title_search_key"]) for r in rows] == [
        ("Straße ＦＯＯ", "strasse foo"),
        ("ＦＯＯ", "foo"),
    ]


def test_insert_failure_rolls_back_title_key_and_projection(search_db) -> None:
    pool, repo = search_db
    with pool.connect() as conn:
        conn.execute(
            "CREATE TRIGGER owned_projection_failure BEFORE INSERT ON thread_history_projection BEGIN SELECT RAISE(ABORT, 'owned failure'); END"
        )
    with pytest.raises(Exception, match="owned failure"):
        _insert(repo, "failed", "Straße")
    with pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM thread_history_projection").fetchone()[0] == 0


def test_non_title_writes_preserve_title_and_key(search_db) -> None:
    pool, repo = search_db
    _insert(repo, "one", "Straße")
    repo.set_pinned("one", True)
    repo.touch_last_active("one")
    repo.update_composer("one", model_ref="synthetic/model")
    repo.append_artifacts("one", ["owned.txt"])
    with pool.connect() as conn:
        row = conn.execute("SELECT title,title_search_key FROM threads").fetchone()
    assert tuple(row) == ("Straße", "strasse")


def test_legacy_repair_updates_title_and_key_without_changing_count(search_db) -> None:
    pool, repo = search_db
    _insert(repo, "legacy", "short")
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE threads SET title=?,title_search_key='stale' WHERE thread_id='legacy'",
            ("Ａ" * 40,),
        )
    assert repair_all_legacy_thread_titles(pool) == 1
    assert repair_all_legacy_thread_titles(pool) == 0
    with pool.connect() as conn:
        row = conn.execute("SELECT title,title_search_key FROM threads").fetchone()
    assert row["title_search_key"] == normalize_project_plan_key(row["title"])
    assert row["title"] == clip_thread_title("Ａ" * 41)


def test_project_task_null_insert_and_registry_blank_preserve_old_signature(search_db) -> None:
    pool, repo = search_db
    with pool.transaction() as conn:
        ProjectTaskRepo(pool)._insert_new_thread(
            conn, thread_id="project", agent_id="expert", user_id=1, session_key="owned", ts=1
        )
    with pool.connect() as conn:
        row = conn.execute(
            "SELECT title,title_search_key FROM threads WHERE thread_id='project'"
        ).fetchone()
    assert tuple(row) == (None, "")
    registry = ThreadRegistry(session_repo=SessionRepo(pool), thread_repo=repo)
    real = repo.list_by_agent_user

    def old_signature(*, agent_id: str, user_id: int, limit: int = 50, archived: bool | None):
        assert archived is None
        return real(agent_id=agent_id, user_id=user_id, limit=limit, archived=archived)

    repo.list_by_agent_user = old_signature
    assert registry.list_threads(agent_id="expert", user_id=1, q=" \t") == real(
        agent_id="expert", user_id=1
    )
