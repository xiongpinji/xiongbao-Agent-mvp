"""030A B3 files-mode task creation — repo, agents-repo and service units.

RED→GREEN coverage for the B3 binding slice:

* ``ProjectTaskRepo.create_files_with_context`` — ONE transaction re-checking
  membership, archive state, the instruction digest, the 029 expert gate, the
  source Agent's ``runtime_kind`` and the internal runtime row (existence,
  kind, owner) before inserting thread/projection/link/context with the 028
  ``mode='files'`` columns. Every refusal leaves zero rows behind; the 028
  unique partial index on ``runtime_agent_id`` makes a concurrent double bind
  a classified ``invalid_runtime`` outcome, never a corrupt task.
* ``runtime_kind`` refusals on the legacy chat paths (``create_with_context``
  source check and ``attach`` thread check) — a DB-marked internal runtime is
  never a valid source or attach target even when the caller knows its id.
* ``AgentRepo.count_project_task_runtimes`` and the two conditional delete
  methods used by compensation and the dedicated full-task delete.
* Owner-only summary projection: owner cards carry ``mode`` /
  ``chat_agent_id`` / ``source_expert_id`` and always show the SOURCE expert
  as ``agent_id``; reader (shared) cards carry ``mode`` only and never the
  runtime id.
* ``ProjectTaskService.precheck_files_task`` / ``bind_files_task`` mapping
  onto the stable 030A error codes, and the i18n/error-code contract
  (422 UNSUPPORTED, 503 UNAVAILABLE, 409 QUOTA).

PostgreSQL lock order is asserted with the same static dual-dialect fake the
029 tests use — live-PG transactional behavior stays UNVERIFIED here.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from octop.i18n import error_message
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import (
    PROJECT_TASK_FILES_GLOBAL_LIMIT,
    PROJECT_TASK_FILES_OWNER_LIMIT,
    AgentRepo,
    _ProjectTaskRowsGuard,
)
from octop.infra.db.repos.project_task_shares import ProjectTaskShareRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.tasks import (
    ProjectTaskService,
    files_quota_error,
    files_unavailable_error,
    files_unsupported_error,
    instructions_sha256,
)

INSTRUCTIONS = "项目指令：整理 /data 下的报表文件。"
RUNTIME_ID = "ptfruntime00000001"
SOURCE_ID = "ag_src_expert"

I18N_DIR = Path(__file__).resolve().parents[3] / "src/octop/i18n"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> ProjectTaskRepo:
    return ProjectTaskRepo(db)


@pytest.fixture
def agents(db: SqlitePool) -> AgentRepo:
    return AgentRepo(db)


@pytest.fixture
def shares(db: SqlitePool) -> ProjectTaskShareRepo:
    return ProjectTaskShareRepo(db)


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
    return users.create(username="fowner", password_hash="h", role="user")


@pytest.fixture
def member_id(users: UserRepo) -> int:
    return users.create(username="fmember", password_hash="h", role="user")


@pytest.fixture
def reader_id(users: UserRepo) -> int:
    return users.create(username="freader", password_hash="h", role="user")


@pytest.fixture
def outsider_id(users: UserRepo) -> int:
    return users.create(username="foutsider", password_hash="h", role="user")


@pytest.fixture
def pid(projects: ProjectRepo, owner_id: int, member_id: int, reader_id: int) -> str:
    project = projects.create_with_owner(creator_user_id=owner_id, name="文件任务项目")
    projects.add_member(project.project_id, member_id, role="member")
    projects.add_member(project.project_id, reader_id, role="member")
    return project.project_id


class _StubServices:
    def __init__(
        self,
        projects: ProjectRepo,
        tasks: ProjectTaskRepo,
        shares: ProjectTaskShareRepo,
        threads: ThreadRepo,
        agents: AgentRepo,
    ) -> None:
        self.project_repo = projects
        self.project_task_repo = tasks
        self.project_task_share_repo = shares
        self.thread_repo = threads
        self.agent_repo = agents


@pytest.fixture
def service(
    projects: ProjectRepo,
    repo: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    db: SqlitePool,
    threads: ThreadRepo,
    agents: AgentRepo,
) -> ProjectTaskService:
    return ProjectTaskService(_StubServices(projects, repo, shares, threads, agents))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_agent(
    db: SqlitePool,
    *,
    agent_id: str,
    user_id: int,
    kind: str = "expert",
    runtime_kind: str = "standard",
    shared: bool = False,
    enabled: bool = True,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, kind, runtime_kind, is_shared, "
            "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (
                agent_id,
                user_id,
                agent_id,
                kind,
                runtime_kind,
                1 if shared else 0,
                1 if enabled else 0,
            ),
        )


def _seed_runtime(db: SqlitePool, *, agent_id: str = RUNTIME_ID, user_id: int) -> None:
    _seed_agent(db, agent_id=agent_id, user_id=user_id, runtime_kind="project_task_files")


def _dashboard_key(agent_id: str, user_id: int) -> str:
    return f"{agent_id}:dashboard:{user_id}:dm"


def _set_instructions(projects: ProjectRepo, pid: str, owner_id: int, text: str) -> None:
    assert projects.update_project(pid, actor_user_id=owner_id, instructions=text) is not None


def _set_archived(db: SqlitePool, pid: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (pid,))


def _revision(db: SqlitePool, pid: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT experts_revision FROM project_spaces WHERE project_id = ?", (pid,)
        ).fetchone()
    return int(row["experts_revision"])


def _set_experts(
    projects: ProjectRepo, db: SqlitePool, pid: str, owner_id: int, agent_ids: list[str]
) -> int:
    mutation = projects.replace_experts(
        project_id=pid,
        actor_user_id=owner_id,
        expected_revision=_revision(db, pid),
        agent_ids=agent_ids,
    )
    assert mutation.outcome in ("replaced", "unchanged"), mutation.outcome
    return mutation.revision


def _raw_list_expert(db: SqlitePool, pid: str, owner_id: int, agent_id: str) -> None:
    """Force an agent into the expert list bypassing replace_experts checks."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_experts(project_id, agent_id, sort_order, added_by, added_at) "
            "VALUES (?, ?, 0, ?, 0)",
            (pid, agent_id, owner_id),
        )


