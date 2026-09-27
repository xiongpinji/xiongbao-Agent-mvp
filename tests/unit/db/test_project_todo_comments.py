"""Migration and repository behavior for PS-04B text comments."""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

import pytest

from octop.infra.db.migrate import _discover, _max_discovered_version, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.ulid import new_ulid

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"
REQUEST_ID = "d6e47312-3f3d-4a27-a43a-23c5df13218b"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def seed(db: SqlitePool) -> tuple[str, str, int]:
    users = UserRepo(db)
    owner_id = users.create(username="comment_owner", password_hash="h", role="user")
    projects = ProjectRepo(db)
    project_id = projects.create_with_owner(
        creator_user_id=owner_id, name="评论迁移测试"
    ).project_id
    todo = ProjectTodoRepo(db).create(project_id=project_id, creator_user_id=owner_id, title="待办")
    assert todo.outcome == "created" and todo.row is not None
    return project_id, todo.row.todo_id, owner_id


def _repo(db: SqlitePool):
    return import_module("octop.infra.db.repos.project_todo_comments").ProjectTodoCommentRepo(db)


def test_033_image_tables_and_paired_postgresql_script(db: SqlitePool) -> None:
    with db.connect() as conn:
        images = {
            row["name"] for row in conn.execute("PRAGMA table_info(project_todo_comment_images)")
        }
        usage = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(project_todo_comment_image_usage)")
        }
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert version >= 33
    assert images == {
        "image_id",
        "comment_id",
        "object_key",
        "size_bytes",
        "sha256",
        "media_type",
        "position",
        "created_at",
    }
    assert usage == {"project_id", "used_bytes"}
    assert (MIGRATIONS / "033_project_todo_comment_images.pg.sql").exists()


def test_033_interrupted_upgrade_replays_and_failed_ddl_rolls_back(
    db: SqlitePool,
    tmp_path: Path,
) -> None:
    with db.transaction() as conn:
        conn.execute("DROP TABLE project_todo_comment_image_usage")
        conn.execute("UPDATE _schema_version SET version = 32")
    run_migrations(db)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 33
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'project_todo_comment_image_usage'"
            ).fetchone()
            is not None
        )

    migration = import_module("octop.infra.db.migrate")
    bad_script = tmp_path / "bad.sql"
    bad_script.write_text(
        "CREATE TABLE ps04b_atomic_probe(id INTEGER);\nCREATE TABLE broken syntax;\n",
        encoding="utf-8",
    )
    with pytest.raises(sqlite3.OperationalError):
        migration._ensure_project_todo_comment_images_v33(db, bad_script)
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'ps04b_atomic_probe'"
            ).fetchone()
            is None
        )


def test_031_migration_shape_and_paired_postgresql_script(db: SqlitePool) -> None:
    with db.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_todo_comments)")}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(project_todo_comments)")}
    assert version == _max_discovered_version("sqlite")
    assert columns == {
        "comment_id",
        "todo_id",
        "author_user_id",
        "body_text",
        "client_request_id",
        "request_fingerprint",
        "created_at",
    }
    assert "idx_project_todo_comments_todo_time" in indexes
    assert "uq_project_todo_comments_request" in indexes
    assert any(
        version == 31 and path.name == "031_project_todo_comments.sql"
        for version, path in _discover("sqlite")
    )
    assert any(
        version == 31 and path.name == "031_project_todo_comments.pg.sql"
        for version, path in _discover("postgresql")
    )
    sqlite_sql = (MIGRATIONS / "031_project_todo_comments.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "031_project_todo_comments.pg.sql").read_text(encoding="utf-8")
    for token in (
        "REFERENCES project_todos(todo_id) ON DELETE CASCADE",
        "REFERENCES users(id) ON DELETE SET NULL",
        "request_fingerprint TEXT NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_todo_comments_request",
    ):
        assert token in sqlite_sql and token in pg_sql


def test_031_upgrades_existing_v30_database(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "old.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS project_todo_comments; UPDATE _schema_version SET version = 30;"
        )
    run_migrations(pool)
    with pool.connect() as conn:
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_todo_comments'"
        ).fetchone()
    assert version >= 31
    assert table is not None


