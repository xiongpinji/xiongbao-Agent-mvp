"""Unit tests for 027 versioned project expert selection (migration + repo + service).

PS-07 expert gate slice 1: owner/admin save an ordered list of globally shared
single experts; the revision versions only admin PUT writes. Covers the paired
027 DDL (columns, ordered rows, unique pair, cascades), the v26 upgrade, static
PostgreSQL DDL/lock-order shape, role and validation semantics, stale-revision
409 with no writes, same-list no-op, GET privacy masking for unshared/disabled/
team/deleted agents, activity payloads carrying a safe count only, and the
two-connection SQLite interleaving where exactly one of two competing saves
wins.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import MAX_PROJECT_EXPERTS, ProjectService

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


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
def admin_id(users: UserRepo) -> int:
    return users.create(username="admin", password_hash="h", role="user")


@pytest.fixture
def member_id(users: UserRepo) -> int:
    return users.create(username="member", password_hash="h", role="user")


@pytest.fixture
def outsider_id(users: UserRepo) -> int:
    return users.create(username="outsider", password_hash="h", role="user")


@pytest.fixture
def pid(projects: ProjectRepo, owner_id: int, admin_id: int, member_id: int) -> str:
    project = projects.create_with_owner(creator_user_id=owner_id, name="专家项目")
    projects.add_member(project.project_id, admin_id, role="admin")
    projects.add_member(project.project_id, member_id, role="member")
    return project.project_id


class _StubServices:
    def __init__(self, projects: ProjectRepo) -> None:
        self.project_repo = projects


@pytest.fixture
def service(projects: ProjectRepo) -> ProjectService:
    return ProjectService(_StubServices(projects))


def _seed_agent(
    db: SqlitePool,
    *,
    agent_id: str,
    user_id: int,
    kind: str = "expert",
    enabled: int = 1,
    is_shared: int = 0,
    name: str | None = None,
    description: str | None = None,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents("
            "agent_id, user_id, name, description, kind, enabled, is_shared, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (agent_id, user_id, name or agent_id, description, kind, enabled, is_shared),
        )


def _share(db: SqlitePool, agent_id: str, value: int = 1) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared = ? WHERE agent_id = ?", (value, agent_id))


def _set_enabled(db: SqlitePool, agent_id: str, value: int) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE agents SET enabled = ? WHERE agent_id = ?", (value, agent_id))


def _set_kind(db: SqlitePool, agent_id: str, value: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE agents SET kind = ? WHERE agent_id = ?", (value, agent_id))


def _row_count(db: SqlitePool, table: str, where: str, params: tuple[object, ...]) -> int:
    with db.connect() as conn:
        return int(
            conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]
        )


def _stored_ids(db: SqlitePool, pid: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT agent_id FROM project_experts WHERE project_id = ? ORDER BY sort_order, agent_id",
            (pid,),
        ).fetchall()
    return [str(r["agent_id"]) for r in rows]


def _revision(db: SqlitePool, pid: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT experts_revision FROM project_spaces WHERE project_id = ?", (pid,)
        ).fetchone()
    return int(row["experts_revision"])


def _events(db: SqlitePool, pid: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type, object_id, payload_json FROM project_events "
            "WHERE project_id = ? ORDER BY id",
            (pid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Migration 027
# ---------------------------------------------------------------------------


def test_migration_027_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        expert_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_experts)").fetchall()
        }
        spaces_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_spaces)").fetchall()
        }
        ctx_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_task_contexts)").fetchall()
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 27
    assert "project_experts" in tables
    assert expert_cols == {"id", "project_id", "agent_id", "sort_order", "added_by", "added_at"}
    assert "experts_revision" in spaces_cols
    assert "expert_selection_revision" in ctx_cols
    assert "idx_project_experts_project" in indexes


def test_migration_027_cascades_and_unique_pair(
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1)
    with db.connect() as conn:
        fks = conn.execute("PRAGMA foreign_key_list(project_experts)").fetchall()
    pairs = {str(fk["from"]): (str(fk["table"]), str(fk["to"]), str(fk["on_delete"])) for fk in fks}
    assert pairs == {
        "project_id": ("project_spaces", "project_id", "CASCADE"),
        "agent_id": ("agents", "agent_id", "CASCADE"),
        "added_by": ("users", "id", "SET NULL"),
    }
    insert = (
        "INSERT INTO project_experts(project_id, agent_id, sort_order, added_by, added_at) "
        "VALUES (?, ?, ?, ?, 0)"
    )
    with db.transaction() as conn:
        conn.execute(insert, (pid, "ag_a", 0, owner_id))
        # The (project_id, agent_id) pair is unique.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, "ag_a", 1, owner_id))
        # Negative sort order is refused.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (pid, "ag_b", -1, owner_id))


def test_migration_027_upgrades_from_v26(
    db: SqlitePool, projects: ProjectRepo, owner_id: int
) -> None:
    project = projects.create_with_owner(creator_user_id=owner_id, name="升级专家")
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_experts(project_id, agent_id, sort_order, added_by, added_at) "
            "VALUES (?, 'ag_a', 0, ?, 0)",
            (project.project_id, owner_id),
        )
        conn.execute(
            "UPDATE project_spaces SET experts_revision = 4 WHERE project_id = ?",
            (project.project_id,),
        )
    with db.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_experts;
            ALTER TABLE project_spaces DROP COLUMN experts_revision;
            ALTER TABLE project_task_contexts DROP COLUMN expert_selection_revision;
            UPDATE _schema_version SET version = 26;
            """
        )
    run_migrations(db)
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        expert_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_experts)").fetchall()
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 27
    assert expert_cols
    # The upgrade adds empty versioned state: legacy projects keep revision 0
    # and gain no expert rows.
    assert _revision(db, project.project_id) == 0
    assert _stored_ids(db, project.project_id) == []


