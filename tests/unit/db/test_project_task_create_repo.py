"""Unit tests for the 026 atomic project-task create (migration + repo + service).

PS-05C/PS-07A slice 1: a project member creates a brand-new private Dashboard
DM thread that is linked to the project (``source='project'``) together with an
immutable snapshot of the project instructions, all inside ONE write
transaction. Covers the paired 026 DDL (cascade from the task link, v25
upgrade without backfill), the validate-inside-transaction digest check with
zero side effects on conflict, non-member / archived / team-agent refusals,
injected-failure rollback of all four row kinds, no session reset/rebind, and
the detach / member-removal / project-delete cascades that keep the owner's
original thread.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_task_shares import ProjectTaskShareRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.tasks import ProjectTaskService, instructions_sha256

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"

INSTRUCTIONS = "项目指令：先读 SOUL.md，再输出周报。"
NEW_INSTRUCTIONS = "项目指令（改版）：先读 README，再输出月报。"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> ProjectTaskRepo:
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
def pid(projects: ProjectRepo, owner_id: int, member_id: int) -> str:
    project = projects.create_with_owner(creator_user_id=owner_id, name="任务项目")
    projects.add_member(project.project_id, member_id, role="member")
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
    projects: ProjectRepo, repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo
) -> ProjectTaskService:
    return ProjectTaskService(_StubServices(projects, repo, ProjectTaskShareRepo(db), threads))


def _seed_agent(db: SqlitePool, *, agent_id: str, user_id: int, kind: str = "expert") -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, kind, created_at, updated_at) "
            "VALUES (?, ?, 'bot', ?, 0, 0)",
            (agent_id, user_id, kind),
        )


def _dashboard_key(agent_id: str, user_id: int) -> str:
    return f"{agent_id}:dashboard:{user_id}:dm"


def _set_instructions(projects: ProjectRepo, pid: str, owner_id: int, text: str) -> None:
    assert projects.update_project(pid, actor_user_id=owner_id, instructions=text) is not None


def _row_count(db: SqlitePool, table: str, where: str, params: tuple[object, ...]) -> int:
    with db.connect() as conn:
        return int(
            conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]
        )


def _assert_no_create_rows(db: SqlitePool, pid: str, user_id: int) -> None:
    assert _row_count(db, "threads", "user_id = ?", (user_id,)) == 0
    assert _row_count(db, "project_task_links", "owner_user_id = ?", (user_id,)) == 0
    assert _row_count(db, "project_task_contexts", "owner_user_id = ?", (user_id,)) == 0


# ---------------------------------------------------------------------------
# Happy path: one transaction, four rows, no session rebind
# ---------------------------------------------------------------------------


def test_member_create_writes_thread_link_snapshot_and_projection(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    expected = instructions_sha256(INSTRUCTIONS)

    view = service.create_project_task(
        pid,
        user_id=member_id,
        agent_id="ag_member",
        expected_instructions_sha256=expected,
    )

    assert view.project_id == pid
    assert view.owner_user_id == member_id
    assert view.agent_id == "ag_member"
    assert view.source == "project"
    assert view.access == "owner"
    assert view.title is None
    assert view.last_active == 0
    assert view.created_at > 0

    thread = threads.get(view.thread_id)
    assert thread is not None
    assert thread.channel_type == "dashboard"
    assert thread.session_key == _dashboard_key("ag_member", member_id)
    assert thread.user_id == member_id

    with db.connect() as conn:
        link = conn.execute(
            "SELECT project_id, thread_id, owner_user_id, source FROM project_task_links "
            "WHERE thread_id = ?",
            (view.thread_id,),
        ).fetchone()
        ctx = conn.execute(
            "SELECT * FROM project_task_contexts WHERE thread_id = ?", (view.thread_id,)
        ).fetchone()
        projection = conn.execute(
            "SELECT status FROM thread_history_projection WHERE thread_id = ?",
            (view.thread_id,),
        ).fetchone()
        session = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE session_key = ?",
            (_dashboard_key("ag_member", member_id),),
        ).fetchone()
        events = conn.execute(
            "SELECT payload_json FROM project_events WHERE project_id = ?", (pid,)
        ).fetchall()
    assert link is not None
    assert link["project_id"] == pid
    assert link["owner_user_id"] == member_id
    assert link["source"] == "project"
    assert ctx is not None
    assert ctx["instructions_snapshot"] == INSTRUCTIONS
    assert ctx["snapshot_version"] == 1
    assert ctx["instructions_sha256"] == expected
    assert ctx["captured_at"] > 0
    assert ctx["owner_user_id"] == member_id
    assert projection is not None and projection["status"] == "ready"
    # Creating a task must not reset/rebind the active dashboard session.
    assert session["n"] == 0
    # No shared event may carry the private thread id.
    for event in events:
        assert view.thread_id not in str(event["payload_json"])


# ---------------------------------------------------------------------------
# Digest conflict / refusals: zero side effects
# ---------------------------------------------------------------------------


def test_stale_digest_conflicts_without_any_new_rows(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)

    with pytest.raises(OctopError) as excinfo:
        service.create_project_task(
            pid,
            user_id=member_id,
            agent_id="ag_member",
            expected_instructions_sha256=instructions_sha256(NEW_INSTRUCTIONS),
        )
    assert excinfo.value.code is ErrorCode.PROJECT_INSTRUCTIONS_CHANGED
    assert excinfo.value.status == 409
    assert INSTRUCTIONS not in str(excinfo.value.details)
    _assert_no_create_rows(db, pid, member_id)


def owner_of(pid: str, projects: ProjectRepo) -> int:
    row = projects.get_project(pid)
    assert row is not None
    return int(row.creator_user_id)


def test_non_member_or_unknown_project_gets_uniform_not_found(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_outsider", user_id=outsider_id)
    _set_instructions(projects, pid, owner_of(pid, projects), INSTRUCTIONS)

    for project_id in (pid, "01GHOSTPROJECT"):
        with pytest.raises(OctopError) as excinfo:
            service.create_project_task(
                project_id,
                user_id=outsider_id,
                agent_id="ag_outsider",
                expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
            )
        assert excinfo.value.code is ErrorCode.NOT_FOUND
    _assert_no_create_rows(db, pid, outsider_id)


def test_archived_project_refused_without_rows(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_of(pid, projects), INSTRUCTIONS)
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (pid,))

    with pytest.raises(OctopError) as excinfo:
        service.create_project_task(
            pid,
            user_id=member_id,
            agent_id="ag_member",
            expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        )
    assert excinfo.value.code is ErrorCode.FORBIDDEN
    _assert_no_create_rows(db, pid, member_id)


def test_team_host_is_rejected_inside_transaction(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_team", user_id=member_id, kind="team")
    _set_instructions(projects, pid, owner_of(pid, projects), INSTRUCTIONS)

    mutation = repo.create_with_context(
        project_id=pid,
        user_id=member_id,
        agent_id="ag_team",
        thread_id="thr_team",
        session_key=_dashboard_key("ag_team", member_id),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )
    assert mutation.outcome == "invalid_agent"
    _assert_no_create_rows(db, pid, member_id)


def test_revoked_shared_agent_is_rejected_inside_transaction(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_shared", user_id=owner_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    with db.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared = 1 WHERE agent_id = 'ag_shared'")
    # The route may have pre-checked while shared, then another request
    # revokes the grant before this write transaction begins.
    with db.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared = 0 WHERE agent_id = 'ag_shared'")

    mutation = repo.create_with_context(
        project_id=pid,
        user_id=member_id,
        agent_id="ag_shared",
        thread_id="thr_revoked",
        session_key=_dashboard_key("ag_shared", member_id),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )
    assert mutation.outcome == "invalid_agent"
    _assert_no_create_rows(db, pid, member_id)


def test_injected_failure_rolls_back_thread_link_and_snapshot(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_of(pid, projects), INSTRUCTIONS)

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected snapshot failure")

    monkeypatch.setattr(repo, "_insert_snapshot", _boom)
    with pytest.raises(RuntimeError, match="injected"):
        repo.create_with_context(
            project_id=pid,
            user_id=member_id,
            agent_id="ag_member",
            thread_id="thr_rollback",
            session_key=_dashboard_key("ag_member", member_id),
            expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        )
    for table in (
        "threads",
        "thread_history_projection",
        "project_task_links",
        "project_task_contexts",
    ):
        assert _row_count(db, table, "thread_id = ?", ("thr_rollback",)) == 0


# ---------------------------------------------------------------------------
# Cascades and immutability
# ---------------------------------------------------------------------------


def test_detach_cascades_snapshot_and_keeps_private_thread(
    service: ProjectTaskService,
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_of(pid, projects), INSTRUCTIONS)
    view = service.create_project_task(
        pid,
        user_id=member_id,
        agent_id="ag_member",
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )

    assert repo.detach(project_id=pid, thread_id=view.thread_id, user_id=member_id) == "removed"
    assert _row_count(db, "project_task_links", "thread_id = ?", (view.thread_id,)) == 0
    assert _row_count(db, "project_task_contexts", "thread_id = ?", (view.thread_id,)) == 0
    assert threads.get(view.thread_id) is not None


def test_member_removal_cascades_snapshot_and_keeps_private_thread(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    view = service.create_project_task(
        pid,
        user_id=member_id,
        agent_id="ag_member",
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )

    projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert _row_count(db, "project_task_links", "thread_id = ?", (view.thread_id,)) == 0
    assert _row_count(db, "project_task_contexts", "thread_id = ?", (view.thread_id,)) == 0
    assert threads.get(view.thread_id) is not None


def test_project_delete_cascades_snapshot_and_keeps_private_thread(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    view = service.create_project_task(
        pid,
        user_id=member_id,
        agent_id="ag_member",
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )

    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _row_count(db, "project_task_links", "thread_id = ?", (view.thread_id,)) == 0
    assert _row_count(db, "project_task_contexts", "thread_id = ?", (view.thread_id,)) == 0
    assert threads.get(view.thread_id) is not None


def test_project_update_never_rewrites_a_captured_snapshot(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    view = service.create_project_task(
        pid,
        user_id=member_id,
        agent_id="ag_member",
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )

    _set_instructions(projects, pid, owner_id, NEW_INSTRUCTIONS)
    with db.connect() as conn:
        ctx = conn.execute(
            "SELECT instructions_snapshot, instructions_sha256 FROM project_task_contexts "
            "WHERE thread_id = ?",
            (view.thread_id,),
        ).fetchone()
    assert ctx["instructions_snapshot"] == INSTRUCTIONS
    assert ctx["instructions_sha256"] == instructions_sha256(INSTRUCTIONS)


def test_runtime_lookup_uses_frozen_snapshot_only_while_project_access_is_active(
    service: ProjectTaskService,
    repo: ProjectTaskRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    view = service.create_project_task(
        pid,
        user_id=member_id,
        agent_id="ag_member",
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )

    def read(*, user_id: int = member_id, agent_id: str = "ag_member") -> str | None:
        return repo.active_instructions_for_thread(
            thread_id=view.thread_id, owner_user_id=user_id, agent_id=agent_id
        )

    assert read() == INSTRUCTIONS
    assert read(user_id=owner_id) is None
    assert read(agent_id="another_agent") is None
    _set_instructions(projects, pid, owner_id, NEW_INSTRUCTIONS)
    assert read() == INSTRUCTIONS

    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (pid,))
    assert read() is None
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 0 WHERE project_id = ?", (pid,))
    assert read() == INSTRUCTIONS

    projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert read() is None


def test_manual_attach_never_creates_a_snapshot(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    threads.insert(
        thread_id="thr_manual",
        agent_id="ag_member",
        user_id=member_id,
        channel_type="dashboard",
        session_key=_dashboard_key("ag_member", member_id),
        title="手动关联",
    )
    assert (
        repo.attach(project_id=pid, user_id=member_id, thread_id="thr_manual").outcome == "created"
    )
    assert _row_count(db, "project_task_contexts", "thread_id = ?", ("thr_manual",)) == 0
    with db.connect() as conn:
        link = conn.execute(
            "SELECT source FROM project_task_links WHERE thread_id = ?", ("thr_manual",)
        ).fetchone()
    assert link["source"] == "manual"
    assert (
        repo.active_instructions_for_thread(
            thread_id="thr_manual", owner_user_id=member_id, agent_id="ag_member"
        )
        is None
    )


# ---------------------------------------------------------------------------
# Migration 026
# ---------------------------------------------------------------------------


def test_migration_026_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_task_contexts)").fetchall()
        }
        pk = {
            str(r["name"])
            for r in conn.execute("PRAGMA table_info(project_task_contexts)").fetchall()
            if int(r["pk"]) > 0
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 26
    assert "project_task_contexts" in tables
    assert cols == {
        "thread_id",
        "project_id",
        "owner_user_id",
        "instructions_snapshot",
        "snapshot_version",
        "instructions_sha256",
        "captured_at",
    }
    assert pk == {"thread_id"}


def test_migration_026_cascades_from_link_project_and_user(db: SqlitePool) -> None:
    with db.connect() as conn:
        fks = conn.execute("PRAGMA foreign_key_list(project_task_contexts)").fetchall()
    pairs = {str(fk["from"]): (str(fk["table"]), str(fk["to"]), str(fk["on_delete"])) for fk in fks}
    assert pairs == {
        "thread_id": ("project_task_links", "thread_id", "CASCADE"),
        "project_id": ("project_spaces", "project_id", "CASCADE"),
        "owner_user_id": ("users", "id", "CASCADE"),
    }


def test_migration_026_rejects_orphans_and_checks_columns(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    threads: ThreadRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    for tid in ("thr_snap", "thr_snap2", "thr_snap3"):
        threads.insert(
            thread_id=tid,
            agent_id="ag_member",
            user_id=member_id,
            channel_type="dashboard",
            session_key=_dashboard_key("ag_member", member_id),
            title="快照",
        )
        assert repo.attach(project_id=pid, user_id=member_id, thread_id=tid).outcome == "created"
    insert = (
        "INSERT INTO project_task_contexts("
        "thread_id, project_id, owner_user_id, instructions_snapshot, snapshot_version, "
        "instructions_sha256, captured_at) VALUES (?, ?, ?, 'x', 1, ?, ?)"
    )
    digest = instructions_sha256("x")
    with db.transaction() as conn:
        # Unknown link (and therefore unknown thread) is refused …
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("thr_ghost", pid, member_id, digest, 5))
        # … a valid snapshot row is accepted …
        conn.execute(insert, ("thr_snap", pid, member_id, digest, 5))
        # … negative captured_at is refused …
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("thr_snap2", pid, member_id, digest, -1))
        # … and a malformed digest length is refused.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("thr_snap3", pid, member_id, "short", 5))
    with db.connect() as conn:
        # One link (thread_id) holds at most one immutable snapshot.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("thr_snap", pid, member_id, digest, 6))
        rows = conn.execute("SELECT thread_id FROM project_task_contexts").fetchall()
    assert [str(r["thread_id"]) for r in rows] == ["thr_snap"]


def test_migration_026_is_idempotent(db: SqlitePool) -> None:
    sql = (MIGRATIONS / "026_project_task_context.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 26


def test_migration_026_pg_pair_declares_same_shape() -> None:
    """Token-level parity of the PG script (source review only — no live PG)."""
    sqlite_sql = (MIGRATIONS / "026_project_task_context.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "026_project_task_context.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "CREATE TABLE IF NOT EXISTS project_task_contexts",
        "thread_id TEXT PRIMARY KEY REFERENCES project_task_links(thread_id) ON DELETE CASCADE",
        "project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE",
        "instructions_snapshot TEXT NOT NULL",
        "snapshot_version INTEGER NOT NULL DEFAULT 1",
        "instructions_sha256 TEXT NOT NULL",
        "captured_at INTEGER NOT NULL CHECK (captured_at >= 0)",
        "UPDATE _schema_version SET version = 26",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    # Bodies are byte-identical; only the first header line names the dialect.
    assert sqlite_sql.split("\n", 1)[1] == pg_sql.split("\n", 1)[1]
    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 2
    assert all(stmt.strip() for stmt in statements)


def test_migration_upgrades_from_v25_without_backfill(tmp_path: Path) -> None:
    """A DB at watermark 25 with an existing manual link gains the 026 table
    with ZERO rows: manual links never gain a snapshot."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    threads = ThreadRepo(pool)
    tasks = ProjectTaskRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    member = users.create(username="member", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级快照")
    projects.add_member(project.project_id, member, role="member")
    _seed_agent(pool, agent_id="ag_member", user_id=member)
    threads.insert(
        thread_id="thr_manual",
        agent_id="ag_member",
        user_id=member,
        channel_type="dashboard",
        session_key=_dashboard_key("ag_member", member),
        title="手动关联",
    )
    assert (
        tasks.attach(project_id=project.project_id, user_id=member, thread_id="thr_manual").outcome
        == "created"
    )
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS project_task_contexts;
            UPDATE _schema_version SET version = 25;
            """
        )
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 26
    assert "project_task_contexts" in tables
    assert _row_count(pool, "project_task_contexts", "1 = 1", ()) == 0
    with pool.connect() as conn:
        link = conn.execute(
            "SELECT source FROM project_task_links WHERE thread_id = ?", ("thr_manual",)
        ).fetchone()
    assert link is not None and link["source"] == "manual"
