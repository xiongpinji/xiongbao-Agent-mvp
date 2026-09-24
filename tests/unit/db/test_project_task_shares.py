"""Unit tests for ProjectTaskShareRepo, migration 024, and share policy.

PS-05B slice 1: a task owner may grant same-project members revocable read
access to the task card summary only. Covers the paired 024 DDL (composite
FKs, CHECKs, index-before-table ordering, v23 upgrade), grant/revoke/regrant
row semantics, uniform guards without existence leaks, archived-project
rules, cascade cleanup on membership removal/detach/thread/project delete,
literal search on the shared/all scopes, the single-statement ``all`` query,
ascending FOR SHARE member lock order, the service-level 404/403 policy, and
the two-connection SQLite interleaved grant/member-removal race.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.project_task_shares import ProjectTaskShareRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.tasks import ProjectTaskService

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def shares(db: SqlitePool) -> ProjectTaskShareRepo:
    return ProjectTaskShareRepo(db)


@pytest.fixture
def tasks(db: SqlitePool) -> ProjectTaskRepo:
    return ProjectTaskRepo(db)


@pytest.fixture
def projects(db: SqlitePool) -> ProjectRepo:
    return ProjectRepo(db)


@pytest.fixture
def users(db: SqlitePool) -> UserRepo:
    return UserRepo(db)


@pytest.fixture
def threads(db: SqlitePool) -> ThreadRepo:
    return ThreadRepo(db)


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
    project = projects.create_with_owner(creator_user_id=owner_id, name="分享项目")
    projects.add_member(project.project_id, member_id, role="member")
    projects.add_member(project.project_id, other_member_id, role="member")
    return project.project_id


class _StubServices:
    def __init__(
        self,
        projects: ProjectRepo,
        tasks: ProjectTaskRepo,
        shares: ProjectTaskShareRepo,
        threads: ThreadRepo,
    ) -> None:
        self.project_repo = projects
        self.project_task_repo = tasks
        self.project_task_share_repo = shares
        self.thread_repo = threads


@pytest.fixture
def service(
    projects: ProjectRepo,
    tasks: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    threads: ThreadRepo,
) -> ProjectTaskService:
    return ProjectTaskService(_StubServices(projects, tasks, shares, threads))


def _seed_agent(db: SqlitePool, *, agent_id: str, user_id: int) -> None:
    # OR IGNORE so one agent can back several seeded threads across calls.
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agents(agent_id, user_id, name, created_at, updated_at) "
            "VALUES (?, ?, 'bot', 0, 0)",
            (agent_id, user_id),
        )


def _dashboard_key(agent_id: str, user_id: int) -> str:
    return f"{agent_id}:dashboard:{user_id}:dm"


def _seed_thread(
    threads: ThreadRepo,
    *,
    thread_id: str,
    agent_id: str,
    user_id: int,
    title: str | None = None,
    last_active: int | None = None,
) -> None:
    threads.insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        channel_type="dashboard",
        session_key=_dashboard_key(agent_id, user_id),
        title=title,
        last_active=last_active,
    )


def _seed_tasks(
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    uid: int,
    specs: tuple[tuple[str, str | None, int | None], ...],
) -> str:
    """Attach one user's own dashboard DM threads; returns the agent id."""
    agent_id = f"ag_u{uid}"
    _seed_agent(db, agent_id=agent_id, user_id=uid)
    for tid, title, last_active in specs:
        _seed_thread(
            threads,
            thread_id=tid,
            agent_id=agent_id,
            user_id=uid,
            title=title,
            last_active=last_active,
        )
        assert tasks.attach(project_id=pid, user_id=uid, thread_id=tid).outcome == "created"
    return agent_id