def test_migration_027_pg_pair_declares_same_shape() -> None:
    """Token-level parity of the PG script (source review only — no live PG)."""
    sqlite_sql = (MIGRATIONS / "027_project_experts.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "027_project_experts.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "ALTER TABLE project_spaces ADD COLUMN experts_revision INTEGER NOT NULL DEFAULT 0",
        "CREATE TABLE IF NOT EXISTS project_experts",
        "project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE",
        "sort_order INTEGER NOT NULL CHECK (sort_order >= 0)",
        "added_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "added_at INTEGER NOT NULL CHECK (added_at >= 0)",
        "UNIQUE (project_id, agent_id)",
        "idx_project_experts_project",
        "ALTER TABLE project_task_contexts ADD COLUMN expert_selection_revision "
        "INTEGER NOT NULL DEFAULT 0",
        "UPDATE _schema_version SET version = 27",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    assert "AUTOINCREMENT" in sqlite_sql
    assert "GENERATED BY DEFAULT AS IDENTITY" in pg_sql
    assert "AUTOINCREMENT" not in pg_sql

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 5  # two ALTERs + table + index + watermark
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# Reads and privacy
# ---------------------------------------------------------------------------


def test_member_reads_ordered_list_and_outsider_gets_uniform_not_found(
    service: ProjectService,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1, name="甲", description="甲专家")
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1, name="乙", description="乙专家")
    outcome = service.set_experts(
        pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_b", "ag_a"]
    )
    assert outcome.revision == 1
    assert [item.agent_id for item in outcome.items] == ["ag_b", "ag_a"]
    assert outcome.items[0].name == "乙" and outcome.items[0].description == "乙专家"
    assert all(item.status == "available" for item in outcome.items)

    view = service.list_experts(pid, user_id=member_id)
    assert view.revision == 1
    assert [item.agent_id for item in view.items] == ["ag_b", "ag_a"]

    for user_id, project_id in ((outsider_id, pid), (member_id, "01GHOSTPROJECT")):
        with pytest.raises(OctopError) as excinfo:
            service.list_experts(project_id, user_id=user_id)
        assert excinfo.value.code is ErrorCode.NOT_FOUND
        assert "ag_" not in str(excinfo.value.message)


def test_get_masks_unavailable_agents_without_leaking_profile(
    service: ProjectService,
    db: SqlitePool,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(
        db, agent_id="ag_a", user_id=owner_id, is_shared=1, name="甲", description="私有资料"
    )
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1, name="乙", description="乙资料")
    _seed_agent(db, agent_id="ag_c", user_id=owner_id, is_shared=1, name="丙", description="丙资料")
    service.set_experts(
        pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_a", "ag_b", "ag_c"]
    )

    _share(db, "ag_a", 0)
    _set_enabled(db, "ag_b", 0)
    _set_kind(db, "ag_c", "team")
    masked = service.list_experts(pid, user_id=owner_id)
    assert masked.revision == 1
    for item in masked.items:
        assert item.name is None
        assert item.description is None
        assert item.status == "unavailable"
    # The private profile never appears in the response payload.
    assert "私有资料" not in json.dumps(
        [item.__dict__ for item in masked.items], ensure_ascii=False
    )

    with db.transaction() as conn:
        conn.execute("DELETE FROM agents WHERE agent_id = 'ag_a'")
    assert [item.agent_id for item in service.list_experts(pid, user_id=owner_id).items] == [
        "ag_b",
        "ag_c",
    ]
    assert _revision(db, pid) == 1


