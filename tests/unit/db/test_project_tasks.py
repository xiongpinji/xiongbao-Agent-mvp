"""Unit tests for ProjectTaskRepo, migration 021, and ProjectTaskService.

PS-05 slice 1: a project member attaches their own existing Dashboard DM
thread to a project. Covers the private-link ACL (uniform invalid_thread for
foreign/unknown/non-dashboard threads), UNIQUE(thread_id) conflict semantics,
literal title search, activity-sentinel paging, cascade cleanup, and the
member-removal detach inside ``ProjectRepo.remove_member``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from octop.i18n import error_message
from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import UNSET, now_ts
from octop.infra.db.repos.project_task_shares import ProjectTaskShareRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.tasks import ProjectTaskService, TaskListPage

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"


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
def pid(projects: ProjectRepo, owner_id: int, member_id: int, other_member_id: int) -> str:
    project = projects.create_with_owner(creator_user_id=owner_id, name="任务项目")
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
    projects: ProjectRepo, repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo
) -> ProjectTaskService:
    return ProjectTaskService(_StubServices(projects, repo, ProjectTaskShareRepo(db), threads))


def _seed_agent(db: SqlitePool, *, agent_id: str, user_id: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at) "
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
    channel_type: str = "dashboard",
    session_key: object = UNSET,
    last_active: int | None = None,
) -> None:
    threads.insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        channel_type=channel_type,
        session_key=(
            _dashboard_key(agent_id, user_id) if session_key is UNSET else str(session_key)
        ),
        title=title,
        last_active=last_active,
    )


def _events(db: SqlitePool, project_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()


def _assert_no_task_events(
    db: SqlitePool, project_id: str, thread_ids: tuple[str, ...] = ()
) -> None:
    """This slice never events task links; no private thread id may appear in
    the member-shared project feed (project.created etc. are still allowed)."""
    events = _events(db, project_id)
    assert not [e for e in events if "task" in str(e["event_type"])]
    for event in events:
        for tid in thread_ids:
            assert tid not in str(event["object_id"])
            assert tid not in str(event["payload_json"])


def _link_rows(db: SqlitePool, project_id: str | None = None) -> list[sqlite3.Row]:
    with db.connect() as conn:
        if project_id is None:
            return conn.execute(
                "SELECT id, project_id, thread_id, owner_user_id, source, created_at "
                "FROM project_task_links ORDER BY id"
            ).fetchall()
        return conn.execute(
            "SELECT id, project_id, thread_id, owner_user_id, source, created_at "
            "FROM project_task_links WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Migration 021
# ---------------------------------------------------------------------------


def test_migration_021_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_task_links)").fetchall()}
    assert v == _max_discovered_version("sqlite")
    assert v >= 21
    assert "project_task_links" in tables
    assert {
        "id",
        "project_id",
        "thread_id",
        "owner_user_id",
        "source",
        "created_at",
    }.issubset(cols)
    assert "idx_project_task_links_project_owner" in indexes


def test_migration_021_thread_id_unique(
    db: SqlitePool, repo: ProjectTaskRepo, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id)
    assert repo.attach(project_id=pid, user_id=owner_id, thread_id="th1").outcome == "created"
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_task_links("
            "project_id, thread_id, owner_user_id, source, created_at) "
            "VALUES (?, 'th1', ?, 'manual', 0)",
            (pid, owner_id),
        )


def test_migration_upgrades_from_v20(tmp_path: Path) -> None:
    """A DB at watermark 20 must gain project_task_links by re-running migrations."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_task_links;
            UPDATE _schema_version SET version = 20;
            """
        )
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert v == _max_discovered_version("sqlite")
    assert "project_task_links" in tables
    # The upgraded schema is functional end to end.
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    threads = ThreadRepo(pool)
    tasks = ProjectTaskRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级任务")
    _seed_agent(pool, agent_id="ag1", user_id=owner)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner)
    mutation = tasks.attach(project_id=project.project_id, user_id=owner, thread_id="th1")
    assert mutation.outcome == "created"
    assert mutation.summary is not None and mutation.summary.thread_id == "th1"


def test_migration_021_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (MIGRATIONS / "021_project_task_links.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    # Re-running an older migration script writes its own watermark, even when
    # newer migrations are installed in the fixture database.
    assert v == 21


def test_migration_021_pg_pair_declares_same_shape() -> None:
    """Token-level parity check of the PostgreSQL script (no live PG needed)."""
    sqlite_sql = (MIGRATIONS / "021_project_task_links.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "021_project_task_links.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "project_task_links",
        "thread_id TEXT NOT NULL UNIQUE",
        "REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "REFERENCES threads(thread_id) ON DELETE CASCADE",
        "owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE",
        "source TEXT NOT NULL DEFAULT 'manual'",
        "created_at INTEGER NOT NULL",
        "idx_project_task_links_project_owner",
        "UPDATE _schema_version SET version = 21",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    assert "GENERATED BY DEFAULT AS IDENTITY" in pg_sql
    assert "AUTOINCREMENT" not in pg_sql
    assert "AUTOINCREMENT" in sqlite_sql
    assert "GENERATED BY DEFAULT" not in sqlite_sql

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 3  # table + index + watermark update
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# Attach
# ---------------------------------------------------------------------------


def test_attach_creates_link_with_server_derived_fields(
    repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(
        threads,
        thread_id="th1",
        agent_id="ag1",
        user_id=owner_id,
        title="私密报告",
        last_active=123,
    )
    mutation = repo.attach(project_id=pid, user_id=owner_id, thread_id="th1")
    assert mutation.outcome == "created"
    summary = mutation.summary
    assert summary is not None
    assert summary.project_id == pid
    assert summary.thread_id == "th1"
    assert summary.owner_user_id == owner_id
    assert summary.agent_id == "ag1"
    assert summary.title == "私密报告"
    assert summary.source == "manual"
    assert summary.last_active == 123
    assert summary.created_at == pytest.approx(now_ts(), abs=5)

    rows = _link_rows(db, pid)
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "th1"
    assert rows[0]["owner_user_id"] == owner_id
    assert rows[0]["source"] == "manual"
    assert rows[0]["created_at"] == pytest.approx(now_ts(), abs=5)
    # This slice never writes shared project events for task links.
    _assert_no_task_events(db, pid, ("th1",))


def test_attach_duplicate_is_idempotent(
    repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id, title="任务")
    first = repo.attach(project_id=pid, user_id=owner_id, thread_id="th1")
    second = repo.attach(project_id=pid, user_id=owner_id, thread_id="th1")
    assert first.outcome == "created"
    assert second.outcome == "duplicate"
    assert second.summary is not None
    assert second.summary.thread_id == "th1"
    assert len(_link_rows(db)) == 1
    _assert_no_task_events(db, pid, ("th1",))


def test_attach_guards_are_uniform_and_write_nothing(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id)

    # Membership is checked first: non-members and unknown projects are alike.
    assert repo.attach(project_id=pid, user_id=outsider_id, thread_id="th1").outcome == "not_member"
    assert repo.attach(project_id="nope", user_id=owner_id, thread_id="th1").outcome == "not_member"
    # A member attacking someone else's thread gets the same outcome as an
    # unknown thread id — no ownership or existence signal.
    assert (
        repo.attach(project_id=pid, user_id=member_id, thread_id="th1").outcome == "invalid_thread"
    )
    assert (
        repo.attach(project_id=pid, user_id=owner_id, thread_id="ghost").outcome == "invalid_thread"
    )
    # Non-dashboard channel.
    _seed_thread(
        threads,
        thread_id="th_feishu",
        agent_id="ag1",
        user_id=owner_id,
        channel_type="feishu",
        session_key="ag1:feishu:g1:group",
    )
    assert (
        repo.attach(project_id=pid, user_id=owner_id, thread_id="th_feishu").outcome
        == "invalid_thread"
    )
    # Dashboard channel but a group session, not the owner's :dm key.
    _seed_thread(
        threads,
        thread_id="th_group",
        agent_id="ag1",
        user_id=owner_id,
        session_key=f"ag1:dashboard:{owner_id}:group",
    )
    assert (
        repo.attach(project_id=pid, user_id=owner_id, thread_id="th_group").outcome
        == "invalid_thread"
    )
    # Dashboard :dm session key of a different user.
    _seed_thread(
        threads,
        thread_id="th_other_dm",
        agent_id="ag1",
        user_id=owner_id,
        session_key=f"ag1:dashboard:{member_id}:dm",
    )
    assert (
        repo.attach(project_id=pid, user_id=owner_id, thread_id="th_other_dm").outcome
        == "invalid_thread"
    )

    assert _link_rows(db) == []
    _assert_no_task_events(db, pid, ("th1", "th_feishu", "th_group", "th_other_dm"))


def test_attach_conflict_when_linked_to_other_project(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    owner_id: int,
) -> None:
    pa = projects.create_with_owner(creator_user_id=owner_id, name="项目A").project_id
    pb = projects.create_with_owner(creator_user_id=owner_id, name="项目B").project_id
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id)

    assert repo.attach(project_id=pb, user_id=owner_id, thread_id="th1").outcome == "created"
    mutation = repo.attach(project_id=pa, user_id=owner_id, thread_id="th1")
    assert mutation.outcome == "conflict"
    assert mutation.conflict_project_id == pb
    assert [r["project_id"] for r in _link_rows(db)] == [pb]
    _assert_no_task_events(db, pa, ("th1",))
    _assert_no_task_events(db, pb, ("th1",))


def test_foreign_already_linked_thread_does_not_expose_project_id(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    owner_id: int,
    member_id: int,
) -> None:
    """The transactional repo must not leak a foreign linked task's project."""
    private_project = projects.create_with_owner(
        creator_user_id=owner_id, name="private"
    ).project_id
    shared_project = projects.create_with_owner(creator_user_id=member_id, name="shared").project_id
    _seed_agent(db, agent_id="owner-agent", user_id=owner_id)
    _seed_thread(
        threads,
        thread_id="private-task",
        agent_id="owner-agent",
        user_id=owner_id,
        title="SECRET",
    )
    assert (
        repo.attach(project_id=private_project, user_id=owner_id, thread_id="private-task").outcome
        == "created"
    )

    mutation = repo.attach(project_id=shared_project, user_id=member_id, thread_id="private-task")
    assert mutation.outcome == "invalid_thread"
    assert mutation.conflict_project_id == ""


