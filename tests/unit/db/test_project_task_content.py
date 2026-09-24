"""Unit tests for ProjectTaskContentRepo, migration 025, and the text policy.

PS-05B-2A: a task owner may grant a named member who ALREADY holds an active
024 card share separate, explicit, revocable read-only access to the task
conversation TEXT. Covers the paired 025 DDL (composite FK onto the 024
unique triple, CHECKs, v24 upgrade without backfill), no auto-upgrade of
existing card shares, text grant/revoke/regrant row semantics, card revoke
deleting the text grant in the same transaction, card regrant never reviving
text, membership/detach/thread/project cascades, archived-project rules,
the single-transaction read authorization + raw-row fetch, projection
status handling, raw-seq cursor paging across skipped rows, the strict
message_to_dict sanitization whitelist (empty vs non-empty tool_calls,
stream errors, role/wire mismatch, oversized rows, 32768-byte truncation),
HistoryArchive mode always answering pending, ascending FOR SHARE lock
order on PostgreSQL, and the two-connection SQLite interleaved text-grant /
card-revoke race.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict,
)

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.project_task_content import ProjectTaskContentRepo
from octop.infra.db.repos.project_task_shares import ProjectTaskShareRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.thread_messages import ThreadMessageRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.history.service import HistoryArchive
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
def content(db: SqlitePool) -> ProjectTaskContentRepo:
    return ProjectTaskContentRepo(db)


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
def messages(db: SqlitePool) -> ThreadMessageRepo:
    return ThreadMessageRepo(db)


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
    project = projects.create_with_owner(creator_user_id=owner_id, name="正文项目")
    projects.add_member(project.project_id, member_id, role="member")
    projects.add_member(project.project_id, other_member_id, role="member")
    return project.project_id


class _StubServices:
    def __init__(
        self,
        projects: ProjectRepo,
        tasks: ProjectTaskRepo,
        shares: ProjectTaskShareRepo,
        content: ProjectTaskContentRepo,
        threads: ThreadRepo,
    ) -> None:
        self.project_repo = projects
        self.project_task_repo = tasks
        self.project_task_share_repo = shares
        self.project_task_content_repo = content
        self.thread_repo = threads


def _stub(
    projects: ProjectRepo,
    tasks: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    threads: ThreadRepo,
) -> _StubServices:
    return _StubServices(projects, tasks, shares, content, threads)


@pytest.fixture
def service(
    projects: ProjectRepo,
    tasks: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    threads: ThreadRepo,
) -> ProjectTaskService:
    return ProjectTaskService(_stub(projects, tasks, shares, content, threads))


@pytest.fixture
def archive_service(
    projects: ProjectRepo,
    tasks: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    threads: ThreadRepo,
) -> ProjectTaskService:
    """Service in versioned-history mode: reads must stay pending."""
    return ProjectTaskService(
        _stub(projects, tasks, shares, content, threads),
        history_archive=MagicMock(spec=HistoryArchive),
    )


def _seed_agent(db: SqlitePool, *, agent_id: str, user_id: int) -> None:
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
    events = _events(db, project_id)
    leaked = [
        e
        for e in events
        if "task" in str(e["event_type"])
        or "share" in str(e["event_type"])
        or "text" in str(e["event_type"])
    ]
    assert not leaked
    for event in events:
        for tid in thread_ids:
            assert tid not in str(event["object_id"])
            assert tid not in str(event["payload_json"])


def _content_rows(db: SqlitePool, project_id: str | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at "
        "FROM project_task_content_grants"
    )
    params: tuple[object, ...] = ()
    if project_id is not None:
        sql += " WHERE project_id = ?"
        params = (project_id,)
    with db.connect() as conn:
        return conn.execute(sql + " ORDER BY grantee_user_id", params).fetchall()


def _set_archived(db: SqlitePool, project_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (project_id,))


def _wire(message: Any) -> str:
    return json.dumps(message_to_dict(message), ensure_ascii=False, default=str)


def _human(text: Any) -> tuple[str, str]:
    return ("human", _wire(HumanMessage(content=text)))


def _ai(text: Any, **kwargs: Any) -> tuple[str, str]:
    return ("ai", _wire(AIMessage(content=text, **kwargs)))


def _seed_rows(db: SqlitePool, thread_id: str, rows: tuple[tuple[int, str, str], ...]) -> None:
    """Seed raw thread_messages rows: (seq, stored role, message_json)."""
    with db.transaction() as conn:
        for seq, role, message_json in rows:
            conn.execute(
                "INSERT INTO thread_messages("
                "thread_id, seq, message_id, role, message_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, seq, f"m{seq}", role, message_json, 1000 + seq),
            )


class _WriterProbe:
    """sqlite3 connection proxy that records the BEGIN IMMEDIATE phase."""

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


def _grant_card(
    shares: ProjectTaskShareRepo, pid: str, tid: str, owner_id: int, member_id: int
) -> None:
    outcome = shares.grant(
        project_id=pid, thread_id=tid, actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "created"


# ---------------------------------------------------------------------------
# Migration 025
# ---------------------------------------------------------------------------


def test_migration_025_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(project_task_content_grants)").fetchall()
        }
        pk = {
            str(r["name"])
            for r in conn.execute("PRAGMA table_info(project_task_content_grants)").fetchall()
            if int(r["pk"]) > 0
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 25
    assert "project_task_content_grants" in tables
    # Exactly the contract's five columns — no surrogate id, no extras.
    assert cols == {
        "project_id",
        "thread_id",
        "grantee_user_id",
        "granted_by_user_id",
        "granted_at",
    }
    assert pk == {"project_id", "thread_id", "grantee_user_id"}


def test_migration_025_composite_fks(db: SqlitePool) -> None:
    with db.connect() as conn:
        fks = conn.execute("PRAGMA foreign_key_list(project_task_content_grants)").fetchall()
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
    assert pairs == {
        frozenset({"project_id", "thread_id", "grantee_user_id"}): (
            "project_task_shares",
            frozenset({"project_id", "thread_id", "grantee_user_id"}),
            "CASCADE",
        ),
        frozenset({"granted_by_user_id"}): ("users", frozenset({"id"}), "SET NULL"),
    }


def test_migration_025_pk_and_check_constraints(
    db: SqlitePool,
    tasks: ProjectTaskRepo,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_shares("
            "project_id, thread_id, grantee_user_id, granted_by_user_id, role, granted_at) "
            "VALUES (?, 'th1', ?, ?, 'reader', 5)",
            (pid, member_id, owner_id),
        )
    insert = (
        "INSERT INTO project_task_content_grants("
        "project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at) "
        "VALUES (?, 'th1', ?, ?, ?)"
    )
    with db.connect() as conn:
        # granted_at must be a non-negative unix-seconds timestamp.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, member_id, owner_id, -1))
        # A valid row is accepted …
        conn.execute(insert, (pid, member_id, owner_id, 5))
        # … and the (project, thread, grantee) primary key forbids duplicates.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, member_id, owner_id, 6))
    rows = _content_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["granted_at"] == 5


def test_migration_025_fk_rejects_orphans_and_cascades(
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
        "INSERT INTO project_task_content_grants("
        "project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at) "
        "VALUES (?, ?, ?, ?, 5)"
    )
    with db.connect() as conn:
        # No card share row at all → composite FK to project_task_shares.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, "th1", member_id, owner_id))
        # Unknown thread / unknown grantee: the parent triple must exist.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, "ghost", member_id, owner_id))
        # Cross-project mismatch: th1's card (once granted) lives in pid only.
        _grant_card(ProjectTaskShareRepo(db), pid, "th1", owner_id, member_id)
        projects.add_member(other_pid, member_id, role="member")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (other_pid, "th1", member_id, owner_id))
        # Deleting the card share row cascades the text grant away.
        conn.execute(insert, (pid, "th1", member_id, owner_id))
        conn.execute(
            "DELETE FROM project_task_shares "
            "WHERE project_id = ? AND thread_id = 'th1' AND grantee_user_id = ?",
            (pid, member_id),
        )
    assert _content_rows(db) == []
    assert outsider_id != member_id


def test_migration_upgrades_from_v24_without_backfill(tmp_path: Path) -> None:
    """A DB at watermark 24 with existing card shares gains the 025 table
    with ZERO rows: card grants never auto-upgrade to text access."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    threads = ThreadRepo(pool)
    tasks = ProjectTaskRepo(pool)
    shares = ProjectTaskShareRepo(pool)
    content = ProjectTaskContentRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    member = users.create(username="member", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级正文")
    projects.add_member(project.project_id, member, role="member")
    _seed_tasks(tasks, pool, threads, project.project_id, owner, (("th1", "T", None),))
    # An existing 024 card grant, created BEFORE the 025 table exists.
    assert (
        shares.grant(
            project_id=project.project_id,
            thread_id="th1",
            actor_user_id=owner,
            grantee_user_id=member,
        ).outcome
        == "created"
    )
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS project_task_content_grants;
            UPDATE _schema_version SET version = 24;
            """
        )
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 25
    assert "project_task_content_grants" in tables
    # No backfill: the pre-existing card grant has no text access.
    assert _content_rows(pool) == []
    listed = shares.list_shares(project.project_id, "th1", actor_user_id=owner)
    assert listed is not None and len(listed) == 1
    assert listed[0].can_read_text is False
    summary = shares.get_shared_summary(project.project_id, "th1", user_id=member)
    assert summary is not None and summary.can_read_text is False
    assert (
        content.read_page(
            project_id=project.project_id,
            thread_id="th1",
            actor_user_id=member,
            limit=10,
        )
        is None
    )
    # The upgraded schema is functional end to end.
    mutation = content.grant(
        project_id=project.project_id,
        thread_id="th1",
        actor_user_id=owner,
        grantee_user_id=member,
    )
    assert mutation.outcome == "created"
    page = content.read_page(
        project_id=project.project_id, thread_id="th1", actor_user_id=member, limit=10
    )
    assert page is not None and page.status == "ready" and page.rows == []


def test_migration_025_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (MIGRATIONS / "025_project_task_content_grants.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 25


def test_migration_025_pg_pair_declares_same_shape() -> None:
    """Token-level parity of the PG script (source review only — no live PG)."""
    sqlite_sql = (MIGRATIONS / "025_project_task_content_grants.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "025_project_task_content_grants.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "CREATE TABLE IF NOT EXISTS project_task_content_grants",
        "granted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "granted_at INTEGER NOT NULL CHECK (granted_at >= 0)",
        "PRIMARY KEY (project_id, thread_id, grantee_user_id)",
        "FOREIGN KEY (project_id, thread_id, grantee_user_id)",
        "REFERENCES project_task_shares(project_id, thread_id, grantee_user_id) ON DELETE CASCADE",
        "UPDATE _schema_version SET version = 25",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    # No surrogate id column, so neither dialect marker may appear at all.
    for sql in (sqlite_sql, pg_sql):
        assert "AUTOINCREMENT" not in sql
        assert "GENERATED BY DEFAULT" not in sql
        assert "CREATE INDEX" not in sql
    # The bodies are byte-identical; only the first header line names the
    # dialect.
    assert sqlite_sql.split("\n", 1)[1] == pg_sql.split("\n", 1)[1]

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 2  # table + watermark
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# No auto-upgrade of existing 024 card grants
# ---------------------------------------------------------------------------


def test_existing_card_grants_never_gain_text(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)

    assert _content_rows(db) == []
    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None and [row.can_read_text for row in listed] == [False]
    summary = shares.get_shared_summary(pid, "th1", user_id=member_id)
    assert summary is not None and summary.can_read_text is False
    shared = shares.list_shared(pid, user_id=member_id)
    assert [row.can_read_text for row in shared] == [False]
    merged = shares.list_all(pid, user_id=member_id)
    assert [(row.access, row.can_read_text) for row in merged] == [("reader", False)]
    # The card holder still cannot read messages.
    assert (
        content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
        is None
    )
    # Owner views always carry can_read_text=True without any 025 row.
    own = tasks.list_for_owner(pid, user_id=owner_id)
    assert [row.can_read_text for row in own] == [True]
    owner_all = shares.list_all(pid, user_id=owner_id)
    assert [(row.access, row.can_read_text) for row in owner_all] == [("owner", True)]


# ---------------------------------------------------------------------------
# Text grant / revoke / regrant row semantics
# ---------------------------------------------------------------------------


def test_text_grant_created_duplicate_revoke_regrant(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)

    first = content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert first.outcome == "created"
    assert first.grant is not None
    assert first.grant.user_id == member_id
    assert first.grant.granted_at == pytest.approx(now_ts(), abs=5)
    rows = _content_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["granted_by_user_id"] == owner_id

    # Active duplicate: no row update at all (granted_at untouched).
    granted_at = rows[0]["granted_at"]
    second = content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert second.outcome == "duplicate"
    assert second.grant is not None and second.grant.granted_at == granted_at
    assert len(_content_rows(db, pid)) == 1

    # Revoke deletes the row; repeat revoke is a no-op.
    assert (
        content.revoke(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        )
        == "revoked"
    )
    assert _content_rows(db) == []
    assert (
        content.revoke(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        )
        == "noop"
    )

    # Regrant after revoke is a brand-new row (201 semantics).
    third = content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert third.outcome == "created"
    assert len(_content_rows(db, pid)) == 1
    # The card grant itself is untouched by any of this.
    card = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert card is not None and len(card) == 1 and card[0].can_read_text is True
    _assert_no_task_or_share_events(db, pid, ("th1",))


def test_text_grant_guards_are_uniform_and_write_nothing(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
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
        return content.grant(**base).outcome

    # Without ANY card share, even a perfectly valid target is refused:
    # text access requires an active 024 card first (uniform 404 outcome).
    assert _grant() == "invalid_recipient"
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
    # A member without a card on THIS task is an invalid recipient too.
    assert _grant(grantee_user_id=other_member_id) == "invalid_recipient"

    # Now grant the card; the previously valid target succeeds, and a
    # revoked card refuses again.
    _grant_card(shares, pid, "th1", owner_id, member_id)
    assert _grant() == "created"
    assert (
        content.revoke(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        )
        == "revoked"
    )
    shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert _grant() == "invalid_recipient"
    assert len(_content_rows(db)) == 1 or _content_rows(db) == []
    assert _content_rows(db) == []
    _assert_no_task_or_share_events(db, pid, ("th1", "th_m"))


def test_text_grant_revalidates_thread_binding_inside_transaction(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    # Post-grant mutation of the thread row must be caught by the write
    # transaction: text stays grantable only while the thread is the
    # owner's live Dashboard :dm conversation.
    with db.transaction() as conn:
        conn.execute("UPDATE threads SET channel_type = 'feishu' WHERE thread_id = 'th1'")
    outcome = content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    ).outcome
    assert outcome == "invalid_task"
    assert _content_rows(db) == []


def test_text_grant_denied_on_archived_project_but_revoke_still_works(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "created"
    )
    _set_archived(db, pid)
    # New grants and duplicate grants are both refused while archived …
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "archived"
    )
    # … reads stay allowed and honest …
    page = content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
    assert page is not None
    # … and revoking (tightening) is allowed on archived projects.
    assert (
        content.revoke(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        )
        == "revoked"
    )
    assert (
        content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
        is None
    )
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "archived"
    )


def test_text_revoke_is_task_owner_only(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
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
    _grant_card(shares, pid, "th1", owner_id, member_id)
    content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    projects.set_member_role(
        project_id=pid, user_id=other_member_id, role="admin", actor_user_id=owner_id
    )
    # Member, project admin, the grantee: uniform missing_task; outsider:
    # not_member. The text grant survives every foreign attempt.
    for actor in (member_id, other_member_id):
        outcome = content.revoke(
            project_id=pid, thread_id="th1", actor_user_id=actor, grantee_user_id=member_id
        )
        assert outcome == "missing_task"
    outcome = content.revoke(
        project_id=pid, thread_id="th1", actor_user_id=outsider_id, grantee_user_id=member_id
    )
    assert outcome == "not_member"
    assert len(_content_rows(db, pid)) == 1


# ---------------------------------------------------------------------------
# Card revoke / regrant interaction
# ---------------------------------------------------------------------------


def test_card_revoke_deletes_text_grant_and_regrant_never_revives_it(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _seed_rows(db, "th1", ((1, *_human("hello")),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "created"
    )
    assert (
        content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
        is not None
    )

    # Card revoke stamps revoked_at AND deletes the text row in ONE
    # transaction — no stale text grant may survive the card.
    assert (
        shares.revoke(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        )
        == "revoked"
    )
    assert _content_rows(db) == []
    with db.connect() as conn:
        row = conn.execute(
            "SELECT revoked_at FROM project_task_shares "
            "WHERE project_id = ? AND thread_id = 'th1' AND grantee_user_id = ?",
            (pid, member_id),
        ).fetchone()
    assert row is not None and row["revoked_at"] is not None

    # Card regrant alone reactivates the card with text CLOSED.
    assert (
        shares.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "regranted"
    )
    assert _content_rows(db) == []
    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None and listed[0].can_read_text is False
    assert (
        content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
        is None
    )
    # Text must be explicitly granted again — and that is a fresh 201.
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "created"
    )
    page = content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
    assert page is not None and [row.seq for row in page.rows] == [1]


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------


def test_member_removal_cascades_text_grants(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert len(_content_rows(db, pid)) == 1

    mutation = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert mutation.outcome == "removed"
    # Membership → card → text all cascade away; the owner's task survives.
    assert _content_rows(db) == []
    with db.connect() as conn:
        card = conn.execute("SELECT COUNT(*) FROM project_task_shares").fetchone()[0]
    assert card == 0
    assert tasks.get_for_owner(pid, "th1", user_id=owner_id) is not None

    # Re-added membership + fresh card grant + text grant: all new rows.
    projects.add_member(pid, member_id, role="member")
    _grant_card(shares, pid, "th1", owner_id, member_id)
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "created"
    )


def test_detach_thread_delete_and_project_delete_cascade_text_grants(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T1", None), ("th2", "T2", None)))
    for tid in ("th1", "th2"):
        _grant_card(shares, pid, tid, owner_id, member_id)
        outcome = content.grant(
            project_id=pid, thread_id=tid, actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        assert outcome == "created"

    # Detach removes the link → card → text; the thread survives.
    assert tasks.detach(project_id=pid, thread_id="th1", user_id=owner_id) == "removed"
    assert [r["thread_id"] for r in _content_rows(db, pid)] == ["th2"]
    assert threads.get("th1") is not None

    # Deleting the thread cascades link → card → text.
    threads.delete("th2")
    assert _content_rows(db) == []

    # Deleting the project cascades everything.
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th3", "T3", None),))
    _grant_card(shares, pid, "th3", owner_id, member_id)
    content.grant(
        project_id=pid, thread_id="th3", actor_user_id=owner_id, grantee_user_id=member_id
    )
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _content_rows(db) == []
    assert threads.get("th3") is not None


# ---------------------------------------------------------------------------
# can_read_text reflections
# ---------------------------------------------------------------------------


def test_can_read_text_flags_follow_the_text_grant(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    _grant_card(shares, pid, "th1", owner_id, other_member_id)
    assert (
        content.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "created"
    )

    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None
    assert {row.user_id: row.can_read_text for row in listed} == {
        member_id: True,
        other_member_id: False,
    }
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is not None
    summary = shares.get_shared_summary(pid, "th1", user_id=member_id)
    assert summary is not None and summary.can_read_text is True
    other = shares.get_shared_summary(pid, "th1", user_id=other_member_id)
    assert other is not None and other.can_read_text is False
    merged = shares.list_all(pid, user_id=owner_id)
    assert [(row.access, row.can_read_text) for row in merged] == [("owner", True)]

    # Text-only revoke flips every reader reflection back to False and
    # leaves the card itself active.
    assert (
        content.revoke(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        )
        == "revoked"
    )
    listed = shares.list_shares(pid, "th1", actor_user_id=owner_id)
    assert listed is not None
    assert [row.can_read_text for row in listed] == [False, False]
    summary = shares.get_shared_summary(pid, "th1", user_id=member_id)
    assert summary is not None and summary.can_read_text is False
    assert len(shares.list_shared(pid, user_id=member_id)) == 1


# ---------------------------------------------------------------------------
# read_page authorization and projection status
# ---------------------------------------------------------------------------


def test_read_page_authorization_matrix(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
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
    _seed_tasks(tasks, db, threads, pid, member_id, (("th_m", "M", None),))
    _seed_rows(db, "th1", ((1, *_human("hello")),))
    other_pid = projects.create_with_owner(creator_user_id=owner_id, name="别的项目").project_id

    def _read(thread_id: str = "th1", actor: int = member_id, project: str | None = None) -> Any:
        return content.read_page(
            project_id=project or pid, thread_id=thread_id, actor_user_id=actor, limit=10
        )

    # Owner reads their own task without any card or text grant.
    page = _read(actor=owner_id)
    assert page is not None and page.status == "ready"
    assert [row.seq for row in page.rows] == [1]
    # Member reads their OWN task too (owner branch).
    assert _read(thread_id="th_m", actor=member_id) is not None

    # Card only, bystander, outsider, unknown thread, cross-project: None.
    _grant_card(shares, pid, "th1", owner_id, member_id)
    assert _read() is None
    assert _read(actor=other_member_id) is None
    assert _read(actor=outsider_id) is None
    assert _read(actor=owner_id, thread_id="ghost") is None
    projects.add_member(other_pid, member_id, role="member")
    assert _read(actor=member_id, project=other_pid) is None
    assert _read(actor=outsider_id, project=other_pid) is None

    # Card + text: the reader is in.
    content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    page = _read()
    assert page is not None and page.status == "ready"

    # Text-only revoke closes the reader immediately; the card stays.
    content.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert _read() is None
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is not None

    # Card revoke closes everything.
    content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert _read() is not None
    shares.revoke(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )
    assert _read() is None


def test_read_page_projection_status(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    messages: ThreadMessageRepo,
    service: ProjectTaskService,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _seed_rows(db, "th1", ((1, *_human("hello")),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    content.grant(
        project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
    )

    # New threads project as ready; the raw rows come back.
    page = content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
    assert page is not None and page.status == "ready"
    assert len(page.rows) == 1

    # A non-ready projection hides every row (repo reports the raw status …
    messages.mark_projection("th1", "pending")
    page = content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
    assert page is not None and page.status == "pending" and page.rows == []
    # … and the service normalizes ALL non-ready states to pending).
    view = service.read_task_messages(pid, "th1", user_id=member_id)
    assert view.status == "pending" and view.items == []
    assert view.has_more is False and view.next_before_seq is None
    messages.mark_projection("th1", "failed")
    view = service.read_task_messages(pid, "th1", user_id=member_id)
    assert view.status == "pending" and view.items == []
    # Pending never triggers a backfill: the raw rows are untouched.
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM thread_messages WHERE thread_id = 'th1'"
        ).fetchone()[0]
    assert count == 1

    messages.mark_projection("th1", "ready")
    view = service.read_task_messages(pid, "th1", user_id=member_id)
    assert view.status == "ready"
    assert [(item.seq, item.role, item.text) for item in view.items] == [(1, "user", "hello")]


# ---------------------------------------------------------------------------
# Service policy: uniform errors, archive mode
# ---------------------------------------------------------------------------


def test_service_text_grant_revoke_flow(
    service: ProjectTaskService,
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
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))

    # Text before card: the same uniform task 404 as any invalid target.
    with pytest.raises(OctopError) as excinfo:
        service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert excinfo.value.status == 404
    assert _content_rows(db) == []

    service.grant_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    view, created = service.grant_text_share(
        pid, "th1", user_id=owner_id, grantee_user_id=member_id
    )
    assert created is True
    assert view.user_id == member_id and view.granted_at > 0
    view2, created = service.grant_text_share(
        pid, "th1", user_id=owner_id, grantee_user_id=member_id
    )
    assert created is False and view2.granted_at == view.granted_at

    # Self, non-member, card-less member: uniform task 404s, nothing written.
    with pytest.raises(OctopError) as excinfo:
        service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=owner_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=outsider_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=other_member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert len(_content_rows(db)) == 1

    # Non-owners never manage text grants; outsiders get the project 404.
    for actor in (member_id, other_member_id):
        with pytest.raises(OctopError) as excinfo:
            service.grant_text_share(pid, "th1", user_id=actor, grantee_user_id=member_id)
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        with pytest.raises(OctopError) as excinfo:
            service.revoke_text_share(pid, "th1", user_id=actor, grantee_user_id=member_id)
        assert excinfo.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        service.grant_text_share(pid, "th1", user_id=outsider_id, grantee_user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert excinfo.value.message == "project not found"
    with pytest.raises(OctopError) as excinfo:
        service.revoke_text_share(pid, "th1", user_id=outsider_id, grantee_user_id=member_id)
    assert excinfo.value.message == "project not found"
    assert len(_content_rows(db)) == 1

    service.revoke_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    service.revoke_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)  # 204
    assert _content_rows(db) == []
    # The card itself survives text revocation.
    assert shares.get_shared_summary(pid, "th1", user_id=member_id) is not None
    _assert_no_task_or_share_events(db, pid, ("th1",))


def test_service_read_messages_uniform_404s(
    service: ProjectTaskService,
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
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "SECRET-标题", None),))
    _seed_rows(db, "th1", ((1, *_human("hello")),))
    projects.set_member_role(
        project_id=pid, user_id=other_member_id, role="admin", actor_user_id=owner_id
    )
    _grant_card(shares, pid, "th1", owner_id, member_id)

    # Card-only reader, bystander, project admin, outsider, unknown ids:
    # all the same task 404 — no projection status or count leaks.
    for actor in (member_id, other_member_id, outsider_id):
        with pytest.raises(OctopError) as excinfo:
            service.read_task_messages(pid, "th1", user_id=actor)
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        assert excinfo.value.status == 404
        assert "SECRET" not in excinfo.value.message
    with pytest.raises(OctopError) as excinfo:
        service.read_task_messages(pid, "ghost", user_id=owner_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        service.read_task_messages("ghost", "th1", user_id=owner_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert excinfo.value.message == "project not found"

    # Owner and (after the explicit text grant) the reader succeed.
    assert service.read_task_messages(pid, "th1", user_id=owner_id).status == "ready"
    service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    view = service.read_task_messages(pid, "th1", user_id=member_id)
    assert view.status == "ready" and len(view.items) == 1
    # The project admin is still locked out.
    with pytest.raises(OctopError):
        service.read_task_messages(pid, "th1", user_id=other_member_id)


def test_service_history_archive_mode_always_pending(
    archive_service: ProjectTaskService,
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _seed_rows(db, "th1", ((1, *_human("hello")), (2, *_ai("world"))))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    archive_service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)

    # Ready projection, real rows — versioned-history mode still answers
    # pending with no items, for the owner and the authorized reader alike.
    for actor in (owner_id, member_id):
        view = archive_service.read_task_messages(pid, "th1", user_id=actor)
        assert view.status == "pending"
        assert view.items == []
        assert view.has_more is False
        assert view.next_before_seq is None
    # Authorization still comes first: outsiders get 404, not pending.
    with pytest.raises(OctopError) as excinfo:
        archive_service.read_task_messages(pid, "th1", user_id=outsider_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Paging: raw-seq cursor, has_more over raw rows, skipped rows consume cursor
# ---------------------------------------------------------------------------


def test_service_pagination_uses_raw_seq_cursor(
    service: ProjectTaskService,
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    _seed_rows(
        db,
        "th1",
        (
            (1, *_human("m1")),
            (2, *_ai("m2")),
            (3, *_human("m3")),
            (4, *_ai("m4")),
            (5, *_human("m5")),
        ),
    )

    def _read(limit: int, before_seq: int | None = None) -> Any:
        return service.read_task_messages(
            pid, "th1", user_id=member_id, limit=limit, before_seq=before_seq
        )

    page = _read(2)
    assert page.status == "ready"
    assert [(item.seq, item.text) for item in page.items] == [(5, "m5"), (4, "m4")]
    assert page.has_more is True
    assert page.next_before_seq == 4
    page = _read(2, before_seq=page.next_before_seq)
    assert [(item.seq, item.text) for item in page.items] == [(3, "m3"), (2, "m2")]
    assert page.has_more is True and page.next_before_seq == 2
    page = _read(2, before_seq=2)
    assert [(item.seq, item.text) for item in page.items] == [(1, "m1")]
    assert page.has_more is False and page.next_before_seq is None
    # A full-size page ends with has_more False and no cursor.
    page = _read(5)
    assert [item.seq for item in page.items] == [5, 4, 3, 2, 1]
    assert page.has_more is False and page.next_before_seq is None
    # before_seq beyond the newest row behaves like no cursor at all.
    page = _read(2, before_seq=999)
    assert [item.seq for item in page.items] == [5, 4]


def test_service_pagination_skipped_rows_consume_cursor(
    service: ProjectTaskService,
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)
    tool_row = ("tool", _wire(ToolMessage(content="TOOL-SECRET", tool_call_id="c1")))
    _seed_rows(
        db,
        "th1",
        (
            (1, *_human("m1")),
            (2, *tool_row),
            (3, *tool_row),
            (4, *_human("m4")),
        ),
    )

    # has_more counts RAW rows: limit=2 reads [4,3,2] → page [4,3], and the
    # skipped tool rows still consume their cursor positions.
    page = service.read_task_messages(pid, "th1", user_id=member_id, limit=2)
    assert [(item.seq, item.text) for item in page.items] == [(4, "m4")]
    assert page.has_more is True
    assert page.next_before_seq == 3
    page = service.read_task_messages(
        pid, "th1", user_id=member_id, limit=2, before_seq=page.next_before_seq
    )
    assert [(item.seq, item.text) for item in page.items] == [(1, "m1")]
    assert page.has_more is False and page.next_before_seq is None
    assert all("TOOL-SECRET" not in item.text for item in page.items)


# ---------------------------------------------------------------------------
# Sanitization: real message_to_dict shapes
# ---------------------------------------------------------------------------


def test_sanitize_projects_plain_text_and_skips_dangerous_rows(
    service: ProjectTaskService,
    shares: ProjectTaskShareRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _grant_card(shares, pid, "th1", owner_id, member_id)
    service.grant_text_share(pid, "th1", user_id=owner_id, grantee_user_id=member_id)

    tool_row = ("tool", _wire(ToolMessage(content="TOOL-SECRET", tool_call_id="c1")))
    system_row = ("system", _wire(SystemMessage(content="SYS-SECRET")))
    calling_ai = (
        "ai",
        _wire(
            AIMessage(
                content="CALLING-SECRET",
                tool_calls=[{"name": "search", "args": {"q": "ARGS-SECRET"}, "id": "c1"}],
            )
        ),
    )
    stream_error = (
        "ai",
        _wire(
            AIMessage(
                content="ERR-STACK-SECRET",
                additional_kwargs={"octop_stream_error": True, "error_code": "E500"},
            )
        ),
    )
    blocks = (
        "human",
        json.dumps(
            {
                "type": "human",
                "data": {
                    "content": [
                        {"type": "text", "text": "A"},
                        {"type": "image_url", "image_url": {"url": "https://IMG-SECRET/i.png"}},
                        "B",
                        {"type": "text"},
                        42,
                    ]
                },
            }
        ),
    )
    empty_list = ("ai", _wire(AIMessage(content=[])))
    dict_content = ("human", json.dumps({"type": "human", "data": {"content": {"a": 1}}}))
    invalid_calls = (
        "ai",
        json.dumps(
            {
                "type": "ai",
                "data": {
                    "content": "INVALID-SECRET",
                    "tool_calls": [],
                    "invalid_tool_calls": [{"oops": 1}],
                },
            }
        ),
    )
    reasoning = (
        "ai",
        _wire(
            AIMessage(
                content="visible-answer",
                additional_kwargs={"reasoning_content": "THINKING-SECRET"},
            )
        ),
    )
    _seed_rows(
        db,
        "th1",
        (
            (1, *_human("你好")),
            (2, *_ai("hi there")),
            (3, *tool_row),
            (4, *system_row),
            (5, *calling_ai),
            (6, *invalid_calls),
            (7, *stream_error),
            (8, *blocks),
            (9, *empty_list),
            (10, *dict_content),
            (11, *reasoning),
        ),
    )

    view = service.read_task_messages(pid, "th1", user_id=member_id, limit=50)
    assert view.status == "ready"
    # Descending seq; only the whitelisted rows survive.
    assert [(item.seq, item.role, item.text) for item in view.items] == [
        (11, "assistant", "visible-answer"),
        (8, "user", "AB"),
        (2, "assistant", "hi there"),
        (1, "user", "你好"),
    ]
    assert all(item.truncated is False for item in view.items)
    assert view.has_more is False and view.next_before_seq is None
    blob = json.dumps(
        [[item.seq, item.role, item.text, item.created_at, item.truncated] for item in view.items],
        ensure_ascii=False,
    )
    for secret in (
        "TOOL-SECRET",
        "SYS-SECRET",
        "CALLING-SECRET",
        "ARGS-SECRET",
        "INVALID-SECRET",
        "ERR-STACK-SECRET",
        "IMG-SECRET",
        "THINKING-SECRET",
    ):
        assert secret not in blob


def test_sanitize_requires_matching_stored_role_and_wire_type(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    _seed_rows(
        db,
        "th1",
        (
            # Stored role and wire type must be the SAME human/user or
            # ai/assistant family — every mismatch is skipped.
            (1, "human", _wire(AIMessage(content="MISMATCH-A"))),
            (2, "ai", _wire(HumanMessage(content="MISMATCH-B"))),
            (3, "human", _wire(HumanMessage(content="ok-user"))),
            (4, "ai", _wire(AIMessage(content="ok-assistant"))),
            (5, "tool", _wire(ToolMessage(content="MISMATCH-C", tool_call_id="c"))),
            # Malformed / non-dict / missing-data rows are skipped.
            (6, "human", "{not json"),
            (7, "human", "[1, 2]"),
            (8, "human", json.dumps({"type": "human"})),
            (9, "human", json.dumps({"type": "human", "data": "nope"})),
            (10, "human", json.dumps({"type": "human", "data": {"content": ""}})),
            # Explicit user/assistant wire spellings are accepted.
            (
                11,
                "user",
                json.dumps({"type": "user", "data": {"content": "alt-user", "tool_calls": []}}),
            ),
            (
                12,
                "assistant",
                json.dumps({"type": "assistant", "data": {"content": "alt-ai"}}),
            ),
        ),
    )
    view = service.read_task_messages(pid, "th1", user_id=owner_id, limit=50)
    assert [(item.seq, item.role, item.text) for item in view.items] == [
        (12, "assistant", "alt-ai"),
        (11, "user", "alt-user"),
        (4, "assistant", "ok-assistant"),
        (3, "user", "ok-user"),
    ]


def test_sanitize_keeps_rows_with_empty_tool_call_lists(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
) -> None:
    """message_to_dict(AIMessage(content='hi')) carries tool_calls=[] and
    invalid_tool_calls=[] — presence of the KEYS must not skip the row;
    only NON-EMPTY lists do."""
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    wire = json.dumps(message_to_dict(AIMessage(content="hi")), ensure_ascii=False)
    assert '"tool_calls": []' in wire
    _seed_rows(db, "th1", ((1, "ai", wire),))
    view = service.read_task_messages(pid, "th1", user_id=owner_id, limit=50)
    assert [(item.seq, item.role, item.text) for item in view.items] == [(1, "assistant", "hi")]


def test_sanitize_truncates_at_32768_utf8_bytes(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    ascii_long = "a" * 40_000
    cjk_long = "汉" * 20_000  # 60000 UTF-8 bytes
    _seed_rows(
        db,
        "th1",
        (
            (1, *_human(ascii_long)),
            (2, *_human(cjk_long)),
            (3, *_human("short")),
        ),
    )
    view = service.read_task_messages(pid, "th1", user_id=owner_id, limit=50)
    by_seq = {item.seq: item for item in view.items}
    assert by_seq[3].truncated is False and by_seq[3].text == "short"

    ascii_item = by_seq[1]
    assert ascii_item.truncated is True
    assert ascii_item.text == "a" * 32768
    assert len(ascii_item.text.encode("utf-8")) == 32768

    cjk_item = by_seq[2]
    assert cjk_item.truncated is True
    encoded = cjk_item.text.encode("utf-8")
    # Truncation lands on a character boundary: 10922 full 3-byte chars.
    assert len(encoded) <= 32768
    assert len(cjk_item.text) == 10922
    assert cjk_item.text == "汉" * 10922


def test_sanitize_skips_rows_over_256kib_raw_json(
    service: ProjectTaskService,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
) -> None:
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "T", None),))
    huge_role, huge_json = _human("x" * 300_000)  # > 256 KiB raw
    big_role, big_json = _human("y" * 260_000)  # < 256 KiB raw, > 32768 text
    assert len(huge_json.encode("utf-8")) > 256 * 1024
    assert len(big_json.encode("utf-8")) <= 256 * 1024
    _seed_rows(
        db,
        "th1",
        (
            (1, huge_role, huge_json),
            (2, big_role, big_json),
            (3, *_human("kept")),
        ),
    )
    view = service.read_task_messages(pid, "th1", user_id=owner_id, limit=50)
    assert [(item.seq, item.truncated) for item in view.items] == [(3, False), (2, True)]
    assert len(view.items[1].text.encode("utf-8")) == 32768


# ---------------------------------------------------------------------------
# PostgreSQL lock shapes (fake connection — no live PG)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.rowcount = 1

    def fetchone(self) -> Any:
        return self._result

    def fetchall(self) -> Any:
        return self._result if isinstance(self._result, list) else []


class _ScriptedConn:
    def __init__(self, script: list[Any]) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._script = list(script)

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _FakeCursor:
        self.calls.append((sql, params))
        result = self._script.pop(0) if self._script else None
        return _FakeCursor(result)


def _use_fake_tx(
    content: ProjectTaskContentRepo, conn: _ScriptedConn, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextlib.contextmanager
    def _fake_transaction() -> Iterator[Any]:
        yield conn

    monkeypatch.setattr(content._db, "transaction", _fake_transaction)


def test_postgres_text_grant_locks_members_then_card_then_insert(
    content: ProjectTaskContentRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG text grants lock both membership rows FOR SHARE in ascending
    user-id order, then the card share row, then write — the same order as
    the 024 write path, deadlock-free against card revoke."""
    conn = _ScriptedConn(
        [
            {"role": "member"},  # member row (lower uid first)
            {"role": "member"},  # member row (higher uid)
            {"archived": 0},
            {1: 1},  # own-task check
            {"revoked_at": None},  # ACTIVE card share row
            {"grantee_user_id": 9, "granted_at": 5},  # INSERT … RETURNING
        ]
    )
    _use_fake_tx(content, conn, monkeypatch)
    monkeypatch.setattr(content._db, "dialect", "postgresql")
    mutation = content.grant(project_id="p1", thread_id="t1", actor_user_id=9, grantee_user_id=3)
    assert mutation.outcome == "created"

    member_calls = [c for c in conn.calls if "project_members" in c[0]]
    assert [params[1] for _, params in member_calls] == [3, 9]
    assert all(sql.endswith(" FOR SHARE") for sql, _ in member_calls)
    share_at = next(i for i, (sql, _) in enumerate(conn.calls) if "project_task_shares" in sql)
    assert conn.calls[share_at][0].endswith(" FOR SHARE")
    insert_at = next(
        i
        for i, (sql, _) in enumerate(conn.calls)
        if sql.startswith("INSERT INTO project_task_content_grants")
    )
    assert member_calls and share_at < insert_at
    assert conn.calls[0][0].startswith("SELECT role FROM project_members")

    # SQLite issues no FOR SHARE suffixes at all.
    conn2 = _ScriptedConn(
        [
            {"role": "member"},
            {"role": "member"},
            {"archived": 0},
            {1: 1},
            {"revoked_at": None},
            {"grantee_user_id": 9, "granted_at": 5},
        ]
    )
    _use_fake_tx(content, conn2, monkeypatch)
    monkeypatch.setattr(content._db, "dialect", "sqlite")
    mutation = content.grant(project_id="p1", thread_id="t1", actor_user_id=9, grantee_user_id=3)
    assert mutation.outcome == "created"
    assert not any(sql.endswith("FOR SHARE") for sql, _ in conn2.calls)


def test_postgres_read_page_locks_member_card_content_in_order(
    content: ProjectTaskContentRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG reads lock member → card share → text grant rows FOR SHARE before
    touching projection/message rows, matching the write-path order."""
    conn = _ScriptedConn(
        [
            {"role": "member"},  # actor membership
            None,  # own-task check: not the owner
            {1: 1},  # active card share
            {1: 1},  # text grant row
            {"status": "ready"},  # projection
            [],  # message rows
        ]
    )
    _use_fake_tx(content, conn, monkeypatch)
    monkeypatch.setattr(content._db, "dialect", "postgresql")
    page = content.read_page(project_id="p1", thread_id="t1", actor_user_id=3, limit=10)
    assert page is not None and page.status == "ready" and page.rows == []

    sqls = [sql for sql, _ in conn.calls]
    member_at = next(i for i, sql in enumerate(sqls) if "project_members" in sql)
    card_at = next(i for i, sql in enumerate(sqls) if "project_task_shares" in sql)
    text_at = next(i for i, sql in enumerate(sqls) if "project_task_content_grants" in sql)
    messages_at = next(i for i, sql in enumerate(sqls) if "thread_messages" in sql)
    assert member_at < card_at < text_at < messages_at
    assert sqls[member_at].endswith(" FOR SHARE")
    assert sqls[card_at].endswith(" FOR SHARE")
    assert sqls[text_at].endswith(" FOR SHARE")
    assert not sqls[messages_at].endswith("FOR SHARE")


# ---------------------------------------------------------------------------
# Two-connection SQLite race: text grant vs card revoke
# ---------------------------------------------------------------------------


def test_two_connection_interleaved_text_grant_and_card_revoke(
    shares: ProjectTaskShareRepo,
    content: ProjectTaskContentRepo,
    tasks: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    service: ProjectTaskService,
    pid: str,
    owner_id: int,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite interleaved writers: text grant racing card revocation.

    The main thread holds the first pool's BEGIN IMMEDIATE transaction and
    runs the production card ``revoke`` inside it; a worker on a second pool
    attempts the text grant. The second writer can only proceed after the
    card revoke commits, so it observes the revoked card, writes nothing,
    and answers ``invalid_recipient`` (uniform 404) — never a stale text
    grant that a later card regrant would revive.
    """
    _seed_tasks(tasks, db, threads, pid, owner_id, (("th1", "私密卡片", 7),))
    _grant_card(shares, pid, "th1", owner_id, member_id)

    second = SqlitePool(db.path)
    began, entered, done = threading.Event(), threading.Event(), threading.Event()
    second._conn = _WriterProbe(second._conn, began=began, entered=entered)
    second_content = ProjectTaskContentRepo(second)

    def _worker() -> Any:
        try:
            return second_content.grant(
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
            outcome = shares.revoke(
                project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
            )
            assert outcome == "revoked"
            future = executor.submit(_worker)
            assert began.wait(timeout=10), "grant worker never attempted BEGIN IMMEDIATE"
            assert not entered.is_set()
            assert not done.wait(timeout=0.5)
        mutation = future.result(timeout=10)
    finally:
        executor.shutdown(wait=True)
        second.close()

    # The text grant serialized behind the card revoke and lost: uniform
    # invalid recipient, no row written, reader still locked out.
    assert entered.is_set()
    assert mutation.outcome == "invalid_recipient"
    assert mutation.grant is None
    assert _content_rows(db) == []
    assert (
        content.read_page(project_id=pid, thread_id="th1", actor_user_id=member_id, limit=10)
        is None
    )
    with pytest.raises(OctopError) as excinfo:
        service.read_task_messages(pid, "th1", user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND

    # Card regrant does NOT revive text; the retry path is a fresh 201.
    assert (
        shares.grant(
            project_id=pid, thread_id="th1", actor_user_id=owner_id, grantee_user_id=member_id
        ).outcome
        == "regranted"
    )
    assert _content_rows(db) == []
    view, created = service.grant_text_share(
        pid, "th1", user_id=owner_id, grantee_user_id=member_id
    )
    assert created is True and view.user_id == member_id
    assert service.read_task_messages(pid, "th1", user_id=member_id).status == "ready"
