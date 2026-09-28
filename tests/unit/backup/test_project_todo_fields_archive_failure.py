"""A failed 033-to-034 restore keeps the existing database and private tree together."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from tests.unit.backup.test_project_todo_fields_archive import _seed_archive_rows

from octop.config import DatabaseConfig
from octop.infra.backup import system_archive
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.db.project_plan_seed import seed_todo_catalog
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ulid import new_ulid


def _private_tree(layout: PathLayout) -> dict[str, str]:
    return {
        path.relative_to(layout.project_todo_comment_images).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in layout.project_todo_comment_images.rglob("*")
        if path.is_file()
    }


def _database_dump(pool: SqlitePool) -> tuple[str, ...]:
    with pool.connect() as conn:
        return tuple(conn.iterdump())


def _seed_plan_fields(
    pool: DatabasePool, layout: PathLayout, project_id: str, todo_id: str, marker: str
) -> None:
    """Give archive/current states different real plan refs and private bytes."""
    png = BytesIO()
    Image.new("RGB", (3, 2), "green" if marker == "current" else "purple").save(png, format="PNG")
    image_bytes = png.getvalue()
    with pool.transaction() as conn:
        seed_todo_catalog(conn, project_id, 21)
        priorities = conn.execute(
            "SELECT priority_id FROM project_todo_priorities WHERE project_id=? ORDER BY position",
            (project_id,),
        ).fetchall()
        priority_id = priorities[1 if marker == "current" else 0][0]
        conn.execute(
            "UPDATE project_todo_priorities SET name=?,name_key=?,archived_at=37,updated_at=37 "
            "WHERE priority_id=?",
            (f"{marker} priority", f"{marker} priority", priority_id),
        )
        tag_id = new_ulid()
        conn.execute(
            "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,archived_at,"
            "created_at,updated_at) VALUES (?,?,?,?,'purple',37,21,37)",
            (tag_id, project_id, f"{marker} tag", f"{marker} tag"),
        )
        conn.execute("DELETE FROM project_todo_tag_links WHERE todo_id=?", (todo_id,))
        conn.execute(
            "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
            (project_id, todo_id, tag_id),
        )
        conn.execute(
            "UPDATE project_todos SET title=?,description=?,version=?,start_date=?,due_date=?,"
            "priority_id=? WHERE todo_id=?",
            (
                f"{marker} todo",
                f"{marker} description",
                11 if marker == "current" else 7,
                "2000-02-29" if marker == "current" else "1900-01-01",
                "9999-12-31" if marker == "current" else "1900-03-01",
                priority_id,
                todo_id,
            ),
        )
        conn.execute("UPDATE project_spaces SET name=? WHERE project_id=?", (marker, project_id))
        conn.execute(
            "UPDATE project_todo_catalog_state SET revision=?,updated_at=37 WHERE project_id=?",
            (9 if marker == "current" else 8, project_id),
        )
        conn.execute(
            "UPDATE project_todo_comments SET body_text=? WHERE todo_id=?", (marker, todo_id)
        )
        object_key = conn.execute("SELECT object_key FROM project_todo_comment_images").fetchone()[
            0
        ]
        conn.execute(
            "UPDATE project_todo_comment_images SET size_bytes=?,sha256=?",
            (len(image_bytes), hashlib.sha256(image_bytes).hexdigest()),
        )
        conn.execute(
            "UPDATE project_todo_comment_image_usage SET used_bytes=? WHERE project_id=?",
            (len(image_bytes), project_id),
        )
    image = layout.project_todo_comment_images / object_key
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(image_bytes)


@pytest.fixture
def restore_case(tmp_path: Path) -> Iterator[dict[str, Any]]:
    source = PathLayout(tmp_path / "archive-034")
    source_pool = SqlitePool(source.db)
    archive = tmp_path / "archive-034.tar.gz"
    try:
        run_migrations(source_pool)
        project_id, todo_id, _ = _seed_archive_rows(source_pool, source)
        _seed_plan_fields(source_pool, source, project_id, todo_id, "archive")
        source_database = _database_dump(source_pool)
        source_tree = _private_tree(source)
        system_archive.create_system_backup(
            paths=source,
            agent_rows=[],
            pool=source_pool,
            db_config=DatabaseConfig(),
            dest=archive,
        )
    finally:
        source_pool.close()
    target = PathLayout(tmp_path / "current-034")
    pool = SqlitePool(target.db)
    try:
        run_migrations(pool)
        project_id, todo_id, _ = _seed_archive_rows(pool, target)
        _seed_plan_fields(pool, target, project_id, todo_id, "current")
        yield {
            "archive": archive,
            "target": target,
            "pool": pool,
            "database_before": _database_dump(pool),
            "tree_before": _private_tree(target),
            "source_database": source_database,
            "source_tree": source_tree,
        }
    finally:
        pool.close()


def _restore(case: dict[str, Any]) -> dict[str, Any]:
    return system_archive.restore_system_backup(
        case["archive"],
        paths=case["target"],
        pool=case["pool"],
        db_config=DatabaseConfig(),
        restore_config=False,
    )


def _assert_unchanged(case: dict[str, Any]) -> None:
    assert _database_dump(case["pool"]) == case["database_before"]
    assert _private_tree(case["target"]) == case["tree_before"]
    with case["pool"].connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("interrupted", [False, True], ids=["upgrade-error", "interrupt"])
def test_restore_upgrade_failure_keeps_database_and_private_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    migration = import_module("octop.infra.db.migrate")
    discover = migration._discover
    source = PathLayout(tmp_path / "source-033")
    source_pool = SqlitePool(source.db)
    archive = tmp_path / "old-033.tar.gz"
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migration,
                "_discover",
                lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 33],
            )
            run_migrations(source_pool)
        _seed_archive_rows(source_pool, source)
        system_archive.create_system_backup(
            paths=source,
            agent_rows=[],
            pool=source_pool,
            db_config=DatabaseConfig(),
            dest=archive,
        )
    finally:
        source_pool.close()

    target = PathLayout(tmp_path / "existing-034")
    target_pool = SqlitePool(target.db)
    try:
        run_migrations(target_pool)
        project_id, todo_id, _image = _seed_archive_rows(target_pool, target)
        _seed_plan_fields(target_pool, target, project_id, todo_id, "current")
        with target_pool.connect() as conn:
            before_database = tuple(conn.iterdump())
        before_tree = _private_tree(target)
        real_seed = migration._seed_project_todo_catalogs_v34
        reached_real_ddl_and_seed = []

        def fail_after_real_seed(conn: Any) -> None:
            real_seed(conn)
            assert conn.execute("SELECT count(*) FROM project_todo_priorities").fetchone()[0] == 4
            reached_real_ddl_and_seed.append(True)
            if interrupted:
                raise KeyboardInterrupt("injected after real 034 DDL and seed")
            raise RuntimeError("injected after real 034 DDL and seed")

        with monkeypatch.context() as patch:
            patch.setattr(migration, "_seed_project_todo_catalogs_v34", fail_after_real_seed)
            expected = KeyboardInterrupt if interrupted else OctopError
            with pytest.raises(expected, match="injected after real 034 DDL and seed"):
                system_archive.restore_system_backup(
                    archive, paths=target, pool=target_pool, db_config=DatabaseConfig()
                )
        assert reached_real_ddl_and_seed == [True]
        with target_pool.connect() as conn:
            assert tuple(conn.iterdump()) == before_database
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _private_tree(target) == before_tree
    finally:
        target_pool.close()


@pytest.mark.parametrize("interrupted", [False, True], ids=["replace-error", "interrupt"])
def test_database_replace_failure_preserves_complete_current_state_and_retry_succeeds(
    restore_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    real_restore = system_archive.restore_sqlite_into_pool
    calls = []

    def fail_after_replace(source: Path, pool: SqlitePool) -> None:
        calls.append(source)
        real_restore(source, pool)
        if len(calls) == 1:
            if interrupted:
                raise KeyboardInterrupt("injected after real database replacement")
            raise RuntimeError("injected after real database replacement")

    with monkeypatch.context() as patch:
        patch.setattr(system_archive, "restore_sqlite_into_pool", fail_after_replace)
        with pytest.raises(
            KeyboardInterrupt if interrupted else RuntimeError,
            match="injected after real database replacement",
        ):
            _restore(restore_case)
    _assert_unchanged(restore_case)
    assert len(calls) == 2
    result = _restore(restore_case)
    assert result["schema_version"] == 34 and result["project_todo_comment_image_files"] == 1
    assert _database_dump(restore_case["pool"]) == restore_case["source_database"]
    assert _private_tree(restore_case["target"]) == restore_case["source_tree"]


@pytest.mark.parametrize("interrupted", [False, True], ids=["snapshot-error", "interrupt"])
def test_pre_restore_snapshot_failure_never_installs_database_or_private_tree(
    restore_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    calls = []

    def fail_snapshot(source: Path, dest: Path) -> None:
        assert source == restore_case["pool"].path
        calls.append("snapshot")
        if interrupted:
            raise KeyboardInterrupt("injected snapshot failure")
        raise OSError("injected snapshot failure")

    def refuse_install(*args: Any, **kwargs: Any) -> None:
        calls.append("install")
        raise AssertionError("snapshot must complete before any install")

    monkeypatch.setattr(system_archive, "snapshot_sqlite_file", fail_snapshot)
    monkeypatch.setattr(system_archive, "restore_sqlite_into_pool", refuse_install)
    monkeypatch.setattr(system_archive, "_install_private_comment_images", refuse_install)
    with pytest.raises(
        KeyboardInterrupt if interrupted else OSError, match="injected snapshot failure"
    ):
        _restore(restore_case)
    assert calls == ["snapshot"]
    _assert_unchanged(restore_case)


@pytest.mark.parametrize("failed_resource", ["database", "private_comment_images"])
def test_rollback_failure_is_explicit_safe_and_still_restores_other_resource(
    restore_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, failed_resource: str
) -> None:
    real_restore = system_archive.restore_sqlite_into_pool
    real_migrate = system_archive.run_migrations
    db_calls, image_calls = [], []

    def database_restore(source: Path, pool: SqlitePool) -> None:
        db_calls.append(source)
        if len(db_calls) == 2 and failed_resource == "database":
            raise RuntimeError("postgresql://secret-user:SECRET_CREDENTIAL@private-host/db")
        real_restore(source, pool)

    def fail_after_upgrade(pool: DatabasePool) -> None:
        real_migrate(pool)
        raise RuntimeError("injected after successful migration")

    real_image_rollback = system_archive._rollback_private_comment_images

    def image_rollback(stage: Path, dest: Path) -> None:
        image_calls.append(dest)
        if failed_resource == "private_comment_images":
            raise KeyboardInterrupt("SECRET_CREDENTIAL image rollback interruption")
        real_image_rollback(stage, dest)

    monkeypatch.setattr(system_archive, "restore_sqlite_into_pool", database_restore)
    monkeypatch.setattr(system_archive, "run_migrations", fail_after_upgrade)
    monkeypatch.setattr(system_archive, "_rollback_private_comment_images", image_rollback)
    with pytest.raises(BaseException) as error:
        _restore(restore_case)
    assert isinstance(error.value, OctopError)
    assert error.value.code == ErrorCode.INTERNAL_ERROR and error.value.status == 500
    assert error.value.details == {
        "reason": "backup_restore_rollback_failed",
        "failed_resources": [failed_resource],
    }
    assert "SECRET_CREDENTIAL" not in str(error.value)
    assert "SECRET_CREDENTIAL" not in str(error.value.to_envelope())
    assert len(db_calls) == 2 and len(image_calls) == 1
    if failed_resource == "database":
        assert _private_tree(restore_case["target"]) == restore_case["tree_before"]
        assert _database_dump(restore_case["pool"]) != restore_case["database_before"]
    else:
        assert _database_dump(restore_case["pool"]) == restore_case["database_before"]
        assert _private_tree(restore_case["target"]) != restore_case["tree_before"]
