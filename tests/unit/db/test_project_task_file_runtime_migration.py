"""030A B1: schema v28 — project-task file runtime persistence (migration half).

Covers the paired 028 DDL on SQLite: fresh installs and v27 upgrades reach
watermark 28 with every pre-existing user / Agent / thread / task-context row
preserved, ``project_task_contexts.mode`` defaulting to ``chat``, and
``agents.runtime_kind`` defaulting to ``standard``. A ``files`` context must
carry BOTH ``source_expert_id`` and ``runtime_agent_id``; old chat rows and
manual links (no context row) stay valid. ``runtime_kind`` is a real database
column with a two-value CHECK — never derived from ``config_json`` — and the
public ``AgentRepo.create`` / ``update_config`` paths cannot set it. Also
covers replay recovery from partial 028 application (same rationale as the
027 helper) and token-level parity of the PostgreSQL pair.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"

_DIGEST = hashlib.sha256("指令".encode()).hexdigest()


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def _seed_user(db: SqlitePool, username: str) -> int:
    return UserRepo(db).create(username=username, password_hash="h", role="user")


def _seed_agent(db: SqlitePool, *, agent_id: str, user_id: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, 'expert', 0, 0)",
            (agent_id, user_id, agent_id),
        )


def _seed_project(db: SqlitePool, owner_id: int) -> str:
    project = ProjectRepo(db).create_with_owner(creator_user_id=owner_id, name="任务项目")
    return project.project_id


def _seed_linked_thread(
    db: SqlitePool, *, thread_id: str, project_id: str, user_id: int, agent_id: str
) -> None:
    """Insert a dashboard DM thread plus its ``source='project'`` task link."""
    ThreadRepo(db).insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        channel_type="dashboard",
        session_key=f"{agent_id}:dashboard:{user_id}:dm",
        title="任务",
    )
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_links("
            "project_id, thread_id, owner_user_id, source, created_at) "
            "VALUES (?, ?, ?, 'project', 0)",
            (project_id, thread_id, user_id),
        )


def _seed_chat_context(db: SqlitePool, *, thread_id: str, project_id: str, user_id: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_task_contexts("
            "thread_id, project_id, owner_user_id, instructions_snapshot, "
            "snapshot_version, instructions_sha256, captured_at) "
            "VALUES (?, ?, ?, '指令', 1, ?, 5)",
            (thread_id, project_id, user_id, _DIGEST),
        )


def _insert_context(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    project_id: str,
    user_id: int,
    mode: str,
    source_expert_id: str | None,
    runtime_agent_id: str | None,
) -> None:
    conn.execute(
        "INSERT INTO project_task_contexts("
        "thread_id, project_id, owner_user_id, instructions_snapshot, "
        "snapshot_version, instructions_sha256, captured_at, "
        "mode, source_expert_id, runtime_agent_id) "
        "VALUES (?, ?, ?, '指令', 1, ?, 5, ?, ?, ?)",
        (thread_id, project_id, user_id, _DIGEST, mode, source_expert_id, runtime_agent_id),
    )


def _downgrade_to_v27(pool: SqlitePool) -> None:
    """Undo every 028 artifact so ``run_migrations`` must replay the upgrade.

    Drop order matters: the partial unique index first, then ``mode`` (its
    CHECK references the id columns), then the id columns themselves.
    """
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP INDEX IF EXISTS idx_project_task_contexts_runtime_agent;
            ALTER TABLE project_task_contexts DROP COLUMN mode;
            ALTER TABLE project_task_contexts DROP COLUMN runtime_agent_id;
            ALTER TABLE project_task_contexts DROP COLUMN source_expert_id;
            ALTER TABLE agents DROP COLUMN runtime_kind;
            UPDATE _schema_version SET version = 27;
            """
        )


def _columns(pool: SqlitePool, table: str) -> set[str]:
    with pool.connect() as conn:
        return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _watermark(pool: SqlitePool) -> int:
    with pool.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def _agent_row(pool: SqlitePool, agent_id: str) -> sqlite3.Row:
    with pool.connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    assert row is not None
    return row


