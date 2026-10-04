"""C1 backup gates: old archives upgrade and current plan references survive."""

from __future__ import annotations

import hashlib
import tarfile
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from octop.config import DatabaseConfig
from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
from octop.infra.db.migrate import _max_discovered_version, run_migrations
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.db.project_plan_seed import seed_todo_catalog, seed_todo_views
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.project_plan_keys import normalize_project_plan_key
from octop.infra.utils.ulid import new_ulid

OLD_COLUMNS = (
    "id,todo_id,project_id,creator_user_id,assignee_user_id,title,description,status,"
    "version,created_at,updated_at,deleted_at,description_format"
)
CHILD_TABLES = (
    "project_todo_comments",
    "project_todo_comment_images",
    "project_todo_comment_image_usage",
)
CATALOG_TABLES = (
    "project_todo_catalog_state",
    "project_todo_priorities",
    "project_todo_tags",
    "project_todo_tag_links",
)


def _rows(conn: Any, table: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]


def _has_plan_keys(conn: Any, dialect: str) -> bool:
    if dialect == "postgresql":
        return (
            conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() "
                "AND table_name='project_todos' AND column_name='title_search_key'"
            ).fetchone()
            is not None
        )
    return "title_search_key" in {
        row["name"] for row in conn.execute("PRAGMA table_info(project_todos)")
    }


def _seed_archive_rows(pool: DatabasePool, layout: PathLayout) -> tuple[str, str, bytes]:
    project_id, todo_id, comment_id, image_id = [new_ulid() for _ in range(4)]
    png = BytesIO()
    Image.new("RGB", (3, 2), "purple").save(png, format="PNG")
    image_bytes = png.getvalue()
    object_key = f"{project_id}/{image_id}"
    with pool.transaction() as conn:
        current_keys = _has_plan_keys(conn, pool.dialect)
        user_column = ",project_plan_display_sort_key" if current_keys else ""
        user_value = ",?" if current_keys else ""
        conn.execute(
            f"INSERT INTO users(id,username,password_hash,role,created_at{user_column}) "
            f"VALUES (1,'c1-backup-owner','synthetic-hash','admin',11{user_value})",
            (normalize_project_plan_key("c1-backup-owner"),) if current_keys else (),
        )
        conn.execute(
            "INSERT INTO project_spaces(project_id,creator_user_id,name,created_at,updated_at) "
            "VALUES (?,1,'旧项目',11,12)",
            (project_id,),
        )
        conn.execute(
            "INSERT INTO project_members(project_id,user_id,role,joined_at) "
            "VALUES (?,1,'owner',11)",
            (project_id,),
        )
        for pk, current_id, deleted, fmt in (
            (17, todo_id, None, "markdown"),
            (29, new_ulid(), 90, "plain"),
        ):
            conn.execute(
                f"INSERT INTO project_todos({OLD_COLUMNS}{',title_search_key' if current_keys else ''}) "
                f"VALUES (?,?,?,1,1,'原待办','**原文**','done',7,11,12,?,?{',?' if current_keys else ''})",
                (
                    pk,
                    current_id,
                    project_id,
                    deleted,
                    fmt,
                    *((normalize_project_plan_key("原待办"),) if current_keys else ()),
                ),
            )
        if conn.execute("SELECT version FROM _schema_version").fetchone()[0] >= 37:
            conn.execute(
                "INSERT INTO project_todo_display_state(project_id,todo_id,revision,updated_at) "
                "SELECT project_id,todo_id,1,updated_at FROM project_todos"
            )
            conn.execute(
                "INSERT INTO project_todo_attachment_state(project_id,todo_id,revision,updated_at) "
                "SELECT project_id,todo_id,1,updated_at FROM project_todos"
            )
            conn.execute(
                "INSERT INTO project_todo_attachment_usage(project_id,used_bytes,updated_at) "
                "VALUES (?,0,11)",
                (project_id,),
            )
        if current_keys:
            seed_todo_catalog(conn, project_id, 11)
            seed_todo_views(conn, project_id, 11)
        conn.execute(
            "INSERT INTO project_todo_comments(comment_id,todo_id,author_user_id,body_text,"
            "client_request_id,request_fingerprint,created_at) VALUES (?,?,1,'原评论',?,?,13)",
            (comment_id, todo_id, "00000000-0000-4000-8000-000000000001", "a" * 64),
        )
        conn.execute(
            "INSERT INTO project_todo_comment_images(image_id,comment_id,object_key,"
            "size_bytes,sha256,media_type,position,created_at) "
            "VALUES (?,?,?,?,?,'image/png',0,14)",
            (
                image_id,
                comment_id,
                object_key,
                len(image_bytes),
                hashlib.sha256(image_bytes).hexdigest(),
            ),
        )
        conn.execute(
            "INSERT INTO project_todo_comment_image_usage(project_id,used_bytes) VALUES (?,?)",
            (project_id, len(image_bytes)),
        )
    image_file = layout.project_todo_comment_images / object_key
    image_file.parent.mkdir(parents=True)
    image_file.write_bytes(image_bytes)
    return project_id, todo_id, image_bytes


