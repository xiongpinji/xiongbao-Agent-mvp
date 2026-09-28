"""Real isolated PG restore boundaries; never consumes an application DSN."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from octop.infra.backup import pg_dump, system_archive
from octop.infra.db.migrate import run_migrations
from octop.infra.errors import OctopError
from octop.infra.utils.paths import PathLayout
from tests.integration.test_project_todo_fields_backup_failure_pg import _database_state
from tests.integration.test_project_todo_fields_pg import postgres_pair as postgres_pair
from tests.integration.test_project_todo_views_archive_pg import _config
from tests.unit.backup.test_project_todo_fields_archive import _seed_archive_rows
from tests.unit.backup.test_project_todo_fields_archive_failure import (
    _private_tree,
    _seed_plan_fields,
)
from tests.unit.backup.test_project_todo_views_archive import _custom_views


def _old_archive_and_current_target(
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    missing_role: str | None = None,
) -> tuple[Path, PathLayout]:
    from psycopg.sql import SQL, Identifier

    pool = env["first"]
    source = PathLayout(tmp_path / "source")
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM public._schema_version").fetchone()[0] == 33
    project, todo, _ = _seed_archive_rows(pool, source)
    for name in tuple(os.environ):
        if name.startswith("PG"):
            monkeypatch.delenv(name, raising=False)
    if missing_role is not None:
        with pool.transaction() as conn:
            conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(missing_role)).as_string())
            conn.execute(
                SQL("GRANT SELECT ON public.project_todos TO {}")
                .format(Identifier(missing_role))
                .as_string()
            )
    archive = tmp_path / "actual-033.tar.gz"
    try:
        system_archive.create_system_backup(
            paths=source,
            agent_rows=[],
            pool=pool,
            db_config=_config(env),
            dest=archive,
        )
    finally:
        if missing_role is not None:
            with pool.transaction() as conn:
                conn.execute(
                    SQL("REVOKE SELECT ON public.project_todos FROM {}")
                    .format(Identifier(missing_role))
                    .as_string()
                )
                conn.execute(SQL("DROP ROLE {}").format(Identifier(missing_role)).as_string())
    run_migrations(pool)
    target = PathLayout(tmp_path / "target")
    _seed_plan_fields(pool, target, project, todo, "current")
    _custom_views(pool, project, "current")
    return archive, target


def _external_schema(pool: Any) -> None:
    with pool.transaction() as conn:
        conn.execute("CREATE SCHEMA ps04c_outside")
        conn.execute("CREATE TABLE ps04c_outside.project_todo_views(marker TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO ps04c_outside.project_todo_views VALUES ('outside sentinel')")
        conn.execute("GRANT USAGE ON SCHEMA ps04c_outside TO PUBLIC")
        conn.execute("GRANT SELECT ON ps04c_outside.project_todo_views TO PUBLIC")


def _outside_metadata(pool: Any) -> dict[str, Any]:
    with pool.connect() as conn:
        return {
            "schemas": [
                tuple(row[i] for i in range(3))
                for row in conn.execute(
                    "SELECT nspname,pg_get_userbyid(nspowner),nspacl::text "
                    "FROM pg_namespace WHERE nspname IN ('public','ps04c_outside') "
                    "ORDER BY nspname"
                )
            ],
            "tables": [
                tuple(row[i] for i in range(4))
                for row in conn.execute(
                    "SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner),c.relacl::text "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='ps04c_outside' OR c.relname='ps04c_external_guard' "
                    "ORDER BY n.nspname,c.relname"
                )
            ],
            "external_rows": [
                row[0]
                for row in conn.execute("SELECT marker FROM ps04c_outside.project_todo_views")
            ],
            "extensions": [
                tuple(row[i] for i in range(4))
                for row in conn.execute(
                    "SELECT e.extname,e.extversion,pg_get_userbyid(e.extowner),n.nspname "
                    "FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
                    "ORDER BY e.extname"
                )
            ],
        }


@pytest.mark.parametrize("postgres_pair", [33], indirect=True)
def test_real_old_pg_restore_preserves_outside_schema_shadow_names_owner_acl_and_extension(
    postgres_pair: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = postgres_pair
    archive, target = _old_archive_and_current_target(env, tmp_path, monkeypatch)
    _external_schema(env["first"])
    outside = _outside_metadata(env["second"])
    assert outside["extensions"] and outside["external_rows"] == ["outside sentinel"]
    result = system_archive.restore_system_backup(
        archive,
        paths=target,
        pool=env["first"],
        db_config=_config(env),
        restore_config=False,
        preserve_users=False,
    )
    assert result["schema_version"] == 35
    assert _outside_metadata(env["second"]) == outside
    assert not list(target.root.glob(".todo-comment-image-restore-*"))
    env["report"]["q2_restore_boundary"] = {
        "kind": "outside_schema",
        "owner_acl_existing_extension_and_shadow_data_unchanged": True,
        "actual_old_dump_to_current_target": True,
    }


@pytest.mark.parametrize("postgres_pair", [33], indirect=True)
def test_real_old_pg_restore_refuses_external_fk_and_recovers_database_png_owner_and_acl(
    postgres_pair: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = postgres_pair
    archive, target = _old_archive_and_current_target(env, tmp_path, monkeypatch)
    pool = env["first"]
    _external_schema(pool)
    with pool.transaction() as conn:
        conn.execute(
            "CREATE TABLE public.ps04c_external_guard("
            "view_id TEXT PRIMARY KEY REFERENCES public.project_todo_views(view_id))"
        )
        conn.execute(
            "INSERT INTO public.ps04c_external_guard "
            "SELECT view_id FROM public.project_todo_views ORDER BY view_id LIMIT 1"
        )
        conn.execute("GRANT SELECT ON public.ps04c_external_guard TO PUBLIC")
    before, images, outside = _database_state(pool), _private_tree(target), _outside_metadata(pool)
    with pytest.raises(OctopError) as raised:
        system_archive.restore_system_backup(
            archive,
            paths=target,
            pool=pool,
            db_config=_config(env),
            restore_config=False,
            preserve_users=False,
        )
    assert raised.value.details == {"reason": "backup_restore_cleanup_failed"}
    assert _database_state(env["second"]) == before
    assert _private_tree(target) == images
    assert _outside_metadata(env["second"]) == outside
    assert not list(target.root.glob(".todo-comment-image-restore-*"))
    env["report"]["q2_restore_boundary"] = {
        "kind": "external_fk_restrict",
        "cleanup_refused": True,
        "full_database_private_images_owner_acl_extension_verified": True,
    }


@pytest.mark.parametrize("postgres_pair", [33], indirect=True)
def test_real_pg_restore_sql_exit_one_rolls_back_and_recovers_full_current_preimage(
    postgres_pair: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = postgres_pair
    role = "ps04c_import_acl_" + uuid4().hex[:12]
    archive, target = _old_archive_and_current_target(env, tmp_path, monkeypatch, missing_role=role)
    pool = env["first"]
    _external_schema(pool)
    before, images, outside = _database_state(pool), _private_tree(target), _outside_metadata(pool)
    real_run = pg_dump.subprocess.run
    restore_exits: list[int] = []

    def observe_tool(command, **kwargs):
        result = real_run(command, **kwargs)
        if Path(command[0]).name == "pg_restore" and "--dbname" in command:
            restore_exits.append(result.returncode)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(pg_dump.subprocess, "run", observe_tool)
        with pytest.raises(OctopError) as raised:
            system_archive.restore_system_backup(
                archive,
                paths=target,
                pool=pool,
                db_config=_config(env),
                restore_config=False,
                preserve_users=False,
            )
    assert restore_exits == [1, 0]
    assert raised.value.details == {
        "reason": "backup_restore_tool_failed",
        "stage": "restore",
        "exit_code": 1,
    }
    assert role not in str(raised.value) and "127.0.0.1" not in str(raised.value)
    assert _database_state(env["second"]) == before
    assert _private_tree(target) == images
    assert _outside_metadata(env["second"]) == outside
    with env["second"].connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM pg_roles WHERE rolname=?", (role,)).fetchone()[0]
            == 0
        )
    assert not list(target.root.glob(".todo-comment-image-restore-*"))
    env["report"]["q2_restore_boundary"] = {
        "kind": "real_sql_exit_one",
        "actual_tool_exit_codes": restore_exits,
        "full_database_private_images_owner_acl_extension_verified": True,
        "synthetic_missing_role_removed": True,
    }