def _context_row(pool: SqlitePool, thread_id: str) -> sqlite3.Row:
    with pool.connect() as conn:
        row = conn.execute(
            "SELECT * FROM project_task_contexts WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Fresh install shape
# ---------------------------------------------------------------------------


def test_fresh_sqlite_migration_reaches_v28_with_pinned_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        index_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_project_task_contexts_runtime_agent'"
        ).fetchone()
        fks = conn.execute("PRAGMA foreign_key_list(project_task_contexts)").fetchall()
    assert _watermark(db) == _max_discovered_version("sqlite")
    assert _watermark(db) >= 28
    assert "runtime_kind" in _columns(db, "agents")
    assert _columns(db, "project_task_contexts") >= {
        "mode",
        "source_expert_id",
        "runtime_agent_id",
    }
    assert index_row is not None
    index_sql = str(index_row["sql"])
    assert "UNIQUE" in index_sql
    assert "WHERE runtime_agent_id IS NOT NULL" in index_sql
    # 028 deliberately adds NO foreign keys: the exact 026 FK set is unchanged
    # (a detached private file task must survive hard deletion of its source
    # expert; runtime cleanup ordering belongs to the 030A delete flows).
    fk_pairs = {
        str(fk["from"]): (str(fk["table"]), str(fk["to"]), str(fk["on_delete"])) for fk in fks
    }
    assert fk_pairs == {
        "thread_id": ("project_task_links", "thread_id", "CASCADE"),
        "project_id": ("project_spaces", "project_id", "CASCADE"),
        "owner_user_id": ("users", "id", "CASCADE"),
    }


# ---------------------------------------------------------------------------
# v27 → v28 upgrade preserves every existing row and default
# ---------------------------------------------------------------------------


def test_v27_upgrade_preserves_rows_and_pins_defaults(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    user_id = _seed_user(pool, "owner")
    other_id = _seed_user(pool, "member")
    _seed_agent(pool, agent_id="ag_normal", user_id=user_id)
    project_id = _seed_project(pool, user_id)
    _seed_linked_thread(
        pool, thread_id="thr_chat", project_id=project_id, user_id=other_id, agent_id="ag_normal"
    )
    _seed_chat_context(pool, thread_id="thr_chat", project_id=project_id, user_id=other_id)

    _downgrade_to_v27(pool)
    assert _watermark(pool) == 27
    assert "runtime_kind" not in _columns(pool, "agents")

    run_migrations(pool)

    assert _watermark(pool) == _max_discovered_version("sqlite")
    # Users, agents, threads, links and contexts all survived.
    with pool.connect() as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]) == 2
        assert int(conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]) == 1
        assert int(conn.execute("SELECT COUNT(*) FROM project_task_links").fetchone()[0]) == 1
    agent = _agent_row(pool, "ag_normal")
    assert str(agent["name"]) == "ag_normal"
    assert str(agent["runtime_kind"]) == "standard"
    ctx = _context_row(pool, "thr_chat")
    assert str(ctx["instructions_snapshot"]) == "指令"
    assert str(ctx["instructions_sha256"]) == _DIGEST
    assert int(ctx["captured_at"]) == 5
    assert str(ctx["mode"]) == "chat"
    assert ctx["source_expert_id"] is None
    assert ctx["runtime_agent_id"] is None
    # Old repo behavior is unchanged after the upgrade.
    repo = AgentRepo(pool)
    repo.create(agent_id="ag_after", user_id=user_id, name="升级后")
    after = repo.get("ag_after")
    assert after is not None
    assert after.runtime_kind == "standard"
    repo.update_config("ag_after", description="d")
    assert repo.get("ag_after") is not None
    assert repo.get("ag_normal") is not None