def test_031_allows_empty_body_for_future_image_comments_but_rejects_overlong(
    db: SqlitePool, seed: tuple[str, str, int]
) -> None:
    _pid, todo_id, owner_id = seed
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_todo_comments("
            "comment_id,todo_id,author_user_id,body_text,client_request_id,"
            "request_fingerprint,created_at) VALUES (?,?,?,?,?,?,?)",
            ("c0", todo_id, owner_id, "", REQUEST_ID, "f" * 64, 1),
        )
    with db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_todo_comments("
            "comment_id,todo_id,author_user_id,body_text,client_request_id,"
            "request_fingerprint,created_at) VALUES (?,?,?,?,?,?,?)",
            ("c1", todo_id, owner_id, "x" * 4001, "another", "f" * 64, 1),
        )
    with db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_todo_comments("
            "comment_id,todo_id,author_user_id,body_text,client_request_id,"
            "request_fingerprint,created_at) VALUES (?,?,?,?,?,?,?)",
            ("c2", todo_id, owner_id, "duplicate", REQUEST_ID, "f" * 64, 1),
        )


def test_031_author_deletion_preserves_comment_and_todo_deletion_cascades(
    db: SqlitePool, seed: tuple[str, str, int]
) -> None:
    project_id, todo_id, _owner_id = seed
    member_id = UserRepo(db).create(username="departing_member", password_hash="h", role="user")
    ProjectRepo(db).add_member(project_id, member_id, role="member")
    repo = _repo(db)
    result = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=member_id,
        body="保留内容",
        client_request_id=REQUEST_ID,
    )
    assert result.outcome == "created"
    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (member_id,))
        row = conn.execute(
            "SELECT author_user_id, body_text FROM project_todo_comments WHERE todo_id = ?",
            (todo_id,),
        ).fetchone()
    assert row["author_user_id"] is None
    assert row["body_text"] == "保留内容"
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_todos WHERE todo_id = ?", (todo_id,))
        remaining = conn.execute(
            "SELECT COUNT(*) FROM project_todo_comments WHERE todo_id = ?", (todo_id,)
        ).fetchone()[0]
    assert remaining == 0


def test_repeated_request_has_one_comment_and_one_event(
    db: SqlitePool, seed: tuple[str, str, int]
) -> None:
    project_id, todo_id, owner_id = seed
    repo = _repo(db)
    first = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=owner_id,
        body="评论",
        client_request_id=REQUEST_ID,
    )
    retry = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=owner_id,
        body="评论",
        client_request_id=REQUEST_ID,
    )
    conflict = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=owner_id,
        body="不同",
        client_request_id=REQUEST_ID,
    )
    assert first.outcome == "created" and first.row is not None
    assert retry.outcome == "reused" and retry.row == first.row
    assert conflict.outcome == "conflict"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM project_todo_comments WHERE todo_id = ?", (todo_id,)
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id = ? "
            "AND event_type = 'project.todo_comment_created'",
            (project_id,),
        ).fetchone()[0]
    assert count == events == 1


def test_image_comment_retry_preserves_order_and_charges_quota_once(
    db: SqlitePool, seed: tuple[str, str, int]
) -> None:
    module = import_module("octop.infra.db.repos.project_todo_comments")
    project_id, todo_id, owner_id = seed

    def image(size: int, digest: str, media_type: str):
        image_id = new_ulid()
        return module.NewCommentImage(
            image_id, f"{project_id}/{image_id}", size, digest, media_type
        )

    first_images = (
        image(21, "a" * 64, "image/png"),
        image(34, "b" * 64, "image/jpeg"),
    )
    repo = _repo(db)
    first = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=owner_id,
        body="",
        client_request_id=REQUEST_ID,
        images=first_images,
    )
    assert first.outcome == "created" and first.row is not None
    assert [image.sha256 for image in first.row.images] == ["a" * 64, "b" * 64]
    assert [image.position for image in first.row.images] == [0, 1]

    retry_images = (
        image(21, "a" * 64, "image/png"),
        image(34, "b" * 64, "image/jpeg"),
    )
    retry = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=owner_id,
        body="",
        client_request_id=REQUEST_ID,
        images=retry_images,
    )
    reversed_images = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=owner_id,
        body="",
        client_request_id=REQUEST_ID,
        images=retry_images[::-1],
    )
    assert retry.outcome == "reused" and retry.row == first.row
    assert reversed_images.outcome == "conflict"
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM project_todo_comment_images").fetchone()[0]
        usage = conn.execute(
            "SELECT used_bytes FROM project_todo_comment_image_usage WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id = ? "
            "AND event_type = 'project.todo_comment_created'",
            (project_id,),
        ).fetchone()[0]
    assert (count, usage, events) == (2, 55, 1)