def _row_count(db: SqlitePool, table: str, where: str, params: tuple[object, ...]) -> int:
    with db.connect() as conn:
        return int(
            conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]
        )


def _assert_no_task_rows(db: SqlitePool, user_id: int, thread_id: str = "") -> None:
    where = "user_id = ?" if not thread_id else "thread_id = ?"
    param: tuple[object, ...] = (user_id,) if not thread_id else (thread_id,)
    assert _row_count(db, "threads", where, param) == 0
    if thread_id:
        assert _row_count(db, "project_task_links", "thread_id = ?", (thread_id,)) == 0
        assert _row_count(db, "project_task_contexts", "thread_id = ?", (thread_id,)) == 0
        assert _row_count(db, "thread_history_projection", "thread_id = ?", (thread_id,)) == 0


def _create_files(
    repo: ProjectTaskRepo,
    *,
    pid: str,
    member_id: int,
    thread_id: str = "thr_files",
    source_agent_id: str = SOURCE_ID,
    runtime_agent_id: str = RUNTIME_ID,
    digest: str | None = None,
    expected_experts_revision: int | None = None,
) -> Any:
    return repo.create_files_with_context(
        project_id=pid,
        user_id=member_id,
        source_agent_id=source_agent_id,
        runtime_agent_id=runtime_agent_id,
        thread_id=thread_id,
        session_key=_dashboard_key(runtime_agent_id, member_id),
        expected_instructions_sha256=(
            instructions_sha256(INSTRUCTIONS) if digest is None else digest
        ),
        expected_experts_revision=expected_experts_revision,
    )


