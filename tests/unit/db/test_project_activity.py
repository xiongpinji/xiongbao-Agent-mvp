"""Unit tests for ProjectActivityRepo, migration 022, and ProjectActivityService.

PS-03A: a project member reads a safe, server-filtered activity timeline and
publishes an atomic plain-text message. Covers the event whitelist (invite /
join-request / unknown events never surface), members|related scoping with the
pre-pagination SQL filter, strict ``(created_at, id)`` seek paging under
same-second ties and new inserts, atomic message creation with rollback, the
member-row lock order shared with ``remove_member``, member target-id hiding,
deleted-author safety, and the ``PROJECT_ACTIVITY_CURSOR_INVALID`` contract.
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest

from octop.i18n import error_message
from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import insert_returning_id, now_ts
from octop.infra.db.repos.project_activity import (
    ACTIVITY_EVENT_TYPES,
    EVENT_MESSAGE_CREATED,
    ActivityRow,
    ProjectActivityRepo,
)
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.activity import (
    MESSAGE_BODY_MAX_LENGTH,
    ProjectActivityService,
    activity_item_view,
    decode_activity_cursor,
    encode_activity_cursor,
    validate_message_body,
)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> ProjectActivityRepo:
    return ProjectActivityRepo(db)


@pytest.fixture
def projects(db: SqlitePool) -> ProjectRepo:
    return ProjectRepo(db)


@pytest.fixture
def todos(db: SqlitePool) -> ProjectTodoRepo:
    return ProjectTodoRepo(db)


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
    project = projects.create_with_owner(creator_user_id=owner_id, name="动态项目")
    projects.add_member(project.project_id, member_id, role="member")
    projects.add_member(project.project_id, other_member_id, role="member")
    return project.project_id


class _StubServices:
    def __init__(
        self, projects: ProjectRepo, activity: ProjectActivityRepo, todos: ProjectTodoRepo
    ) -> None:
        self.project_repo = projects
        self.project_activity_repo = activity
        self.project_todo_repo = todos


@pytest.fixture
def service(
    projects: ProjectRepo, repo: ProjectActivityRepo, todos: ProjectTodoRepo
) -> ProjectActivityService:
    return ProjectActivityService(_StubServices(projects, repo, todos))


def _seed_event(
    db: SqlitePool,
    project_id: str,
    *,
    actor_user_id: int | None,
    event_type: str,
    object_id: str = "",
    payload_json: str = "{}",
    ts: int | None = None,
) -> tuple[int, int]:
    """Insert one project_events row directly; returns (event_id, created_at)."""
    stamp = now_ts() if ts is None else ts
    with db.transaction() as conn:
        event_id = insert_returning_id(
            conn,
            "INSERT INTO project_events("
            "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, actor_user_id, event_type, object_id, payload_json, stamp),
        )
    return event_id, stamp


def _message_rows(db: SqlitePool, project_id: str | None = None) -> list[sqlite3.Row]:
    with db.connect() as conn:
        if project_id is None:
            return conn.execute(
                "SELECT message_id, project_id, author_user_id, body, created_at "
                "FROM project_messages ORDER BY created_at, message_id"
            ).fetchall()
        return conn.execute(
            "SELECT message_id, project_id, author_user_id, body, created_at "
            "FROM project_messages WHERE project_id = ? ORDER BY created_at, message_id",
            (project_id,),
        ).fetchall()


def _message_events(db: SqlitePool, project_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json, created_at "
            "FROM project_events WHERE project_id = ? AND event_type = ? ORDER BY id",
            (project_id, EVENT_MESSAGE_CREATED),
        ).fetchall()


# ---------------------------------------------------------------------------
# Migration 022
# ---------------------------------------------------------------------------


def test_migration_022_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_messages)").fetchall()}
    assert v == _max_discovered_version("sqlite")
    assert v >= 22
    assert "project_messages" in tables
    assert cols == {"message_id", "project_id", "author_user_id", "body", "created_at"}
    # Message paging index + the project_events activity index required by 022.
    assert "idx_project_messages_project_time" in indexes
    assert "idx_project_events_project_activity" in indexes


def test_migration_022_body_length_check(db: SqlitePool, pid: str, owner_id: int) -> None:
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_messages("
            "message_id, project_id, author_user_id, body, created_at) "
            "VALUES ('m_empty', ?, ?, '', 0)",
            (pid, owner_id),
        )
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_messages("
            "message_id, project_id, author_user_id, body, created_at) "
            "VALUES ('m_long', ?, ?, ?, 0)",
            (pid, owner_id, "x" * (MESSAGE_BODY_MAX_LENGTH + 1)),
        )
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_messages("
            "message_id, project_id, author_user_id, body, created_at) "
            "VALUES ('m_one', ?, ?, 'x', 0)",
            (pid, owner_id),
        )
        conn.execute(
            "INSERT INTO project_messages("
            "message_id, project_id, author_user_id, body, created_at) "
            "VALUES ('m_max', ?, ?, ?, 0)",
            (pid, owner_id, "y" * MESSAGE_BODY_MAX_LENGTH),
        )
    assert len(_message_rows(db, pid)) == 2


def test_migration_022_author_set_null_and_project_cascade(
    db: SqlitePool, repo: ProjectActivityRepo, pid: str, member_id: int
) -> None:
    created = repo.create_message(project_id=pid, author_user_id=member_id, body="留言")
    assert created.outcome == "created" and created.row is not None
    message_id = created.row.object_id
    assert message_id is not None
    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (member_id,))
    rows = _message_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["message_id"] == message_id
    assert rows[0]["author_user_id"] is None
    assert rows[0]["body"] == "留言"
    # Deleting the project cascades messages away.
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _message_rows(db) == []


def test_migration_upgrades_from_v21(tmp_path: Path) -> None:
    """A DB at watermark 21 must gain project_messages by re-running migrations."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_messages;
            UPDATE _schema_version SET version = 21;
            """
        )
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert v == _max_discovered_version("sqlite")
    assert "project_messages" in tables
    assert "idx_project_events_project_activity" in indexes
    # The upgraded schema is functional end to end.
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    activity = ProjectActivityRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级动态")
    created = activity.create_message(
        project_id=project.project_id, author_user_id=owner, body="升级后留言"
    )
    assert created.outcome == "created"
    rows = activity.list_activity(project.project_id, user_id=owner, scope="members", limit=10)
    assert rows[0].event_type == EVENT_MESSAGE_CREATED
    assert rows[0].message_body == "升级后留言"


def test_migration_022_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (MIGRATIONS / "022_project_messages.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == _max_discovered_version("sqlite")


def test_migration_022_pg_pair_declares_same_shape() -> None:
    """Token-level parity check of the PostgreSQL script (no live PG needed)."""
    sqlite_sql = (MIGRATIONS / "022_project_messages.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "022_project_messages.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "project_messages",
        "message_id TEXT PRIMARY KEY",
        "project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "author_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "body TEXT NOT NULL",
        "CHECK (length(body) BETWEEN 1 AND 4000)",
        "created_at INTEGER NOT NULL",
        "idx_project_messages_project_time",
        "(project_id, created_at, message_id)",
        "idx_project_events_project_activity",
        "project_events(project_id, created_at DESC, id DESC)",
        "UPDATE _schema_version SET version = 22",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    # No surrogate identity column in this table on either dialect.
    assert "AUTOINCREMENT" not in sqlite_sql
    assert "GENERATED BY DEFAULT" not in pg_sql

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 4  # table + 2 indexes + watermark update
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# Whitelist + membership (repo)
# ---------------------------------------------------------------------------


def test_list_excludes_invite_request_and_unknown_events(
    repo: ProjectActivityRepo, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    forbidden = {
        "project.invite_created": "inv-secret-id",
        "project.invite_revoked": "inv-secret-id",
        "project.join_requested": "req-1",
        "project.join_approved": "req-1",
        "project.join_rejected": "req-2",
        "project.future_unknown": "x",
    }
    for i, (event_type, object_id) in enumerate(sorted(forbidden.items())):
        _seed_event(
            db,
            pid,
            actor_user_id=owner_id,
            event_type=event_type,
            object_id=object_id,
            payload_json='{"token": "SECRET-TOKEN"}',
            ts=1000 + i,
        )
    allowed_id, _ = _seed_event(
        db, pid, actor_user_id=owner_id, event_type="project.updated", object_id=pid, ts=2000
    )
    rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=50)
    # project.created (from the fixture) + project.updated only.
    assert {r.event_type for r in rows} <= set(ACTIVITY_EVENT_TYPES)
    assert allowed_id in {r.event_id for r in rows}
    for event_type, object_id in forbidden.items():
        assert event_type not in {r.event_type for r in rows}
        assert object_id not in {r.object_id for r in rows}
    for row in rows:
        assert not hasattr(row, "payload_json")
        assert "SECRET-TOKEN" not in repr(row)


def test_list_requires_membership(
    repo: ProjectActivityRepo, pid: str, owner_id: int, member_id: int, outsider_id: int
) -> None:
    assert repo.list_activity(pid, user_id=outsider_id, scope="members", limit=50) is None
    assert repo.list_activity("ghost", user_id=owner_id, scope="members", limit=50) is None
    owner_rows = repo.list_activity(pid, user_id=owner_id, scope="members", limit=50)
    member_rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=50)
    assert owner_rows, "owner sees the creation event"
    assert [r.event_id for r in owner_rows] == [r.event_id for r in member_rows]


def test_removal_between_service_and_repo_checks_returns_404(
    service: ProjectActivityService,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_check = service._require_membership

    def checked_then_removed(project_id: str, user_id: int) -> None:
        original_check(project_id, user_id)
        removed = projects.remove_member(
            project_id=project_id,
            user_id=user_id,
            actor_user_id=owner_id,
        )
        assert removed.outcome == "removed"

    monkeypatch.setattr(service, "_require_membership", checked_then_removed)
    with pytest.raises(OctopError) as excinfo:
        service.list_activity(pid, user_id=member_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_message_event_cannot_join_body_from_another_project(
    repo: ProjectActivityRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    other_project = projects.create_with_owner(creator_user_id=owner_id, name="其他项目")
    other_message = repo.create_message(
        project_id=other_project.project_id,
        author_user_id=owner_id,
        body="OTHER-PROJECT-SECRET",
    )
    assert other_message.row is not None
    forged_id, _ = _seed_event(
        db,
        pid,
        actor_user_id=owner_id,
        event_type=EVENT_MESSAGE_CREATED,
        object_id=other_message.row.object_id or "",
    )

    rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=20)
    forged = next(row for row in rows if row.event_id == forged_id)
    assert forged.message_body is None
    assert "OTHER-PROJECT-SECRET" not in repr(rows)


def test_list_orders_created_at_desc_then_id_desc(
    repo: ProjectActivityRepo, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    ids = []
    for _ in range(3):
        event_id, _ = _seed_event(
            db,
            pid,
            actor_user_id=owner_id,
            event_type="project.updated",
            object_id=pid,
            ts=5000,  # same second on purpose: id DESC breaks the tie
        )
        ids.append(event_id)
    older, _ = _seed_event(
        db, pid, actor_user_id=owner_id, event_type="project.updated", object_id=pid, ts=4000
    )
    rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=50)
    ordered = [(r.created_at, r.event_id) for r in rows]
    assert ordered == sorted(ordered, key=lambda k: (-k[0], -k[1]))
    # Restrict to the seeded events (the fixture's project.created uses now()).
    seeded = set(ids) | {older}
    filtered = [r.event_id for r in rows if r.event_id in seeded]
    assert filtered == [*reversed(ids), older]


# ---------------------------------------------------------------------------
# Related scope
# ---------------------------------------------------------------------------


def test_related_scope_actor_target_and_assignee(
    repo: ProjectActivityRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    todos: ProjectTodoRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    # Actor hit: member updates the project themself.
    projects.update_project(pid, actor_user_id=member_id, description="by member")
    # Target hit: owner changes member's role (object_id = str(member)).
    mutation = projects.set_member_role(
        project_id=pid, user_id=member_id, role="admin", actor_user_id=owner_id
    )
    assert mutation.outcome == "changed"
    # Assignee hit: owner creates a todo assigned to member.
    created = todos.create(
        project_id=pid, creator_user_id=owner_id, title="给成员", assignee_user_id=member_id
    )
    assert created.outcome == "created"
    # Unrelated: other member creates a todo assigned to themself.
    unrelated = todos.create(
        project_id=pid,
        creator_user_id=other_member_id,
        title="别人的",
        assignee_user_id=other_member_id,
    )
    assert unrelated.outcome == "created"
    # Unrelated: owner-created project.created (actor=owner, object=project).
    member_rows = repo.list_activity(pid, user_id=member_id, scope="related", limit=50)
    types = [r.event_type for r in member_rows]
    assert "project.updated" in types  # actor hit
    assert "project.member_role_changed" in types  # target hit
    assert "project.todo_created" in types  # assignee hit
    assert {r.object_id for r in member_rows if r.event_type == "project.todo_created"} == {
        created.row.todo_id
    }
    assert unrelated.row is not None
    assert unrelated.row.todo_id not in {r.object_id for r in member_rows}
    assert "project.created" not in types  # owner-only event

    # The unrelated member's related feed does not carry member's events.
    other_rows = repo.list_activity(pid, user_id=other_member_id, scope="related", limit=50)
    assert {r.object_id for r in other_rows if r.event_type == "project.todo_created"} == {
        unrelated.row.todo_id
    }
    assert created.row is not None
    assert created.row.todo_id not in {r.object_id for r in other_rows}


def test_related_scope_historical_assignee(
    repo: ProjectActivityRepo,
    todos: ProjectTodoRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    created = todos.create(
        project_id=pid, creator_user_id=owner_id, title="历史", assignee_user_id=member_id
    )
    assert created.row is not None
    todo = created.row
    # Reassign member -> other: member keeps a historical-assignee hit and
    # other gains a current-assignee hit.
    updated = todos.update(
        project_id=pid,
        todo_id=todo.todo_id,
        actor_user_id=owner_id,
        expected_version=todo.version,
        assignee_user_id=other_member_id,
    )
    assert updated.outcome == "updated" and updated.row is not None
    # Delete while assigned to other: only other (from_assignee) matches.
    deleted = todos.delete(
        project_id=pid,
        todo_id=todo.todo_id,
        actor_user_id=owner_id,
        expected_version=updated.row.version,
    )
    assert deleted.outcome == "deleted"

    member_types = sorted(
        r.event_type for r in repo.list_activity(pid, user_id=member_id, scope="related", limit=50)
    )
    assert member_types == ["project.todo_created", "project.todo_updated"]
    other_types = sorted(
        r.event_type
        for r in repo.list_activity(pid, user_id=other_member_id, scope="related", limit=50)
    )
    assert other_types == ["project.todo_deleted", "project.todo_updated"]


def test_related_scope_excludes_other_members_messages(
    repo: ProjectActivityRepo, pid: str, owner_id: int, member_id: int, other_member_id: int
) -> None:
    mine = repo.create_message(project_id=pid, author_user_id=member_id, body="我的留言")
    theirs = repo.create_message(project_id=pid, author_user_id=other_member_id, body="别人的留言")
    assert mine.outcome == "created" and theirs.outcome == "created"
    assert mine.row is not None and theirs.row is not None

    member_rows = repo.list_activity(pid, user_id=member_id, scope="related", limit=50)
    bodies = {r.message_body for r in member_rows if r.event_type == EVENT_MESSAGE_CREATED}
    assert bodies == {"我的留言"}
    # The members feed carries both.
    all_rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=50)
    all_bodies = {r.message_body for r in all_rows if r.event_type == EVENT_MESSAGE_CREATED}
    assert all_bodies == {"我的留言", "别人的留言"}


def test_related_scope_rejects_string_and_null_ids_and_fails_closed_on_bad_json(
    repo: ProjectActivityRepo, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    # A JSON *string* that looks like the member id must not match.
    _seed_event(
        db,
        pid,
        actor_user_id=owner_id,
        event_type="project.todo_created",
        object_id="todo_str",
        payload_json=f'{{"assignee_user_id": "{member_id}"}}',
        ts=3000,
    )
    # JSON null must not match either.
    _seed_event(
        db,
        pid,
        actor_user_id=owner_id,
        event_type="project.todo_created",
        object_id="todo_null",
        payload_json='{"assignee_user_id": null}',
        ts=3001,
    )
    rows = repo.list_activity(pid, user_id=member_id, scope="related", limit=50)
    assert {r.object_id for r in rows if r.event_type == "project.todo_created"} == set()
    # The members scope tolerates both rows (no JSON predicate involved).
    member_rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=50)
    assert {"todo_str", "todo_null"} <= {r.object_id for r in member_rows}

    # Malformed historical JSON on a whitelisted todo event: the related list
    # fails closed (server error), never matches, and never leaks the payload.
    _seed_event(
        db,
        pid,
        actor_user_id=owner_id,
        event_type="project.todo_created",
        object_id="todo_bad",
        payload_json='{"BROKEN-PAYLOAD"',
        ts=3002,
    )
    with pytest.raises(Exception) as excinfo:
        repo.list_activity(pid, user_id=member_id, scope="related", limit=50)
    assert "BROKEN-PAYLOAD" not in str(excinfo.value)
    # Members scope is unaffected by the malformed row.
    member_rows = repo.list_activity(pid, user_id=member_id, scope="members", limit=50)
    assert "todo_bad" in {r.object_id for r in member_rows}


# ---------------------------------------------------------------------------
# Cursor paging
# ---------------------------------------------------------------------------


def test_same_second_cursor_paging_no_duplicates_no_skips(
    service: ProjectActivityService, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    seeded: list[int] = []
    for _ in range(7):
        event_id, _ = _seed_event(
            db,
            pid,
            actor_user_id=owner_id,
            event_type="project.updated",
            object_id=pid,
            ts=7000,  # all in the same second: id DESC must break ties
        )
        seeded.append(event_id)
    older, _ = _seed_event(
        db, pid, actor_user_id=owner_id, event_type="project.updated", object_id=pid, ts=6000
    )
    with db.connect() as conn:
        created_id = int(
            conn.execute(
                "SELECT id FROM project_events WHERE project_id = ? AND event_type = ?",
                (pid, "project.created"),
            ).fetchone()[0]
        )

    seen: list[int] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = service.list_activity(
            pid, user_id=member_id, scope="members", limit=2, cursor=cursor
        )
        pages += 1
        seen.extend(item.event_id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
        assert pages <= 10, "paging did not terminate"
    # project.created (ts=now, newest) → 7 same-second events by id DESC →
    # the older one. Strict key-set paging: no duplicates, no skips.
    assert seen == [created_id, *reversed(seeded), older]
    assert len(seen) == len(set(seen)) == 9


def test_paging_stable_when_new_events_arrive(
    service: ProjectActivityService, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    old_ids = []
    for _ in range(4):
        event_id, _ = _seed_event(
            db,
            pid,
            actor_user_id=owner_id,
            event_type="project.updated",
            object_id=pid,
            ts=8000,
        )
        old_ids.append(event_id)
    first = service.list_activity(pid, user_id=member_id, scope="members", limit=2)
    first_ids = [item.event_id for item in first.items]
    assert first.next_cursor is not None
    # A newer event lands between pages; it must not shift or duplicate the
    # already-seen page, and no committed old event may be skipped.
    new_id, _ = _seed_event(
        db,
        pid,
        actor_user_id=owner_id,
        event_type="project.updated",
        object_id=pid,
        ts=9000,
    )
    second = service.list_activity(
        pid, user_id=member_id, scope="members", limit=2, cursor=first.next_cursor
    )
    second_ids = [item.event_id for item in second.items]
    assert new_id not in first_ids
    assert new_id not in second_ids
    assert not (set(second_ids) & set(first_ids))
    third = service.list_activity(
        pid, user_id=member_id, scope="members", limit=10, cursor=second.next_cursor
    )
    walked = first_ids + second_ids + [item.event_id for item in third.items]
    # Every event committed before paging started is delivered exactly once.
    assert set(old_ids) <= set(walked)
    assert len(walked) == len(set(walked))
    assert third.next_cursor is None


def test_related_full_page_despite_unrelated_interleaving(
    service: ProjectActivityService, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    related_ids: list[int] = []
    ts = 10_000
    for _ in range(6):
        # Unrelated noise: owner-only updates between every related event.
        for _ in range(5):
            ts += 1
            _seed_event(
                db,
                pid,
                actor_user_id=owner_id,
                event_type="project.updated",
                object_id=pid,
                ts=ts,
            )
        ts += 1
        event_id, _ = _seed_event(
            db,
            pid,
            actor_user_id=member_id,  # actor hit for member
            event_type="project.updated",
            object_id=pid,
            ts=ts,
        )
        related_ids.append(event_id)

    page = service.list_activity(pid, user_id=member_id, scope="related", limit=5)
    assert [item.event_id for item in page.items] == list(reversed(related_ids))[:5]
    assert page.next_cursor is not None
    page2 = service.list_activity(
        pid, user_id=member_id, scope="related", limit=5, cursor=page.next_cursor
    )
    assert [item.event_id for item in page2.items] == [related_ids[0]]
    assert page2.next_cursor is None


# ---------------------------------------------------------------------------
# Message creation (repo)
# ---------------------------------------------------------------------------


def test_create_message_writes_message_and_event_atomically(
    repo: ProjectActivityRepo, db: SqlitePool, pid: str, member_id: int
) -> None:
    before = now_ts()
    created = repo.create_message(project_id=pid, author_user_id=member_id, body="大家好")
    assert created.outcome == "created"
    row = created.row
    assert row is not None
    assert row.event_type == EVENT_MESSAGE_CREATED
    assert row.object_kind == "message"
    assert row.actor_user_id == member_id
    assert row.actor_name == "member"
    assert row.message_body == "大家好"
    assert row.object_id
    assert before <= row.created_at <= now_ts() + 1

    messages = _message_rows(db, pid)
    assert len(messages) == 1
    assert messages[0]["message_id"] == row.object_id
    assert messages[0]["author_user_id"] == member_id
    assert messages[0]["body"] == "大家好"
    assert messages[0]["created_at"] == row.created_at

    events = _message_events(db, pid)
    assert len(events) == 1
    assert events[0]["object_id"] == row.object_id
    assert events[0]["payload_json"] == "{}"
    assert events[0]["actor_user_id"] == member_id
    assert events[0]["created_at"] == row.created_at
    assert row.event_id > 0


def test_create_message_nonmember_and_unknown_project_write_nothing(
    repo: ProjectActivityRepo, db: SqlitePool, pid: str, outsider_id: int, owner_id: int
) -> None:
    assert (
        repo.create_message(project_id=pid, author_user_id=outsider_id, body="hi").outcome
        == "not_member"
    )
    assert (
        repo.create_message(project_id="ghost", author_user_id=owner_id, body="hi").outcome
        == "not_member"
    )
    assert _message_rows(db) == []
    assert _message_events(db, pid) == []


def test_create_message_after_removal_is_not_member(
    repo: ProjectActivityRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    mutation = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert mutation.outcome == "removed"
    created = repo.create_message(project_id=pid, author_user_id=member_id, body="迟到的留言")
    assert created.outcome == "not_member"
    assert created.row is None
    assert _message_rows(db, pid) == []
    assert _message_events(db, pid) == []


def test_create_message_rolls_back_when_event_insert_fails(
    repo: ProjectActivityRepo,
    db: SqlitePool,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr("octop.infra.db.repos.project_activity._append_message_event", _boom)
    with pytest.raises(RuntimeError, match="injected event failure"):
        repo.create_message(project_id=pid, author_user_id=member_id, body="不会留下")
    # Neither a half message nor a bodyless event survives the rollback.
    assert _message_rows(db, pid) == []
    assert _message_events(db, pid) == []


def test_postgres_membership_lock_uses_for_share(repo: ProjectActivityRepo) -> None:
    """PG message writes lock the membership row FOR SHARE before inserting;
    lock order member-row → message/event rows matches remove_member."""

    class _Cursor:
        def fetchone(self) -> dict[str, str] | None:
            return {"role": "member"}

    class _Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql: str, params: tuple[object, ...]) -> _Cursor:
            self.statements.append(sql)
            return _Cursor()

    conn = _Connection()
    repo._db.dialect = "postgresql"
    assert repo._member_role_locked(conn, "p1", 7) == "member"
    sql = conn.statements[0]
    assert sql.startswith("SELECT role FROM project_members WHERE project_id = ? AND user_id = ?")
    assert sql.endswith(" FOR SHARE")

    repo._db.dialect = "sqlite"
    conn.statements.clear()
    assert repo._member_role_locked(conn, "p1", 7) == "member"
    assert not conn.statements[0].endswith("FOR SHARE")


# ---------------------------------------------------------------------------
# Service policy
# ---------------------------------------------------------------------------


def test_validate_message_body() -> None:
    assert validate_message_body("  hello  ") == "hello"
    assert validate_message_body(" a\n b ") == "a\n b"  # inner whitespace survives
    assert validate_message_body("x" * MESSAGE_BODY_MAX_LENGTH) == "x" * MESSAGE_BODY_MAX_LENGTH
    for bad in ("", "   ", "\n\t ", "y" * (MESSAGE_BODY_MAX_LENGTH + 1)):
        with pytest.raises(ValueError):
            validate_message_body(bad)


def test_cursor_roundtrip_and_strict_validation() -> None:
    raw = encode_activity_cursor(1700000000, 42)
    assert decode_activity_cursor(raw) == (1700000000, 42)
    assert "=" not in raw and raw == raw.strip()
    assert len(raw) <= 64

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    bad_cursors = (
        "!!!not-base64!!!",
        "x" * 65,  # too long
        _b64(b"0:5"),  # non-positive created_at
        _b64(b"5:0"),  # non-positive id
        _b64(b"abc"),  # not a:b
        _b64(b"5"),  # no colon
        _b64(b"5:6:7"),  # too many parts
        _b64(b"-1:5"),  # negative
        _b64(b"0123:45"),  # non-canonical leading zero
        _b64(b"12:34") + "=",  # strict unpadded only
        _b64(b"+:5"),  # garbage integer
    )
    for bad in bad_cursors:
        with pytest.raises(OctopError) as excinfo:
            decode_activity_cursor(bad)
        assert excinfo.value.code is ErrorCode.PROJECT_ACTIVITY_CURSOR_INVALID
        assert excinfo.value.status == 422


def test_cursor_error_code_has_422_and_locales() -> None:
    assert ErrorCode.PROJECT_ACTIVITY_CURSOR_INVALID.value == "PROJECT_ACTIVITY_CURSOR_INVALID"
    err = OctopError(ErrorCode.PROJECT_ACTIVITY_CURSOR_INVALID, "x")
    assert err.status == 422
    envelope_zh = err.to_envelope(locale="zh")
    envelope_en = err.to_envelope(locale="en")
    assert envelope_zh["error"]["code"] == "PROJECT_ACTIVITY_CURSOR_INVALID"
    assert envelope_zh["error"]["message"]
    assert envelope_en["error"]["message"]
    assert envelope_zh["error"]["message"] != envelope_en["error"]["message"]
    assert error_message("PROJECT_ACTIVITY_CURSOR_INVALID", "en")
    assert error_message("PROJECT_ACTIVITY_CURSOR_INVALID", "zh")


def test_service_validates_scope_and_limit(
    service: ProjectActivityService, pid: str, member_id: int
) -> None:
    with pytest.raises(ValueError):
        service.list_activity(pid, user_id=member_id, scope="bogus")
    with pytest.raises(ValueError):
        service.list_activity(pid, user_id=member_id, scope="members", limit=0)
    with pytest.raises(ValueError):
        service.list_activity(pid, user_id=member_id, scope="members", limit=51)


def test_service_uniform_404_for_outsider_and_unknown_project(
    service: ProjectActivityService, pid: str, owner_id: int, outsider_id: int
) -> None:
    calls = (
        lambda: service.list_activity(pid, user_id=outsider_id, scope="members"),
        lambda: service.list_activity(pid, user_id=outsider_id, scope="related"),
        lambda: service.post_message(pid, user_id=outsider_id, body="hi"),
        lambda: service.list_activity("ghost", user_id=owner_id, scope="members"),
        lambda: service.post_message("ghost", user_id=owner_id, body="hi"),
    )
    for call in calls:
        with pytest.raises(OctopError) as excinfo:
            call()
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        assert excinfo.value.status == 404
        assert excinfo.value.message == "project not found"


def test_service_post_message_trims_and_maps_view(
    service: ProjectActivityService, db: SqlitePool, pid: str, member_id: int
) -> None:
    view = service.post_message(pid, user_id=member_id, body="  修剪后的留言  ")
    assert view.event_type == EVENT_MESSAGE_CREATED
    assert view.object_kind == "message"
    assert view.message_body == "修剪后的留言"
    assert view.actor_user_id == member_id
    assert view.actor_name == "member"
    assert view.object_id
    # The stored body is the trimmed one.
    rows = _message_rows(db, pid)
    assert rows[0]["body"] == "修剪后的留言"
    with pytest.raises(ValueError):
        service.post_message(pid, user_id=member_id, body="   ")


def test_service_post_message_after_removal_is_404(
    service: ProjectActivityService,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    with pytest.raises(OctopError) as excinfo:
        service.post_message(pid, user_id=member_id, body="还在吗")
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    # The removed member also cannot read the timeline any more.
    with pytest.raises(OctopError) as excinfo:
        service.list_activity(pid, user_id=member_id, scope="members")
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_service_hides_member_target_ids(
    service: ProjectActivityService,
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    _seed_event(
        db,
        pid,
        actor_user_id=member_id,
        event_type="project.member_joined",
        object_id=str(member_id),
        payload_json="{}",
        ts=11_000,
    )
    projects.set_member_role(
        project_id=pid, user_id=other_member_id, role="admin", actor_user_id=owner_id
    )
    projects.remove_member(project_id=pid, user_id=other_member_id, actor_user_id=owner_id)

    page = service.list_activity(pid, user_id=member_id, scope="members", limit=50)
    member_items = [item for item in page.items if item.object_kind == "member"]
    assert len(member_items) == 3
    for item in member_items:
        assert item.object_id is None  # member target ids are never exposed
        assert item.message_body is None
    kinds = {item.event_type: item.object_kind for item in page.items}
    assert kinds["project.created"] == "project"
    assert kinds["project.member_removed"] == "member"


def test_service_view_mapping_whitelist_recheck() -> None:
    row = ActivityRow(
        event_id=1,
        event_type="project.some_future_event",
        actor_user_id=None,
        actor_name=None,
        object_kind="project",
        object_id="x",
        message_body=None,
        created_at=5,
    )
    assert activity_item_view(row) is None
    known = ActivityRow(
        event_id=2,
        event_type=EVENT_MESSAGE_CREATED,
        actor_user_id=7,
        actor_name="someone",
        object_kind="message",
        object_id="m1",
        message_body="body",
        created_at=6,
    )
    view = activity_item_view(known)
    assert view is not None
    assert view.message_body == "body"
    assert view.object_kind == "message"


def test_service_message_view_after_author_deleted(
    service: ProjectActivityService,
    repo: ProjectActivityRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    created = repo.create_message(project_id=pid, author_user_id=member_id, body="作者会消失")
    assert created.outcome == "created"
    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (member_id,))
    page = service.list_activity(pid, user_id=owner_id, scope="members", limit=50)
    message_items = [item for item in page.items if item.event_type == EVENT_MESSAGE_CREATED]
    assert len(message_items) == 1
    item = message_items[0]
    assert item.actor_user_id is None
    assert item.actor_name is None
    assert item.message_body == "作者会消失"


def test_service_list_never_leaks_payload_fields(
    service: ProjectActivityService, db: SqlitePool, pid: str, owner_id: int, member_id: int
) -> None:
    _seed_event(
        db,
        pid,
        actor_user_id=owner_id,
        event_type="project.invite_created",
        object_id="inv-1",
        payload_json='{"token": "PLAIN-TOKEN-SECRET"}',
        ts=12_000,
    )
    page = service.list_activity(pid, user_id=member_id, scope="members", limit=50)
    blob = repr(page)
    assert "PLAIN-TOKEN-SECRET" not in blob
    assert "payload" not in blob.lower()
    assert "inv-1" not in blob
    for item in page.items:
        assert item.event_type in ACTIVITY_EVENT_TYPES