def test_image_metadata_cannot_point_into_another_project(
    db: SqlitePool, seed: tuple[str, str, int]
) -> None:
    module = import_module("octop.infra.db.repos.project_todo_comments")
    project_id, todo_id, owner_id = seed
    image_id = new_ulid()
    with pytest.raises(ValueError):
        _repo(db).create_comment(
            project_id=project_id,
            todo_id=todo_id,
            author_user_id=owner_id,
            body="",
            client_request_id=REQUEST_ID,
            images=[
                module.NewCommentImage(
                    image_id, f"{new_ulid()}/{image_id}", 21, "a" * 64, "image/png"
                )
            ],
        )
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_todo_comments").fetchone()[0] == 0


@pytest.mark.parametrize("competing_change", ["remove_member", "delete_todo"])
def test_comment_commit_serializes_with_revocation_or_todo_delete(
    db: SqlitePool,
    seed: tuple[str, str, int],
    monkeypatch: pytest.MonkeyPatch,
    competing_change: str,
) -> None:
    """Two independent SQLite connections overlap at the comment event write."""
    project_id, todo_id, owner_id = seed
    member_id = UserRepo(db).create(username="comment_member", password_hash="h", role="user")
    ProjectRepo(db).add_member(project_id, member_id, role="member")
    other_pool = SqlitePool(db.path)
    repo = _repo(db)
    module = import_module("octop.infra.db.repos.project_todo_comments")
    original_append = module._append_comment_event
    comment_paused = threading.Event()
    release_comment = threading.Event()
    competing_started = threading.Event()

    def paused_append(*args: object) -> None:
        comment_paused.set()
        assert release_comment.wait(timeout=10), "comment transaction never resumed"
        original_append(*args)

    def compete() -> object:
        competing_started.set()
        if competing_change == "remove_member":
            return ProjectRepo(other_pool).remove_member(
                project_id=project_id, user_id=member_id, actor_user_id=owner_id
            )
        return ProjectTodoRepo(other_pool).delete(
            project_id=project_id,
            todo_id=todo_id,
            actor_user_id=owner_id,
            expected_version=1,
        )

    monkeypatch.setattr(module, "_append_comment_event", paused_append)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            comment_future = workers.submit(
                repo.create_comment,
                project_id=project_id,
                todo_id=todo_id,
                author_user_id=member_id,
                body="竞争中的评论",
                client_request_id=REQUEST_ID,
            )
            assert comment_paused.wait(timeout=10), "comment did not reach its write transaction"
            competing_future = workers.submit(compete)
            assert competing_started.wait(timeout=10), "competing writer did not start"
            time.sleep(0.1)
            assert not competing_future.done(), "competing writer bypassed the comment transaction"
            release_comment.set()
            comment = comment_future.result(timeout=10)
            change = competing_future.result(timeout=10)
    finally:
        release_comment.set()
        other_pool.close()

    assert comment.outcome == "created"
    assert change.outcome == ("removed" if competing_change == "remove_member" else "deleted")
    denied = repo.create_comment(
        project_id=project_id,
        todo_id=todo_id,
        author_user_id=member_id,
        body="不应成功",
        client_request_id="d6e47313-3f3d-4a27-a43a-23c5df13218b",
    )
    assert denied.outcome == "missing"
    with db.connect() as conn:
        comments = conn.execute(
            "SELECT COUNT(*) FROM project_todo_comments WHERE todo_id = ?", (todo_id,)
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id = ? "
            "AND event_type = 'project.todo_comment_created'",
            (project_id,),
        ).fetchone()[0]
    assert comments == events == 1
