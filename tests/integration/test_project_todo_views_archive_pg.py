"""Real pg_dump/restore and PNG gates; absent synthetic lease means SKIPPED."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup import system_archive
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.utils.paths import PathLayout
from tests.integration.test_project_todo_fields_backup_failure_pg import _database_state
from tests.integration.test_project_todo_fields_pg import postgres_pair as postgres_pair
from tests.integration.test_project_todo_views_migration_pg import _pg_fault_after_sql, _rows
from tests.unit.backup.test_project_todo_fields_archive import (
    CATALOG_TABLES,
    CHILD_TABLES,
    OLD_COLUMNS,
    _seed_archive_rows,
)
from tests.unit.backup.test_project_todo_fields_archive_failure import (
    _private_tree,
    _seed_plan_fields,
)
from tests.unit.backup.test_project_todo_views_archive import VIEW_TABLES, _custom_views
from tests.unit.db.test_project_todo_views_migration import (
    DISPLAY,
    FAULT_SQL,
    _assert_035,
    _assert_defaults,
    _key,
)


def _config(env: dict[str, Any]) -> DatabaseConfig:
    return DatabaseConfig(
        driver="postgresql",
        host="127.0.0.1",
        port=env["context"]["port"],
        database=env["database"],
        user=env["context"]["user"],
    )


@pytest.mark.parametrize("postgres_pair", [33, 34, 35], indirect=True)
@pytest.mark.parametrize("preserve_users", [False, True])
def test_real_pg_archive_all_history_and_final_names_preserve_views_and_png(
    postgres_pair: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preserve_users: bool,
) -> None:
    env, source = postgres_pair, PathLayout(tmp_path / "source")
    pool = env["first"]
    with pool.connect() as conn:
        source_version = int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])
    project, todo, png = _seed_archive_rows(pool, source)
    if source_version >= 34:
        _seed_plan_fields(pool, source, project, todo, "archive")
    if source_version == 35:
        _custom_views(pool, project)
        UserRepo(pool).set_display_name(1, "Archive Ｓtraße")
    else:
        with pool.transaction() as conn:
            conn.execute("UPDATE users SET display_name='Archive Ｓtraße' WHERE id=1")
    with pool.connect() as conn:
        old_todos = _rows(conn, f"SELECT {OLD_COLUMNS} FROM project_todos ORDER BY id")
        children = {
            table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in CHILD_TABLES
        }
        catalog = (
            {table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in CATALOG_TABLES}
            if source_version >= 34
            else None
        )
        views = (
            {table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in VIEW_TABLES}
            if source_version == 35
            else None
        )
        fields = (
            _rows(conn, "SELECT start_date,due_date,priority_id FROM project_todos ORDER BY id")
            if source_version >= 34
            else [(None, None, None)] * 2
        )
        object_key = conn.execute("SELECT object_key FROM project_todo_comment_images").fetchone()[
            0
        ]
        if source_version == 35:
            conn.execute("UPDATE project_todos SET title_search_key='forged archive key'")
            conn.execute("UPDATE users SET project_plan_display_sort_key='forged archive key'")
    for variable in tuple(os.environ):
        if variable.startswith("PG"):
            monkeypatch.delenv(variable, raising=False)
    archive = tmp_path / f"actual-{source_version}.tar.gz"
    config = _config(env)
    system_archive.create_system_backup(
        paths=source,
        agent_rows=[],
        pool=pool,
        db_config=config,
        dest=archive,
    )
    run_migrations(pool)
    if preserve_users:
        users = UserRepo(pool)
        users.set_display_name(1, DISPLAY)
        with pool.transaction() as conn:
            conn.execute(
                "UPDATE users SET username='final-user',project_plan_display_sort_key=? WHERE id=1",
                (_key(DISPLAY),),
            )
    target = PathLayout(tmp_path / "target")
    target.root.mkdir()
    result = system_archive.restore_system_backup(
        archive,
        paths=target,
        pool=pool,
        db_config=config,
        restore_config=False,
        preserve_users=preserve_users,
    )
    assert result["schema_version"] == 35 and result["project_todo_comment_image_files"] == 1
    with env["second"].connect() as conn:
        _assert_035(conn)
        assert _rows(conn, f"SELECT {OLD_COLUMNS} FROM project_todos ORDER BY id") == old_todos
        assert {
            table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in CHILD_TABLES
        } == children
        assert (
            _rows(conn, "SELECT start_date,due_date,priority_id FROM project_todos ORDER BY id")
            == fields
        )
        if catalog is not None:
            assert {
                table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in CATALOG_TABLES
            } == catalog
        if views is not None:
            assert {
                table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in VIEW_TABLES
            } == views
        else:
            _assert_defaults(conn, project)
        for row in conn.execute("SELECT title,title_search_key FROM project_todos"):
            assert row["title_search_key"] == _key(row["title"])
        user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
        assert user["display_name"] == (DISPLAY if preserve_users else "Archive Ｓtraße")
        assert user["username"] == ("final-user" if preserve_users else "c1-backup-owner")
        assert user["project_plan_display_sort_key"] == _key(user["display_name"])
    assert (target.project_todo_comment_images / object_key).read_bytes() == png
    env["report"]["q2_archive"] = {
        "source_version": source_version,
        "preserve_users": preserve_users,
        "private_png_bytes_verified": True,
    }


@pytest.mark.parametrize("postgres_pair", [34], indirect=True)
@pytest.mark.parametrize("phase", ["ddl", "title", "first_view", "watermark"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_real_pg_archive_035_sql_fault_restores_complete_original_view_and_png_pair(
    postgres_pair: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure: type[BaseException],
) -> None:
    env, pool = postgres_pair, postgres_pair["first"]
    source = PathLayout(tmp_path / "source")
    project, todo, _ = _seed_archive_rows(pool, source)
    _seed_plan_fields(pool, source, project, todo, "archive")
    for variable in tuple(os.environ):
        if variable.startswith("PG"):
            monkeypatch.delenv(variable, raising=False)
    config = _config(env)
    archive = tmp_path / "actual-034.tar.gz"
    system_archive.create_system_backup(
        paths=source,
        agent_rows=[],
        pool=pool,
        db_config=config,
        dest=archive,
    )
    run_migrations(pool)
    target = PathLayout(tmp_path / "target")
    _seed_plan_fields(pool, target, project, todo, "current")
    _custom_views(pool, project, "current")
    before, private_before = _database_state(pool), _private_tree(target)
    real_migrate = system_archive.run_migrations
    reached = []

    def fault(target_pool: PostgresPool) -> None:
        with _pg_fault_after_sql(target_pool, monkeypatch, FAULT_SQL[phase], failure) as checkpoint:
            try:
                real_migrate(target_pool)
            finally:
                reached.extend(checkpoint)

    with monkeypatch.context() as patch:
        patch.setattr(system_archive, "run_migrations", fault)
        with pytest.raises(
            KeyboardInterrupt if failure is KeyboardInterrupt else OctopError,
            match="injected after actual PG 035 SQL",
        ):
            system_archive.restore_system_backup(
                archive,
                paths=target,
                pool=pool,
                db_config=config,
                restore_config=False,
                preserve_users=False,
            )
    assert len(reached) == 1 and reached[0]["sql_executed"]
    assert _database_state(env["second"]) == before
    assert _private_tree(target) == private_before
    assert not list(target.root.glob(".todo-comment-image-restore-*"))
    env["report"]["q2_archive_fault"] = {
        "phase": phase,
        "failure": failure.__name__,
        "reached": reached,
        "database_and_private_pair_verified": True,
    }