def _events(db: SqlitePool, project_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()


def _assert_no_task_or_share_events(
    db: SqlitePool, project_id: str, thread_ids: tuple[str, ...] = ()
) -> None:
    """Shares never write shared project events; no private thread id may
    appear in the member-readable feed (project.created etc. still allowed)."""
    events = _events(db, project_id)
    leaked = [
        e for e in events if "task" in str(e["event_type"]) or "share" in str(e["event_type"])
    ]
    assert not leaked
    for event in events:
        for tid in thread_ids:
            assert tid not in str(event["object_id"])
            assert tid not in str(event["payload_json"])


def _share_rows(db: SqlitePool, project_id: str | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT id, project_id, thread_id, grantee_user_id, granted_by_user_id, "
        "role, granted_at, revoked_at FROM project_task_shares"
    )
    params: tuple[object, ...] = ()
    if project_id is not None:
        sql += " WHERE project_id = ?"
        params = (project_id,)
    with db.connect() as conn:
        return conn.execute(sql + " ORDER BY id", params).fetchall()


def _set_archived(db: SqlitePool, project_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (project_id,))


class _WriterProbe:
    """sqlite3 connection proxy that records the BEGIN IMMEDIATE phase.

    ``began`` fires as the statement is issued — it then blocks while another
    connection holds the write lock; ``entered`` fires only once that lock is
    ours. Lets the test prove the second connection really was contending.
    """

    def __init__(self, conn: Any, *, began: threading.Event, entered: threading.Event) -> None:
        self._conn = conn
        self._began = began
        self._entered = entered

    def execute(self, sql: str, *params: object) -> Any:
        if str(sql).strip().upper() == "BEGIN IMMEDIATE":
            self._began.set()
            cursor = self._conn.execute(sql, *params)
            self._entered.set()
            return cursor
        return self._conn.execute(sql, *params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# Migration 024
# ---------------------------------------------------------------------------


def test_migration_024_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"]: r for r in conn.execute("SELECT * FROM sqlite_master WHERE type='index'")
        }
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_task_shares)").fetchall()
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 24
    assert "project_task_shares" in tables
    assert {
        "id",
        "project_id",
        "thread_id",
        "grantee_user_id",
        "granted_by_user_id",
        "role",
        "granted_at",
        "revoked_at",
    }.issubset(cols)
    # Composite-FK parent key: a UNIQUE, non-partial index on the link pair.
    parent = indexes["idx_project_task_links_project_thread"]
    assert parent["tbl_name"] == "project_task_links"
    assert "UNIQUE" in str(parent["sql"])
    assert "WHERE" not in str(parent["sql"])
    # Active-grant read paths are partial indexes on revoked_at IS NULL.
    assert "revoked_at IS NULL" in str(indexes["idx_project_task_shares_active_grantee"]["sql"])
    assert "revoked_at IS NULL" in str(indexes["idx_project_task_shares_active_thread"]["sql"])


def test_migration_024_composite_fks(db: SqlitePool) -> None:
    with db.connect() as conn:
        fks = conn.execute("PRAGMA foreign_key_list(project_task_shares)").fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    for fk in fks:
        grouped.setdefault(int(fk["id"]), []).append(fk)
    pairs = {
        frozenset(str(r["from"]) for r in rows): (
            str(rows[0]["table"]),
            frozenset(str(r["to"]) for r in rows),
            str(rows[0]["on_delete"]),
        )
        for rows in grouped.values()
    }

    assert pairs[frozenset({"project_id"})] == (
        "project_spaces",
        frozenset({"project_id"}),
        "CASCADE",
    )
    assert pairs[frozenset({"project_id", "thread_id"})] == (
        "project_task_links",
        frozenset({"project_id", "thread_id"}),
        "CASCADE",
    )
    assert pairs[frozenset({"project_id", "grantee_user_id"})] == (
        "project_members",
        frozenset({"project_id", "user_id"}),
        "CASCADE",
    )
    assert pairs[frozenset({"granted_by_user_id"})] == (
        "users",
        frozenset({"id"}),
        "SET NULL",
    )
    # thread_id keeps a direct cascade too (thread delete → share delete).
    assert pairs[frozenset({"thread_id"})] == (
        "threads",
        frozenset({"thread_id"}),
        "CASCADE",
    )