def _seed_session(db: SqlitePool, *, agent_id: str, user_id: int, thread_id: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO sessions(session_key, agent_id, user_id, channel_type, chat_type, "
            "thread_id, updated_at) VALUES (?, ?, ?, 'dashboard', 'dm', ?, 0)",
            (_dashboard_key(agent_id, user_id), agent_id, user_id, thread_id),
        )


def _grant_share_and_text(
    shares: ProjectTaskShareRepo,
    db: SqlitePool,
    *,
    pid: str,
    thread_id: str,
    actor_id: int,
    grantee_id: int,
) -> None:
    mutation = shares.grant(
        project_id=pid, thread_id=thread_id, actor_user_id=actor_id, grantee_user_id=grantee_id
    )
    assert mutation.outcome == "created", mutation.outcome
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_content_grants(project_id, thread_id, grantee_user_id, "
            "granted_by_user_id, granted_at) VALUES (?, ?, ?, ?, 0)",
            (pid, thread_id, grantee_id, actor_id),
        )


# ---------------------------------------------------------------------------
# AgentRepo: quota counts + conditional deletes
# ---------------------------------------------------------------------------


def test_count_project_task_runtimes_owner_and_global(
    db: SqlitePool, agents: AgentRepo, owner_id: int, member_id: int
) -> None:
    _seed_agent(db, agent_id="ag_std", user_id=member_id)
    _seed_runtime(db, agent_id="rt_m1", user_id=member_id)
    _seed_runtime(db, agent_id="rt_m2", user_id=member_id)
    _seed_runtime(db, agent_id="rt_o1", user_id=owner_id)
    assert agents.count_project_task_runtimes(user_id=member_id) == 2
    assert agents.count_project_task_runtimes(user_id=owner_id) == 1
    assert agents.count_project_task_runtimes() == 3


def test_delete_runtime_if_unreferenced_removes_unlinked_internal_row(
    db: SqlitePool, agents: AgentRepo, member_id: int
) -> None:
    _seed_runtime(db, user_id=member_id)
    assert agents.delete_project_task_runtime_if_unreferenced(RUNTIME_ID) is True
    assert agents.get(RUNTIME_ID) is None


def test_delete_runtime_if_unreferenced_refuses_referenced_internal_row(
    db: SqlitePool, agents: AgentRepo, threads: ThreadRepo, member_id: int
) -> None:
    _seed_runtime(db, user_id=member_id)
    threads.insert(
        thread_id="thr_linked",
        agent_id=RUNTIME_ID,
        user_id=member_id,
        channel_type="dashboard",
        session_key=_dashboard_key(RUNTIME_ID, member_id),
        title="t",
    )
    assert agents.delete_project_task_runtime_if_unreferenced(RUNTIME_ID) is False
    assert agents.get(RUNTIME_ID) is not None


def test_delete_runtime_if_unreferenced_refuses_standard_row(
    db: SqlitePool, agents: AgentRepo, member_id: int
) -> None:
    _seed_agent(db, agent_id="ag_std", user_id=member_id)
    assert agents.delete_project_task_runtime_if_unreferenced("ag_std") is False
    assert agents.get("ag_std") is not None


def test_delete_runtime_if_unreferenced_unknown_id_is_false(agents: AgentRepo) -> None:
    assert agents.delete_project_task_runtime_if_unreferenced("ptfnope") is False


def test_delete_file_task_rows_removes_everything_for_last_thread(
    db: SqlitePool,
    agents: AgentRepo,
    repo: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    reader_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id)
    assert mutation.outcome == "created"
    thread_id = mutation.summary.thread_id
    _seed_session(db, agent_id=RUNTIME_ID, user_id=member_id, thread_id=thread_id)
    _grant_share_and_text(
        shares, db, pid=pid, thread_id=thread_id, actor_id=member_id, grantee_id=reader_id
    )

    assert (
        agents.delete_project_task_file_task_rows(
            agent_id=RUNTIME_ID, thread_id=thread_id, owner_user_id=member_id
        )
        is True
    )
    assert _row_count(db, "threads", "thread_id = ?", (thread_id,)) == 0
    assert _row_count(db, "sessions", "thread_id = ?", (thread_id,)) == 0
    assert _row_count(db, "project_task_links", "thread_id = ?", (thread_id,)) == 0
    assert _row_count(db, "project_task_contexts", "thread_id = ?", (thread_id,)) == 0
    assert _row_count(db, "project_task_shares", "thread_id = ?", (thread_id,)) == 0
    assert _row_count(db, "project_task_content_grants", "thread_id = ?", (thread_id,)) == 0
    assert _row_count(db, "thread_history_projection", "thread_id = ?", (thread_id,)) == 0
    # The last private thread is gone, so the internal runtime row goes too.
    assert agents.get(RUNTIME_ID) is None


def test_delete_file_task_rows_refuses_unexpected_second_thread(
    db: SqlitePool,
    agents: AgentRepo,
    repo: ProjectTaskRepo,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    """030A B4: a full delete must cover the WHOLE runtime — one thread only.

    An unexpected second thread raises the rollback guard BEFORE any child
    delete; the B3 behavior of removing just the named thread's rows is gone.
    """
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id)
    assert mutation.outcome == "created"
    first = mutation.summary.thread_id
    threads.insert(
        thread_id="thr_second",
        agent_id=RUNTIME_ID,
        user_id=member_id,
        channel_type="dashboard",
        session_key="session-thr_second",
        title="second",
    )
    with pytest.raises(_ProjectTaskRowsGuard):
        agents.delete_project_task_file_task_rows(
            agent_id=RUNTIME_ID, thread_id=first, owner_user_id=member_id
        )
    # Nothing was removed: both threads, the task rows and the runtime stay.
    assert _row_count(db, "threads", "thread_id = ?", (first,)) == 1
    assert threads.get("thr_second") is not None
    assert _row_count(db, "project_task_links", "thread_id = ?", (first,)) == 1
    assert _row_count(db, "project_task_contexts", "thread_id = ?", (first,)) == 1
    assert agents.get(RUNTIME_ID) is not None


class _ZeroRowcountCursor:
    rowcount = 0


class _ConnProxy:
    """Pass-through connection reporting rowcount 0 for the threads DELETE."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Any:
        cursor = self._inner.execute(sql, params)
        if sql.strip().startswith("DELETE FROM threads"):
            return _ZeroRowcountCursor()
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_delete_file_task_rows_rowcount_mismatch_rolls_back_children(
    db: SqlitePool,
    agents: AgentRepo,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    reader_id: int,
    shares: ProjectTaskShareRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """030A B4 (e): an unexpected threads-DELETE rowcount rolls back ALL children.

    The B3 code returned False after the child deletes had already run inside
    the transaction — which COMMITTED them. The guard exception now propagates
    out of ``db.transaction()``, so sessions/grants/shares/contexts/projection/
    links are restored together with the thread and the runtime row.
    """
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id)
    assert mutation.outcome == "created"
    thread_id = mutation.summary.thread_id
    _seed_session(db, agent_id=RUNTIME_ID, user_id=member_id, thread_id=thread_id)
    _grant_share_and_text(
        shares, db, pid=pid, thread_id=thread_id, actor_id=member_id, grantee_id=reader_id
    )

    real_transaction = db.transaction

    @contextmanager
    def proxied() -> Any:
        with real_transaction() as conn:
            yield _ConnProxy(conn)

    monkeypatch.setattr(db, "transaction", proxied)
    with pytest.raises(_ProjectTaskRowsGuard):
        agents.delete_project_task_file_task_rows(
            agent_id=RUNTIME_ID, thread_id=thread_id, owner_user_id=member_id
        )
    assert _row_count(db, "threads", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "sessions", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "project_task_links", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "project_task_contexts", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "project_task_shares", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "project_task_content_grants", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "thread_history_projection", "thread_id = ?", (thread_id,)) == 1
    assert agents.get(RUNTIME_ID) is not None


def test_delete_file_task_rows_guard_rolls_back_on_owner_mismatch(
    db: SqlitePool,
    agents: AgentRepo,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id)
    assert mutation.outcome == "created"
    thread_id = mutation.summary.thread_id
    assert (
        agents.delete_project_task_file_task_rows(
            agent_id=RUNTIME_ID, thread_id=thread_id, owner_user_id=outsider_id
        )
        is False
    )
    # Nothing was removed: the thread, its link and the runtime all survive.
    assert _row_count(db, "threads", "thread_id = ?", (thread_id,)) == 1
    assert _row_count(db, "project_task_links", "thread_id = ?", (thread_id,)) == 1
    assert agents.get(RUNTIME_ID) is not None


def test_delete_file_task_rows_guard_rolls_back_on_agent_mismatch(
    db: SqlitePool,
    agents: AgentRepo,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id)
    assert mutation.outcome == "created"
    thread_id = mutation.summary.thread_id
    assert (
        agents.delete_project_task_file_task_rows(
            agent_id=SOURCE_ID, thread_id=thread_id, owner_user_id=member_id
        )
        is False
    )
    assert _row_count(db, "threads", "thread_id = ?", (thread_id,)) == 1
    assert agents.get(RUNTIME_ID) is not None


# ---------------------------------------------------------------------------
# create_files_with_context — happy path and refusals (SQLite)
# ---------------------------------------------------------------------------


def test_files_create_writes_thread_link_context_projection(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)

    mutation = _create_files(repo, pid=pid, member_id=member_id)

    assert mutation.outcome == "created"
    summary = mutation.summary
    assert summary is not None
    # Owner-facing projection: the card shows the SOURCE expert, never the
    # runtime id as agent_id; the runtime id travels in chat_agent_id only.
    assert summary.agent_id == SOURCE_ID
    assert summary.mode == "files"
    assert summary.chat_agent_id == RUNTIME_ID
    assert summary.source_expert_id == SOURCE_ID
    assert summary.owner_user_id == member_id
    assert summary.source == "project"

    thread = threads.get(summary.thread_id)
    assert thread is not None
    assert thread.agent_id == RUNTIME_ID
    assert thread.user_id == member_id
    assert thread.channel_type == "dashboard"
    assert thread.session_key == _dashboard_key(RUNTIME_ID, member_id)

    with db.connect() as conn:
        ctx = conn.execute(
            "SELECT mode, source_expert_id, runtime_agent_id, instructions_snapshot, "
            "instructions_sha256 FROM project_task_contexts WHERE thread_id = ?",
            (summary.thread_id,),
        ).fetchone()
        projection = conn.execute(
            "SELECT status FROM thread_history_projection WHERE thread_id = ?",
            (summary.thread_id,),
        ).fetchone()
        link = conn.execute(
            "SELECT source FROM project_task_links WHERE thread_id = ?",
            (summary.thread_id,),
        ).fetchone()
        sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    assert ctx is not None
    assert str(ctx["mode"]) == "files"
    assert str(ctx["source_expert_id"]) == SOURCE_ID
    assert str(ctx["runtime_agent_id"]) == RUNTIME_ID
    assert str(ctx["instructions_snapshot"]) == INSTRUCTIONS
    assert str(ctx["instructions_sha256"]) == instructions_sha256(INSTRUCTIONS)
    assert projection is not None and str(projection["status"]) == "ready"
    assert link is not None and str(link["source"]) == "project"
    # No session rebind: the caller's active dashboard session is untouched.
    assert int(sessions["n"]) == 0


def test_files_create_outsider_is_not_member_without_rows(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=outsider_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=outsider_id, thread_id="thr_out")
    assert mutation.outcome == "not_member"
    assert mutation.summary is None
    _assert_no_task_rows(db, outsider_id)


def test_files_create_archived_project_is_refused(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    _set_archived(db, pid)
    mutation = _create_files(repo, pid=pid, member_id=member_id, thread_id="thr_arch")
    assert mutation.outcome == "archived"
    _assert_no_task_rows(db, member_id, "thr_arch")


def test_files_create_stale_digest_is_refused(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_stale",
        digest=instructions_sha256("别的指令"),
    )
    assert mutation.outcome == "stale_instructions"
    _assert_no_task_rows(db, member_id, "thr_stale")


def test_files_create_stale_experts_revision_is_refused(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    revision = _set_experts(projects, db, pid, owner_id, [SOURCE_ID])
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_rev",
        expected_experts_revision=revision + 1,
    )
    assert mutation.outcome == "stale_experts"
    _assert_no_task_rows(db, member_id, "thr_rev")


def test_files_create_unlisted_source_when_gated_is_invalid_expert(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_agent(db, agent_id="ag_listed", user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    revision = _set_experts(projects, db, pid, owner_id, ["ag_listed"])
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_unlisted",
        expected_experts_revision=revision,
    )
    assert mutation.outcome == "invalid_expert"
    _assert_no_task_rows(db, member_id, "thr_unlisted")


def test_files_create_gated_with_listed_source_records_revision(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    revision = _set_experts(projects, db, pid, owner_id, [SOURCE_ID])
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_gated",
        expected_experts_revision=revision,
    )
    assert mutation.outcome == "created"
    with db.connect() as conn:
        ctx = conn.execute(
            "SELECT expert_selection_revision FROM project_task_contexts WHERE thread_id = ?",
            ("thr_gated",),
        ).fetchone()
    assert ctx is not None and int(ctx["expert_selection_revision"]) == revision


def test_files_create_unknown_runtime_is_invalid_runtime(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(
        repo, pid=pid, member_id=member_id, thread_id="thr_norun", runtime_agent_id="ptfgone"
    )
    assert mutation.outcome == "invalid_runtime"
    _assert_no_task_rows(db, member_id, "thr_norun")


def test_files_create_standard_kind_runtime_is_invalid_runtime(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_agent(db, agent_id="ag_std_run", user_id=member_id)  # standard kind
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_std",
        runtime_agent_id="ag_std_run",
    )
    assert mutation.outcome == "invalid_runtime"
    _assert_no_task_rows(db, member_id, "thr_std")


def test_files_create_foreign_owner_runtime_is_invalid_runtime(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=owner_id)  # runtime belongs to somebody else
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id, thread_id="thr_foreign")
    assert mutation.outcome == "invalid_runtime"
    _assert_no_task_rows(db, member_id, "thr_foreign")
    # The refused runtime row is untouched — cleanup is the caller's job.
    assert _row_count(db, "agents", "agent_id = ?", (RUNTIME_ID,)) == 1


def test_files_create_internal_source_is_refused_ungated(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_runtime(db, agent_id="rt_src", user_id=owner_id)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_int_src",
        source_agent_id="rt_src",
    )
    assert mutation.outcome == "invalid_agent"
    _assert_no_task_rows(db, member_id, "thr_int_src")


def test_files_create_internal_source_is_refused_even_when_listed(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    # Even a hand-forced expert-list entry cannot launder an internal runtime
    # into a source: the transaction re-checks runtime_kind.
    _seed_agent(
        db, agent_id="rt_src", user_id=owner_id, runtime_kind="project_task_files", shared=True
    )
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    _raw_list_expert(db, pid, owner_id, "rt_src")
    revision = _revision(db, pid)
    mutation = _create_files(
        repo,
        pid=pid,
        member_id=member_id,
        thread_id="thr_listed_int",
        source_agent_id="rt_src",
        expected_experts_revision=revision,
    )
    assert mutation.outcome == "invalid_expert"
    _assert_no_task_rows(db, member_id, "thr_listed_int")


def test_files_create_double_bind_hits_unique_index_as_invalid_runtime(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    """The 028 unique partial index forbids binding one runtime twice; the
    loser is classified invalid_runtime and rolls back completely."""
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    first = _create_files(repo, pid=pid, member_id=member_id, thread_id="thr_a")
    assert first.outcome == "created"
    second = _create_files(repo, pid=pid, member_id=member_id, thread_id="thr_b")
    assert second.outcome == "invalid_runtime"
    _assert_no_task_rows(db, member_id, "thr_b")
    # The winner is untouched.
    assert _row_count(db, "threads", "thread_id = ?", ("thr_a",)) == 1
    assert _row_count(db, "project_task_contexts", "thread_id = ?", ("thr_a",)) == 1


# ---------------------------------------------------------------------------
# Legacy chat paths refuse DB-marked internal runtimes
# ---------------------------------------------------------------------------


def test_chat_create_refuses_internal_source_ungated(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_runtime(db, agent_id="rt_src", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = repo.create_with_context(
        project_id=pid,
        user_id=member_id,
        agent_id="rt_src",
        thread_id="thr_chat_int",
        session_key=_dashboard_key("rt_src", member_id),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )
    assert mutation.outcome == "invalid_agent"
    _assert_no_task_rows(db, member_id, "thr_chat_int")


def test_chat_create_refuses_internal_source_gated(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(
        db, agent_id="rt_src", user_id=owner_id, runtime_kind="project_task_files", shared=True
    )
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    _raw_list_expert(db, pid, owner_id, "rt_src")
    mutation = repo.create_with_context(
        project_id=pid,
        user_id=member_id,
        agent_id="rt_src",
        thread_id="thr_chat_int2",
        session_key=_dashboard_key("rt_src", member_id),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        expected_experts_revision=_revision(db, pid),
    )
    assert mutation.outcome == "invalid_expert"
    _assert_no_task_rows(db, member_id, "thr_chat_int2")


def test_attach_refuses_internal_runtime_thread(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    threads: ThreadRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_runtime(db, user_id=member_id)
    threads.insert(
        thread_id="thr_rt_dm",
        agent_id=RUNTIME_ID,
        user_id=member_id,
        channel_type="dashboard",
        session_key=_dashboard_key(RUNTIME_ID, member_id),
        title="runtime dm",
    )
    mutation = repo.attach(project_id=pid, user_id=member_id, thread_id="thr_rt_dm")
    assert mutation.outcome == "invalid_thread"
    assert _row_count(db, "project_task_links", "thread_id = ?", ("thr_rt_dm",)) == 0


def test_attach_refuses_already_linked_files_thread(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    """Manual attach of a files task thread is refused uniformly (404 shape),
    not answered as an idempotent duplicate."""
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id, thread_id="thr_linked_files")
    assert mutation.outcome == "created"
    again = repo.attach(project_id=pid, user_id=member_id, thread_id="thr_linked_files")
    assert again.outcome == "invalid_thread"


def test_service_attach_and_authorize_refuse_internal_runtime(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    pid: str,
    member_id: int,
) -> None:
    _seed_runtime(db, user_id=member_id)
    threads.insert(
        thread_id="thr_rt_svc",
        agent_id=RUNTIME_ID,
        user_id=member_id,
        channel_type="dashboard",
        session_key=_dashboard_key(RUNTIME_ID, member_id),
        title="runtime dm",
    )
    with pytest.raises(OctopError) as exc_info:
        service.authorize_attach_target(pid, user_id=member_id, thread_id="thr_rt_svc")
    assert exc_info.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as exc_info:
        service.attach_task(pid, user_id=member_id, thread_id="thr_rt_svc")
    assert exc_info.value.code == ErrorCode.NOT_FOUND
    assert _row_count(db, "project_task_links", "thread_id = ?", ("thr_rt_svc",)) == 0


# ---------------------------------------------------------------------------
# Summary projection: owner sees runtime binding, readers never do
# ---------------------------------------------------------------------------


def test_owner_and_reader_summary_projection(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    reader_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = _create_files(repo, pid=pid, member_id=member_id)
    assert mutation.outcome == "created"
    thread_id = mutation.summary.thread_id
    _grant_share_and_text(
        shares, db, pid=pid, thread_id=thread_id, actor_id=member_id, grantee_id=reader_id
    )

    own = repo.get_for_owner(pid, thread_id, user_id=member_id)
    assert own is not None
    assert own.agent_id == SOURCE_ID
    assert own.mode == "files"
    assert own.chat_agent_id == RUNTIME_ID
    assert own.source_expert_id == SOURCE_ID

    listed = repo.list_for_owner(pid, user_id=member_id, limit=5)
    assert len(listed) == 1
    assert listed[0].chat_agent_id == RUNTIME_ID
    assert listed[0].mode == "files"
    assert listed[0].agent_id == SOURCE_ID

    shared_list = shares.list_shared(pid, user_id=reader_id, limit=5)
    assert len(shared_list) == 1
    reader_card = shared_list[0]
    assert reader_card.access == "reader"
    assert reader_card.agent_id == SOURCE_ID
    assert reader_card.mode == "files"
    # The runtime identity never reaches a reader card.
    assert reader_card.chat_agent_id is None
    assert reader_card.source_expert_id is None

    detail = shares.get_shared_summary(pid, thread_id, user_id=reader_id)
    assert detail is not None
    assert detail.agent_id == SOURCE_ID
    assert detail.chat_agent_id is None
    assert detail.source_expert_id is None

    merged = shares.list_all(pid, user_id=reader_id, limit=5)
    assert [row.thread_id for row in merged] == [thread_id]
    assert merged[0].chat_agent_id is None
    assert merged[0].source_expert_id is None
    assert merged[0].mode == "files"

    owner_merged = shares.list_all(pid, user_id=member_id, limit=5)
    assert len(owner_merged) == 1
    assert owner_merged[0].access == "owner"
    assert owner_merged[0].chat_agent_id == RUNTIME_ID
    assert owner_merged[0].source_expert_id == SOURCE_ID
    assert owner_merged[0].mode == "files"


def test_chat_task_projection_stays_backward_compatible(
    db: SqlitePool,
    repo: ProjectTaskRepo,
    shares: ProjectTaskShareRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    reader_id: int,
) -> None:
    _seed_agent(db, agent_id="ag_member", user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    mutation = repo.create_with_context(
        project_id=pid,
        user_id=member_id,
        agent_id="ag_member",
        thread_id="thr_chat",
        session_key=_dashboard_key("ag_member", member_id),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )
    assert mutation.outcome == "created"
    assert mutation.summary is not None
    assert mutation.summary.mode == "chat"
    assert mutation.summary.agent_id == "ag_member"
    assert mutation.summary.chat_agent_id == "ag_member"
    assert mutation.summary.source_expert_id is None

    _grant_share_and_text(
        shares, db, pid=pid, thread_id="thr_chat", actor_id=member_id, grantee_id=reader_id
    )
    shared_list = shares.list_shared(pid, user_id=reader_id, limit=5)
    assert len(shared_list) == 1
    assert shared_list[0].mode == "chat"
    assert shared_list[0].agent_id == "ag_member"
    assert shared_list[0].chat_agent_id is None
    assert shared_list[0].source_expert_id is None

    # A manual (021) attach still works and projects mode='chat'.
    db2_threads = ThreadRepo(db)
    db2_threads.insert(
        thread_id="thr_manual_chat",
        agent_id="ag_member",
        user_id=member_id,
        channel_type="dashboard",
        session_key=_dashboard_key("ag_member", member_id),
        title="manual",
    )
    manual = repo.attach(project_id=pid, user_id=member_id, thread_id="thr_manual_chat")
    assert manual.outcome == "created"
    assert manual.summary is not None
    assert manual.summary.mode == "chat"
    assert manual.summary.chat_agent_id == "ag_member"


# ---------------------------------------------------------------------------
# PostgreSQL lock order (static fake; live-PG behavior UNVERIFIED)
# ---------------------------------------------------------------------------


class _PgCursor:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows


class _FilesPgConn:
    """Routes by statement text; distinguishes source/runtime by params."""

    def __init__(
        self,
        *,
        member_role: str,
        project: dict[str, Any],
        expert_rows: list[dict[str, Any]],
        source_row: dict[str, Any],
        runtime_row: dict[str, Any] | None,
        runtime_id: str,
    ) -> None:
        self.statements: list[tuple[str, Any]] = []
        self._member_role = member_role
        self._project = project
        self._expert_rows = expert_rows
        self._source_row = source_row
        self._runtime_row = runtime_row
        self._runtime_id = runtime_id

    def execute(self, sql: str, params: Any = None) -> _PgCursor:
        self.statements.append((sql, params))
        if "FROM project_members" in sql:
            return _PgCursor(row={"role": self._member_role})
        if "FROM project_spaces" in sql:
            return _PgCursor(row=self._project)
        if "FROM project_experts" in sql:
            return _PgCursor(rows=self._expert_rows)
        if "FROM agents" in sql:
            agent_id = params[0] if params else None
            if agent_id == self._runtime_id:
                return _PgCursor(row=self._runtime_row)
            return _PgCursor(row=self._source_row)
        return _PgCursor()


class _PgTxn:
    def __init__(self, conn: _FilesPgConn) -> None:
        self._conn = conn

    def __enter__(self) -> _FilesPgConn:
        return self._conn

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _PgPool:
    def __init__(self, conn: _FilesPgConn) -> None:
        self.dialect = "postgresql"
        self.conn = conn

    def transaction(self) -> _PgTxn:
        return _PgTxn(self.conn)

    def connect(self) -> _PgTxn:
        return _PgTxn(self.conn)

    def close(self) -> None:
        pass


def _first_index(statements: list[tuple[str, Any]], needle: str) -> int:
    return next(i for i, (s, _) in enumerate(statements) if needle in s)


def test_postgres_files_create_locks_member_project_source_then_runtime() -> None:
    """PG lock order: member FOR SHARE → project FOR SHARE → experts →
    source agent FOR SHARE → runtime agent FOR SHARE → inserts.
    Static dual-dialect assertion only — no live PostgreSQL."""
    conn = _FilesPgConn(
        member_role="member",
        project={"instructions": INSTRUCTIONS, "archived": 0, "experts_revision": 1},
        expert_rows=[{"agent_id": SOURCE_ID}],
        source_row={
            "kind": "expert",
            "user_id": 9,
            "is_shared": 1,
            "enabled": 1,
            "runtime_kind": "standard",
        },
        runtime_row={"user_id": 9, "runtime_kind": "project_task_files"},
        runtime_id=RUNTIME_ID,
    )
    repo = ProjectTaskRepo(cast(Any, _PgPool(conn)))
    mutation = repo.create_files_with_context(
        project_id="p1",
        user_id=9,
        source_agent_id=SOURCE_ID,
        runtime_agent_id=RUNTIME_ID,
        thread_id="thr_pg_files",
        session_key=_dashboard_key(RUNTIME_ID, 9),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        expected_experts_revision=1,
    )
    assert mutation.outcome == "created"
    stmts = conn.statements
    i_member = _first_index(stmts, "FROM project_members")
    i_project = _first_index(stmts, "FROM project_spaces")
    i_experts = _first_index(stmts, "FROM project_experts")
    agent_indexes = [i for i, (s, _) in enumerate(stmts) if "FROM agents" in s]
    assert len(agent_indexes) == 2
    i_source, i_runtime = agent_indexes
    i_insert = _first_index(stmts, "INSERT INTO threads")
    assert stmts[i_member][0].endswith("FOR SHARE")
    assert stmts[i_project][0].endswith("FOR SHARE")
    assert stmts[i_source][0].endswith("FOR SHARE")
    assert stmts[i_runtime][0].endswith("FOR SHARE")
    assert stmts[i_source][1][0] == SOURCE_ID
    assert stmts[i_runtime][1][0] == RUNTIME_ID
    assert i_member < i_project < i_experts < i_source < i_runtime < i_insert
    context_insert = stmts[_first_index(stmts, "INSERT INTO project_task_contexts")][0]
    assert "mode" in context_insert
    assert "runtime_agent_id" in context_insert


def test_postgres_files_create_runtime_kind_mismatch_is_invalid_runtime() -> None:
    conn = _FilesPgConn(
        member_role="member",
        project={"instructions": INSTRUCTIONS, "archived": 0, "experts_revision": 0},
        expert_rows=[],
        source_row={
            "kind": "expert",
            "user_id": 9,
            "is_shared": 1,
            "enabled": 1,
            "runtime_kind": "standard",
        },
        runtime_row={"user_id": 9, "runtime_kind": "standard"},  # not internal
        runtime_id=RUNTIME_ID,
    )
    repo = ProjectTaskRepo(cast(Any, _PgPool(conn)))
    mutation = repo.create_files_with_context(
        project_id="p1",
        user_id=9,
        source_agent_id=SOURCE_ID,
        runtime_agent_id=RUNTIME_ID,
        thread_id="thr_pg_bad",
        session_key=_dashboard_key(RUNTIME_ID, 9),
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )
    assert mutation.outcome == "invalid_runtime"
    assert not any("INSERT INTO threads" in s for s, _ in conn.statements)


# ---------------------------------------------------------------------------
# Service: precheck_files_task / bind_files_task
# ---------------------------------------------------------------------------


def test_precheck_files_task_member_ok(
    service: ProjectTaskService, projects: ProjectRepo, pid: str, owner_id: int, member_id: int
) -> None:
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    # No exception, no side effects.
    service.precheck_files_task(
        pid, user_id=member_id, expected_instructions_sha256=instructions_sha256(INSTRUCTIONS)
    )


def test_precheck_files_task_outsider_project_not_found(
    service: ProjectTaskService, projects: ProjectRepo, pid: str, owner_id: int, outsider_id: int
) -> None:
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    with pytest.raises(OctopError) as exc_info:
        service.precheck_files_task(
            pid,
            user_id=outsider_id,
            expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        )
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_precheck_files_task_archived_forbidden(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    _set_archived(db, pid)
    with pytest.raises(OctopError) as exc_info:
        service.precheck_files_task(
            pid, user_id=member_id, expected_instructions_sha256=instructions_sha256(INSTRUCTIONS)
        )
    assert exc_info.value.code == ErrorCode.FORBIDDEN


def test_precheck_files_task_stale_digest(
    service: ProjectTaskService, projects: ProjectRepo, pid: str, owner_id: int, member_id: int
) -> None:
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    with pytest.raises(OctopError) as exc_info:
        service.precheck_files_task(
            pid, user_id=member_id, expected_instructions_sha256=instructions_sha256("过期摘要")
        )
    assert exc_info.value.code == ErrorCode.PROJECT_INSTRUCTIONS_CHANGED


def test_precheck_files_task_owner_quota(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
        _seed_runtime(db, agent_id=f"rt_q{i:02d}", user_id=member_id)
    with pytest.raises(OctopError) as exc_info:
        service.precheck_files_task(
            pid, user_id=member_id, expected_instructions_sha256=instructions_sha256(INSTRUCTIONS)
        )
    assert exc_info.value.code == ErrorCode.PROJECT_TASK_FILES_QUOTA
    assert exc_info.value.details.get("scope") == "owner"
    assert exc_info.value.status == 409


def test_precheck_files_task_global_quota(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    users: UserRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    bulk_id = users.create(username="bulk", password_hash="h", role="user")
    for i in range(PROJECT_TASK_FILES_GLOBAL_LIMIT):
        _seed_runtime(db, agent_id=f"rt_g{i:02d}", user_id=bulk_id)
    with pytest.raises(OctopError) as exc_info:
        service.precheck_files_task(
            pid, user_id=member_id, expected_instructions_sha256=instructions_sha256(INSTRUCTIONS)
        )
    assert exc_info.value.code == ErrorCode.PROJECT_TASK_FILES_QUOTA
    assert exc_info.value.details.get("scope") == "global"


def test_bind_files_task_created_view_fields(
    service: ProjectTaskService,
    db: SqlitePool,
    threads: ThreadRepo,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    view = service.bind_files_task(
        pid,
        user_id=member_id,
        source_agent_id=SOURCE_ID,
        runtime_agent_id=RUNTIME_ID,
        expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
    )
    assert view.mode == "files"
    assert view.agent_id == SOURCE_ID
    assert view.chat_agent_id == RUNTIME_ID
    assert view.source_expert_id == SOURCE_ID
    assert view.access == "owner"
    thread = threads.get(view.thread_id)
    assert thread is not None
    assert thread.agent_id == RUNTIME_ID
    assert thread.session_key == _dashboard_key(RUNTIME_ID, member_id)


def test_bind_files_task_invalid_runtime_maps_unavailable(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    with pytest.raises(OctopError) as exc_info:
        service.bind_files_task(
            pid,
            user_id=member_id,
            source_agent_id=SOURCE_ID,
            runtime_agent_id="ptfmissing",
            expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        )
    assert exc_info.value.code == ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE
    assert exc_info.value.status == 503
    _assert_no_task_rows(db, member_id)


def test_bind_files_task_stale_digest_maps_conflict(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=member_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    with pytest.raises(OctopError) as exc_info:
        service.bind_files_task(
            pid,
            user_id=member_id,
            source_agent_id=SOURCE_ID,
            runtime_agent_id=RUNTIME_ID,
            expected_instructions_sha256=instructions_sha256("过期"),
        )
    assert exc_info.value.code == ErrorCode.PROJECT_INSTRUCTIONS_CHANGED
    _assert_no_task_rows(db, member_id)


def test_bind_files_task_outsider_maps_not_found(
    service: ProjectTaskService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    outsider_id: int,
) -> None:
    _seed_agent(db, agent_id=SOURCE_ID, user_id=owner_id, shared=True)
    _seed_runtime(db, user_id=outsider_id)
    _set_instructions(projects, pid, owner_id, INSTRUCTIONS)
    with pytest.raises(OctopError) as exc_info:
        service.bind_files_task(
            pid,
            user_id=outsider_id,
            source_agent_id=SOURCE_ID,
            runtime_agent_id=RUNTIME_ID,
            expected_instructions_sha256=instructions_sha256(INSTRUCTIONS),
        )
    assert exc_info.value.code == ErrorCode.NOT_FOUND
    _assert_no_task_rows(db, outsider_id)


# ---------------------------------------------------------------------------
# 030A error-code + i18n contract
# ---------------------------------------------------------------------------


def test_files_error_helpers_carry_contract_codes_and_statuses() -> None:
    unsupported = files_unsupported_error()
    assert unsupported.code == ErrorCode.PROJECT_TASK_FILES_UNSUPPORTED
    assert unsupported.status == 422
    unavailable = files_unavailable_error()
    assert unavailable.code == ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE
    assert unavailable.status == 503
    for scope in ("owner", "global"):
        quota = files_quota_error(scope)
        assert quota.code == ErrorCode.PROJECT_TASK_FILES_QUOTA
        assert quota.status == 409
        assert quota.details == {"scope": scope}


@pytest.mark.parametrize("locale", ["en", "zh"])
def test_new_error_codes_have_server_messages(locale: str) -> None:
    for code in (
        "PROJECT_TASK_FILES_UNSUPPORTED",
        "PROJECT_TASK_FILES_UNAVAILABLE",
    ):
        assert error_message(code, locale)
    # The quota message interpolates the hit scope.
    for scope in ("owner", "global"):
        assert error_message("PROJECT_TASK_FILES_QUOTA", locale, scope=scope)


@pytest.mark.parametrize("name", ["en.json", "zh.json"])
def test_new_error_codes_present_in_catalogs(name: str) -> None:
    data = json.loads((I18N_DIR / name).read_text(encoding="utf-8"))
    errors = data["errors"]
    for code in (
        "PROJECT_TASK_FILES_UNSUPPORTED",
        "PROJECT_TASK_FILES_UNAVAILABLE",
        "PROJECT_TASK_FILES_QUOTA",
    ):
        assert code in errors, f"{code} missing from {name}"
