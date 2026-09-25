"""030A B1: atomic ``project_task_files`` runtime registration with fixed quota.

``AgentRepo.create_project_task_runtime_with_quota`` is the ONLY path that may
write the internal ``runtime_kind``. These tests pin: row shape and defaults;
both fixed limits at their exact boundaries (8th/9th per owner, 64th/65th per
instance); normal agents never counting toward the quota; per-owner isolation;
two independent SQLite pools racing for the last owner seat and the last
global seat (exactly one winner, uniform refusal); rollback leaving no row on
refusal or INSERT failure; and the PostgreSQL lock/transaction contract via a
static fake pool (fixed advisory lock first, counts, then one INSERT with the
kind hard-coded in SQL). No mocks mirroring implementation logic.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import (
    _PG_RUNTIME_QUOTA_LOCK_KEY,
    PROJECT_TASK_FILES_GLOBAL_LIMIT,
    PROJECT_TASK_FILES_OWNER_LIMIT,
    AgentRepo,
    ProjectTaskFilesQuotaError,
)
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> AgentRepo:
    return AgentRepo(db)


@pytest.fixture
def users(db: SqlitePool) -> UserRepo:
    return UserRepo(db)


def _make_user(users: UserRepo, username: str) -> int:
    return users.create(username=username, password_hash="h", role="user")


def _runtime_count(db: SqlitePool, user_id: int | None = None) -> int:
    sql = "SELECT COUNT(*) FROM agents WHERE runtime_kind = 'project_task_files'"
    params: tuple[object, ...] = ()
    if user_id is not None:
        sql += " AND user_id = ?"
        params = (user_id,)
    with db.connect() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def _seed_internal_row(db: SqlitePool, *, agent_id: str, user_id: int, name: str) -> None:
    """Register an internal runtime the way a previous migration-era flow would.

    Quota counting must treat every ``project_task_files`` row as registered
    regardless of which code path inserted it.
    """
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents("
            "agent_id, user_id, name, runtime_kind, created_at, updated_at) "
            "VALUES (?, ?, ?, 'project_task_files', 0, 0)",
            (agent_id, user_id, name),
        )


# ---------------------------------------------------------------------------
# Row shape
# ---------------------------------------------------------------------------


def test_registered_runtime_row_shape_and_defaults(
    db: SqlitePool, repo: AgentRepo, users: UserRepo
) -> None:
    user_id = _make_user(users, "owner")
    returned = repo.create_project_task_runtime_with_quota(
        user_id=user_id,
        agent_id="ag_rt_shape",
        name="rt-9f3k2m",
        default_model="model-x",
        system_prompt="你是项目任务文件助手。",
        icon_name="folder-lock",
        color="#6366f1",
    )
    assert returned == "ag_rt_shape"
    row = repo.get("ag_rt_shape")
    assert row is not None
    assert row.runtime_kind == "project_task_files"
    assert row.user_id == user_id
    assert row.name == "rt-9f3k2m"
    assert row.default_model == "model-x"
    assert row.system_prompt == "你是项目任务文件助手。"
    assert row.icon_name == "folder-lock"
    assert row.color == "#6366f1"
    # Minimal-field registration: shared defaults, nothing extra, no config.
    assert row.enabled == 1
    assert row.kind == "expert"
    assert row.is_shared == 0
    assert row.config_json is None
    assert row.description is None
    assert row.published_expert_id is None
    assert _runtime_count(db) == 1


def test_runtime_registration_requires_owner(db: SqlitePool, repo: AgentRepo) -> None:
    with pytest.raises(ValueError, match="requires an owner"):
        repo.create_project_task_runtime_with_quota(
            user_id=cast(Any, None), agent_id="ag_no_owner", name="rt-no-owner"
        )
    assert _runtime_count(db) == 0


# ---------------------------------------------------------------------------
# Owner limit boundary: exactly 8 allowed, 9th refused
# ---------------------------------------------------------------------------


def test_owner_limit_boundary_refuses_ninth(
    db: SqlitePool, repo: AgentRepo, users: UserRepo
) -> None:
    user_id = _make_user(users, "owner")
    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id=f"ag_o_{i}", name=f"rt-o-{i}"
        )
    assert _runtime_count(db, user_id) == PROJECT_TASK_FILES_OWNER_LIMIT

    with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id="ag_o_overflow", name="rt-o-overflow"
        )
    err = excinfo.value
    assert err.scope == "owner"
    assert err.limit == PROJECT_TASK_FILES_OWNER_LIMIT == 8
    assert err.current == 8
    assert repo.get("ag_o_overflow") is None
    assert _runtime_count(db) == PROJECT_TASK_FILES_OWNER_LIMIT


def test_normal_agents_never_count_toward_quota(
    db: SqlitePool, repo: AgentRepo, users: UserRepo
) -> None:
    user_id = _make_user(users, "owner")
    other_id = _make_user(users, "neighbor")
    for i in range(10):
        repo.create(agent_id=f"ag_n_{i}", user_id=user_id, name=f"normal-{i}")
    for i in range(5):
        repo.create(agent_id=f"ag_m_{i}", user_id=other_id, name=f"other-{i}")

    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id=f"ag_q_{i}", name=f"rt-q-{i}"
        )
    with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id="ag_q_8", name="rt-q-8"
        )
    assert excinfo.value.scope == "owner"
    # A config_json claiming the internal kind does not make a row count.
    repo.create(
        agent_id="ag_forged",
        user_id=other_id,
        name="forged",
        config_json='{"runtime_kind": "project_task_files"}',
    )
    assert _runtime_count(db) == PROJECT_TASK_FILES_OWNER_LIMIT
    assert _runtime_count(db, other_id) == 0


def test_quota_is_isolated_per_owner(db: SqlitePool, repo: AgentRepo, users: UserRepo) -> None:
    owner_a = _make_user(users, "owner_a")
    owner_b = _make_user(users, "owner_b")
    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
        repo.create_project_task_runtime_with_quota(
            user_id=owner_a, agent_id=f"ag_a_{i}", name=f"rt-a-{i}"
        )
    # owner_a is full, but owner_b still has every seat available.
    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
        repo.create_project_task_runtime_with_quota(
            user_id=owner_b, agent_id=f"ag_b_{i}", name=f"rt-b-{i}"
        )
    with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
        repo.create_project_task_runtime_with_quota(
            user_id=owner_a, agent_id="ag_a_full", name="rt-a-full"
        )
    assert excinfo.value.scope == "owner"
    assert excinfo.value.current == PROJECT_TASK_FILES_OWNER_LIMIT
    assert _runtime_count(db, owner_a) == PROJECT_TASK_FILES_OWNER_LIMIT
    assert _runtime_count(db, owner_b) == PROJECT_TASK_FILES_OWNER_LIMIT
    assert repo.get("ag_a_full") is None


# ---------------------------------------------------------------------------
# Global limit boundary: exactly 64 allowed, 65th refused
# ---------------------------------------------------------------------------


def test_global_limit_boundary_refuses_sixty_fifth(
    db: SqlitePool, repo: AgentRepo, users: UserRepo
) -> None:
    owner_ids = [_make_user(users, f"owner_{i}") for i in range(8)]
    for owner_id in owner_ids:
        for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
            repo.create_project_task_runtime_with_quota(
                user_id=owner_id, agent_id=f"ag_g_{owner_id}_{i}", name=f"rt-g-{owner_id}-{i}"
            )
    assert _runtime_count(db) == PROJECT_TASK_FILES_GLOBAL_LIMIT == 64

    fresh_owner = _make_user(users, "owner_late")
    with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
        repo.create_project_task_runtime_with_quota(
            user_id=fresh_owner, agent_id="ag_g_overflow", name="rt-g-overflow"
        )
    err = excinfo.value
    assert err.scope == "global"
    assert err.limit == PROJECT_TASK_FILES_GLOBAL_LIMIT == 64
    assert err.current == 64
    assert repo.get("ag_g_overflow") is None
    assert _runtime_count(db) == PROJECT_TASK_FILES_GLOBAL_LIMIT

    # An existing full owner hitting the same call is still refused at the
    # owner scope first — refusals are uniform and leak no seat information.
    with pytest.raises(ProjectTaskFilesQuotaError) as again:
        repo.create_project_task_runtime_with_quota(
            user_id=owner_ids[0], agent_id="ag_g_again", name="rt-g-again"
        )
    assert again.value.scope == "owner"


# ---------------------------------------------------------------------------
# Rollback: refusal or INSERT failure leaves no row and no residue
# ---------------------------------------------------------------------------


def test_refusal_and_insert_failure_leave_no_row(
    db: SqlitePool, repo: AgentRepo, users: UserRepo
) -> None:
    user_id = _make_user(users, "owner")
    repo.create(agent_id="ag_norm", user_id=user_id, name="taken-name")

    # Duplicate per-user name aborts the INSERT inside the quota transaction.
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id="ag_dup_name", name="taken-name"
        )
    assert repo.get("ag_dup_name") is None
    assert _runtime_count(db) == 0

    repo.create_project_task_runtime_with_quota(
        user_id=user_id,
        agent_id="ag_rt_ok",
        name="rt-ok",
    )
    # Duplicate agent_id aborts the same way.
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id="ag_rt_ok", name="rt-fresh"
        )
    assert _runtime_count(db) == 1

    # Quota refusal after a full owner also leaves nothing behind.
    for i in range(1, PROJECT_TASK_FILES_OWNER_LIMIT):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id=f"ag_fill_{i}", name=f"rt-fill-{i}"
        )
    with pytest.raises(ProjectTaskFilesQuotaError):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id="ag_refused", name="rt-refused"
        )
    assert repo.get("ag_refused") is None
    assert _runtime_count(db) == PROJECT_TASK_FILES_OWNER_LIMIT


# ---------------------------------------------------------------------------
# Two-pool races: the last seat goes to exactly one registrant
# ---------------------------------------------------------------------------


def test_two_pools_race_for_last_owner_seat(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    repo = AgentRepo(db)
    users = UserRepo(db)
    user_id = _make_user(users, "racer")
    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT - 1):
        repo.create_project_task_runtime_with_quota(
            user_id=user_id, agent_id=f"ag_r_{i}", name=f"rt-r-{i}"
        )

    second = SqlitePool(db.path)
    second_repo = AgentRepo(second)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        # Pool A takes the write lock, registers the 8th row directly, while
        # pool B is already queued on BEGIN IMMEDIATE. When B finally runs its
        # serialized count, it must see A's committed row and refuse.
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO agents("
                "agent_id, user_id, name, runtime_kind, created_at, updated_at) "
                "VALUES ('ag_direct_last', ?, 'rt-direct-last', 'project_task_files', 0, 0)",
                (user_id,),
            )
            future = executor.submit(
                lambda: second_repo.create_project_task_runtime_with_quota(
                    user_id=user_id, agent_id="ag_race", name="rt-race"
                )
            )
        with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
            future.result(timeout=15)
    finally:
        executor.shutdown(wait=True)
        second.close()
        db.close()

    assert excinfo.value.scope == "owner"
    assert excinfo.value.current == PROJECT_TASK_FILES_OWNER_LIMIT
    reopened = SqlitePool(tmp_path / "octop.db")
    try:
        assert AgentRepo(reopened).get("ag_race") is None
        assert _runtime_count(reopened, user_id) == PROJECT_TASK_FILES_OWNER_LIMIT
    finally:
        reopened.close()


def test_two_pools_race_for_last_global_seat(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    users = UserRepo(db)
    owner_ids = [_make_user(users, f"owner_{i}") for i in range(8)]
    # 7 owners x 8 + 1 owner x 7 = 63 registered rows: one global seat left.
    for index, owner_id in enumerate(owner_ids):
        seats = PROJECT_TASK_FILES_OWNER_LIMIT if index < 7 else 7
        for i in range(seats):
            _seed_internal_row(
                db, agent_id=f"ag_s_{owner_id}_{i}", user_id=owner_id, name=f"rt-s-{owner_id}-{i}"
            )
    assert _runtime_count(db) == PROJECT_TASK_FILES_GLOBAL_LIMIT - 1

    second = SqlitePool(db.path)
    second_repo = AgentRepo(second)
    late_owner = _make_user(users, "owner_late")
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO agents("
                "agent_id, user_id, name, runtime_kind, created_at, updated_at) "
                "VALUES ('ag_direct_global', ?, 'rt-direct-global', "
                "'project_task_files', 0, 0)",
                (owner_ids[7],),
            )
            future = executor.submit(
                lambda: second_repo.create_project_task_runtime_with_quota(
                    user_id=late_owner, agent_id="ag_race_global", name="rt-race-global"
                )
            )
        with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
            future.result(timeout=15)
    finally:
        executor.shutdown(wait=True)
        second.close()
        db.close()

    # The late owner had free owner seats; only the global limit refused it.
    assert excinfo.value.scope == "global"
    assert excinfo.value.current == PROJECT_TASK_FILES_GLOBAL_LIMIT
    reopened = SqlitePool(tmp_path / "octop.db")
    try:
        assert AgentRepo(reopened).get("ag_race_global") is None
        assert _runtime_count(reopened) == PROJECT_TASK_FILES_GLOBAL_LIMIT
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# PostgreSQL lock/transaction contract (static fake pool — no live PG here)
# ---------------------------------------------------------------------------


class _PgCursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return [] if self._row is None else [self._row]


class _PgConn:
    """Records every statement; answers COUNTs from a prepared queue."""

    def __init__(self, counts: list[int]) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.counts = list(counts)

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> _PgCursor:
        self.statements.append((sql, params))
        if "COUNT(*)" in sql:
            return _PgCursor({"n": self.counts.pop(0)})
        return _PgCursor()


class _PgPool:
    dialect = "postgresql"

    def __init__(self, conn: _PgConn) -> None:
        self._conn = conn

    @contextmanager
    def transaction(self) -> Iterator[_PgConn]:
        yield self._conn


def _indexes(statements: list[str], needle: str) -> list[int]:
    return [i for i, sql in enumerate(statements) if needle in sql]


def test_pg_fixed_advisory_lock_then_counts_then_single_insert() -> None:
    conn = _PgConn(counts=[0, 0])
    repo = AgentRepo(cast(Any, _PgPool(conn)))

    returned = repo.create_project_task_runtime_with_quota(
        user_id=7, agent_id="ag_pg_ok", name="rt-pg-ok"
    )

    assert returned == "ag_pg_ok"
    stmts = [sql for sql, _ in conn.statements]
    lock_idx = _indexes(stmts, "pg_advisory_xact_lock")
    count_idx = _indexes(stmts, "COUNT(*)")
    insert_idx = _indexes(stmts, "INSERT INTO agents")
    # Exactly one fixed transaction-level lock, acquired first. The xact
    # variant is released at commit/rollback; no session-level lock may leak.
    assert len(lock_idx) == 1 and lock_idx[0] == 0
    assert all("pg_advisory_lock(" not in sql for sql in stmts)
    assert conn.statements[lock_idx[0]][1] == (_PG_RUNTIME_QUOTA_LOCK_KEY,)
    # Owner count, then global count, then the single INSERT — in order.
    assert len(count_idx) == 2 and len(insert_idx) == 1
    owner_i, global_i = count_idx
    assert lock_idx[0] < owner_i < global_i < insert_idx[0]
    assert "runtime_kind = 'project_task_files'" in stmts[owner_i]
    assert "user_id = ?" in stmts[owner_i]
    assert conn.statements[owner_i][1] == (7,)
    assert "runtime_kind = 'project_task_files'" in stmts[global_i]
    assert "user_id" not in stmts[global_i]
    assert conn.statements[global_i][1] == ()
    # The internal kind is hard-coded in SQL, not passed as data.
    assert "'project_task_files'" in stmts[insert_idx[0]]
    params = conn.statements[insert_idx[0]][1]
    assert params is not None
    assert "project_task_files" not in params


def test_pg_owner_refusal_happens_inside_transaction_before_insert() -> None:
    conn = _PgConn(counts=[PROJECT_TASK_FILES_OWNER_LIMIT])
    repo = AgentRepo(cast(Any, _PgPool(conn)))

    with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
        repo.create_project_task_runtime_with_quota(
            user_id=7, agent_id="ag_pg_owner", name="rt-pg-owner"
        )

    assert excinfo.value.scope == "owner"
    assert excinfo.value.current == PROJECT_TASK_FILES_OWNER_LIMIT
    stmts = [sql for sql, _ in conn.statements]
    # The lock was taken (refusal happens inside the serialized transaction),
    # the owner count short-circuited before the global count, no INSERT ran.
    assert _indexes(stmts, "pg_advisory_xact_lock") == [0]
    assert len(_indexes(stmts, "COUNT(*)")) == 1
    assert _indexes(stmts, "INSERT INTO agents") == []


def test_pg_global_refusal_after_owner_count_passes() -> None:
    conn = _PgConn(counts=[0, PROJECT_TASK_FILES_GLOBAL_LIMIT])
    repo = AgentRepo(cast(Any, _PgPool(conn)))

    with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
        repo.create_project_task_runtime_with_quota(
            user_id=7, agent_id="ag_pg_global", name="rt-pg-global"
        )

    assert excinfo.value.scope == "global"
    assert excinfo.value.current == PROJECT_TASK_FILES_GLOBAL_LIMIT
    stmts = [sql for sql, _ in conn.statements]
    assert _indexes(stmts, "pg_advisory_xact_lock") == [0]
    assert len(_indexes(stmts, "COUNT(*)")) == 2
    assert _indexes(stmts, "INSERT INTO agents") == []