def test_postgres_membership_lock_uses_for_share(repo: ProjectTaskRepo) -> None:
    """PG member validation locks the membership row FOR SHARE before the link
    write; lock order member-row → link-row matches remove_member's DELETE."""

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
# List / search / paging
# ---------------------------------------------------------------------------


def _seed_three(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_o", user_id=owner_id)
    _seed_agent(db, agent_id="ag_m", user_id=member_id)
    _seed_thread(
        threads,
        thread_id="th_o1",
        agent_id="ag_o",
        user_id=owner_id,
        title="Owner private 报告",
        last_active=300,
    )
    _seed_thread(
        threads,
        thread_id="th_o2",
        agent_id="ag_o",
        user_id=owner_id,
        title="Second",
        last_active=100,
    )
    _seed_thread(
        threads,
        thread_id="th_m1",
        agent_id="ag_m",
        user_id=member_id,
        title="Member private",
        last_active=200,
    )
    for tid, uid in (("th_o1", owner_id), ("th_o2", owner_id), ("th_m1", member_id)):
        assert repo.attach(project_id=pid, user_id=uid, thread_id=tid).outcome == "created"


def test_list_isolated_per_owner(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_three(repo, db, threads, pid, owner_id, member_id)
    owner_rows = repo.list_for_owner(pid, user_id=owner_id)
    assert [r.thread_id for r in owner_rows] == ["th_o1", "th_o2"]
    assert all(r.owner_user_id == owner_id for r in owner_rows)
    member_rows = repo.list_for_owner(pid, user_id=member_id)
    assert [r.thread_id for r in member_rows] == ["th_m1"]
    # No cross-member title or id leak through the shared project.
    assert "Member private" not in {r.title for r in owner_rows}
    assert "Owner private 报告" not in {r.title for r in member_rows}


def test_list_fetches_one_extra_row_for_has_more(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_three(repo, db, threads, pid, owner_id, member_id)
    rows = repo.list_for_owner(pid, user_id=owner_id, limit=1)
    assert len(rows) == 2


def test_list_search_is_literal_and_case_insensitive(
    repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    for tid, title, la in (
        ("th_a", "Report 100%", 300),
        ("th_b", "report_100x", 200),
        ("th_c", "周报", 100),
    ):
        _seed_thread(
            threads, thread_id=tid, agent_id="ag1", user_id=owner_id, title=title, last_active=la
        )
        assert repo.attach(project_id=pid, user_id=owner_id, thread_id=tid).outcome == "created"

    assert {r.thread_id for r in repo.list_for_owner(pid, user_id=owner_id, q="REPORT")} == {
        "th_a",
        "th_b",
    }
    # '%' stays literal: only "Report 100%" contains it.
    assert {r.thread_id for r in repo.list_for_owner(pid, user_id=owner_id, q="100%")} == {"th_a"}
    # '_' stays literal: only "report_100x" contains "t_1".
    assert {r.thread_id for r in repo.list_for_owner(pid, user_id=owner_id, q="t_1")} == {"th_b"}
    # Backslash stays literal.
    _seed_thread(
        threads,
        thread_id="th_d",
        agent_id="ag1",
        user_id=owner_id,
        title="back\\slash",
        last_active=50,
    )
    assert repo.attach(project_id=pid, user_id=owner_id, thread_id="th_d").outcome == "created"
    assert {r.thread_id for r in repo.list_for_owner(pid, user_id=owner_id, q="\\")} == {"th_d"}
    # Whitespace-only query is treated as no filter.
    assert len(repo.list_for_owner(pid, user_id=owner_id, q="   ")) == 4
    # CJK substring search matches too.
    assert repo.list_for_owner(pid, user_id=owner_id, q="周报")[0].thread_id == "th_c"


def test_list_sort_activity_sentinel_then_thread_id_desc(
    repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th_x", agent_id="ag1", user_id=owner_id, last_active=500)
    _seed_thread(threads, thread_id="th_b", agent_id="ag1", user_id=owner_id, last_active=700)
    _seed_thread(threads, thread_id="th_a", agent_id="ag1", user_id=owner_id, last_active=700)
    for tid in ("th_x", "th_b", "th_a"):
        assert repo.attach(project_id=pid, user_id=owner_id, thread_id=tid).outcome == "created"
    assert [r.thread_id for r in repo.list_for_owner(pid, user_id=owner_id)] == [
        "th_b",
        "th_a",
        "th_x",
    ]
    # offset walks the stable order.
    rows = repo.list_for_owner(pid, user_id=owner_id, limit=1, offset=1)
    assert [r.thread_id for r in rows] == ["th_a", "th_x"]


# ---------------------------------------------------------------------------
# Detail / detach / cascades
# ---------------------------------------------------------------------------


def test_get_for_owner_scopes_project_thread_and_owner(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id, title="T")
    assert repo.attach(project_id=pid, user_id=owner_id, thread_id="th1").outcome == "created"
    other_pid = projects.create_with_owner(creator_user_id=owner_id, name="别的项目").project_id

    summary = repo.get_for_owner(pid, "th1", user_id=owner_id)
    assert summary is not None and summary.title == "T"
    # Fellow member cannot read the owner's private task.
    assert repo.get_for_owner(pid, "th1", user_id=member_id) is None
    # Cross-project lookup misses.
    assert repo.get_for_owner(other_pid, "th1", user_id=owner_id) is None
    assert repo.get_for_owner(pid, "ghost", user_id=owner_id) is None


def test_detach_only_own_link_and_keeps_thread(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id, title="T")
    assert repo.attach(project_id=pid, user_id=owner_id, thread_id="th1").outcome == "created"

    assert repo.detach(project_id=pid, thread_id="th1", user_id=member_id) == "missing"
    assert len(_link_rows(db)) == 1
    assert repo.detach(project_id=pid, thread_id="th1", user_id=outsider_id) == "not_member"
    assert len(_link_rows(db)) == 1
    assert repo.detach(project_id=pid, thread_id="th1", user_id=owner_id) == "removed"
    assert _link_rows(db) == []
    # Detach only removes the project attribution; the thread itself survives.
    assert threads.get("th1") is not None
    assert repo.detach(project_id=pid, thread_id="th1", user_id=owner_id) == "missing"
    _assert_no_task_events(db, pid, ("th1",))


def test_thread_delete_cascades_link(
    repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id)
    assert repo.attach(project_id=pid, user_id=owner_id, thread_id="th1").outcome == "created"
    threads.delete("th1")
    assert _link_rows(db) == []


def test_project_delete_cascades_link(
    repo: ProjectTaskRepo, db: SqlitePool, threads: ThreadRepo, pid: str, owner_id: int
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id)
    assert repo.attach(project_id=pid, user_id=owner_id, thread_id="th1").outcome == "created"
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _link_rows(db) == []
    assert threads.get("th1") is not None


def test_remove_member_detaches_links_in_same_transaction(
    repo: ProjectTaskRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    other_member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_owner", user_id=owner_id)
    _seed_agent(db, agent_id="ag_m", user_id=member_id)
    _seed_agent(db, agent_id="ag_om", user_id=other_member_id)
    _seed_thread(threads, thread_id="th_owner", agent_id="ag_owner", user_id=owner_id)
    _seed_thread(threads, thread_id="th_m", agent_id="ag_m", user_id=member_id, title="M private")
    _seed_thread(threads, thread_id="th_om", agent_id="ag_om", user_id=other_member_id)
    for tid, uid in (("th_owner", owner_id), ("th_m", member_id), ("th_om", other_member_id)):
        assert repo.attach(project_id=pid, user_id=uid, thread_id=tid).outcome == "created"

    mutation = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert mutation.outcome == "removed"

    remaining = {(r["thread_id"], r["owner_user_id"]) for r in _link_rows(db, pid)}
    assert remaining == {("th_owner", owner_id), ("th_om", other_member_id)}
    # The removed member's original thread is untouched.
    assert threads.get("th_m") is not None
    # No shared project event exposes the detached private thread id.
    events = _events(db, pid)
    assert events, "member removal itself is still evented"
    for event in events:
        assert "th_m" not in str(event["object_id"])
        assert "th_m" not in str(event["payload_json"])


# ---------------------------------------------------------------------------
# Service policy
# ---------------------------------------------------------------------------


def test_service_authorize_and_attach_flow(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id, title="报告")
    agent_id = service.authorize_attach_target(pid, user_id=owner_id, thread_id="th1")
    assert agent_id == "ag1"
    view, created = service.attach_task(pid, user_id=owner_id, thread_id="th1")
    assert created is True
    assert view.thread_id == "th1"
    assert view.title == "报告"
    assert view.source == "manual"
    view2, created2 = service.attach_task(pid, user_id=owner_id, thread_id="th1")
    assert created2 is False
    assert view2 == view


def test_service_outsider_and_unknown_project_uniform_404(
    service: ProjectTaskService, pid: str, owner_id: int, outsider_id: int
) -> None:
    calls = (
        lambda: service.list_tasks(pid, user_id=outsider_id),
        lambda: service.get_task(pid, "th1", user_id=outsider_id),
        lambda: service.authorize_attach_target(pid, user_id=outsider_id, thread_id="th1"),
        lambda: service.attach_task(pid, user_id=outsider_id, thread_id="th1"),
        lambda: service.detach_task(pid, "th1", user_id=outsider_id),
        lambda: service.list_tasks("ghost", user_id=owner_id),
        lambda: service.get_task("ghost", "th1", user_id=owner_id),
        lambda: service.attach_task("ghost", user_id=owner_id, thread_id="th1"),
        lambda: service.detach_task("ghost", "th1", user_id=owner_id),
    )
    for call in calls:
        with pytest.raises(OctopError) as excinfo:
            call()
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        assert excinfo.value.status == 404


def test_service_foreign_or_invalid_thread_uniform_404_without_title_leak(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(
        threads, thread_id="th_secret", agent_id="ag1", user_id=owner_id, title="SECRET-标题"
    )
    _seed_thread(
        threads,
        thread_id="th_feishu",
        agent_id="ag1",
        user_id=owner_id,
        channel_type="feishu",
        session_key="ag1:feishu:g1:group",
    )
    cases = (
        (member_id, "th_secret"),  # another member's private thread
        (member_id, "ghost"),  # unknown id
        (owner_id, "th_feishu"),  # own but not a dashboard DM
    )
    for uid, tid in cases:
        for call in (
            lambda uid=uid, tid=tid: service.authorize_attach_target(
                pid, user_id=uid, thread_id=tid
            ),
            lambda uid=uid, tid=tid: service.attach_task(pid, user_id=uid, thread_id=tid),
            lambda uid=uid, tid=tid: service.get_task(pid, tid, user_id=uid),
            lambda uid=uid, tid=tid: service.detach_task(pid, tid, user_id=uid),
        ):
            with pytest.raises(OctopError) as excinfo:
                call()
            err = excinfo.value
            assert err.code is ErrorCode.NOT_FOUND
            assert "SECRET" not in err.message
            assert "SECRET" not in repr(err.details)


def test_service_conflict_maps_to_409(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    owner_id: int,
) -> None:
    pa = projects.create_with_owner(creator_user_id=owner_id, name="A").project_id
    pb = projects.create_with_owner(creator_user_id=owner_id, name="B").project_id
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id)
    service.attach_task(pb, user_id=owner_id, thread_id="th1")
    with pytest.raises(OctopError) as excinfo:
        service.attach_task(pa, user_id=owner_id, thread_id="th1")
    err = excinfo.value
    assert err.code is ErrorCode.PROJECT_TASK_LINK_CONFLICT
    assert err.status == 409
    assert err.details.get("project_id") == pb


def test_conflict_error_code_has_409_and_locales() -> None:
    assert ErrorCode.PROJECT_TASK_LINK_CONFLICT.value == "PROJECT_TASK_LINK_CONFLICT"
    assert OctopError(ErrorCode.PROJECT_TASK_LINK_CONFLICT, "x").status == 409
    envelope = OctopError(ErrorCode.PROJECT_TASK_LINK_CONFLICT, "x").to_envelope(locale="zh")
    assert envelope["error"]["code"] == "PROJECT_TASK_LINK_CONFLICT"
    assert error_message("PROJECT_TASK_LINK_CONFLICT", "en")
    assert error_message("PROJECT_TASK_LINK_CONFLICT", "zh")


def test_service_list_page_and_privacy(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_three(ProjectTaskRepo(db), db, threads, pid, owner_id, member_id)
    page = service.list_tasks(pid, user_id=owner_id, limit=1)
    assert isinstance(page, TaskListPage)
    assert page.limit == 1 and page.offset == 0
    assert page.has_more is True
    assert [v.thread_id for v in page.items] == ["th_o1"]

    member_page = service.list_tasks(pid, user_id=member_id)
    assert [v.thread_id for v in member_page.items] == ["th_m1"]
    assert all(v.owner_user_id == member_id for v in member_page.items)
    assert member_page.has_more is False

    searched = service.list_tasks(pid, user_id=owner_id, q="owner private")
    assert [v.thread_id for v in searched.items] == ["th_o1"]


def test_service_get_and_detach(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag1", user_id=owner_id)
    _seed_thread(threads, thread_id="th1", agent_id="ag1", user_id=owner_id, title="T")
    service.attach_task(pid, user_id=owner_id, thread_id="th1")

    view = service.get_task(pid, "th1", user_id=owner_id)
    assert view.title == "T"
    with pytest.raises(OctopError) as excinfo:
        service.get_task(pid, "th1", user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(OctopError) as excinfo:
        service.detach_task(pid, "th1", user_id=member_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    service.detach_task(pid, "th1", user_id=owner_id)
    with pytest.raises(OctopError) as excinfo:
        service.get_task(pid, "th1", user_id=owner_id)
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert threads.get("th1") is not None