def test_migration_024_check_constraints(
    db: SqlitePool,
    tasks: ProjectTaskRepo,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    insert = (
        "INSERT INTO project_task_shares("
        "project_id, thread_id, grantee_user_id, role, granted_at, revoked_at) "
        "VALUES (?, 'th1', ?, ?, ?, ?)"
    )
    with db.connect() as conn:
        # role is fixed to 'reader' in this slice.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, member_id, "writer", 5, None))
        # granted_at must be a non-negative unix-seconds timestamp.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, member_id, "reader", -1, None))
        # revoked_at can never precede granted_at.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, member_id, "reader", 5, 4))
        # A valid active row is accepted; NULL revoked_at means active.
        conn.execute(insert, (pid, member_id, "reader", 5, None))
    rows = _share_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["revoked_at"] is None


def test_migration_024_composite_fks_reject_orphans(
    db: SqlitePool,
    tasks: ProjectTaskRepo,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    other_pid = projects.create_with_owner(creator_user_id=owner_id, name="别的项目").project_id
    insert = (
        "INSERT INTO project_task_shares("
        "project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at) "
        "VALUES (?, ?, ?, ?, 5)"
    )
    with db.connect() as conn:
        # No task link for (pid, ghost) → composite FK to project_task_links.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, "ghost", member_id, owner_id))
        # Grantee is not a member of this project → FK to project_members.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, "th1", outsider_id, owner_id))
        # Cross-project mismatch: th1 is linked in pid, not other_pid.
        projects.add_member(other_pid, member_id, role="member")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (other_pid, "th1", member_id, owner_id))
    assert _share_rows(db) == []


def test_migration_upgrades_from_v23(tmp_path: Path) -> None:
    """A DB at watermark 23 must gain project_task_shares by re-running."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS project_task_shares;
            DROP INDEX IF EXISTS idx_project_task_links_project_thread;
            UPDATE _schema_version SET version = 23;
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
    assert "project_task_shares" in tables
    assert "idx_project_task_links_project_thread" in indexes
    # The upgraded schema is functional end to end.
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    threads = ThreadRepo(pool)
    tasks = ProjectTaskRepo(pool)
    shares = ProjectTaskShareRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    member = users.create(username="member", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级分享")
    projects.add_member(project.project_id, member, role="member")
    _seed_tasks(tasks, pool, threads, project.project_id, owner, (("th1", "T", None),))
    mutation = shares.grant(
        project_id=project.project_id,
        thread_id="th1",
        actor_user_id=owner,
        grantee_user_id=member,
    )
    assert mutation.outcome == "created"
    summary = shares.get_shared_summary(project.project_id, "th1", user_id=member)
    assert summary is not None and summary.access == "reader"


def test_migration_024_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (MIGRATIONS / "024_project_task_shares.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 24


def test_migration_024_pg_pair_declares_same_shape() -> None:
    """Token-level parity + ordering check of the PG script (no live PG)."""
    sqlite_sql = (MIGRATIONS / "024_project_task_shares.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "024_project_task_shares.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "project_task_shares",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_task_links_project_thread",
        "ON project_task_links(project_id, thread_id)",
        "REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE",
        "grantee_user_id INTEGER NOT NULL",
        "granted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "role TEXT NOT NULL DEFAULT 'reader' CHECK (role IN ('reader'))",
        "CHECK (granted_at >= 0)",
        "CHECK (revoked_at IS NULL OR revoked_at >= granted_at)",
        "UNIQUE (project_id, thread_id, grantee_user_id)",
        "REFERENCES project_task_links(project_id, thread_id) ON DELETE CASCADE",
        "REFERENCES project_members(project_id, user_id) ON DELETE CASCADE",
        "idx_project_task_shares_active_grantee",
        "idx_project_task_shares_active_thread",
        "WHERE revoked_at IS NULL",
        "UPDATE _schema_version SET version = 24",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    assert "GENERATED BY DEFAULT AS IDENTITY" in pg_sql
    assert "AUTOINCREMENT" not in pg_sql
    assert "AUTOINCREMENT" in sqlite_sql
    assert "GENERATED BY DEFAULT" not in sqlite_sql

    # The composite-FK parent index must be created before the child table in
    # BOTH dialects, or PostgreSQL rejects the CREATE TABLE outright.
    for sql in (sqlite_sql, pg_sql):
        index_at = sql.index("idx_project_task_links_project_thread")
        table_at = sql.index("CREATE TABLE IF NOT EXISTS project_task_shares")
        assert index_at < table_at

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 5  # parent index + table + 2 indexes + watermark
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# Grant / revoke / regrant row semantics
# ---------------------------------------------------------------------------


def test_grant_created_duplicate_revoke_regrant(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))

    first = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert first.outcome == "created"
    assert first.share is not None
    assert first.share.user_id == member_id
    assert first.share.role == "reader"
    assert first.share.granted_at == pytest.approx(now_ts(), abs=5)
    rows = _share_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["granted_by_user_id"] == owner_id
    assert rows[0]["revoked_at"] is None

    # Active duplicate: no row update at all (granted_at untouched).
    granted_at = rows[0]["granted_at"]
    second = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert second.outcome == "duplicate"
    assert second.share is not None and second.share.granted_at == granted_at
    assert len(_share_rows(db, pid)) == 1

    # Revoke stamps revoked_at; the row survives. Repeat revoke is a no-op.
    outcome = shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert outcome == "revoked"
    revoked = _share_rows(db, pid)
    assert len(revoked) == 1 and revoked[0]["revoked_at"] is not None
    stamp = revoked[0]["revoked_at"]
    outcome = shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert outcome == "noop"
    assert _share_rows(db, pid)[0]["revoked_at"] == stamp

    # Regrant reactivates the SAME row (no duplicates, revoked_at cleared).
    third = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert third.outcome == "regranted"
    assert third.share is not None and third.share.role == "reader"
    rows = _share_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["id"] == revoked[0]["id"]
    assert rows[0]["revoked_at"] is None
    assert rows[0]["granted_at"] >= stamp
    _assert_no_task_or_share_events(db, pid, ("th1",))


def test_grant_guards_are_uniform_and_write_nothing(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _seed_tasks(tasks, db, threads, pid, member_id, (("th_m", "M", None),))
    other_pid = projects.create_with_owner(creator_user_id=owner_id, name="别的项目").project_id

    def _grant(**kw: Any) -> str:
        base: dict[str, Any] = {
            "project_id": pid,
            "thread_id": "th1",
            "actor_user_id": owner_id,
            "grantee_user_id": member_id,
        }
        base.update(kw)
        return shares.grant(**base).outcome

    # Actor membership first: outsiders and unknown projects are alike.
    assert _grant(actor_user_id=outsider_id) == "not_member"
    assert _grant(project_id="nope") == "not_member"
    # Not the actor's own linked task: foreign task, unknown id, cross-project.
    assert _grant(actor_user_id=member_id) == "invalid_task"
    assert _grant(thread_id="ghost") == "invalid_task"
    assert _grant(project_id=other_pid) == "invalid_task"
    # Recipient must be a current member of the same project; self is invalid.
    assert _grant(grantee_user_id=outsider_id) == "invalid_recipient"
    assert _grant(grantee_user_id=owner_id) == "invalid_recipient"

    assert _share_rows(db) == []
    _assert_no_task_or_share_events(db, pid, ("th1", "th_m"))


def test_grant_revalidates_thread_binding_inside_transaction(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    agent_id = f"ag_u{owner_id}"
    # Post-attach mutation of the thread row must be caught by the write
    # transaction: a card stays grantable only while the thread is the
    # owner's live Dashboard :dm conversation.
    with db.transaction() as conn:
        conn.execute("UPDATE threads SET channel_type = 'feishu' WHERE thread_id = 'th1'")
    outcome = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "invalid_task"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE threads SET channel_type = 'dashboard', session_key = ? "
            "WHERE thread_id = 'th1'",
            (_dashboard_key(agent_id, member_id),),
        )
    outcome = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "invalid_task"
    assert _share_rows(db) == []


def test_grant_denied_on_archived_project_but_revoke_still_works(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    outcome = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "created"
    _set_archived(db, pid)
    # New grants and regrants are both refused while archived …
    outcome = shares.grant(
        project_id=pid,
        thread_id="th1",
        actor_user_id=owner_id,
        grantee_user_id=other_member_id,
    ).outcome
    assert outcome == "archived"
    outcome = shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert outcome == "revoked"
    outcome = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "archived"
    # … but reads (owner list + recipient summary) stay allowed and honest.
    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None and listed == []
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is None
    assert len(_share_rows(db, pid)) == 1  # revoked row survives


def test_revoke_and_list_shares_are_task_owner_only(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    shares.grant(project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id)

    # A project member (or admin!) who does not own the task can neither
    # revoke nor list its grants — uniform outcomes, no existence signal.
    projects.set_member_role(
        project_id=pid, user_id=other_member_id, role="admin", actor_user_id=owner_id
    )
    for actor in (member_id, other_member_id):
        outcome = shares.revoke(
            project_id=pid, thread_id="th1", actor_user_id=actor, grantee_user_id=member_id
        )
        assert outcome == "missing_task"
        assert shares.list_shares(pid, "th1", actor_user_id=actor) is None
    outcome = shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=outsider_id, grantee_user_id=member_id
    )
    assert outcome == "not_member"
    assert shares.list_shares(pid, "th1", actor_user_id=outsider_id) is None
    assert shares.list_shares(pid, "ghost", actor_user_id=owner_id) is None

    # The owner lists grant metadata only: grantee id, role, grant time.
    shares.grant(
        project_id=pid,
        thread_id="th1",
        actor_user_id=owner_id,
        grantee_user_id=other_member_id,
    )
    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None
    assert [(row.user_id, row.role) for row in listed] == [
        (member_id, "reader"),
        (other_member_id, "reader"),
    ]
    assert all(row.granted_at > 0 for row in listed)
    # Revoked grants drop off the active list but keep their row.
    shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None and [row.user_id for row in listed] == [other_member_id]
    assert len(_share_rows(db, pid)) == 2


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------


def test_grantee_member_removal_cascades_and_readd_regrants(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    outcome = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "created"
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is not None

    mutation = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert mutation.outcome == "removed"
    # The grant row is gone with the membership; the owner's task survives.
    assert _share_rows(db, pid) == []
    assert tasks.get_for_owner(pid, "th1", user_id=owner_id) is not None

    # Re-added membership + regrant is a brand-new row (201 semantics).
    projects.add_member(pid, member_id, role="member")
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is None
    outcome = shares.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "created"
    assert len(_share_rows(db, pid)) == 1


def test_two_connection_interleaved_grant_and_member_removal(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    service: ProjectTaskService,
    pid: str,
    owner_id: int,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite interleaved writers: grant racing membership removal.

    Two independently opened ``SqlitePool`` connections point at the same
    file. The main thread holds the first pool's ``BEGIN IMMEDIATE`` write
    transaction and runs the production ``remove_member`` path inside it; a
    worker thread on the second pool attempts the grant. The second writer's
    BEGIN IMMEDIATE can only complete once the removal commits, so the grant
    observes the missing membership, writes nothing, and answers
    ``invalid_recipient`` (the uniform 404) — never an orphan share row or a
    card the removed member can read.
    """
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "私密卡片", 7),))

    second = SqlitePool(db.path)
    began, entered, done = threading.Event(), threading.Event(), threading.Event()
    second._conn = _WriterProbe(second._conn, began=began, entered=entered)
    second_shares = ProjectTaskShareRepo(second)

    def _worker() -> Any:
        try:
            return second_shares.grant(
                project_id=pid,
                thread_id="th1",
                actor_user_id=owner_id,
                grantee_user_id=member_id,
            )
        finally:
            done.set()

    original_transaction = db.transaction
    held: list[Any] = []

    @contextlib.contextmanager
    def _joined_transaction() -> Iterator[Any]:
        # SqlitePool.transaction is not reentrant; join the held transaction
        # so the real remove_member path runs inside the writer under test.
        if held:
            yield held[0]
            return
        with original_transaction() as conn:
            held.append(conn)
            try:
                yield conn
            finally:
                held.clear()

    monkeypatch.setattr(db, "transaction", _joined_transaction)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with db.transaction():
            removal = projects.remove_member(
                project_id=pid, user_id=member_id, actor_user_id=owner_id
            )
            assert removal.outcome == "removed"
            future = executor.submit(_worker)
            assert began.wait(timeout=10), "grant worker never attempted BEGIN IMMEDIATE"
            # While the removal transaction is open the second writer neither
            # acquires the write lock nor finishes.
            assert not entered.is_set()
            assert not done.wait(timeout=0.5)
        mutation = future.result(timeout=10)
    finally:
        executor.shutdown(wait=True)
        second.close()

    # The grant serialized behind the removal and lost: uniform invalid
    # recipient, no row written, no private card reachable by either read.
    assert entered.is_set()
    assert mutation.outcome == "invalid_recipient"
    assert mutation.share is None
    assert _share_rows(db, pid) == []
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is None
    assert shares.list_shared(pid, user_id=member_id) == []
    # The owner's task link is untouched by the recipient's removal.
    assert tasks.get_for_owner(pid, "th1", user_id=owner_id) is not None
    # invalid_recipient collapses into the same uniform task 404 as any other
    # invalid target, also on the retry path after the race.
    with pytest.raises(OctopError) as excinfo:
        service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert excinfo.value.status == 404