@pytest.mark.parametrize("old_archive", [True, False], ids=["033-upgrade", "034-roundtrip"])
def test_plan_backup_restore_preserves_ids_references_and_private_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_archive: bool
) -> None:
    source = PathLayout(tmp_path / "source")
    source_pool = SqlitePool(source.db)
    try:
        source_version = 33 if old_archive else 34
        migration = import_module("octop.infra.db.migrate")
        discover = migration._discover
        with monkeypatch.context() as patch:
            patch.setattr(
                migration,
                "_discover",
                lambda dialect: [
                    (version, path)
                    for version, path in discover(dialect)
                    if version <= source_version
                ],
            )
            run_migrations(source_pool)
        project_id, todo_id, image_bytes = _seed_archive_rows(source_pool, source)
        if not old_archive:
            with source_pool.transaction() as conn:
                seed_todo_catalog(conn, project_id, 19)
                priority_id = conn.execute(
                    "SELECT priority_id FROM project_todo_priorities WHERE project_id=? "
                    "ORDER BY position LIMIT 1",
                    (project_id,),
                ).fetchone()[0]
                conn.execute(
                    "UPDATE project_todo_priorities SET name='历史优先级',name_key='历史优先级',"
                    "archived_at=37,updated_at=37 WHERE priority_id=?",
                    (priority_id,),
                )
                tag_id = new_ulid()
                conn.execute(
                    "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,"
                    "archived_at,created_at,updated_at) VALUES (?,?,'历史标签','历史标签','purple',37,19,37)",
                    (tag_id, project_id),
                )
                conn.execute(
                    "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
                    (project_id, todo_id, tag_id),
                )
                conn.execute(
                    "UPDATE project_todos SET start_date='2000-02-29',due_date='2000-03-01',"
                    "priority_id=? WHERE todo_id=?",
                    (priority_id, todo_id),
                )
                conn.execute(
                    "UPDATE project_todo_catalog_state SET revision=8,updated_at=37 WHERE project_id=?",
                    (project_id,),
                )
        with source_pool.connect() as conn:
            old_todos = [
                tuple(row)
                for row in conn.execute(f"SELECT {OLD_COLUMNS} FROM project_todos ORDER BY id")
            ]
            children = {table: _rows(conn, table) for table in CHILD_TABLES}
            catalog = (
                {table: _rows(conn, table) for table in CATALOG_TABLES} if not old_archive else None
            )
            plan_fields = (
                [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT start_date,due_date,priority_id FROM project_todos ORDER BY id"
                    )
                ]
                if not old_archive
                else [(None, None, None)] * 2
            )
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == (
                33 if old_archive else 34
            )
        archive = tmp_path / "plan.tar.gz"
        create_system_backup(
            paths=source,
            agent_rows=[],
            pool=source_pool,
            db_config=DatabaseConfig(),
            dest=archive,
        )
        with tarfile.open(archive, "r:gz") as tf:
            assert any(name.startswith("project-todo-comment-images/") for name in tf.getnames())
    finally:
        source_pool.close()

    restored = PathLayout(tmp_path / "restored")
    restored_pool = SqlitePool(restored.db)
    try:
        run_migrations(restored_pool)
        result = restore_system_backup(
            archive,
            paths=restored,
            pool=restored_pool,
            db_config=DatabaseConfig(),
            restore_config=False,
        )
        assert result["schema_version"] == _max_discovered_version("sqlite")
        with restored_pool.connect() as conn:
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
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            if old_archive:
                assert (
                    conn.execute(
                        "SELECT revision FROM project_todo_catalog_state WHERE project_id=?",
                        (project_id,),
                    ).fetchone()[0]
                    == 1
                )
                assert [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT name,color,position FROM project_todo_priorities WHERE project_id=? ORDER BY position",
                        (project_id,),
                    )
                ] == [("紧急", "red", 0), ("高", "orange", 1), ("中", "blue", 2), ("低", "gray", 3)]
            else:
                assert {table: _rows(conn, table) for table in CATALOG_TABLES} == catalog
            object_key = conn.execute(
                "SELECT object_key FROM project_todo_comment_images"
            ).fetchone()[0]
        assert (restored.project_todo_comment_images / object_key).read_bytes() == image_bytes
        assert result["project_todo_comment_image_files"] == 1
    finally:
        restored_pool.close()
