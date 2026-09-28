"""Actual SQLite tar/database/private PNG round trips for shared plan views."""

from __future__ import annotations

import json
import tarfile
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from tests.unit.backup.test_project_todo_fields_archive import (
    CATALOG_TABLES,
    CHILD_TABLES,
    OLD_COLUMNS,
    _rows,
    _seed_archive_rows,
)
from tests.unit.backup.test_project_todo_fields_archive_failure import (
    _private_tree,
    _seed_plan_fields,
)
from tests.unit.db.test_project_todo_views_migration import (
    FAULT_SQL,
    TABLE_DEFINITION,
    _assert_035,
    _assert_defaults,
    _build_legacy,
    _fault_after_sql,
)

from octop.config import DatabaseConfig
from octop.infra.backup import system_archive
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ulid import new_ulid

VIEW_TABLES = ("project_todo_view_state", "project_todo_views")


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _custom_views(pool: DatabasePool, project_id: str, marker: str = "archive") -> None:
    custom = new_ulid()
    definition = {
        **TABLE_DEFINITION,
        "fields": ["title", "status", "assignee", "priority"],
        "calendar": {"date_basis": "start_date", "mode": "week"},
    }
    with pool.transaction() as conn:
        _assert_035(conn)
        conn.execute(
            "UPDATE project_todo_views SET archived_at=45,version=7,position=8 "
            "WHERE project_id=? AND view_type='table'",
            (project_id,),
        )
        conn.execute(
            "UPDATE project_todo_views SET name=?,name_key=?,position=0,version=9 "
            "WHERE project_id=? AND view_type='board'",
            (f"{marker} board", f"{marker} board", project_id),
        )
        conn.execute(
            "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,"
            "definition_json,version,position,created_at,updated_at) "
            "VALUES (?,?,?,?,'calendar',?,6,1,41,52)",
            (
                custom,
                project_id,
                f"{marker} calendar",
                f"{marker} calendar",
                json.dumps(definition),
            ),
        )
        conn.execute(
            "UPDATE project_todo_view_state SET default_view_id=?,revision=17,updated_at=52 "
            "WHERE project_id=?",
            (custom, project_id),
        )


def _plan_snapshot(conn: Any) -> dict[str, Any]:
    tables = (*CHILD_TABLES, *CATALOG_TABLES, *VIEW_TABLES)
    return {table: _rows(conn, table) for table in tables}