def test_028_sql_file_applies_cleanly_on_a_v27_database(tmp_path: Path) -> None:
    """The canonical SQLite file itself (not just the runner helper) works."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    user_id = _seed_user(pool, "owner")
    _seed_agent(pool, agent_id="ag_normal", user_id=user_id)
    project_id = _seed_project(pool, user_id)
    _seed_linked_thread(
        pool, thread_id="thr_chat", project_id=project_id, user_id=user_id, agent_id="ag_normal"
    )
    _seed_chat_context(pool, thread_id="thr_chat", project_id=project_id, user_id=user_id)
    _downgrade_to_v27(pool)

    sql = (MIGRATIONS / "028_project_task_files.sql").read_text(encoding="utf-8")
    with pool.connect() as conn:
        conn.executescript(sql)

    assert _watermark(pool) == 28
    assert str(_agent_row(pool, "ag_normal")["runtime_kind"]) == "standard"
    ctx = _context_row(pool, "thr_chat")
    assert str(ctx["mode"]) == "chat"
    assert ctx["source_expert_id"] is None


# ---------------------------------------------------------------------------
# Partial-replay recovery (same rationale as the 027 helper)
# ---------------------------------------------------------------------------


def test_partial_replay_with_all_columns_present_recovers(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "partial-028.db")
    run_migrations(pool)
    # A stopped upgrade can leave the full DDL applied while the watermark
    # still reads 27; replaying 028 must be a no-op besides the watermark.
    with pool.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 27")

    run_migrations(pool)

    assert _watermark(pool) == _max_discovered_version("sqlite")
    assert "runtime_kind" in _columns(pool, "agents")
    assert _columns(pool, "project_task_contexts") >= {
        "mode",
        "source_expert_id",
        "runtime_agent_id",
    }


def test_partial_replay_after_a_prefix_of_the_ddl_recovers(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "partial-028-prefix.db")
    run_migrations(pool)
    # Simulate a crash after the first ALTERs: runtime_kind and both id
    # columns exist, but ``mode``, the unique index and the watermark do not.
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP INDEX IF EXISTS idx_project_task_contexts_runtime_agent;
            ALTER TABLE project_task_contexts DROP COLUMN mode;
            UPDATE _schema_version SET version = 27;
            """
        )

    run_migrations(pool)

    assert _watermark(pool) == _max_discovered_version("sqlite")
    assert _columns(pool, "project_task_contexts") >= {
        "mode",
        "source_expert_id",
        "runtime_agent_id",
    }
    with pool.connect() as conn:
        index_row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_project_task_contexts_runtime_agent'"
        ).fetchone()
    assert index_row is not None


# ---------------------------------------------------------------------------
# DDL authority: mode / runtime_kind CHECKs and the unique runtime id
# ---------------------------------------------------------------------------


def test_files_context_requires_both_runtime_ids(db: SqlitePool) -> None:
    user_id = _seed_user(db, "owner")
    _seed_agent(db, agent_id="ag_expert", user_id=user_id)
    _seed_agent(db, agent_id="ag_runtime", user_id=user_id)
    project_id = _seed_project(db, user_id)
    for tag in ("files_none", "files_half", "files_ok", "chat_ok", "bad_mode"):
        _seed_linked_thread(
            db,
            thread_id=f"thr_{tag}",
            project_id=project_id,
            user_id=user_id,
            agent_id="ag_runtime",
        )

    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_context(
                conn,
                thread_id="thr_files_none",
                project_id=project_id,
                user_id=user_id,
                mode="files",
                source_expert_id=None,
                runtime_agent_id=None,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_context(
                conn,
                thread_id="thr_files_half",
                project_id=project_id,
                user_id=user_id,
                mode="files",
                source_expert_id="ag_expert",
                runtime_agent_id=None,
            )
        _insert_context(
            conn,
            thread_id="thr_files_ok",
            project_id=project_id,
            user_id=user_id,
            mode="files",
            source_expert_id="ag_expert",
            runtime_agent_id="ag_runtime",
        )
        # Old chat rows and manually linked tasks stay valid with NULL ids.
        _insert_context(
            conn,
            thread_id="thr_chat_ok",
            project_id=project_id,
            user_id=user_id,
            mode="chat",
            source_expert_id=None,
            runtime_agent_id=None,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_context(
                conn,
                thread_id="thr_bad_mode",
                project_id=project_id,
                user_id=user_id,
                mode="shell",
                source_expert_id=None,
                runtime_agent_id=None,
            )
    with db.connect() as conn:
        rows = conn.execute("SELECT thread_id, mode FROM project_task_contexts").fetchall()
    assert {str(r["thread_id"]): str(r["mode"]) for r in rows} == {
        "thr_files_ok": "files",
        "thr_chat_ok": "chat",
    }


def test_runtime_agent_id_is_unique_when_present(db: SqlitePool) -> None:
    user_id = _seed_user(db, "owner")
    _seed_agent(db, agent_id="ag_expert", user_id=user_id)
    _seed_agent(db, agent_id="ag_runtime", user_id=user_id)
    project_id = _seed_project(db, user_id)
    for thread_id in ("thr_a", "thr_b", "thr_c"):
        _seed_linked_thread(
            db,
            thread_id=thread_id,
            project_id=project_id,
            user_id=user_id,
            agent_id="ag_runtime",
        )

    with db.transaction() as conn:
        _insert_context(
            conn,
            thread_id="thr_a",
            project_id=project_id,
            user_id=user_id,
            mode="files",
            source_expert_id="ag_expert",
            runtime_agent_id="ag_runtime",
        )
        # A second context claiming the same runtime Agent is refused …
        with pytest.raises(sqlite3.IntegrityError):
            _insert_context(
                conn,
                thread_id="thr_b",
                project_id=project_id,
                user_id=user_id,
                mode="files",
                source_expert_id="ag_expert",
                runtime_agent_id="ag_runtime",
            )
        # … while any number of NULL runtime ids (chat rows) coexist.
        _insert_context(
            conn,
            thread_id="thr_c",
            project_id=project_id,
            user_id=user_id,
            mode="chat",
            source_expert_id=None,
            runtime_agent_id=None,
        )


def test_runtime_kind_check_rejects_foreign_values(db: SqlitePool) -> None:
    user_id = _seed_user(db, "owner")
    _seed_agent(db, agent_id="ag_normal", user_id=user_id)
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agents SET runtime_kind = 'files' WHERE agent_id = 'ag_normal'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agents("
                "agent_id, user_id, name, runtime_kind, created_at, updated_at) "
                "VALUES ('ag_bad', ?, 'bad', 'project-task-files', 0, 0)",
                (user_id,),
            )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, runtime_kind, created_at, updated_at) "
            "VALUES ('ag_internal', ?, 'internal', 'project_task_files', 0, 0)",
            (user_id,),
        )
    assert str(_agent_row(db, "ag_normal")["runtime_kind"]) == "standard"
    assert str(_agent_row(db, "ag_internal")["runtime_kind"]) == "project_task_files"