def test_hard_delete_cascades_and_user_delete_nulls_actor(
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    admin_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    projects.replace_experts(
        project_id=pid, actor_user_id=admin_id, expected_revision=0, agent_ids=["ag_a"]
    )
    with db.connect() as conn:
        row = conn.execute(
            "SELECT added_by FROM project_experts WHERE project_id = ? AND agent_id = 'ag_a'",
            (pid,),
        ).fetchone()
    assert row is not None and int(row["added_by"]) == admin_id

    # added_by SET NULL on actor deletion (no owning rows cascade the agent).
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_members WHERE user_id = ?", (admin_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (admin_id,))
    with db.connect() as conn:
        row = conn.execute(
            "SELECT added_by FROM project_experts WHERE project_id = ? AND agent_id = 'ag_a'",
            (pid,),
        ).fetchone()
    assert row is not None and row["added_by"] is None

    with db.transaction() as conn:
        conn.execute("DELETE FROM agents WHERE agent_id = 'ag_a'")
    assert _stored_ids(db, pid) == []
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1)
    projects.replace_experts(
        project_id=pid, actor_user_id=owner_id, expected_revision=1, agent_ids=["ag_b"]
    )
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _row_count(db, "project_experts", "project_id = ?", (pid,)) == 0


# ---------------------------------------------------------------------------
# PUT semantics
# ---------------------------------------------------------------------------