@pytest.mark.parametrize("source_version", [33, 34, 35])
@pytest.mark.parametrize(
    "preserve_users", [False, True], ids=["archive-users", "final-preserved-users"]
)
def test_real_sqlite_archive_preserves_plan_views_and_recomputes_final_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_version: int,
    preserve_users: bool,
) -> None:
    source = PathLayout(tmp_path / "source")
    source_pool = SqlitePool(source.db)
    try:
        if source_version < 35:
            _build_legacy(source_pool, source_version, monkeypatch)
        else:
            run_migrations(source_pool)
        project_id, todo_id, png = _seed_archive_rows(source_pool, source)
        if source_version >= 34:
            _seed_plan_fields(source_pool, source, project_id, todo_id, "archive")
        if source_version == 35:
            _custom_views(source_pool, project_id)
        if source_version < 35:
            with source_pool.transaction() as conn:
                conn.execute("UPDATE users SET display_name='Archive Ｓtraße' WHERE id=1")
        else:
            UserRepo(source_pool).set_display_name(1, "Archive Ｓtraße")
        with source_pool.connect() as conn:
            old_todos = [
                tuple(row)
                for row in conn.execute(f"SELECT {OLD_COLUMNS} FROM project_todos ORDER BY id")
            ]
            children = {table: _rows(conn, table) for table in CHILD_TABLES}
            catalog = (
                {table: _rows(conn, table) for table in CATALOG_TABLES}
                if source_version >= 34
                else None
            )
            views = (
                {table: _rows(conn, table) for table in VIEW_TABLES}
                if source_version == 35
                else None
            )
            plan_fields = (
                [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT start_date,due_date,priority_id FROM project_todos ORDER BY id"
                    )
                ]
                if source_version >= 34
                else [(None, None, None)] * 2
            )
            object_key = conn.execute(
                "SELECT object_key FROM project_todo_comment_images"
            ).fetchone()[0]
            if source_version == 35:
                conn.execute("UPDATE project_todos SET title_search_key='forged archive key'")
                conn.execute("UPDATE users SET project_plan_display_sort_key='forged archive key'")
        archive = tmp_path / f"real-{source_version}.tar.gz"
        system_archive.create_system_backup(
            paths=source,
            agent_rows=[],
            pool=source_pool,
            db_config=DatabaseConfig(),
            dest=archive,
        )
        with tarfile.open(archive, "r:gz") as tf:
            assert any(name.startswith("project-todo-comment-images/") for name in tf.getnames())
            assert not any("todo-view" in name for name in tf.getnames())
    finally:
        source_pool.close()

    target = PathLayout(tmp_path / "target")
    target_pool = SqlitePool(target.db)
    try:
        run_migrations(target_pool)
        final_name = "\ufdfa" * 800 + "Final Ｓtraße"
        if preserve_users:
            assert (
                UserRepo(target_pool).create(
                    username="final-user",
                    role="admin",
                    display_name=final_name,
                )
                == 1
            )
        result = system_archive.restore_system_backup(
            archive,
            paths=target,
            pool=target_pool,
            db_config=DatabaseConfig(),
            restore_config=False,
            preserve_users=preserve_users,
        )
        assert result["schema_version"] == 35
        assert result["project_todo_comment_image_files"] == 1
        with target_pool.connect() as conn:
            _assert_035(conn)
            assert [
                tuple(row)
                for row in conn.execute(f"SELECT {OLD_COLUMNS} FROM project_todos ORDER BY id")
            ] == old_todos
            assert {table: _rows(conn, table) for table in CHILD_TABLES} == children
            assert [
                tuple(row)
                for row in conn.execute(
                    "SELECT start_date,due_date,priority_id FROM project_todos ORDER BY id"
                )
            ] == plan_fields
            if catalog is not None:
                assert {table: _rows(conn, table) for table in CATALOG_TABLES} == catalog
            if views is not None:
                assert {table: _rows(conn, table) for table in VIEW_TABLES} == views
            else:
                _assert_defaults(conn, project_id)
            for row in conn.execute("SELECT title,title_search_key FROM project_todos"):
                assert row["title_search_key"] == _key(row["title"])
            user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
            assert user["display_name"] == (final_name if preserve_users else "Archive Ｓtraße")
            assert user["username"] == ("final-user" if preserve_users else "c1-backup-owner")
            assert user["project_plan_display_sort_key"] == _key(user["display_name"])
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (target.project_todo_comment_images / object_key).read_bytes() == png
    finally:
        target_pool.close()


@pytest.mark.parametrize("phase", ["ddl", "title", "first_view", "watermark"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_real_archive_035_actual_sql_failure_restores_original_database_views_and_png_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure: type[BaseException],
) -> None:
    source = PathLayout(tmp_path / "source")
    source_pool = SqlitePool(source.db)
    try:
        _build_legacy(source_pool, 34, monkeypatch)
        project_id, todo_id, _ = _seed_archive_rows(source_pool, source)
        _seed_plan_fields(source_pool, source, project_id, todo_id, "archive")
        archive = tmp_path / "real-034.tar.gz"
        system_archive.create_system_backup(
            paths=source,
            agent_rows=[],
            pool=source_pool,
            db_config=DatabaseConfig(),
            dest=archive,
        )
    finally:
        source_pool.close()
    target = PathLayout(tmp_path / "target")
    pool = SqlitePool(target.db)
    try:
        run_migrations(pool)
        current_project, current_todo, _ = _seed_archive_rows(pool, target)
        _seed_plan_fields(pool, target, current_project, current_todo, "current")
        _custom_views(pool, current_project, "current")
        with pool.connect() as conn:
            before = tuple(conn.iterdump())
        private_before = _private_tree(target)
        migrate = system_archive.run_migrations
        reached = []

        def fail_after_sql(target_pool: SqlitePool) -> None:
            with _fault_after_sql(
                target_pool, monkeypatch, FAULT_SQL[phase], failure
            ) as checkpoint:
                try:
                    migrate(target_pool)
                finally:
                    reached.extend(checkpoint)

        with monkeypatch.context() as patch:
            patch.setattr(system_archive, "run_migrations", fail_after_sql)
            from octop.infra.errors import OctopError

            with pytest.raises(
                KeyboardInterrupt if failure is KeyboardInterrupt else OctopError,
                match="injected after actual 035 SQL",
            ):
                system_archive.restore_system_backup(
                    archive,
                    paths=target,
                    pool=pool,
                    db_config=DatabaseConfig(),
                    restore_config=False,
                    preserve_users=False,
                )
        assert reached == [(1, True)]
        with pool.connect() as conn:
            assert tuple(conn.iterdump()) == before
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _private_tree(target) == private_before
        assert not list(target.root.glob(".todo-comment-image-restore-*"))
    finally:
        pool.close()
