"""Gated live-PostgreSQL tests for 030A B1 (skipped without OCTOP_TEST_DATABASE_URL).

Points at a **dedicated** database — the schema reset drops ``public``. The
static lock/transaction contract is asserted in
``test_project_task_file_runtime_quota.py`` with a fake pool; this file only
covers what genuinely needs a live server: the 028 DDL shape on PostgreSQL,
the fixed advisory-lock quota path end to end, and a two-pool race for the
last owner seat.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import uuid

import pytest
from tests.support.postgresql import requires_postgresql


def _conninfo() -> str:
    return os.environ["OCTOP_TEST_DATABASE_URL"]


def _reset_public_schema(pool: object) -> None:
    with pool.connect() as conn:  # type: ignore[attr-defined]
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
        conn.execute("COMMIT")


def _unique_suffix() -> str:
    return uuid.uuid4().hex[:10]


@requires_postgresql
@pytest.mark.postgresql
def test_pg_028_schema_shape_and_quota_boundaries() -> None:
    from octop.infra.db.migrate import _max_discovered_version, run_migrations
    from octop.infra.db.pool import PostgresPool
    from octop.infra.db.repos.agents import (
        PROJECT_TASK_FILES_OWNER_LIMIT,
        AgentRepo,
        ProjectTaskFilesQuotaError,
    )
    from octop.infra.db.repos.users import UserRepo

    pool = PostgresPool(_conninfo())
    try:
        _reset_public_schema(pool)
        run_migrations(pool)
        with pool.connect() as conn:
            version = conn.execute("SELECT version FROM _schema_version").fetchone()["version"]
            runtime_kind = conn.execute(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'agents' "
                "AND column_name = 'runtime_kind'"
            ).fetchone()
            mode = conn.execute(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'project_task_contexts' "
                "AND column_name = 'mode'"
            ).fetchone()
            check_rows = conn.execute(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid IN "
                "('agents'::regclass, 'project_task_contexts'::regclass)"
            ).fetchall()
            checks = {row["conname"] for row in check_rows}
            index_row = conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = 'idx_project_task_contexts_runtime_agent'"
            ).fetchone()
        assert int(version) == _max_discovered_version("postgresql")
        assert int(version) >= 28
        assert runtime_kind is not None
        assert runtime_kind["is_nullable"] == "NO"
        assert "standard" in str(runtime_kind["column_default"])
        assert mode is not None
        assert mode["is_nullable"] == "NO"
        assert "chat" in str(mode["column_default"])
        assert "agents_runtime_kind_check" in checks
        assert "project_task_contexts_mode_check" in checks
        assert index_row is not None
        indexdef = str(index_row["indexdef"])
        assert "UNIQUE" in indexdef
        assert "WHERE (runtime_agent_id IS NOT NULL)" in indexdef

        users = UserRepo(pool)
        repo = AgentRepo(pool)
        user_id = users.create(
            username=f"pg_owner_{_unique_suffix()}", password_hash="x", role="user"
        )
        for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
            repo.create_project_task_runtime_with_quota(
                user_id=user_id,
                agent_id=f"ag_pg_{i}_{_unique_suffix()}",
                name=f"rt-pg-{i}-{_unique_suffix()}",
            )
        with pytest.raises(ProjectTaskFilesQuotaError) as excinfo:
            repo.create_project_task_runtime_with_quota(
                user_id=user_id,
                agent_id=f"ag_pg_overflow_{_unique_suffix()}",
                name=f"rt-pg-overflow-{_unique_suffix()}",
            )
        assert excinfo.value.scope == "owner"
        assert excinfo.value.current == PROJECT_TASK_FILES_OWNER_LIMIT
        with pool.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM agents WHERE runtime_kind = 'project_task_files' "
                "AND user_id = %s",
                (user_id,),
            ).fetchone()["n"]
        assert int(count) == PROJECT_TASK_FILES_OWNER_LIMIT

        # Public creation still lands on 'standard'; config_json cannot forge it.
        std_id = f"ag_pg_std_{_unique_suffix()}"
        repo.create(
            agent_id=std_id,
            user_id=user_id,
            name=f"pg-normal-{_unique_suffix()}",
            config_json='{"runtime_kind": "project_task_files"}',
        )
        std_row = repo.get(std_id)
        assert std_row is not None
        assert std_row.runtime_kind == "standard"
    finally:
        pool.close()


@requires_postgresql
@pytest.mark.postgresql
def test_pg_two_pools_race_for_last_owner_seat() -> None:
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool
    from octop.infra.db.repos.agents import (
        PROJECT_TASK_FILES_OWNER_LIMIT,
        AgentRepo,
        ProjectTaskFilesQuotaError,
    )
    from octop.infra.db.repos.users import UserRepo

    pool = PostgresPool(_conninfo())
    other = PostgresPool(_conninfo())
    try:
        _reset_public_schema(pool)
        run_migrations(pool)
        users = UserRepo(pool)
        repo = AgentRepo(pool)
        user_id = users.create(
            username=f"pg_race_{_unique_suffix()}", password_hash="x", role="user"
        )
        for i in range(PROJECT_TASK_FILES_OWNER_LIMIT - 1):
            repo.create_project_task_runtime_with_quota(
                user_id=user_id,
                agent_id=f"ag_pg_r{i}_{_unique_suffix()}",
                name=f"rt-pg-r{i}-{_unique_suffix()}",
            )

        barrier = threading.Barrier(2, timeout=30)

        def race(active: PostgresPool, tag: str) -> str:
            racer = AgentRepo(active)
            barrier.wait()
            try:
                racer.create_project_task_runtime_with_quota(
                    user_id=user_id,
                    agent_id=f"ag_pg_{tag}_{_unique_suffix()}",
                    name=f"rt-pg-{tag}-{_unique_suffix()}",
                )
                return "created"
            except ProjectTaskFilesQuotaError as exc:
                assert exc.scope == "owner"
                assert exc.current == PROJECT_TASK_FILES_OWNER_LIMIT
                return "refused"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(race, pool, "a"), executor.submit(race, other, "b")]
            outcomes = [future.result(timeout=60) for future in futures]
        assert sorted(outcomes) == ["created", "refused"]

        with pool.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM agents WHERE runtime_kind = 'project_task_files' "
                "AND user_id = %s",
                (user_id,),
            ).fetchone()["n"]
        assert int(count) == PROJECT_TASK_FILES_OWNER_LIMIT
    finally:
        other.close()
        pool.close()


@requires_postgresql
@pytest.mark.postgresql
def test_pg_files_context_requires_both_ids_and_unique_runtime() -> None:
    from psycopg import IntegrityError

    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import PostgresPool
    from octop.infra.db.repos.projects import ProjectRepo
    from octop.infra.db.repos.threads import ThreadRepo
    from octop.infra.db.repos.users import UserRepo

    pool = PostgresPool(_conninfo())
    try:
        _reset_public_schema(pool)
        run_migrations(pool)
        users = UserRepo(pool)
        projects = ProjectRepo(pool)
        threads = ThreadRepo(pool)
        user_id = users.create(
            username=f"pg_ctx_{_unique_suffix()}", password_hash="x", role="user"
        )
        project_id = projects.create_with_owner(
            creator_user_id=user_id, name=f"pg项目{_unique_suffix()}"
        ).project_id
        agent_id = f"ag_pg_ctx_{_unique_suffix()}"
        suffix = _unique_suffix()
        thread_ids = [f"thr_pg_{suffix}_{tag}" for tag in ("bad", "half", "ok", "dup", "chat")]
        for thread_id in thread_ids:
            threads.insert(
                thread_id=thread_id,
                agent_id=agent_id,
                user_id=user_id,
                channel_type="dashboard",
                session_key=f"{agent_id}:dashboard:{user_id}:{thread_id}",
                title="pg 任务",
            )
            with pool.transaction() as conn:
                conn.execute(
                    "INSERT INTO project_task_links("
                    "project_id, thread_id, owner_user_id, source, created_at) "
                    "VALUES (?, ?, ?, 'project', 0)",
                    (project_id, thread_id, user_id),
                )

        def _add_context(thread_id: str, mode: str, expert: str | None, runtime: str | None):
            with pool.transaction() as conn:
                conn.execute(
                    "INSERT INTO project_task_contexts("
                    "thread_id, project_id, owner_user_id, instructions_snapshot, "
                    "snapshot_version, instructions_sha256, captured_at, "
                    "mode, source_expert_id, runtime_agent_id) "
                    "VALUES (?, ?, ?, '指令', 1, ?, 5, ?, ?, ?)",
                    (
                        thread_id,
                        project_id,
                        user_id,
                        "0" * 64,
                        mode,
                        expert,
                        runtime,
                    ),
                )

        bad, half, ok, dup, chat = thread_ids
        with pytest.raises(IntegrityError):
            _add_context(bad, "files", None, None)
        with pytest.raises(IntegrityError):
            _add_context(half, "files", f"ag_expert_{suffix}", None)
        _add_context(ok, "files", f"ag_expert_{suffix}", f"ag_runtime_{suffix}")
        with pytest.raises(IntegrityError):
            _add_context(dup, "files", f"ag_expert_{suffix}", f"ag_runtime_{suffix}")
        _add_context(chat, "chat", None, None)
        with pool.connect() as conn:
            rows = conn.execute(
                "SELECT thread_id, mode FROM project_task_contexts ORDER BY thread_id"
            ).fetchall()
        assert {str(r["thread_id"]): str(r["mode"]) for r in rows} == {ok: "files", chat: "chat"}
    finally:
        pool.close()