def test_member_put_forbidden_admin_and_owner_allowed(
    service: ProjectService,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    admin_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    with pytest.raises(OctopError) as excinfo:
        service.set_experts(pid, actor_user_id=member_id, expected_revision=0, agent_ids=["ag_a"])
    assert excinfo.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(OctopError) as excinfo:
        service.set_experts(pid, actor_user_id=outsider_id, expected_revision=0, agent_ids=["ag_a"])
    assert excinfo.value.code is ErrorCode.NOT_FOUND

    admin_view = service.set_experts(
        pid, actor_user_id=admin_id, expected_revision=0, agent_ids=["ag_a"]
    )
    assert admin_view.revision == 1
    owner_view = service.set_experts(
        pid, actor_user_id=owner_id, expected_revision=1, agent_ids=["ag_a"]
    )
    assert owner_view.revision == 1


def test_put_demotion_or_removal_after_pre_read_is_rechecked_in_transaction(
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    admin_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE project_members SET role = 'member' WHERE project_id = ? AND user_id = ?",
            (pid, admin_id),
        )
    mutation = projects.replace_experts(
        project_id=pid, actor_user_id=admin_id, expected_revision=0, agent_ids=["ag_a"]
    )
    assert mutation.outcome == "forbidden"
    assert _stored_ids(db, pid) == []

    projects.remove_member(project_id=pid, user_id=admin_id, actor_user_id=owner_id)
    mutation = projects.replace_experts(
        project_id=pid, actor_user_id=admin_id, expected_revision=0, agent_ids=["ag_a"]
    )
    assert mutation.outcome == "not_member"
    assert _stored_ids(db, pid) == []


def test_replace_rejects_duplicates_over_limit_and_invalid_agents(
    service: ProjectService,
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_shared", user_id=owner_id, is_shared=1)
    _seed_agent(db, agent_id="ag_private", user_id=owner_id, is_shared=0)
    _seed_agent(db, agent_id="ag_disabled", user_id=owner_id, is_shared=1, enabled=0)
    _seed_agent(db, agent_id="ag_team", user_id=owner_id, is_shared=1, kind="team")
    agent_ids = [f"ag_{i:02d}" for i in range(MAX_PROJECT_EXPERTS)]
    for agent_id in agent_ids:
        _seed_agent(db, agent_id=agent_id, user_id=owner_id, is_shared=1)
    over_limit = [*agent_ids, "ag_shared"]

    for bad in (
        ["ag_shared", "ag_shared"],
        over_limit,
        ["ag_private"],
        ["ag_disabled"],
        ["ag_team"],
        ["ag_ghost"],
        ["ag_shared", "ag_ghost"],
    ):
        with pytest.raises(OctopError) as excinfo:
            service.set_experts(pid, actor_user_id=owner_id, expected_revision=0, agent_ids=bad)
        assert excinfo.value.code is ErrorCode.PROJECT_EXPERT_INVALID
        assert excinfo.value.status == 422
        # No agent id or private profile is echoed back in the error.
        assert "ag_" not in str(excinfo.value.details)
    assert _revision(db, pid) == 0
    assert _stored_ids(db, pid) == []

    # Exactly the limit is accepted.
    view = service.set_experts(
        pid, actor_user_id=owner_id, expected_revision=0, agent_ids=agent_ids
    )
    assert view.revision == 1
    assert len(view.items) == MAX_PROJECT_EXPERTS

    # The member cannot mutate the list even with a valid body.
    with pytest.raises(OctopError) as excinfo:
        service.set_experts(
            pid, actor_user_id=member_id, expected_revision=1, agent_ids=["ag_shared"]
        )
    assert excinfo.value.code is ErrorCode.FORBIDDEN
    selection = projects.get_experts(pid)
    assert selection is not None
    assert [row.agent_id for row in selection.items] == agent_ids


def test_stale_revision_is_409_without_writes(
    service: ProjectService,
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1)
    service.set_experts(pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_a"])

    with pytest.raises(OctopError) as excinfo:
        service.set_experts(pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_b"])
    assert excinfo.value.code is ErrorCode.PROJECT_EXPERTS_CHANGED
    assert excinfo.value.status == 409
    assert _revision(db, pid) == 1
    assert _stored_ids(db, pid) == ["ag_a"]


def test_same_list_is_a_noop_but_reorder_increments_once(
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1)
    events_before = len(_events(db, pid))

    first = projects.replace_experts(
        project_id=pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_a", "ag_b"]
    )
    assert first.outcome == "replaced" and first.revision == 1
    same = projects.replace_experts(
        project_id=pid, actor_user_id=owner_id, expected_revision=1, agent_ids=["ag_a", "ag_b"]
    )
    assert same.outcome == "unchanged" and same.revision == 1
    reordered = projects.replace_experts(
        project_id=pid, actor_user_id=owner_id, expected_revision=1, agent_ids=["ag_b", "ag_a"]
    )
    assert reordered.outcome == "replaced" and reordered.revision == 2
    assert _stored_ids(db, pid) == ["ag_b", "ag_a"]
    # Only the two real writes append events; the no-op writes nothing.
    events = _events(db, pid)
    assert len(events) == events_before + 2
    for event in events:
        if event["event_type"] == "project.experts_updated":
            payload = json.loads(str(event["payload_json"]))
            assert payload == {"count": 2}
            assert "ag_" not in str(event["payload_json"])


def test_archived_project_put_refused_and_get_still_reads(
    service: ProjectService,
    db: SqlitePool,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    service.set_experts(pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_a"])
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (pid,))

    with pytest.raises(OctopError) as excinfo:
        service.set_experts(pid, actor_user_id=owner_id, expected_revision=1, agent_ids=[])
    assert excinfo.value.code is ErrorCode.FORBIDDEN
    assert _stored_ids(db, pid) == ["ag_a"]
    assert [item.agent_id for item in service.list_experts(pid, user_id=member_id).items] == [
        "ag_a"
    ]


def test_clearing_the_list_increments_once_and_writes_safe_event(
    service: ProjectService,
    db: SqlitePool,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    service.set_experts(pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_a"])
    cleared = service.set_experts(pid, actor_user_id=owner_id, expected_revision=1, agent_ids=[])
    assert cleared.revision == 2
    assert cleared.items == []
    assert _stored_ids(db, pid) == []
    events = [
        json.loads(str(event["payload_json"]))
        for event in _events(db, pid)
        if event["event_type"] == "project.experts_updated"
    ]
    assert events[-1] == {"count": 0}


# ---------------------------------------------------------------------------
# PostgreSQL lock order (static fake; no live PG)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows


class _RecordingConn:
    def __init__(
        self,
        *,
        role: str | None,
        project: dict[str, Any] | None,
        agent_rows: list[dict[str, Any]],
        current_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.statements: list[str] = []
        self._role = role
        self._project = project
        self._agent_rows = agent_rows
        self._current_rows = current_rows or []

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        self.statements.append(sql)
        if "FROM project_members" in sql:
            return _Cursor(row=None if self._role is None else {"role": self._role})
        if "FROM project_spaces" in sql:
            return _Cursor(row=self._project)
        if "FROM agents" in sql:
            return _Cursor(rows=self._agent_rows)
        if "FROM project_experts" in sql:
            return _Cursor(rows=self._current_rows)
        return _Cursor()


class _RecordingTxn:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def __enter__(self) -> _RecordingConn:
        return self._conn

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _RecordingPool:
    def __init__(self, dialect: str, conn: _RecordingConn) -> None:
        self.dialect = dialect
        self.conn = conn

    def transaction(self) -> _RecordingTxn:
        return _RecordingTxn(self.conn)

    def connect(self) -> _RecordingTxn:
        return _RecordingTxn(self.conn)

    def close(self) -> None:
        pass


def _first_index(statements: list[str], needle: str) -> int:
    return next(i for i, s in enumerate(statements) if needle in s)


def test_postgres_replace_experts_lock_order_and_sorted_agent_lock() -> None:
    """PG: member FOR SHARE → project FOR UPDATE → target agents FOR SHARE
    (sorted by agent_id) → only then replace rows. Static dual-dialect
    assertion only — no live PostgreSQL was run."""
    conn = _RecordingConn(
        role="admin",
        project={"archived": 0, "experts_revision": 0},
        agent_rows=[{"agent_id": "ag_a"}, {"agent_id": "ag_b"}],
    )
    repo = ProjectRepo(cast(Any, _RecordingPool("postgresql", conn)))
    result = repo.replace_experts(
        project_id="p1", actor_user_id=7, expected_revision=0, agent_ids=["ag_b", "ag_a"]
    )
    assert result.outcome == "replaced" and result.revision == 1
    stmts = conn.statements
    i_member = _first_index(stmts, "FROM project_members")
    i_project = _first_index(stmts, "FROM project_spaces")
    i_agents = _first_index(stmts, "WHERE agent_id IN")
    i_delete = _first_index(stmts, "DELETE FROM project_experts")
    assert stmts[i_member].endswith("FOR SHARE")
    assert stmts[i_project].endswith("FOR UPDATE")
    assert stmts[i_agents].endswith("FOR SHARE")
    assert "ORDER BY agent_id" in stmts[i_agents]
    assert i_member < i_project < i_agents < i_delete


def test_sqlite_replace_experts_uses_no_row_lock_clauses() -> None:
    conn = _RecordingConn(
        role="owner",
        project={"archived": 0, "experts_revision": 0},
        agent_rows=[{"agent_id": "ag_a"}],
    )
    repo = ProjectRepo(cast(Any, _RecordingPool("sqlite", conn)))
    assert (
        repo.replace_experts(
            project_id="p1", actor_user_id=7, expected_revision=0, agent_ids=["ag_a"]
        ).outcome
        == "replaced"
    )
    assert all(not s.endswith("FOR SHARE") for s in conn.statements)
    assert all(not s.endswith("FOR UPDATE") for s in conn.statements)


# ---------------------------------------------------------------------------
# Two-connection SQLite race for the config revision
# ---------------------------------------------------------------------------


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


def test_two_connection_competing_saves_only_one_wins(
    projects: ProjectRepo,
    db: SqlitePool,
    pid: str,
    owner_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_a", user_id=owner_id, is_shared=1)
    _seed_agent(db, agent_id="ag_b", user_id=owner_id, is_shared=1)

    second = SqlitePool(db.path)
    began, entered = threading.Event(), threading.Event()
    second._conn = _WriterProbe(second._conn, began=began, entered=entered)
    second_repo = ProjectRepo(cast(Any, second))

    def _second_save() -> Any:
        return second_repo.replace_experts(
            project_id=pid, actor_user_id=owner_id, expected_revision=0, agent_ids=["ag_b"]
        )

    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        # The first writer holds BEGIN IMMEDIATE and publishes revision 1;
        # the second save can only proceed after that commit.
        with db.transaction() as conn:
            conn.execute(
                "UPDATE project_spaces SET experts_revision = 1 WHERE project_id = ?",
                (pid,),
            )
            conn.execute(
                "INSERT INTO project_experts("
                "project_id, agent_id, sort_order, added_by, added_at"
                ") VALUES (?, 'ag_a', 0, ?, 0)",
                (pid, owner_id),
            )
            future = executor.submit(_second_save)
            assert began.wait(timeout=10), "second save never attempted BEGIN IMMEDIATE"
            assert not entered.is_set()
        outcome = future.result(timeout=15)
    finally:
        executor.shutdown(wait=True)
        second.close()
    assert outcome.outcome == "stale"
    assert outcome.revision == 1
    assert _stored_ids(db, pid) == ["ag_a"]
    assert _revision(db, pid) == 1