def test_public_repo_paths_cannot_write_internal_runtime_kind(db: SqlitePool) -> None:
    """``config_json`` is opaque text; the column only accepts the SQL path."""
    repo = AgentRepo(db)
    user_id = _seed_user(db, "owner")
    forged = '{"runtime_kind": "project_task_files", "internal": true}'
    repo.create(agent_id="ag_public", user_id=user_id, name="公开", config_json=forged)
    row = repo.get("ag_public")
    assert row is not None
    assert row.runtime_kind == "standard"
    assert row.config_json == forged

    repo.update_config("ag_public", name="公开2", config_json=forged)
    row = repo.get("ag_public")
    assert row is not None
    assert row.runtime_kind == "standard"
    assert row.name == "公开2"

    # Neither public method exposes a runtime_kind parameter at all.
    with pytest.raises(TypeError):
        cast(Any, repo).create(
            agent_id="ag_sneaky",
            user_id=user_id,
            name="sneaky",
            runtime_kind="project_task_files",
        )
    with pytest.raises(TypeError):
        cast(Any, repo).update_config(
            "ag_public",
            runtime_kind="project_task_files",
        )
    assert repo.get("ag_sneaky") is None


# ---------------------------------------------------------------------------
# PostgreSQL pair parity (source review only — no live PG here)
# ---------------------------------------------------------------------------


def test_migration_028_pg_pair_declares_same_shape() -> None:
    sqlite_sql = (MIGRATIONS / "028_project_task_files.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "028_project_task_files.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "ALTER TABLE agents ADD COLUMN runtime_kind TEXT NOT NULL DEFAULT 'standard'",
        "CHECK (runtime_kind IN ('standard', 'project_task_files'))",
        "ALTER TABLE project_task_contexts ADD COLUMN source_expert_id TEXT",
        "ALTER TABLE project_task_contexts ADD COLUMN runtime_agent_id TEXT",
        "ALTER TABLE project_task_contexts ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'",
        "CHECK (mode IN ('chat', 'files'))",
        "mode <> 'files'",
        "source_expert_id IS NOT NULL AND runtime_agent_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_task_contexts_runtime_agent",
        "ON project_task_contexts(runtime_agent_id) WHERE runtime_agent_id IS NOT NULL",
        "UPDATE _schema_version SET version = 28",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    # Bodies are byte-identical; only the first header line names the dialect.
    assert sqlite_sql.split("\n", 1)[1] == pg_sql.split("\n", 1)[1]
    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 6
    assert all(stmt.strip() for stmt in statements)
    assert statements[-1] == "UPDATE _schema_version SET version = 28"