def test_owner_member_removal_detaches_link_and_cascades_shares(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, member_id, (("th_m", "M", None),))
    shares.grant(
        project_id=pid, thread_id="th_m", actor_user_id=member_id, grantee_user_id=owner_id
    )
    assert len(_share_rows(db, pid)) == 1
    mutation = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert mutation.outcome == "removed"
    assert _share_rows(db, pid) == []
    # The removed member's original thread is untouched.
    assert threads.get("th_m") is not None
    # The former recipient sees nothing shared anymore.
    assert shares.list_shared(pid, user_id=owner_id) == []


def test_detach_thread_delete_and_project_delete_cascade_shares(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T1", None), ("th2", "T2", None)))
    for tid in ("th1", "th2"):
        outcome = shares.grant(
            project_id=pid, thread_id=tid, actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        assert outcome == "created"

    # Detach removes the link and cascades its shares; the thread survives.
    assert tasks.detach(project_id=pid, thread_id="th1", user_id=owner_id) == "removed"
    assert [r["thread_id"] for r in _share_rows(db, pid)] == ["th2"]
    assert threads.get("th1") is not None

    # Deleting the thread cascades link → share.
    threads.delete("th2")
    assert _share_rows(db, pid) == []

    # Deleting the project cascades everything; threads still survive.
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th3", "T3", None),))
    shares.grant(project_id=pid, thread_id="th3", actor_user_id=owner_id, grantee_user_id=member_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _share_rows(db) == []
    assert threads.get("th3") is not None


# ---------------------------------------------------------------------------
# Shared / all reads
# ---------------------------------------------------------------------------


def test_list_shared_is_scoped_literal_and_paged(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(
        tasks,
        db,
        threads,
        pid,
        owner_id,
        (("th_a", "Report 100%", 300), ("th_b", "report_100x", 200), ("th_c", "周报", 100)),
    )
    _seed_tasks(tasks, db, threads, pid, other_member_id, (("th_x", "Other private", 250),))
    for tid in ("th_a", "th_b", "th_c"):
        shares.grant(
            project_id=pid, thread_id=tid, actor_user_id=owner_id, grantee_user_id=member_id
        )

    rows = shares.list_shared(pid, user_id=member_id)
    assert [r.thread_id for r in rows] == ["th_a", "th_b", "th_c"]
    assert all(r.access == "reader" for r in rows)
    assert all(r.owner_user_id == owner_id for r in rows)
    # Never-granted, outsider, and unknown-project reads are empty.
    assert shares.list_shared(pid, user_id=other_member_id) == []
    assert shares.list_shared(pid, user_id=outsider_id) == []
    assert shares.list_shared("ghost", user_id=member_id) == []

    # limit + 1 for has_more.
    assert len(shares.list_shared(pid, user_id=member_id, limit=1)) == 2

    # Literal, case-insensitive search: %, _, and CJK stay literal.
    assert {r.thread_id for r in shares.list_shared(pid, user_id=member_id, q="REPORT")} == {
        "th_a",
        "th_b",
    }
    assert {r.thread_id for r in shares.list_shared(pid, user_id=member_id, q="100%")} == {"th_a"}
    assert {r.thread_id for r in shares.list_shared(pid, user_id=member_id, q="t_1")} == {"th_b"}
    assert {r.thread_id for r in shares.list_shared(pid, user_id=member_id, q="周报")} == {"th_c"}
    # Revoked grants disappear from the shared scope immediately.
    shares.revoke(
        project_id=pid, thread_id="th_a", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert "th_a" not in {r.thread_id for r in shares.list_shared(pid, user_id=member_id)}


def test_list_all_merges_own_and_shared_in_one_statement(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_tasks(
        tasks,
        db,
        threads,
        pid,
        owner_id,
        (("th_o1", "Own hot", 500), ("th_o2", "Own cold", 300)),
    )
    _seed_tasks(
        tasks,
        db,
        threads,
        pid,
        member_id,
        (("th_s1", "Shared warm", 400), ("th_s2", "Shared off", 100), ("th_p", "Private", 350)),
    )
    for tid in ("th_s1", "th_s2"):
        shares.grant(
            project_id=pid, thread_id=tid, actor_user_id=member_id, grantee_user_id=owner_id
        )

    # The contract mandates ONE SQL statement: UNION subquery + one final
    # ORDER BY / LIMIT / OFFSET, never two paged queries merged in Python.
    statements: list[str] = []
    original_connect = db.connect

    @contextlib.contextmanager
    def counting_connect() -> Iterator[Any]:
        with original_connect() as conn:

            class _Conn:
                def execute(self, sql: str, params: object = ()) -> Any:
                    statements.append(sql)
                    return conn.execute(sql, params)

            yield _Conn()

    monkeypatch.setattr(db, "connect", counting_connect)
    rows = shares.list_all(pid, user_id=owner_id)
    assert len(statements) == 1
    upper = statements[0].upper()
    assert upper.count("UNION") == 1
    assert upper.count("ORDER BY") == 1
    assert upper.count("LIMIT") == 1

    # Interleaved activity order across the own/shared boundary; the member's
    # ungranted private task never appears.
    assert [(r.thread_id, r.access) for r in rows] == [
        ("th_o1", "owner"),
        ("th_s1", "reader"),
        ("th_o2", "owner"),
        ("th_s2", "reader"),
    ]
    assert "th_p" not in {r.thread_id for r in rows}

    # Stable paging across the boundary with limit + 1 has_more detection.
    page = shares.list_all(pid, user_id=owner_id, limit=2)
    assert [r.thread_id for r in page] == ["th_o1", "th_s1", "th_o2"]
    page = shares.list_all(pid, user_id=owner_id, limit=2, offset=2)
    assert [r.thread_id for r in page] == ["th_o2", "th_s2"]
    assert len(shares.list_all(pid, user_id=owner_id, limit=2, offset=3)) == 1

    # Literal search applies inside both branches.
    own_hits = {r.thread_id for r in shares.list_all(pid, user_id=owner_id, q="own")}
    assert own_hits == {"th_o1", "th_o2"}
    shared_hits = {r.thread_id for r in shares.list_all(pid, user_id=owner_id, q="shared")}
    assert shared_hits == {"th_s1", "th_s2"}


def test_get_shared_summary_scoping(
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "卡片", 7),))
    shares.grant(project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id)

    summary = shares.get_shared_summary(pid, "th1", user_id=member_id)
    assert summary is not None
    assert summary.access == "reader"
    assert summary.title == "卡片"
    assert summary.owner_user_id == owner_id
    assert summary.last_active == 7

    # Non-granted member, the owner themself (owner path is get_for_owner),
    # unknown ids, and cross-project reads are all None.
    assert shares.get_shared_summary(pid, "th1", user_id=other_member_id) is None
    assert shares.get_shared_summary(pid, "th1", user_id=owner_id) is None
    assert shares.get_shared_summary(pid, "ghost", user_id=member_id) is None
    other_pid = projects.create_with_owner(creator_user_id=owner_id, name="别的").project_id
    projects.add_member(other_pid, member_id, role="member")
    assert shares.get_shared_summary(other_pid, "th1", user_id=member_id) is None
    # Revoked grants lose the detail read immediately.
    shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is None


def test_postgres_grant_locks_member_rows_ascending_for_share(
    shares: ProjectTaskShareRepo,
) -> None:
    """PG writes lock both membership rows FOR SHARE in ascending user-id
    order before any share row — deadlock-free against opposite-direction
    grants and remove_member."""

    class _Cursor:
        def fetchone(self) -> dict[str, str] | None:
            return {"role": "member"}

    class _Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def execute(self, sql: str, params: tuple[object, ...]) -> _Cursor:
            self.calls.append((sql, params))
            return _Cursor()

    conn = _Connection()
    shares._db.dialect = "postgresql"
    roles = shares._members_locked(conn, "p1", [9, 3, 9])
    assert roles == {3: "member", 9: "member"}
    assert [params[1] for _, params in conn.calls] == [3, 9]
    assert all(
        sql.startswith("SELECT role FROM project_members WHERE project_id = ? AND user_id = ?")
        and sql.endswith(" FOR SHARE")
        for sql, _ in conn.calls
    )

    shares._db.dialect = "sqlite"
    conn.calls.clear()
    assert shares._members_locked(conn, "p1", [9, 3]) == {3: "member", 9: "member"}
    assert not any(sql.endswith("FOR SHARE") for sql, _ in conn.calls)


# ---------------------------------------------------------------------------
# Service policy
# ---------------------------------------------------------------------------


def test_service_scope_own_is_default_and_shared_all_merge(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(
        tasks, db, threads, pid, owner_id, (("th_o1", "Own A", 300), ("th_o2", "Own B", 100))
    )
    _seed_tasks(tasks, db, threads, pid, member_id, (("th_m1", "Member 秘密", 200),))
    view, created = service.grant_share(pid, "th_m1", user_id=member_id, grantee_user_id=owner_id)
    assert created is True
    assert (view.user_id, view.role) == (owner_id, "reader")

    default = service.list_tasks(pid, user_id=owner_id)
    own = service.list_tasks(pid, user_id=owner_id, scope="own")
    assert [v.thread_id for v in default.items] == ["th_o1", "th_o2"]
    assert [v.thread_id for v in own.items] == [v.thread_id for v in default.items]
    assert all(v.access == "owner" for v in own.items)

    shared = service.list_tasks(pid, user_id=owner_id, scope="shared")
    assert [v.thread_id for v in shared.items] == ["th_m1"]
    assert shared.items[0].access == "reader"
    assert shared.has_more is False

    everything = service.list_tasks(pid, user_id=owner_id, scope="all")
    assert [(v.thread_id, v.access) for v in everything.items] == [
        ("th_o1", "owner"),
        ("th_m1", "reader"),
        ("th_o2", "owner"),
    ]
    # The member's own view is unchanged by granting: own scope only.
    member_own = service.list_tasks(pid, user_id=member_id)
    assert [v.thread_id for v in member_own.items] == ["th_m1"]
    assert service.list_tasks(pid, user_id=member_id, scope="shared").items == []


def test_service_get_task_owner_then_reader_then_uniform_404(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "SECRET-标题", None),))
    projects.set_member_role(
        project_id=pid, user_id=other_member_id, role="admin", actor_user_id=owner_id
    )

    # Project owner/admin role never overrides threads.user_id: until an
    # explicit grant, the project admin gets the same 404 as a bystander.
    for actor in (member_id, other_member_id, outsider_id):
        with pytest.raises(OctopError) as excinfo:
            service.get_task(pid, "th1", user_id=actor)
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        assert "SECRET" not in excinfo.value.message

    owner_view = service.get_task(pid, "th1", user_id=owner_id)
    assert owner_view.access == "owner"

    service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    reader_view = service.get_task(pid, "th1", user_id=member_id)
    assert reader_view.access == "reader"
    assert reader_view.title == "SECRET-标题"
    # The project admin is still locked out.
    with pytest.raises(OctopError):
        service.get_task(pid, "th1", user_id=other_member_id)

    # Revoke removes access immediately.
    service.revoke_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    with pytest.raises(OctopError) as excinfo:
        service.get_task(pid, "th1", user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_service_grant_revoke_list_flow(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))

    # Self-grant is the same uniform 404 as any invalid target.
    with pytest.raises(OctopError) as excinfo:
        service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=owner_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    # Non-member recipient → uniform task 404, nothing written.
    with pytest.raises(OctopError) as excinfo:
        service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=outsider_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert _share_rows(db) == []

    view, created = service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    assert created is True and view.role == "reader" and view.user_id == member_id
    view, created = service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    assert created is False
    service.revoke_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    service.revoke_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)  # no-op 204
    view, created = service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    assert created is False  # regrant reactivates → 200 semantics
    service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=other_member_id)

    listed = service.list_shares(pid, "th1", user_id=owner_id)
    assert {row.user_id for row in listed} == {member_id, other_member_id}
    assert all(row.role == "reader" for row in listed)

    # Non-owners (grantee, bystander, outsider) never list or manage grants.
    for actor in (member_id, other_member_id, outsider_id):
        with pytest.raises(OctopError) as excinfo:
            service.list_shares(pid, "th1", user_id=actor)
        assert excinfo.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        service.revoke_share(pid, "th1", user_id=member_id, grantee_user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    _assert_no_task_or_share_events(db, pid, ("th1",))


def test_service_share_guards_uniform_404_for_outsiders(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    calls = (
        lambda: service.grant_share(pid, "th1", user_id=outsider_id, grantee_user_id=member_id),
        lambda: service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=outsider_id),
        lambda: service.grant_share("ghost", "th1", user_id=owner_id, grantee_user_id=member_id),
        lambda: service.revoke_share(pid, "th1", user_id=outsider_id, grantee_user_id=member_id),
        lambda: service.revoke_share("ghost", "th1", user_id=owner_id, grantee_user_id=member_id),
        lambda: service.list_shares(pid, "th1", user_id=outsider_id),
        lambda: service.list_shares("ghost", "th1", user_id=owner_id),
        lambda: service.list_tasks(pid, user_id=outsider_id, scope="shared"),
        lambda: service.list_tasks(pid, user_id=outsider_id, scope="all"),
        lambda: service.get_task(pid, "th1", user_id=outsider_id),
    )
    for call in calls:
        with pytest.raises(OctopError) as excinfo:
            call()
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        assert excinfo.value.status == 404
    assert _share_rows(db) == []


def test_service_archived_project_forbids_grant_but_allows_reads_and_revoke(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    _set_archived(db, pid)

    with pytest.raises(OctopError) as excinfo:
        service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    err = excinfo.value
    assert err.code is ErrorCode.FORBIDDEN
    assert err.status == 403
    assert "archived" in err.message

    # Reads stay allowed while archived: owner detail, reader detail, lists.
    assert service.get_task(pid, "th1", user_id=owner_id).access == "owner"
    assert service.get_task(pid, "th1", user_id=member_id).access == "reader"
    page = service.list_tasks(pid, user_id=member_id, scope="all")
    assert [v.thread_id for v in page.items] == ["th1"]
    assert len(service.list_shares(pid, "th1", user_id=owner_id)) == 1

    # Revoke (tightening access) is allowed on archived projects.
    service.revoke_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    with pytest.raises(OctopError):
        service.get_task(pid, "th1", user_id=member_id)
