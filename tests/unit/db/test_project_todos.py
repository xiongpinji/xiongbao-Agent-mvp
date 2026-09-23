"""Unit tests for ProjectTodoRepo, migration 020, and remove_member unassign."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import UNSET, now_ts
from octop.infra.db.repos.project_todos import (
    EVENT_TODO_CREATED,
    EVENT_TODO_DELETED,
    EVENT_TODO_UPDATED,
    ProjectTodoRepo,
)
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.projects.todos import ProjectTodoService

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> ProjectTodoRepo:
    return ProjectTodoRepo(db)


@pytest.fixture
def projects(db: SqlitePool) -> ProjectRepo:
    return ProjectRepo(db)


@pytest.fixture
def users(db: SqlitePool) -> UserRepo:
    return UserRepo(db)


@pytest.fixture
def owner_id(users: UserRepo) -> int:
    return users.create(username="owner", password_hash="h", role="user")


@pytest.fixture
def member_id(users: UserRepo) -> int:
    return users.create(username="member", password_hash="h", role="user")


@pytest.fixture
def other_member_id(users: UserRepo) -> int:
    return users.create(username="othermember", password_hash="h", role="user")


@pytest.fixture
def outsider_id(users: UserRepo) -> int:
    return users.create(username="outsider", password_hash="h", role="user")


@pytest.fixture
def pid(projects: ProjectRepo, owner_id: int, member_id: int, other_member_id: int) -> str:
    project = projects.create_with_owner(creator_user_id=owner_id, name="待办项目")
    projects.add_member(project.project_id, member_id, role="member")
    projects.add_member(project.project_id, other_member_id, role="member")
    return project.project_id


class _StubServices:
    def __init__(self, projects: ProjectRepo, todos: ProjectTodoRepo) -> None:
        self.project_repo = projects
        self.project_todo_repo = todos


def _events(db: SqlitePool, project_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()


def _todo_rows(db: SqlitePool, project_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT todo_id, status, assignee_user_id, version, deleted_at "
            "FROM project_todos WHERE project_id = ? ORDER BY todo_id",
            (project_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Migration 020
# ---------------------------------------------------------------------------


def test_postgres_membership_locks_are_taken_in_user_id_order(repo: ProjectTodoRepo) -> None:
    """Cross-assignment must not acquire membership locks in opposite orders."""

    class _Cursor:
        def __init__(self, role: str) -> None:
            self.role = role

        def fetchone(self) -> dict[str, str]:
            return {"role": self.role}

    class _Connection:
        def __init__(self) -> None:
            self.locked_users: list[int] = []

        def execute(self, sql: str, params: tuple[str, int]) -> _Cursor:
            assert sql.endswith(" FOR UPDATE")
            self.locked_users.append(params[1])
            return _Cursor("member")

    connection = _Connection()
    repo._db.dialect = "postgresql"
    roles = repo._locked_member_roles(connection, "project", [3, 2, 3])
    assert connection.locked_users == [2, 3]
    assert roles == {2: "member", 3: "member"}


def test_migration_020_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_todos)").fetchall()}
    assert v == _max_discovered_version("sqlite")
    assert v >= 20
    assert "project_todos" in tables
    assert {
        "id",
        "todo_id",
        "project_id",
        "creator_user_id",
        "assignee_user_id",
        "title",
        "description",
        "status",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    }.issubset(cols)
    assert {
        "idx_project_todos_project_list",
        "idx_project_todos_status",
        "idx_project_todos_assignee",
    }.issubset(indexes)


def test_migration_020_status_check_constraint(db: SqlitePool) -> None:
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_todos("
            "todo_id, project_id, creator_user_id, title, status, version, "
            "created_at, updated_at) VALUES "
            "('T1', 'P1', 1, 'x', 'bogus', 1, 0, 0)"
        )


def test_migration_upgrades_from_v19(tmp_path: Path) -> None:
    """A DB at watermark 19 must gain project_todos by re-running migrations."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_todos;
            UPDATE _schema_version SET version = 19;
            """
        )
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert v == _max_discovered_version("sqlite")
    assert "project_todos" in tables
    # The upgraded schema is functional end to end.
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    todos = ProjectTodoRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级待办")
    mutation = todos.create(project_id=project.project_id, creator_user_id=owner, title="升级后")
    assert mutation.outcome == "created"
    assert mutation.row is not None and mutation.row.version == 1


def test_migration_020_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (MIGRATIONS / "020_project_todos.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == _max_discovered_version("sqlite")


def test_migration_020_pg_pair_declares_same_shape() -> None:
    """Token-level parity check of the PostgreSQL script (no live PG needed)."""
    sqlite_sql = (MIGRATIONS / "020_project_todos.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "020_project_todos.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "project_todos",
        "todo_id TEXT NOT NULL UNIQUE",
        "REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "creator_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE",
        "assignee_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "CHECK (status IN ('todo', 'in_progress', 'done'))",
        "version INTEGER NOT NULL DEFAULT 1",
        "deleted_at INTEGER",
        "idx_project_todos_project_list",
        "idx_project_todos_status",
        "idx_project_todos_assignee",
        "UPDATE _schema_version SET version = 20",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    assert "GENERATED BY DEFAULT AS IDENTITY" in pg_sql
    assert "AUTOINCREMENT" not in pg_sql
    assert "AUTOINCREMENT" in sqlite_sql
    assert "GENERATED BY DEFAULT" not in sqlite_sql

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 5  # table + 3 indexes + watermark update
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_writes_row_and_safe_event(
    repo: ProjectTodoRepo, db: SqlitePool, pid: str, owner_id: int
) -> None:
    mutation = repo.create(
        project_id=pid,
        creator_user_id=owner_id,
        title="SECRET-标题",
        description="SECRET-描述",
        assignee_user_id=None,
    )
    assert mutation.outcome == "created"
    row = mutation.row
    assert row is not None
    assert row.title == "SECRET-标题"
    assert row.status == "todo"
    assert row.version == 1
    assert row.created_at == row.updated_at == pytest.approx(now_ts(), abs=5)
    assert row.deleted_at is None

    events = _events(db, pid)
    assert [e["event_type"] for e in events if e["event_type"] == EVENT_TODO_CREATED] == [
        EVENT_TODO_CREATED
    ]
    created = next(e for e in events if e["event_type"] == EVENT_TODO_CREATED)
    assert created["object_id"] == row.todo_id
    assert created["actor_user_id"] == owner_id
    payload = json.loads(created["payload_json"])
    assert payload == {"creator_user_id": owner_id, "assignee_user_id": None}
    assert "SECRET" not in created["payload_json"]


def test_create_guards(
    repo: ProjectTodoRepo,
    db: SqlitePool,
    pid: str,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    # Non-member creator.
    assert repo.create(project_id=pid, creator_user_id=outsider_id, title="x").outcome == (
        "not_member"
    )
    # Ordinary member assigning someone else.
    mutation = repo.create(
        project_id=pid, creator_user_id=member_id, title="x", assignee_user_id=other_member_id
    )
    assert mutation.outcome == "forbidden"
    # Anyone assigning a non-member.
    mutation = repo.create(
        project_id=pid, creator_user_id=member_id, title="x", assignee_user_id=outsider_id
    )
    assert mutation.outcome == "forbidden"
    # Self-assignment is fine.
    mutation = repo.create(
        project_id=pid, creator_user_id=member_id, title="x", assignee_user_id=member_id
    )
    assert mutation.outcome == "created"
    # Nothing was written by the rejected attempts.
    assert len(_todo_rows(db, pid)) == 1
    assert not [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_CREATED][1:]


def test_create_unknown_project_is_not_member(repo: ProjectTodoRepo, owner_id: int) -> None:
    mutation = repo.create(project_id="nope", creator_user_id=owner_id, title="x")
    assert mutation.outcome == "not_member"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_get_hides_deleted_and_foreign_todos(
    repo: ProjectTodoRepo, projects: ProjectRepo, pid: str, owner_id: int
) -> None:
    todo = repo.create(project_id=pid, creator_user_id=owner_id, title="t").row
    assert todo is not None
    other = projects.create_with_owner(creator_user_id=owner_id, name="另一个")
    assert repo.get(other.project_id, todo.todo_id) is None
    assert repo.get(pid, "unknown-id") is None
    assert (
        repo.delete(
            project_id=pid, todo_id=todo.todo_id, actor_user_id=owner_id, expected_version=1
        ).outcome
        == "deleted"
    )
    assert repo.get(pid, todo.todo_id) is None


def test_list_order_filters_and_has_more(
    repo: ProjectTodoRepo, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    a = repo.create(project_id=pid, creator_user_id=owner_id, title="Alpha").row
    b = repo.create(project_id=pid, creator_user_id=owner_id, title="100% done").row
    c = repo.create(
        project_id=pid, creator_user_id=owner_id, title="a_b", assignee_user_id=member_id
    ).row
    assert a is not None and b is not None and c is not None
    # Force distinct updated_at values: b newest, then c, then a.
    with db.transaction() as conn:
        conn.execute("UPDATE project_todos SET updated_at = 300 WHERE todo_id = ?", (b.todo_id,))
        conn.execute("UPDATE project_todos SET updated_at = 200 WHERE todo_id = ?", (c.todo_id,))
        conn.execute("UPDATE project_todos SET updated_at = 100 WHERE todo_id = ?", (a.todo_id,))

    assert [r.todo_id for r in repo.list_todos(pid)] == [b.todo_id, c.todo_id, a.todo_id]
    # has_more detection returns limit + 1 rows.
    assert len(repo.list_todos(pid, limit=2)) == 3
    assert len(repo.list_todos(pid, limit=3)) == 3
    assert len(repo.list_todos(pid, limit=2, offset=2)) == 1
    # LIKE wildcards in q stay literal.
    assert [r.todo_id for r in repo.list_todos(pid, q="100%")] == [b.todo_id]
    assert [r.todo_id for r in repo.list_todos(pid, q="a_b")] == [c.todo_id]
    assert {r.todo_id for r in repo.list_todos(pid, q="a")} == {a.todo_id, c.todo_id}
    # Column filters.
    assert [r.todo_id for r in repo.list_todos(pid, assignee_user_id=member_id)] == [c.todo_id]
    assert repo.list_todos(pid, assignee_user_id=owner_id) == []
    # Deleted rows never appear.
    repo.delete(project_id=pid, todo_id=b.todo_id, actor_user_id=owner_id, expected_version=1)
    assert b.todo_id not in {r.todo_id for r in repo.list_todos(pid)}


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_success_bumps_version_and_event_fields(
    repo: ProjectTodoRepo, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    todo = repo.create(
        project_id=pid, creator_user_id=owner_id, title="SECRET-原标题", description="SECRET-原描述"
    ).row
    assert todo is not None
    mutation = repo.update(
        project_id=pid,
        todo_id=todo.todo_id,
        actor_user_id=owner_id,
        expected_version=1,
        title="SECRET-新标题",
        status="in_progress",
        assignee_user_id=member_id,
    )
    assert mutation.outcome == "updated"
    row = mutation.row
    assert row is not None
    assert (row.title, row.status, row.assignee_user_id, row.version) == (
        "SECRET-新标题",
        "in_progress",
        member_id,
        2,
    )
    assert row.description == "SECRET-原描述"
    assert row.created_at == todo.created_at
    assert row.updated_at >= todo.updated_at

    updated = [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_UPDATED]
    assert len(updated) == 1
    payload = json.loads(updated[0]["payload_json"])
    assert payload["fields"] == ["assignee_user_id", "status", "title"]
    assert payload["from_status"] == "todo"
    assert payload["to_status"] == "in_progress"
    assert payload["from_assignee_user_id"] is None
    assert payload["to_assignee_user_id"] == member_id
    assert "SECRET" not in updated[0]["payload_json"]


def test_update_omitted_fields_stay_untouched(
    repo: ProjectTodoRepo, pid: str, owner_id: int
) -> None:
    todo = repo.create(project_id=pid, creator_user_id=owner_id, title="原", description="描").row
    assert todo is not None
    mutation = repo.update(
        project_id=pid,
        todo_id=todo.todo_id,
        actor_user_id=owner_id,
        expected_version=1,
        status="done",
    )
    assert mutation.outcome == "updated"
    row = mutation.row
    assert row is not None
    assert (row.title, row.description, row.assignee_user_id) == ("原", "描", None)


def test_update_guards(
    repo: ProjectTodoRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    todo = repo.create(project_id=pid, creator_user_id=owner_id, title="守门").row
    assert todo is not None
    tid = todo.todo_id

    # Stale version.
    assert (
        repo.update(
            project_id=pid, todo_id=tid, actor_user_id=owner_id, expected_version=99, status="done"
        ).outcome
        == "stale"
    )
    # Non-member.
    assert (
        repo.update(
            project_id=pid,
            todo_id=tid,
            actor_user_id=outsider_id,
            expected_version=1,
            status="done",
        ).outcome
        == "not_member"
    )
    # Member who is neither creator nor assignee.
    assert (
        repo.update(
            project_id=pid,
            todo_id=tid,
            actor_user_id=member_id,
            expected_version=1,
            status="done",
        ).outcome
        == "forbidden"
    )
    # Non-manager touching the assignee (even clearing it).
    repo.update(
        project_id=pid,
        todo_id=tid,
        actor_user_id=owner_id,
        expected_version=1,
        assignee_user_id=member_id,
    )
    assert (
        repo.update(
            project_id=pid,
            todo_id=tid,
            actor_user_id=member_id,
            expected_version=2,
            assignee_user_id=None,
        ).outcome
        == "forbidden"
    )
    # Assignee to a non-member.
    assert (
        repo.update(
            project_id=pid,
            todo_id=tid,
            actor_user_id=owner_id,
            expected_version=2,
            assignee_user_id=outsider_id,
        ).outcome
        == "invalid_assignee"
    )
    # No actual change.
    assert (
        repo.update(
            project_id=pid, todo_id=tid, actor_user_id=owner_id, expected_version=2, title="守门"
        ).outcome
        == "no_change"
    )
    assert (
        repo.update(project_id=pid, todo_id=tid, actor_user_id=owner_id, expected_version=2).outcome
        == "no_change"
    )
    # Explicit assignee edits are manager-only, including a repeated value.
    assert (
        repo.update(
            project_id=pid,
            todo_id=tid,
            actor_user_id=member_id,
            expected_version=2,
            assignee_user_id=member_id,
            title="守门",
        ).outcome
        == "forbidden"
    )
    # Unknown / foreign / deleted todos.
    assert (
        repo.update(
            project_id=pid,
            todo_id="nope",
            actor_user_id=owner_id,
            expected_version=1,
            status="done",
        ).outcome
        == "missing"
    )

    # Every rejected write left the row exactly as it was: version 2.
    rows = _todo_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["version"] == 2
    assert rows[0]["status"] == "todo"
    assert rows[0]["assignee_user_id"] == member_id
    updated = [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_UPDATED]
    assert len(updated) == 1  # only the successful assignee write


def test_update_current_assignee_may_edit_content(
    repo: ProjectTodoRepo, pid: str, owner_id: int, member_id: int
) -> None:
    todo = repo.create(
        project_id=pid, creator_user_id=owner_id, title="指派", assignee_user_id=member_id
    ).row
    assert todo is not None
    mutation = repo.update(
        project_id=pid,
        todo_id=todo.todo_id,
        actor_user_id=member_id,
        expected_version=1,
        title="被指派人改的",
    )
    assert mutation.outcome == "updated"


def test_update_deleted_todo_is_missing(repo: ProjectTodoRepo, pid: str, owner_id: int) -> None:
    todo = repo.create(project_id=pid, creator_user_id=owner_id, title="删").row
    assert todo is not None
    repo.delete(project_id=pid, todo_id=todo.todo_id, actor_user_id=owner_id, expected_version=1)
    assert (
        repo.update(
            project_id=pid,
            todo_id=todo.todo_id,
            actor_user_id=owner_id,
            expected_version=2,
            status="done",
        ).outcome
        == "missing"
    )


def test_update_concurrent_threads_single_winner(
    repo: ProjectTodoRepo, db: SqlitePool, pid: str, owner_id: int
) -> None:
    """Racing two patches on one version must yield exactly one winner."""
    todo = repo.create(project_id=pid, creator_user_id=owner_id, title="竞速").row
    assert todo is not None
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _patch(title: str) -> None:
        barrier.wait(timeout=10)
        result = repo.update(
            project_id=pid,
            todo_id=todo.todo_id,
            actor_user_id=owner_id,
            expected_version=1,
            title=title,
        )
        with lock:
            outcomes.append(result.outcome)

    threads = [threading.Thread(target=_patch, args=(t,)) for t in ("赢家A", "赢家B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(outcomes) == ["stale", "updated"]
    row = repo.get(pid, todo.todo_id)
    assert row is not None and row.version == 2
    updated = [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_UPDATED]
    assert len(updated) == 1


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_soft_deletes_and_guards(
    repo: ProjectTodoRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    mine = repo.create(project_id=pid, creator_user_id=member_id, title="成员的").row
    theirs = repo.create(project_id=pid, creator_user_id=owner_id, title="主人的").row
    assert mine is not None and theirs is not None

    # Wrong version → stale, row untouched.
    assert (
        repo.delete(
            project_id=pid, todo_id=mine.todo_id, actor_user_id=member_id, expected_version=7
        ).outcome
        == "stale"
    )
    # Unrelated member and outsider.
    assert (
        repo.delete(
            project_id=pid, todo_id=mine.todo_id, actor_user_id=other_member_id, expected_version=1
        ).outcome
        == "forbidden"
    )
    assert (
        repo.delete(
            project_id=pid, todo_id=mine.todo_id, actor_user_id=outsider_id, expected_version=1
        ).outcome
        == "not_member"
    )
    # Creator deletes their own.
    assert (
        repo.delete(
            project_id=pid, todo_id=mine.todo_id, actor_user_id=member_id, expected_version=1
        ).outcome
        == "deleted"
    )
    # Owner (manager) deletes anyone's.
    assert (
        repo.delete(
            project_id=pid, todo_id=theirs.todo_id, actor_user_id=owner_id, expected_version=1
        ).outcome
        == "deleted"
    )
    # Second delete → missing (never resurrected).
    assert (
        repo.delete(
            project_id=pid, todo_id=mine.todo_id, actor_user_id=member_id, expected_version=2
        ).outcome
        == "missing"
    )

    rows = {r["todo_id"]: r for r in _todo_rows(db, pid)}
    assert rows[mine.todo_id]["deleted_at"] is not None
    assert rows[mine.todo_id]["version"] == 2
    assert rows[theirs.todo_id]["deleted_at"] is not None
    deleted = [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_DELETED]
    assert len(deleted) == 2
    for event in deleted:
        payload = json.loads(event["payload_json"])
        assert set(payload) == {"fields", "from_status", "from_assignee_user_id"}
        assert "成员的" not in event["payload_json"]


# ---------------------------------------------------------------------------
# Bulk update
# ---------------------------------------------------------------------------


def test_bulk_updates_all_rows_in_one_transaction(
    repo: ProjectTodoRepo, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    a = repo.create(project_id=pid, creator_user_id=owner_id, title="A").row
    b = repo.create(project_id=pid, creator_user_id=owner_id, title="B").row
    assert a is not None and b is not None

    result = repo.bulk_update(
        project_id=pid,
        items=[(a.todo_id, 1), (b.todo_id, 1)],
        actor_user_id=owner_id,
        status="done",
        assignee_user_id=member_id,
    )
    assert result.outcome == "updated"
    assert {r.todo_id for r in result.rows} == {a.todo_id, b.todo_id}
    for row in result.rows:
        assert row.status == "done"
        assert row.version == 2
        assert row.assignee_user_id == member_id
    updated = [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_UPDATED]
    assert len(updated) == 2
    payload = json.loads(updated[0]["payload_json"])
    assert payload["fields"] == ["assignee_user_id", "status"]


def test_bulk_stale_item_rolls_back_everything(
    repo: ProjectTodoRepo, db: SqlitePool, pid: str, owner_id: int
) -> None:
    a = repo.create(project_id=pid, creator_user_id=owner_id, title="A").row
    b = repo.create(project_id=pid, creator_user_id=owner_id, title="B").row
    assert a is not None and b is not None

    result = repo.bulk_update(
        project_id=pid,
        items=[(a.todo_id, 1), (b.todo_id, 55)],
        actor_user_id=owner_id,
        status="done",
    )
    assert result.outcome == "stale"
    assert result.todo_id == b.todo_id
    assert result.rows == []
    rows = _todo_rows(db, pid)
    assert [(r["version"], r["status"]) for r in rows] == [(1, "todo"), (1, "todo")]
    assert not [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_UPDATED]


def test_bulk_guards(
    repo: ProjectTodoRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    todo = repo.create(project_id=pid, creator_user_id=owner_id, title="批量守门").row
    assert todo is not None

    # Non-manager member / non-member.
    assert (
        repo.bulk_update(
            project_id=pid, items=[(todo.todo_id, 1)], actor_user_id=member_id, status="done"
        ).outcome
        == "forbidden"
    )
    assert (
        repo.bulk_update(
            project_id=pid, items=[(todo.todo_id, 1)], actor_user_id=outsider_id, status="done"
        ).outcome
        == "not_member"
    )
    # Unknown / foreign / deleted ids.
    assert (
        repo.bulk_update(
            project_id=pid, items=[("nope", 1)], actor_user_id=owner_id, status="done"
        ).outcome
        == "missing"
    )
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的")
    foreign = repo.create(project_id=other.project_id, creator_user_id=owner_id, title="外").row
    assert foreign is not None
    assert (
        repo.bulk_update(
            project_id=pid, items=[(foreign.todo_id, 1)], actor_user_id=owner_id, status="done"
        ).outcome
        == "missing"
    )
    # Invalid assignee rolls back.
    result = repo.bulk_update(
        project_id=pid,
        items=[(todo.todo_id, 1)],
        actor_user_id=owner_id,
        assignee_user_id=outsider_id,
    )
    assert result.outcome == "invalid_assignee"
    assert _todo_rows(db, pid)[0]["version"] == 1
    # Programming contract: neither status nor assignee.
    with pytest.raises(ValueError):
        repo.bulk_update(project_id=pid, items=[(todo.todo_id, 1)], actor_user_id=owner_id)
    # The foreign todo was never touched either.
    assert repo.get(other.project_id, foreign.todo_id) is not None
    assert repo.get(other.project_id, foreign.todo_id).version == 1  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# remove_member unassignment (ProjectRepo integration)
# ---------------------------------------------------------------------------


def test_remove_member_unassigns_todos_and_events(
    repo: ProjectTodoRepo,
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    assigned = repo.create(
        project_id=pid,
        creator_user_id=owner_id,
        title="SECRET-指派",
        assignee_user_id=member_id,
    ).row
    untouched = repo.create(project_id=pid, creator_user_id=owner_id, title="无主").row
    gone = repo.create(
        project_id=pid, creator_user_id=owner_id, title="已删", assignee_user_id=member_id
    ).row
    assert assigned is not None and untouched is not None and gone is not None
    repo.delete(project_id=pid, todo_id=gone.todo_id, actor_user_id=owner_id, expected_version=1)

    mutation = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert mutation.outcome == "removed"

    rows = {r["todo_id"]: r for r in _todo_rows(db, pid)}
    # Undeleted assigned todo: cleared, version bumped for stale-write safety.
    assert rows[assigned.todo_id]["assignee_user_id"] is None
    assert rows[assigned.todo_id]["version"] == 2
    # Unassigned todo untouched.
    assert rows[untouched.todo_id]["version"] == 1
    # Soft-deleted todo keeps its historical assignee; only the delete bumped it.
    assert rows[gone.todo_id]["assignee_user_id"] == member_id
    assert rows[gone.todo_id]["version"] == 2
    assert rows[gone.todo_id]["deleted_at"] is not None

    unassign_events = [
        e
        for e in _events(db, pid)
        if e["event_type"] == EVENT_TODO_UPDATED and e["object_id"] == assigned.todo_id
    ]
    assert len(unassign_events) == 1
    payload = json.loads(unassign_events[0]["payload_json"])
    assert payload == {
        "fields": ["assignee_user_id"],
        "from_assignee_user_id": member_id,
        "to_assignee_user_id": None,
    }
    dumped = json.dumps([dict(e) for e in _events(db, pid)], ensure_ascii=False)
    assert "SECRET" not in dumped


def test_forbidden_member_removal_leaves_todos_untouched(
    repo: ProjectTodoRepo,
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    todo = repo.create(
        project_id=pid, creator_user_id=owner_id, title="留任", assignee_user_id=member_id
    ).row
    assert todo is not None
    # owner is in the default forbid_roles: removal must be rejected atomically.
    mutation = projects.remove_member(project_id=pid, user_id=owner_id, actor_user_id=owner_id)
    assert mutation.outcome == "forbidden_role"
    rows = _todo_rows(db, pid)
    assert rows[0]["assignee_user_id"] == member_id
    assert rows[0]["version"] == 1
    assert not [e for e in _events(db, pid) if e["event_type"] == EVENT_TODO_UPDATED]
    assert projects.get_membership(pid, owner_id) is not None


# ---------------------------------------------------------------------------
# Service error mapping (ACL surface without HTTP)
# ---------------------------------------------------------------------------


def test_service_maps_outcomes_to_errors(
    projects: ProjectRepo,
    repo: ProjectTodoRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    service = ProjectTodoService(_StubServices(projects, repo))

    # Outsiders get the same 404 as unknown projects on every entry point.
    for call in (
        lambda: service.list_todos(pid, user_id=outsider_id),
        lambda: service.get_todo(pid, "whatever", user_id=outsider_id),
        lambda: service.create_todo(pid, actor_user_id=outsider_id, title="x"),
        lambda: service.update_todo(
            pid, "whatever", actor_user_id=outsider_id, expected_version=1, status="done"
        ),
        lambda: service.delete_todo(pid, "whatever", actor_user_id=outsider_id, expected_version=1),
        lambda: service.bulk_update_todos(
            pid, actor_user_id=outsider_id, items=[("x", 1)], status="done"
        ),
    ):
        with pytest.raises(OctopError) as err:
            call()
        assert err.value.status == 404
        assert err.value.code == "NOT_FOUND"
    # Unknown project behaves identically.
    with pytest.raises(OctopError) as err:
        service.list_todos("nope", user_id=owner_id)
    assert err.value.status == 404

    # Member without manager role cannot bulk update.
    todo = service.create_todo(pid, actor_user_id=owner_id, title="服务层")
    with pytest.raises(OctopError) as err:
        service.bulk_update_todos(
            pid, actor_user_id=member_id, items=[(todo.todo_id, 1)], status="done"
        )
    assert err.value.status == 403

    # Stale version → 409 with a machine-readable reason.
    service.update_todo(
        pid, todo.todo_id, actor_user_id=owner_id, expected_version=1, status="done"
    )
    with pytest.raises(OctopError) as err:
        service.update_todo(
            pid, todo.todo_id, actor_user_id=owner_id, expected_version=1, status="todo"
        )
    assert err.value.status == 409
    assert err.value.details["reason"] == "version_conflict"

    # Validation surfaces ValueError for router-level validators to catch.
    with pytest.raises(ValueError):
        service.create_todo(pid, actor_user_id=owner_id, title="   ")

    assert service.get_todo(pid, todo.todo_id, user_id=member_id).status == "done"
    assert UNSET is not None  # sentinel identity documented for readers
