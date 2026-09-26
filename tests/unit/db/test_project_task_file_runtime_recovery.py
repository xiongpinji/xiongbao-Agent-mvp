"""030A B2: narrow stale-unreferenced query used by the bounded boot cleanup.

Pins ``AgentRepo.list_unreferenced_project_task_runtimes``: it selects only
DB-marked ``project_task_files`` rows with no thread reference older than the
cutoff, oldest first, capped by ``limit``. Rows with private threads are never
selectable even when the project link is gone; standard rows are never
selectable at any age.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import (
    RUNTIME_KIND_PROJECT_TASK_FILES,
    RUNTIME_KIND_STANDARD,
    AgentRepo,
)
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo

_STALE_SECONDS = 24 * 60 * 60


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def users(db: SqlitePool) -> UserRepo:
    return UserRepo(db)


@pytest.fixture
def repo(db: SqlitePool) -> AgentRepo:
    return AgentRepo(db)


@pytest.fixture
def threads(db: SqlitePool) -> ThreadRepo:
    return ThreadRepo(db)


def _make_user(users: UserRepo, name: str) -> int:
    return users.create(username=name, password_hash="h", role="user")


def _seed_row(
    db: SqlitePool,
    *,
    agent_id: str,
    user_id: int,
    name: str,
    runtime_kind: str,
    created_at: int,
    enabled: int = 1,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents"
            "(agent_id, user_id, name, runtime_kind, created_at, updated_at, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (agent_id, user_id, name, runtime_kind, created_at, created_at, enabled),
        )


def _seed_thread(threads: ThreadRepo, *, thread_id: str, agent_id: str, user_id: int) -> None:
    threads.insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        channel_type="dashboard",
        session_key=f"session-{thread_id}",
    )


def test_returns_empty_when_no_internal_rows(
    repo: AgentRepo, users: UserRepo, db: SqlitePool
) -> None:
    uid = _make_user(users, "u1")
    _seed_row(
        db,
        agent_id="std-old",
        user_id=uid,
        name="std-old",
        runtime_kind=RUNTIME_KIND_STANDARD,
        created_at=1,
    )
    rows = repo.list_unreferenced_project_task_runtimes(created_before=10**12, limit=32)
    assert rows == []


def test_selects_only_stale_unreferenced_internal_rows(
    repo: AgentRepo, users: UserRepo, threads: ThreadRepo, db: SqlitePool
) -> None:
    uid = _make_user(users, "u2")
    cutoff = 1_000_000
    stale = cutoff - 10
    fresh = cutoff + 10

    # Stale internal, no threads → selected.
    _seed_row(
        db,
        agent_id="ptf-stale",
        user_id=uid,
        name="task-stale",
        runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES,
        created_at=stale,
    )
    # Stale internal WITH a thread → never selected.
    _seed_row(
        db,
        agent_id="ptf-linked",
        user_id=uid,
        name="task-linked",
        runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES,
        created_at=stale,
    )
    _seed_thread(threads, thread_id="th-1", agent_id="ptf-linked", user_id=uid)
    # Fresh internal, no threads → not yet stale.
    _seed_row(
        db,
        agent_id="ptf-fresh",
        user_id=uid,
        name="task-fresh",
        runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES,
        created_at=fresh,
    )
    # Stale standard row → never selected.
    _seed_row(
        db,
        agent_id="std-stale",
        user_id=uid,
        name="std-stale",
        runtime_kind=RUNTIME_KIND_STANDARD,
        created_at=stale,
    )
    # Stale disabled internal, no threads → still selected (cleanup targets
    # disabled internals too; they are never user-visible).
    _seed_row(
        db,
        agent_id="ptf-disabled",
        user_id=uid,
        name="task-disabled",
        runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES,
        created_at=stale,
        enabled=0,
    )

    rows = repo.list_unreferenced_project_task_runtimes(created_before=cutoff, limit=32)

    assert [row.agent_id for row in rows] == ["ptf-stale", "ptf-disabled"]
    assert all(row.runtime_kind == RUNTIME_KIND_PROJECT_TASK_FILES for row in rows)


def test_link_survives_thread_even_after_project_detach(
    repo: AgentRepo, users: UserRepo, threads: ThreadRepo, db: SqlitePool
) -> None:
    """A single remaining private thread keeps the runtime out of the scan."""
    uid = _make_user(users, "u3")
    cutoff = 1_000_000
    _seed_row(
        db,
        agent_id="ptf-one-thread",
        user_id=uid,
        name="task-one",
        runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES,
        created_at=cutoff - 10,
    )
    _seed_thread(threads, thread_id="th-a", agent_id="ptf-one-thread", user_id=uid)
    _seed_thread(threads, thread_id="th-b", agent_id="ptf-one-thread", user_id=uid)
    with db.transaction() as conn:
        conn.execute("DELETE FROM threads WHERE thread_id = 'th-a'")

    rows = repo.list_unreferenced_project_task_runtimes(created_before=cutoff, limit=32)
    assert rows == []


def test_ordered_oldest_first_and_bounded_by_limit(
    repo: AgentRepo, users: UserRepo, db: SqlitePool
) -> None:
    uid = _make_user(users, "u4")
    cutoff = 1_000_000
    for i in range(5):
        _seed_row(
            db,
            agent_id=f"ptf-ord-{i}",
            user_id=uid,
            name=f"task-ord-{i}",
            runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES,
            created_at=cutoff - 100 + i * 10,  # ptf-ord-0 oldest … ptf-ord-4 newest
        )

    bounded = repo.list_unreferenced_project_task_runtimes(created_before=cutoff, limit=2)
    assert [row.agent_id for row in bounded] == ["ptf-ord-0", "ptf-ord-1"]

    everything = repo.list_unreferenced_project_task_runtimes(created_before=cutoff, limit=32)
    assert [row.agent_id for row in everything] == [f"ptf-ord-{i}" for i in range(5)]
