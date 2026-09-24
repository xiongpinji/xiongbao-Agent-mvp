"""Unit tests for ProjectAssetRepo, migration 023, asset storage, and service.

PS-06A / 023A: project members keep private files and folders in their own
project-asset root. Covers the frozen ``docs/xiongbao/PROJECT_ASSET_CONTRACT.md``
schema (composite child FKs, parent-side composite UNIQUE, single hidden root
per project, single current version per file, 3-level CASCADE, user SET NULL),
the SQLite→PostgreSQL migration-pair token parity (no live PG in this suite),
DB-enforced same-parent ``name_key`` conflicts (Foo vs foo), the fixed publish
order with its four crash windows (W1 mid-stream temp, W2 pre-rename temp,
W3 orphaned final, W4 committed retry → 409), the restricted 24h-grace orphan
cleaner, membership/archived policy with uniform 404s, filter-before-page
listing with the full ``(kind, name_key, node_id)`` order, and usage sums that
only count committed current file versions.
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import stat
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any, BinaryIO, cast

import pytest

from octop.infra.db.migrate import _max_discovered_version, _split_pg_sql, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_assets import ProjectAssetRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.asset_storage import (
    RECLAIM_GRACE_SECONDS,
    AssetStorageError,
    AssetUploadTooLarge,
    ProjectAssetStorage,
    make_object_key,
    open_regular_file_no_follow,
)
from octop.infra.projects.assets import (
    ASSET_NAME_MAX_LENGTH,
    ProjectAssetService,
    asset_name_key,
    validate_asset_name,
)
from octop.infra.utils.ulid import new_ulid

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"

_MAX_BYTES = 1024 * 1024


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> ProjectAssetRepo:
    return ProjectAssetRepo(db)


@pytest.fixture
def projects(db: SqlitePool) -> ProjectRepo:
    return ProjectRepo(db)


@pytest.fixture
def users(db: SqlitePool) -> UserRepo:
    return UserRepo(db)


@pytest.fixture
def owner_id(users: UserRepo) -> int:
    return users.create(username="owner", password_hash="h", role="user")


@pytest.fixture
def member_id(users: UserRepo) -> int:
    return users.create(username="member", password_hash="h", role="user")


@pytest.fixture
def other_member_id(users: UserRepo) -> int:
    return users.create(username="othermember", password_hash="h", role="user")


@pytest.fixture
def outsider_id(users: UserRepo) -> int:
    return users.create(username="outsider", password_hash="h", role="user")


@pytest.fixture
def pid(projects: ProjectRepo, owner_id: int, member_id: int, other_member_id: int) -> str:
    project = projects.create_with_owner(creator_user_id=owner_id, name="资产项目")
    projects.add_member(project.project_id, member_id, role="member")
    projects.add_member(project.project_id, other_member_id, role="member")
    return project.project_id


@pytest.fixture
def storage(tmp_path: Path) -> ProjectAssetStorage:
    return ProjectAssetStorage(tmp_path / "assets")


class _StubServices:
    def __init__(self, projects: ProjectRepo, assets: ProjectAssetRepo) -> None:
        self.project_repo = projects
        self.project_asset_repo = assets


@pytest.fixture
def service(
    projects: ProjectRepo, repo: ProjectAssetRepo, storage: ProjectAssetStorage
) -> ProjectAssetService:
    return ProjectAssetService(_StubServices(projects, repo), storage=storage)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _upload(
    service: ProjectAssetService,
    project_id: str,
    user_id: int,
    *,
    name: str = "报告.txt",
    data: bytes = b"hello",
    parent_id: str | None = None,
    max_bytes: int = _MAX_BYTES,
) -> Any:
    return service.upload_file(
        project_id,
        user_id=user_id,
        filename=name,
        stream=io.BytesIO(data),
        max_bytes=max_bytes,
        parent_id=parent_id,
    )


def _node_rows(db: SqlitePool, project_id: str | None = None) -> list[sqlite3.Row]:
    with db.connect() as conn:
        if project_id is None:
            return conn.execute(
                "SELECT node_id, project_id, parent_node_id, kind, name, name_key "
                "FROM project_asset_nodes ORDER BY created_at, node_id"
            ).fetchall()
        return conn.execute(
            "SELECT node_id, project_id, parent_node_id, kind, name, name_key "
            "FROM project_asset_nodes WHERE project_id = ? ORDER BY created_at, node_id",
            (project_id,),
        ).fetchall()


def _version_rows(db: SqlitePool, project_id: str | None = None) -> list[sqlite3.Row]:
    with db.connect() as conn:
        if project_id is None:
            return conn.execute(
                "SELECT version_id, project_id, node_id, object_key, size_bytes, "
                "sha256, media_type, uploaded_by, is_current "
                "FROM project_asset_versions ORDER BY created_at, version_id"
            ).fetchall()
        return conn.execute(
            "SELECT version_id, project_id, node_id, object_key, size_bytes, "
            "sha256, media_type, uploaded_by, is_current "
            "FROM project_asset_versions WHERE project_id = ? ORDER BY created_at, version_id",
            (project_id,),
        ).fetchall()


def _archive(db: SqlitePool, project_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (project_id,))


def _seed_root(db: SqlitePool, project_id: str) -> str:
    node_id = new_ulid()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_asset_nodes("
            "node_id, project_id, parent_node_id, kind, name, name_key, "
            "created_by, created_at, updated_at"
            ") VALUES (?, ?, NULL, 'folder', '', '', NULL, 0, 0)",
            (node_id, project_id),
        )
    return node_id


def _seed_node(
    db: SqlitePool,
    *,
    project_id: str,
    parent_node_id: str,
    kind: str = "folder",
    name: str = "seed",
    name_key: str | None = None,
    created_by: int | None = None,
) -> str:
    node_id = new_ulid()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_asset_nodes("
            "node_id, project_id, parent_node_id, kind, name, name_key, "
            "created_by, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (
                node_id,
                project_id,
                parent_node_id,
                kind,
                name,
                name_key or name.casefold(),
                created_by,
            ),
        )
    return node_id


def _seed_version(
    db: SqlitePool,
    *,
    project_id: str,
    node_id: str,
    size_bytes: int = 10,
    is_current: int = 1,
    uploaded_by: int | None = None,
) -> str:
    version_id = new_ulid()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, ?)",
            (
                version_id,
                project_id,
                node_id,
                make_object_key(project_id, version_id),
                size_bytes,
                "0" * 64,
                uploaded_by,
                is_current,
            ),
        )
    return version_id


def _files_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _boom(*args: object, **kwargs: object) -> None:
    raise RuntimeError("injected failure")


# ---------------------------------------------------------------------------
# Migration 023
# ---------------------------------------------------------------------------


def test_migration_023_shape(db: SqlitePool) -> None:
    with db.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        node_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_asset_nodes)").fetchall()
        }
        version_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_asset_versions)").fetchall()
        }
    assert v == _max_discovered_version("sqlite")
    assert v >= 23
    assert {"project_asset_nodes", "project_asset_versions"} <= tables
    assert node_cols == {
        "node_id",
        "project_id",
        "parent_node_id",
        "kind",
        "name",
        "name_key",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert version_cols == {
        "version_id",
        "project_id",
        "node_id",
        "object_key",
        "size_bytes",
        "sha256",
        "media_type",
        "uploaded_by",
        "created_at",
        "is_current",
    }
    assert {
        "idx_project_asset_nodes_root",
        "idx_project_asset_nodes_parent",
        "idx_project_asset_versions_current",
        "idx_project_asset_versions_node",
    } <= indexes


def test_migration_023_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (MIGRATIONS / "023_project_assets.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 23
    # Replaying the older DDL lowers its own watermark; the normal upgrader
    # must be able to reapply later migrations and restore the current one.
    run_migrations(db)
    with db.connect() as conn:
        restored = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert restored == _max_discovered_version("sqlite")


def test_migration_upgrades_from_v22(tmp_path: Path) -> None:
    """A DB at watermark 22 must gain both asset tables by re-running migrations."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_asset_versions;
            DROP TABLE project_asset_nodes;
            UPDATE _schema_version SET version = 22;
            """
        )
    run_migrations(pool)
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert v == _max_discovered_version("sqlite")
    assert {"project_asset_nodes", "project_asset_versions"} <= tables
    # The upgraded schema is functional end to end.
    users = UserRepo(pool)
    projects = ProjectRepo(pool)
    assets = ProjectAssetRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    project = projects.create_with_owner(creator_user_id=owner, name="升级资产")
    root = assets.ensure_root(project.project_id)
    assert root is not None
    created = assets.create_folder(
        project_id=project.project_id,
        actor_user_id=owner,
        parent_node_id=root,
        name="资料",
        name_key="资料",
    )
    assert created.outcome == "created"


def test_migration_023_pg_pair_declares_same_shape() -> None:
    """Token-level parity check of the PostgreSQL script.

    NOTE: no live PostgreSQL was run for 023A; this static check plus the
    identical-dialect DDL (022 precedent) is the extent of PG verification.
    """
    sqlite_sql = (MIGRATIONS / "023_project_assets.sql").read_text(encoding="utf-8")
    pg_sql = (MIGRATIONS / "023_project_assets.pg.sql").read_text(encoding="utf-8")

    shared_tokens = (
        "project_asset_nodes",
        "project_asset_versions",
        "node_id TEXT NOT NULL",
        "project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE",
        "kind TEXT NOT NULL CHECK (kind IN ('file', 'folder'))",
        "name_key TEXT NOT NULL",
        "UNIQUE (project_id, node_id)",
        "UNIQUE (project_id, parent_node_id, name_key)",
        "FOREIGN KEY (project_id, parent_node_id)",
        "REFERENCES project_asset_nodes(project_id, node_id) ON DELETE CASCADE",
        "FOREIGN KEY (project_id, node_id)",
        "created_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "idx_project_asset_nodes_root",
        "ON project_asset_nodes(project_id) WHERE parent_node_id IS NULL",
        "idx_project_asset_nodes_parent",
        "version_id TEXT NOT NULL",
        "object_key TEXT NOT NULL",
        "size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)",
        "sha256 TEXT NOT NULL",
        "is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1))",
        "UNIQUE (project_id, version_id)",
        "idx_project_asset_versions_current",
        "ON project_asset_versions(node_id) WHERE is_current = 1",
        "idx_project_asset_versions_node",
        "UPDATE _schema_version SET version = 23",
    )
    for token in shared_tokens:
        assert token in sqlite_sql, token
        assert token in pg_sql, token
    # No surrogate identity column in either table on either dialect.
    assert "AUTOINCREMENT" not in sqlite_sql
    assert "GENERATED BY DEFAULT" not in pg_sql

    statements = _split_pg_sql(pg_sql)
    assert len(statements) >= 7  # 2 tables + 4 indexes + watermark update
    assert all(stmt.strip() for stmt in statements)


# ---------------------------------------------------------------------------
# DB-enforced constraints
# ---------------------------------------------------------------------------


def test_db_enforces_same_parent_name_key_unique(db: SqlitePool, pid: str) -> None:
    root = _seed_root(db, pid)
    _seed_node(db, project_id=pid, parent_node_id=root, name="Foo", name_key="foo")
    # Same parent, case-variant display name, identical name_key → conflict.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_nodes("
            "node_id, project_id, parent_node_id, kind, name, name_key, "
            "created_by, created_at, updated_at"
            ") VALUES (?, ?, ?, 'folder', 'foo', 'foo', NULL, 0, 0)",
            (new_ulid(), pid, root),
        )
    # A different parent (subfolder) may reuse the name_key.
    folder = _seed_node(db, project_id=pid, parent_node_id=root)
    _seed_node(db, project_id=pid, parent_node_id=folder, name="foo", name_key="foo")


def test_db_enforces_single_root_per_project(db: SqlitePool, pid: str) -> None:
    _seed_root(db, pid)
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_nodes("
            "node_id, project_id, parent_node_id, kind, name, name_key, "
            "created_by, created_at, updated_at"
            ") VALUES (?, ?, NULL, 'folder', '', '', NULL, 0, 0)",
            (new_ulid(), pid),
        )


def test_db_composite_child_fk_blocks_cross_project_links(
    db: SqlitePool, projects: ProjectRepo, pid: str, owner_id: int
) -> None:
    other = projects.create_with_owner(creator_user_id=owner_id, name="其他项目")
    root_a = _seed_root(db, pid)
    root_b = _seed_root(db, other.project_id)
    # Child node claiming project A but pointing at project B's root: the
    # composite (project_id, parent_node_id) FK must reject it.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_nodes("
            "node_id, project_id, parent_node_id, kind, name, name_key, "
            "created_by, created_at, updated_at"
            ") VALUES (?, ?, ?, 'folder', 'x', 'x', NULL, 0, 0)",
            (new_ulid(), pid, root_b),
        )
    file_node = _seed_node(db, project_id=pid, parent_node_id=root_a, kind="file", name="a.txt")
    # Version claiming project B for a node that lives in project A.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, 'k', 1, 'h', NULL, NULL, 0, 1)",
            (new_ulid(), other.project_id, file_node),
        )
    # Version referencing a nonexistent node.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, 'k', 1, 'h', NULL, NULL, 0, 1)",
            (new_ulid(), pid, new_ulid()),
        )
    assert root_b  # referenced to keep the fixture explicit


def test_db_enforces_single_current_version_and_checks(db: SqlitePool, pid: str) -> None:
    root = _seed_root(db, pid)
    file_node = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="a.txt")
    _seed_version(db, project_id=pid, node_id=file_node, is_current=1)
    # Second current version for the same node → partial unique index rejects.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, 'k2', 1, 'h', NULL, NULL, 0, 1)",
            (new_ulid(), pid, file_node),
        )
    # Historical (is_current = 0) versions are allowed alongside the current one.
    _seed_version(db, project_id=pid, node_id=file_node, is_current=0)
    # is_current CHECK only admits 0/1.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, 'k3', 1, 'h', NULL, NULL, 0, 2)",
            (new_ulid(), pid, file_node),
        )
    # size_bytes CHECK rejects negatives; kind CHECK rejects unknown kinds.
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, 'k4', -1, 'h', NULL, NULL, 0, 0)",
            (new_ulid(), pid, file_node),
        )
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_asset_nodes("
            "node_id, project_id, parent_node_id, kind, name, name_key, "
            "created_by, created_at, updated_at"
            ") VALUES (?, ?, ?, 'symlink', 'x', 'x', NULL, 0, 0)",
            (new_ulid(), pid, root),
        )


def test_project_delete_cascades_three_levels(db: SqlitePool, pid: str) -> None:
    root = _seed_root(db, pid)
    folder = _seed_node(db, project_id=pid, parent_node_id=root)
    file_node = _seed_node(db, project_id=pid, parent_node_id=folder, kind="file", name="a.txt")
    _seed_version(db, project_id=pid, node_id=file_node)
    with db.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id = ?", (pid,))
    assert _node_rows(db) == []
    assert _version_rows(db) == []


def test_folder_delete_cascades_children_and_versions(db: SqlitePool, pid: str) -> None:
    root = _seed_root(db, pid)
    folder = _seed_node(db, project_id=pid, parent_node_id=root)
    file_node = _seed_node(db, project_id=pid, parent_node_id=folder, kind="file", name="a.txt")
    _seed_version(db, project_id=pid, node_id=file_node)
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM project_asset_nodes WHERE project_id = ? AND node_id = ?",
            (pid, folder),
        )
    remaining = {r["node_id"] for r in _node_rows(db, pid)}
    assert remaining == {root}
    assert _version_rows(db, pid) == []


def test_user_delete_nulls_actor_columns(db: SqlitePool, pid: str, member_id: int) -> None:
    root = _seed_root(db, pid)
    folder = _seed_node(db, project_id=pid, parent_node_id=root, created_by=member_id)
    file_node = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="a.txt")
    version_id = _seed_version(db, project_id=pid, node_id=file_node, uploaded_by=member_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (member_id,))
    nodes = {r["node_id"]: r for r in _node_rows(db, pid)}
    assert nodes[folder] is not None
    versions = {r["version_id"]: r for r in _version_rows(db, pid)}
    with db.connect() as conn:
        node_row = conn.execute(
            "SELECT created_by FROM project_asset_nodes WHERE node_id = ?", (folder,)
        ).fetchone()
        version_row = conn.execute(
            "SELECT uploaded_by FROM project_asset_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
    assert node_row["created_by"] is None
    assert version_row["uploaded_by"] is None
    assert nodes and versions and file_node


# ---------------------------------------------------------------------------
# Repo: root init, folder create, file commit
# ---------------------------------------------------------------------------


def test_ensure_root_is_idempotent(repo: ProjectAssetRepo, db: SqlitePool, pid: str) -> None:
    first = repo.ensure_root(pid)
    second = repo.ensure_root(pid)
    assert first is not None and first == second
    roots = [r for r in _node_rows(db, pid) if r["parent_node_id"] is None]
    assert len(roots) == 1
    assert roots[0]["kind"] == "folder"
    # Old projects are covered: the read-only getter never inserts.
    assert repo.get_root(pid) == first
    assert repo.ensure_root("ghost-project") is None
    assert repo.get_root("ghost-project") is None


def test_create_folder_outcomes(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int, outsider_id: int
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    created = repo.create_folder(
        project_id=pid,
        actor_user_id=member_id,
        parent_node_id=root,
        name="资料",
        name_key="资料",
    )
    assert created.outcome == "created"
    assert created.row is not None
    assert created.row.kind == "folder"
    assert created.row.parent_node_id == root
    assert created.row.created_by == member_id

    assert (
        repo.create_folder(
            project_id=pid,
            actor_user_id=outsider_id,
            parent_node_id=root,
            name="外人的",
            name_key="外人的",
        ).outcome
        == "not_member"
    )
    assert (
        repo.create_folder(
            project_id="ghost-project",
            actor_user_id=member_id,
            parent_node_id=root,
            name="x",
            name_key="x",
        ).outcome
        == "not_member"
    )
    assert (
        repo.create_folder(
            project_id=pid,
            actor_user_id=member_id,
            parent_node_id=new_ulid(),
            name="x",
            name_key="x",
        ).outcome
        == "parent_missing"
    )
    file_commit = _commit_file(repo, pid, member_id, root, name="a.txt")
    assert file_commit.outcome == "committed" and file_commit.node is not None
    assert (
        repo.create_folder(
            project_id=pid,
            actor_user_id=member_id,
            parent_node_id=file_commit.node.node_id,
            name="x",
            name_key="x",
        ).outcome
        == "parent_not_folder"
    )
    assert (
        repo.create_folder(
            project_id=pid,
            actor_user_id=member_id,
            parent_node_id=root,
            name="资料",
            name_key="资料",
        ).outcome
        == "name_conflict"
    )
    # Case-variant display name folds to the same name_key → DB-enforced conflict.
    assert (
        repo.create_folder(
            project_id=pid,
            actor_user_id=member_id,
            parent_node_id=root,
            name="资料",
            name_key=asset_name_key("资料"),
        ).outcome
        == "name_conflict"
    )
    _archive(db, pid)
    assert (
        repo.create_folder(
            project_id=pid,
            actor_user_id=member_id,
            parent_node_id=root,
            name="归档后",
            name_key="归档后",
        ).outcome
        == "archived"
    )


def _commit_file(
    repo: ProjectAssetRepo,
    project_id: str,
    actor_user_id: int,
    parent_node_id: str,
    *,
    name: str,
    size_bytes: int = 5,
    sha256: str | None = None,
    media_type: str | None = "text/plain",
) -> Any:
    version_id = repo.new_version_id()
    return repo.commit_file(
        project_id=project_id,
        actor_user_id=actor_user_id,
        parent_node_id=parent_node_id,
        name=name,
        name_key=asset_name_key(name),
        media_type=media_type,
        version_id=version_id,
        object_key=make_object_key(project_id, version_id),
        size_bytes=size_bytes,
        sha256=sha256 or hashlib.sha256(b"hello").hexdigest(),
    )


def test_commit_file_writes_node_and_current_version(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    data = b"hello"
    commit = _commit_file(repo, pid, member_id, root, name="a.txt", size_bytes=len(data))
    assert commit.outcome == "committed"
    assert commit.node is not None and commit.version is not None
    assert commit.node.kind == "file"
    assert commit.node.name == "a.txt"
    assert commit.version.is_current is True
    assert commit.version.size_bytes == len(data)
    assert commit.version.sha256 == hashlib.sha256(data).hexdigest()
    assert commit.version.object_key == make_object_key(pid, commit.version.version_id)
    assert commit.version.uploaded_by == member_id
    nodes = _node_rows(db, pid)
    versions = _version_rows(db, pid)
    assert len(nodes) == 2  # root + file
    assert len(versions) == 1
    # Non-member and archived commits write nothing.
    outsider_commit = _commit_file(repo, pid, 999999, root, name="intruder.txt")
    assert outsider_commit.outcome == "not_member"
    _archive(db, pid)
    archived_commit = _commit_file(repo, pid, member_id, root, name="late.txt")
    assert archived_commit.outcome == "archived"
    assert len(_node_rows(db, pid)) == 2
    assert len(_version_rows(db, pid)) == 1


def test_commit_file_rolls_back_when_version_insert_fails(
    repo: ProjectAssetRepo,
    db: SqlitePool,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    monkeypatch.setattr("octop.infra.db.repos.project_assets._insert_asset_version", _boom)
    with pytest.raises(RuntimeError, match="injected failure"):
        _commit_file(repo, pid, member_id, root, name="rollback.txt")
    # Neither a half node nor a bodyless version survives the rollback.
    assert [r for r in _node_rows(db, pid) if r["kind"] == "file"] == []
    assert _version_rows(db, pid) == []


def test_postgres_membership_lock_uses_for_share(repo: ProjectAssetRepo) -> None:
    """PG asset writes lock the membership row FOR SHARE before inserting;
    lock order member-row → asset rows matches remove_member."""

    class _Cursor:
        def fetchone(self) -> dict[str, str] | None:
            return {"role": "member"}

    class _Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql: str, params: tuple[object, ...]) -> _Cursor:
            self.statements.append(sql)
            return _Cursor()

    conn = _Connection()
    repo._db.dialect = "postgresql"
    assert repo._member_role_locked(conn, "p1", 7) == "member"
    sql = conn.statements[0]
    assert sql.startswith("SELECT role FROM project_members WHERE project_id = ? AND user_id = ?")
    assert sql.endswith(" FOR SHARE")

    repo._db.dialect = "sqlite"
    conn.statements.clear()
    assert repo._member_role_locked(conn, "p1", 7) == "member"
    assert not conn.statements[0].endswith("FOR SHARE")


# ---------------------------------------------------------------------------
# Repo: listing, usage, download
# ---------------------------------------------------------------------------


def _seed_tree(repo: ProjectAssetRepo, pid: str, member_id: int) -> tuple[str, str, list[str]]:
    """root + folders (Beta, alpha) + files (Report.PDF, notes.txt).

    Returns ``(root, subfolder, file_node_ids)``; the subfolder is empty.
    """
    root = repo.ensure_root(pid)
    assert root is not None
    beta = repo.create_folder(
        project_id=pid,
        actor_user_id=member_id,
        parent_node_id=root,
        name="Beta",
        name_key="beta",
    )
    alpha = repo.create_folder(
        project_id=pid,
        actor_user_id=member_id,
        parent_node_id=root,
        name="alpha",
        name_key="alpha",
    )
    assert beta.row is not None and alpha.row is not None
    report = _commit_file(repo, pid, member_id, root, name="Report.PDF", size_bytes=7)
    notes = _commit_file(repo, pid, member_id, beta.row.node_id, name="notes.txt", size_bytes=3)
    assert report.node is not None and notes.node is not None
    return root, beta.row.node_id, [report.node.node_id, notes.node.node_id]


def test_list_nodes_scopes_filters_sorts_paginates(
    repo: ProjectAssetRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    root, beta, file_ids = _seed_tree(repo, pid, member_id)
    # Another project's nodes must never leak into this project's listing even
    # when queried with the same caller.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目")
    projects.add_member(other.project_id, member_id, role="member")
    other_root = repo.ensure_root(other.project_id)
    assert other_root is not None
    repo.create_folder(
        project_id=other.project_id,
        actor_user_id=owner_id,
        parent_node_id=other_root,
        name="OTHER-SECRET",
        name_key="other-secret",
    )

    listed = repo.list_nodes(
        pid,
        user_id=member_id,
        parent_node_id=root,
        kind=None,
        name_like=None,
        limit=100,
        offset=0,
    )
    assert listed is not None
    rows, total = listed
    assert total == 3  # root children: Report.PDF + Beta + alpha
    # Full deterministic order: (kind, name_key, node_id) — 'file' < 'folder',
    # and within a kind by folded name ('alpha' < 'beta').
    ordered = [(r.kind, asset_name_key(r.name), r.node_id) for r in rows]
    assert ordered == sorted(ordered)
    names = [r.name for r in rows]
    assert names == ["Report.PDF", "alpha", "Beta"]
    assert {r.node_id for r in rows} & set(file_ids)
    assert all(r.name != "OTHER-SECRET" for r in rows)
    report_row = next(r for r in rows if r.name == "Report.PDF")
    assert report_row.size_bytes == 7 and report_row.media_type == "text/plain"
    folder_row = next(r for r in rows if r.name == "Beta")
    assert folder_row.size_bytes is None and folder_row.media_type is None

    # kind filter.
    listed = repo.list_nodes(
        pid,
        user_id=member_id,
        parent_node_id=root,
        kind="folder",
        name_like=None,
        limit=100,
        offset=0,
    )
    assert listed is not None
    assert [r.name for r in listed[0]] == ["alpha", "Beta"] and listed[1] == 2
    # name search (pre-folded, pre-escaped pattern from the service).
    listed = repo.list_nodes(
        pid,
        user_id=member_id,
        parent_node_id=root,
        kind=None,
        name_like="%report%",
        limit=100,
        offset=0,
    )
    assert listed is not None
    assert [r.name for r in listed[0]] == ["Report.PDF"] and listed[1] == 1
    # Pagination walks the full order without duplicates or skips.
    seen: list[str] = []
    offset = 0
    while True:
        listed = repo.list_nodes(
            pid,
            user_id=member_id,
            parent_node_id=root,
            kind=None,
            name_like=None,
            limit=2,
            offset=offset,
        )
        assert listed is not None
        page_rows, page_total = listed
        assert page_total == 3
        seen.extend(r.node_id for r in page_rows)
        if len(page_rows) < 2:
            break
        offset += 2
    assert sorted(seen) == sorted([r.node_id for r in rows])
    # Non-members and unknown projects get the None sentinel (uniform 404).
    assert (
        repo.list_nodes(
            pid,
            user_id=outsider_id,
            parent_node_id=root,
            kind=None,
            name_like=None,
            limit=10,
            offset=0,
        )
        is None
    )
    # Subfolder listing is scoped to that parent.
    listed = repo.list_nodes(
        pid,
        user_id=member_id,
        parent_node_id=beta,
        kind=None,
        name_like=None,
        limit=100,
        offset=0,
    )
    assert listed is not None
    assert [r.name for r in listed[0]] == ["notes.txt"] and listed[1] == 1


def test_usage_counts_only_current_file_bytes(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int
) -> None:
    root, beta, file_ids = _seed_tree(repo, pid, member_id)
    # A historical (is_current = 0) version must not inflate the sums.
    report_node = file_ids[0]
    _seed_version(db, project_id=pid, node_id=report_node, size_bytes=999, is_current=0)
    # A zero-byte file counts as a file with zero bytes.
    _commit_file(repo, pid, member_id, root, name="empty.bin", size_bytes=0, media_type=None)
    usage = repo.usage(pid, user_id=member_id)
    assert usage == (3, 10)  # Report.PDF (7) + notes.txt (3) + empty.bin (0)
    assert repo.usage(pid, user_id=999999) is None
    assert repo.usage("ghost-project", user_id=member_id) is None
    assert root and beta


def test_get_download_scoping(
    repo: ProjectAssetRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    root, beta, file_ids = _seed_tree(repo, pid, member_id)
    row = repo.get_download(pid, user_id=member_id, node_id=file_ids[0])
    assert row is not None
    assert row.name == "Report.PDF"
    assert row.object_key == make_object_key(pid, row.version_id)
    assert row.size_bytes == 7
    # Folder nodes are never downloadable.
    assert repo.get_download(pid, user_id=member_id, node_id=beta) is None
    # Non-members, unknown nodes, and the hidden root → None (uniform 404).
    assert repo.get_download(pid, user_id=outsider_id, node_id=file_ids[0]) is None
    assert repo.get_download(pid, user_id=member_id, node_id=new_ulid()) is None
    assert repo.get_download(pid, user_id=member_id, node_id=root) is None
    # A node id from another project never resolves inside this one.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目2")
    other_root = repo.ensure_root(other.project_id)
    assert other_root is not None
    other_commit = _commit_file(repo, other.project_id, owner_id, other_root, name="b.txt")
    assert other_commit.node is not None
    projects.add_member(other.project_id, member_id, role="member")
    assert repo.get_download(pid, user_id=member_id, node_id=other_commit.node.node_id) is None
    assert repo.get_download(other.project_id, user_id=member_id, node_id=file_ids[0]) is None


def test_all_object_keys(repo: ProjectAssetRepo, pid: str, member_id: int) -> None:
    root, _beta, file_ids = _seed_tree(repo, pid, member_id)
    keys = repo.all_object_keys()
    rows = [repo.get_download(pid, user_id=member_id, node_id=n) for n in file_ids]
    assert {r.object_key for r in rows if r is not None} <= keys
    assert all(k.startswith(f"{pid}/") or "/" in k for k in keys)
    assert root


# ---------------------------------------------------------------------------
# Storage: streaming, publish, download safety, orphan reclaim
# ---------------------------------------------------------------------------


def test_write_temp_hashes_incrementally_and_allows_zero_bytes(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    version_id = new_ulid()
    data = b"x" * (256 * 1024 + 17)  # spans multiple chunks
    temp, stored = storage.write_temp(version_id, io.BytesIO(data), max_bytes=len(data))
    assert temp == storage.temp_path(version_id)
    assert temp.exists()
    assert stored.size_bytes == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()

    empty_id = new_ulid()
    _temp, empty = storage.write_temp(empty_id, io.BytesIO(b""), max_bytes=10)
    assert empty.size_bytes == 0
    assert empty.sha256 == hashlib.sha256(b"").hexdigest()


def test_write_temp_enforces_cap_and_discards_temp(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    version_id = new_ulid()
    with pytest.raises(AssetUploadTooLarge) as excinfo:
        storage.write_temp(version_id, io.BytesIO(b"y" * 100), max_bytes=99)
    assert excinfo.value.max_bytes == 99
    assert not storage.temp_path(version_id).exists()
    assert _files_under(storage.root) == []


def test_discard_failure_does_not_log_private_object_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    private_path = storage.final_path(new_ulid(), new_ulid())

    def deny_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        raise OSError(f"access denied: {self}")

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    storage.discard(private_path)

    assert "failed to discard asset object" in caplog.text
    assert str(private_path) not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_write_temp_midstream_crash_leaves_no_temp(tmp_path: Path) -> None:
    """W1: a crash mid-stream leaves at most the temp — and the writer cleans it."""

    class _CrashStream:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise RuntimeError("injected stream crash")

    storage = ProjectAssetStorage(tmp_path / "assets")
    version_id = new_ulid()
    with pytest.raises(RuntimeError, match="injected stream crash"):
        storage.write_temp(version_id, cast(BinaryIO, _CrashStream()), max_bytes=1000)
    assert not storage.temp_path(version_id).exists()
    assert _files_under(storage.root) == []


def test_publish_moves_temp_to_final_atomically(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    project_id, version_id = new_ulid(), new_ulid()
    data = b"publish-me"
    temp, stored = storage.write_temp(version_id, io.BytesIO(data), max_bytes=100)
    final = storage.publish(temp, project_id, version_id)
    assert final == storage.final_path(project_id, version_id)
    assert not temp.exists()
    assert final.read_bytes() == data
    assert stored.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_storage_creates_private_directories_and_files(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    project_id, version_id = new_ulid(), new_ulid()
    temp, _ = storage.write_temp(version_id, io.BytesIO(b"private"), max_bytes=100)
    assert stat.S_IMODE(storage.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(temp.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(temp.stat().st_mode) == 0o600

    final = storage.publish(temp, project_id, version_id)
    assert stat.S_IMODE(final.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(final.stat().st_mode) == 0o600


def test_object_paths_derive_only_from_server_ids(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    project_id, version_id = new_ulid(), new_ulid()
    final = storage.final_path(project_id, version_id)
    # The display filename never appears anywhere in the physical path.
    assert "恶..名.txt" not in str(final)
    assert final.parent.name == project_id and final.name == version_id
    assert make_object_key(project_id, version_id) == f"{project_id}/{version_id}"
    # Malformed or escaping keys are rejected before any filesystem access.
    for bad in ("../escape", f"{project_id}/../x", "x/y", f"{project_id}/{version_id}/z", ""):
        with pytest.raises(AssetStorageError):
            storage.object_path(bad)
    with pytest.raises(AssetStorageError):
        storage.temp_path("not-a-ulid")


def test_open_download_rejects_missing_symlink_and_dir(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    project_id, version_id = new_ulid(), new_ulid()
    key = make_object_key(project_id, version_id)
    with pytest.raises(AssetStorageError):
        storage.open_download(key)  # missing
    final = storage.final_path(project_id, version_id)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"real")
    assert storage.open_download(key) == final
    # A directory in place of the object is rejected.
    dir_id = new_ulid()
    dir_path = storage.final_path(project_id, dir_id)
    dir_path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AssetStorageError):
        storage.open_download(make_object_key(project_id, dir_id))


def test_open_regular_file_no_follow_reads_plain_file(tmp_path: Path) -> None:
    path = tmp_path / "plain"
    path.write_bytes(b"safe")
    with open_regular_file_no_follow(path) as stream:
        assert stream.read() == b"safe"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink race")
def test_open_regular_file_no_follow_rejects_symlink_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "object"
    path.write_bytes(b"safe")
    secret = tmp_path / "secret"
    secret.write_bytes(b"secret")
    original_open = os.open

    def swap_then_open(
        candidate: str | os.PathLike[str], flags: int, *args: Any, **kwargs: Any
    ) -> int:
        if os.fspath(candidate) in (os.fspath(path), path.name):
            path.unlink()
            path.symlink_to(secret)
        return original_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_then_open)
    with pytest.raises(AssetStorageError):
        open_regular_file_no_follow(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX intermediate symlink race")
def test_open_regular_file_no_follow_rejects_intermediate_directory_swap(
    tmp_path: Path,
) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    project_id, version_id = new_ulid(), new_ulid()
    temp, _ = storage.write_temp(version_id, io.BytesIO(b"safe"), max_bytes=4)
    final = storage.publish(temp, project_id, version_id)
    checked = storage.open_download(make_object_key(project_id, version_id))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / version_id).write_bytes(b"secret")
    final.parent.rename(tmp_path / "moved-project")
    final.parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AssetStorageError), open_regular_file_no_follow(checked) as stream:
        stream.read()


@pytest.mark.skipif(os.name != "posix", reason="symlink creation is POSIX-only here")
def test_open_download_rejects_symlink(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"TOP-SECRET")
    project_id, version_id = new_ulid(), new_ulid()
    link = storage.final_path(project_id, version_id)
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(secret, link)
    with pytest.raises(AssetStorageError):
        storage.open_download(make_object_key(project_id, version_id))


def test_reclaim_orphans_respects_grace_and_references(tmp_path: Path) -> None:
    storage = ProjectAssetStorage(tmp_path / "assets")
    storage.ensure_root()
    project_id = new_ulid()
    old = RECLAIM_GRACE_SECONDS + 120

    def _final(pid_value: str, version_id: str) -> Path:
        path = storage.final_path(pid_value, version_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"obj")
        return path

    orphan_old = _final(project_id, new_ulid())
    _age(orphan_old, old)
    referenced_old = _final(project_id, new_ulid())
    _age(referenced_old, old)
    orphan_fresh = _final(project_id, new_ulid())
    old_temp = storage.temp_path(new_ulid())
    old_temp.write_bytes(b"part")
    _age(old_temp, old)
    fresh_temp = storage.temp_path(new_ulid())
    fresh_temp.write_bytes(b"part")
    # Foreign material must never be touched, however old.
    readme = storage.root / "README"
    readme.write_text("keep me")
    _age(readme, old)
    foreign = storage.root / project_id / "not-a-ulid.bin"
    foreign.write_bytes(b"keep")
    _age(foreign, old)

    known = {make_object_key(project_id, referenced_old.name)}
    report = storage.reclaim_orphans(known)
    assert report.temps_removed == 1
    assert report.finals_removed == 1
    assert not orphan_old.exists()
    assert referenced_old.exists()
    assert orphan_fresh.exists()
    assert not old_temp.exists()
    assert fresh_temp.exists()
    assert readme.exists() and foreign.exists()
    # Directories are never removed recursively — the project dir survives.
    assert (storage.root / project_id).is_dir()
    # A second pass with everything referenced removes nothing.
    _age(orphan_fresh, old)
    known_now = {
        make_object_key(project_id, referenced_old.name),
        make_object_key(project_id, orphan_fresh.name),
    }
    second = storage.reclaim_orphans(known_now)
    assert second.temps_removed == 0 and second.finals_removed == 0
    assert orphan_fresh.exists()


def test_reclaim_orphans_entry_failure_does_not_log_private_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """027: a per-entry OSError warning must stay static — no path, no traceback."""
    storage = ProjectAssetStorage(tmp_path / "assets")
    storage.ensure_root()
    old = RECLAIM_GRACE_SECONDS + 120

    # Two generated project folders; the lexicographically first one raises.
    first_project, second_project = sorted((new_ulid(), new_ulid()))
    bad_dir = storage.root / first_project
    bad_dir.mkdir(parents=True, exist_ok=True)
    private_path = bad_dir / new_ulid()

    # A valid aged orphan in the *later* folder: skip-and-continue must still
    # reclaim it after the earlier entry raised.
    orphan = storage.final_path(second_project, new_ulid())
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"obj")
    _age(orphan, old)

    real_reclaim_finals = ProjectAssetStorage._reclaim_finals

    def failing_reclaim_finals(
        self: ProjectAssetStorage,
        project_dir: str,
        project_id: str,
        known_object_keys: set[str],
        moment: float,
        grace: float,
    ) -> int:
        if project_id == first_project:
            raise OSError(13, f"permission denied: {private_path}")
        return real_reclaim_finals(self, project_dir, project_id, known_object_keys, moment, grace)

    monkeypatch.setattr(ProjectAssetStorage, "_reclaim_finals", failing_reclaim_finals)

    # os.scandir order is filesystem-dependent; sort entries so the failing
    # project folder is demonstrably examined before the valid one.
    real_scandir = os.scandir

    class _SortedEntries:
        def __init__(self, entries: list[Any]) -> None:
            self._entries = entries

        def __enter__(self) -> Any:
            return iter(self._entries)

        def __exit__(self, *exc_info: object) -> bool:
            return False

    def sorted_scandir(path: Any) -> Any:
        with real_scandir(path) as it:
            return _SortedEntries(sorted(it, key=lambda entry: entry.name))

    monkeypatch.setattr(os, "scandir", sorted_scandir)

    report = storage.reclaim_orphans(set())

    assert report.finals_removed == 1
    assert not orphan.exists()
    assert "asset reclaim skipped an entry" in caplog.text
    assert str(private_path) not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


# ---------------------------------------------------------------------------
# Service: naming rules
# ---------------------------------------------------------------------------


def test_validate_asset_name_rules() -> None:
    assert validate_asset_name("  报告.TXT  ") == "报告.TXT"
    decomposed = "café.txt"
    assert validate_asset_name(decomposed) == unicodedata.normalize("NFC", decomposed)
    assert validate_asset_name("a" * ASSET_NAME_MAX_LENGTH) == "a" * ASSET_NAME_MAX_LENGTH
    bad_names = (
        "",
        "   ",
        ".",
        "..",
        "a/b",
        "a\\b",
        "bad\x00name",
        "bad\nname",
        "bad\tname",
        "win:path",
        "star*name",
        "ques?tion",
        'quo"te',
        "less<than",
        "great>er",
        "pipe|name",
        "\x7fdel",
        "CON",
        "con.txt",
        "PRN.tar.gz",
        "aux",
        "NUL",
        "com1.log",
        "LPT9",
        "a" * (ASSET_NAME_MAX_LENGTH + 1),
    )
    for bad in bad_names:
        with pytest.raises(ValueError):
            validate_asset_name(bad)


def test_asset_name_key_casefolds() -> None:
    assert asset_name_key("Foo Bar.TXT") == "foo bar.txt"
    assert asset_name_key("STRASSE") == "strasse"
    decomposed = unicodedata.normalize("NFD", "Café")
    assert asset_name_key(decomposed) == asset_name_key(unicodedata.normalize("NFC", decomposed))


# ---------------------------------------------------------------------------
# Service: policy
# ---------------------------------------------------------------------------


def test_service_uniform_404_for_outsiders_and_unknown_projects(
    service: ProjectAssetService, pid: str, outsider_id: int, member_id: int
) -> None:
    def _ops(project_id: str, user_id: int) -> list[Any]:
        return [
            lambda: service.list_assets(project_id, user_id=user_id),
            lambda: service.get_usage(project_id, user_id=user_id),
            lambda: service.prepare_download(project_id, user_id=user_id, node_id="X"),
            lambda: service.create_folder(project_id, user_id=user_id, name="f"),
            lambda: service.upload_file(
                project_id,
                user_id=user_id,
                filename="f.txt",
                stream=io.BytesIO(b"x"),
                max_bytes=_MAX_BYTES,
            ),
        ]

    for op in _ops(pid, outsider_id) + _ops("ghost-project", member_id):
        with pytest.raises(OctopError) as excinfo:
            op()
        assert excinfo.value.code == ErrorCode.NOT_FOUND
        assert excinfo.value.status == 404
        assert excinfo.value.message == "project not found"


def test_service_removed_member_loses_everything(
    service: ProjectAssetService,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
) -> None:
    uploaded = _upload(service, pid, member_id, name="笔记.txt", data=b"bytes")
    removed = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert removed.outcome == "removed"
    with pytest.raises(OctopError) as excinfo:
        service.list_assets(pid, user_id=member_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(OctopError):
        service.get_usage(pid, user_id=member_id)
    with pytest.raises(OctopError):
        service.prepare_download(pid, user_id=member_id, node_id=uploaded.node.node_id)
    with pytest.raises(OctopError):
        service.create_folder(pid, user_id=member_id, name="晚了")
    with pytest.raises(OctopError):
        _upload(service, pid, member_id, name="晚了.txt")
    # The owner still sees and downloads the historic asset.
    page = service.list_assets(pid, user_id=owner_id)
    assert [item.name for item in page.items] == ["笔记.txt"]
    view = service.prepare_download(pid, user_id=owner_id, node_id=uploaded.node.node_id)
    assert view.path.read_bytes() == b"bytes"


def test_service_archived_blocks_writes_allows_reads(
    service: ProjectAssetService,
    db: SqlitePool,
    pid: str,
    member_id: int,
) -> None:
    folder = service.create_folder(pid, user_id=member_id, name="资料")
    uploaded = _upload(service, pid, member_id, name="a.txt", data=b"12345")
    _archive(db, pid)
    # Reads and downloads keep working on archived projects.
    page = service.list_assets(pid, user_id=member_id)
    assert page.total == 2
    assert {item.name for item in page.items} == {"资料", "a.txt"}
    usage = service.get_usage(pid, user_id=member_id)
    assert (usage.file_count, usage.total_bytes) == (1, 5)
    view = service.prepare_download(pid, user_id=member_id, node_id=uploaded.node.node_id)
    assert view.path.read_bytes() == b"12345"
    # Writes answer 403 and leave nothing behind.
    with pytest.raises(OctopError) as excinfo:
        service.create_folder(pid, user_id=member_id, name="归档后")
    assert excinfo.value.code == ErrorCode.FORBIDDEN and excinfo.value.status == 403
    with pytest.raises(OctopError) as excinfo:
        _upload(service, pid, member_id, name="归档后.txt")
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    assert _files_under(service._storage.root) == [view.path]
    names = {r["name"] for r in _node_rows(db, pid)}
    assert "归档后" not in names and "归档后.txt" not in names
    assert folder.node_id and uploaded.node.node_id


def test_service_folder_and_file_roundtrip(
    service: ProjectAssetService, repo: ProjectAssetRepo, pid: str, member_id: int
) -> None:
    folder = service.create_folder(pid, user_id=member_id, name="  资料 ")
    assert folder.name == "资料"
    assert folder.kind == "folder"
    assert folder.size_bytes is None and folder.media_type is None
    assert folder.parent_node_id == repo.get_root(pid)
    data = b"PDF-BYTES"
    uploaded = _upload(
        service, pid, member_id, name="报告.txt", data=data, parent_id=folder.node_id
    )
    node, version = uploaded.node, uploaded.version
    assert node.kind == "file" and node.name == "报告.txt"
    assert node.size_bytes == len(data)
    assert node.media_type == "text/plain"
    assert node.parent_node_id == folder.node_id
    assert version.size_bytes == len(data)
    assert version.sha256 == hashlib.sha256(data).hexdigest()
    assert version.media_type == "text/plain"
    # The upload response view never carries the object key or disk path.
    assert not hasattr(node, "object_key")
    assert not hasattr(version, "object_key")
    assert str(service._storage.root) not in repr(uploaded)

    root_page = service.list_assets(pid, user_id=member_id)
    assert [item.name for item in root_page.items] == ["资料"]
    assert root_page.items[0].node_id != root_page.items[0].parent_node_id  # root hidden
    inner = service.list_assets(pid, user_id=member_id, parent_id=folder.node_id)
    assert [item.name for item in inner.items] == ["报告.txt"]
    assert inner.items[0].size_bytes == len(data)
    usage = service.get_usage(pid, user_id=member_id)
    assert (usage.file_count, usage.total_bytes) == (1, len(data))
    view = service.prepare_download(pid, user_id=member_id, node_id=node.node_id)
    assert view.filename == "报告.txt"
    assert view.path.read_bytes() == data


def test_service_reads_before_any_write_never_create_the_root(
    service: ProjectAssetService,
    repo: ProjectAssetRepo,
    db: SqlitePool,
    pid: str,
    member_id: int,
) -> None:
    page = service.list_assets(pid, user_id=member_id)
    assert page.items == [] and page.total == 0 and page.has_more is False
    usage = service.get_usage(pid, user_id=member_id)
    assert (usage.file_count, usage.total_bytes) == (0, 0)
    assert repo.get_root(pid) is None
    assert _node_rows(db, pid) == []


def test_service_name_conflict_409(
    service: ProjectAssetService, db: SqlitePool, pid: str, member_id: int
) -> None:
    service.create_folder(pid, user_id=member_id, name="Docs")
    with pytest.raises(OctopError) as excinfo:
        service.create_folder(pid, user_id=member_id, name="docs")
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_NAME_CONFLICT
    assert excinfo.value.status == 409
    _upload(service, pid, member_id, name="Notes.txt")
    with pytest.raises(OctopError) as excinfo:
        _upload(service, pid, member_id, name="notes.TXT", data=b"other")
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_NAME_CONFLICT
    # The losing upload left no node, no version, and no orphan object.
    assert len(_version_rows(db, pid)) == 1
    names = [r["name"] for r in _node_rows(db, pid)]
    assert names.count("Notes.txt") == 1 and "notes.TXT" not in names
    folder = service.create_folder(pid, user_id=member_id, name="sub")
    # A different folder may reuse the name.
    _upload(service, pid, member_id, name="Notes.txt", parent_id=folder.node_id)
    assert len(_version_rows(db, pid)) == 2
    # A failed conflict must not corrupt the winner's bytes (W4 retry → 409).
    winner = service.prepare_download(
        pid,
        user_id=member_id,
        node_id=next(
            item.node_id
            for item in service.list_assets(pid, user_id=member_id).items
            if item.name == "Notes.txt"
        ),
    )
    assert winner.path.read_bytes() == b"hello"


def test_service_invalid_names_422(
    service: ProjectAssetService,
    db: SqlitePool,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
) -> None:
    for bad in ("CON.txt", "..", ".", "", "a/b", "x" * (ASSET_NAME_MAX_LENGTH + 1)):
        with pytest.raises(OctopError) as excinfo:
            service.create_folder(pid, user_id=member_id, name=bad)
        assert excinfo.value.code == ErrorCode.PROJECT_ASSET_INVALID
        assert excinfo.value.status == 422
    for bad_filename in ("..", "CON", "", "bad\x00name", "../../etc/passwd/.."):
        with pytest.raises(OctopError) as excinfo:
            _upload(service, pid, member_id, name=bad_filename)
        assert excinfo.value.code == ErrorCode.PROJECT_ASSET_INVALID
    assert _node_rows(db, pid) == []
    assert _version_rows(db, pid) == []
    assert _files_under(storage.root) == []


def test_service_baselines_upload_filenames(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    uploaded = _upload(service, pid, member_id, name="some/dir\\报告.txt", data=b"x")
    assert uploaded.node.name == "报告.txt"


def test_service_invalid_parents(
    service: ProjectAssetService,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    owner_id: int,
) -> None:
    uploaded = _upload(service, pid, member_id, name="a.txt")
    # A file node as parent → 422.
    with pytest.raises(OctopError) as excinfo:
        service.create_folder(pid, user_id=member_id, name="f", parent_id=uploaded.node.node_id)
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_INVALID
    with pytest.raises(OctopError) as excinfo:
        _upload(service, pid, member_id, name="b.txt", parent_id=uploaded.node.node_id)
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_INVALID
    # Unknown parent → uniform 404.
    with pytest.raises(OctopError) as excinfo:
        service.create_folder(pid, user_id=member_id, name="f", parent_id=new_ulid())
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    # A parent node id from another project → uniform 404, never a cross-write.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目3")
    other_folder = service.create_folder(other.project_id, user_id=owner_id, name="外部")
    with pytest.raises(OctopError) as excinfo:
        service.create_folder(pid, user_id=member_id, name="f", parent_id=other_folder.node_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        _upload(service, pid, member_id, name="c.txt", parent_id=other_folder.node_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    # Listing with a bad parent follows the same policy.
    with pytest.raises(OctopError) as excinfo:
        service.list_assets(pid, user_id=member_id, parent_id=uploaded.node.node_id)
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_INVALID
    with pytest.raises(OctopError) as excinfo:
        service.list_assets(pid, user_id=member_id, parent_id=new_ulid())
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_service_too_large_413_leaves_nothing(
    service: ProjectAssetService,
    db: SqlitePool,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
) -> None:
    with pytest.raises(OctopError) as excinfo:
        _upload(service, pid, member_id, name="big.bin", data=b"z" * 200, max_bytes=100)
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_TOO_LARGE
    assert excinfo.value.status == 413
    assert excinfo.value.details.get("max_mb") == 1
    assert _node_rows(db, pid) == []
    assert _version_rows(db, pid) == []
    assert _files_under(storage.root) == []


def test_upload_w3_db_failure_unlinks_final_and_retry_succeeds(
    service: ProjectAssetService,
    db: SqlitePool,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W3: the final object exists but the commit failed → nothing visible."""
    monkeypatch.setattr("octop.infra.db.repos.project_assets._insert_asset_version", _boom)
    with pytest.raises(RuntimeError, match="injected failure"):
        _upload(service, pid, member_id, name="注定回滚.txt", data=b"doomed")
    assert _node_rows(db, pid) == []
    assert _version_rows(db, pid) == []
    # The published final was unlinked: no orphan object, no temp residue.
    assert _files_under(storage.root) == []
    monkeypatch.undo()
    uploaded = _upload(service, pid, member_id, name="注定回滚.txt", data=b"doomed")
    assert uploaded.node.name == "注定回滚.txt"
    view = service.prepare_download(pid, user_id=member_id, node_id=uploaded.node.node_id)
    assert view.path.read_bytes() == b"doomed"


def test_upload_w4_committed_retry_is_conflict(
    service: ProjectAssetService, db: SqlitePool, pid: str, member_id: int
) -> None:
    first = _upload(service, pid, member_id, name="done.txt", data=b"first")
    with pytest.raises(OctopError) as excinfo:
        _upload(service, pid, member_id, name="done.txt", data=b"second")
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_NAME_CONFLICT
    assert len(_version_rows(db, pid)) == 1
    view = service.prepare_download(pid, user_id=member_id, node_id=first.node.node_id)
    assert view.path.read_bytes() == b"first"  # the winner is untouched


def test_concurrent_same_name_uploads_keep_one_committed_object(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(2, timeout=15)
    original_write = storage.write_temp

    def synchronized_write(version_id: str, stream: BinaryIO, *, max_bytes: int) -> Any:
        result = original_write(version_id, stream, max_bytes=max_bytes)
        barrier.wait()
        return result

    monkeypatch.setattr(storage, "write_temp", synchronized_write)

    def attempt(data: bytes) -> Any:
        try:
            return _upload(service, pid, member_id, name="same.txt", data=data)
        except OctopError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            executor.submit(attempt, b"first"),
            executor.submit(attempt, b"second"),
        ]
        results = [future.result(timeout=30) for future in outcomes]

    winners = [item for item in results if not isinstance(item, OctopError)]
    losers = [item for item in results if isinstance(item, OctopError)]
    assert len(winners) == 1
    assert len(losers) == 1 and losers[0].code == ErrorCode.PROJECT_ASSET_NAME_CONFLICT
    assert len(_version_rows(db, pid)) == 1
    assert len(_files_under(storage.root)) == 1
    view = service.prepare_download(pid, user_id=member_id, node_id=winners[0].node.node_id)
    assert view.path.read_bytes() in (b"first", b"second")


def test_service_zero_byte_upload(service: ProjectAssetService, pid: str, member_id: int) -> None:
    uploaded = _upload(service, pid, member_id, name="empty.bin", data=b"")
    assert uploaded.version.size_bytes == 0
    assert uploaded.version.sha256 == hashlib.sha256(b"").hexdigest()
    usage = service.get_usage(pid, user_id=member_id)
    assert (usage.file_count, usage.total_bytes) == (1, 0)
    view = service.prepare_download(pid, user_id=member_id, node_id=uploaded.node.node_id)
    assert view.path.read_bytes() == b""


def test_service_search_casefold_nfc_and_wildcard_escaping(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    _upload(service, pid, member_id, name="100%_fun.txt", data=b"a")
    _upload(service, pid, member_id, name="plain.txt", data=b"b")
    _upload(service, pid, member_id, name="café-menu.pdf", data=b"c")
    _upload(service, pid, member_id, name="Report.PDF", data=b"d")

    page = service.list_assets(pid, user_id=member_id, q="%")
    assert [item.name for item in page.items] == ["100%_fun.txt"]
    page = service.list_assets(pid, user_id=member_id, q="_")
    assert [item.name for item in page.items] == ["100%_fun.txt"]
    page = service.list_assets(pid, user_id=member_id, q="REPORT")
    assert [item.name for item in page.items] == ["Report.PDF"]
    page = service.list_assets(pid, user_id=member_id, q="pdf")
    assert {item.name for item in page.items} == {"café-menu.pdf", "Report.PDF"}
    # A composed query matches the NFC-normalized stored key (both directions
    # are covered by test_service_search_normalizes_nfd_on_both_sides below).
    page = service.list_assets(pid, user_id=member_id, q="café")
    assert [item.name for item in page.items] == ["café-menu.pdf"]
    # Empty/whitespace q is no filter.
    assert service.list_assets(pid, user_id=member_id, q="   ").total == 4


def test_service_pagination_filters_before_paging(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    for i in range(5):
        _upload(service, pid, member_id, name=f"file{i}.txt", data=b"x")
    for i in range(2):
        service.create_folder(pid, user_id=member_id, name=f"zzz{i}")
    page = service.list_assets(pid, user_id=member_id, q="file", limit=2, offset=0)
    assert page.total == 5 and page.has_more is True
    assert [item.name for item in page.items] == ["file0.txt", "file1.txt"]
    page = service.list_assets(pid, user_id=member_id, q="file", limit=2, offset=4)
    assert page.total == 5 and page.has_more is False
    assert [item.name for item in page.items] == ["file4.txt"]
    page = service.list_assets(pid, user_id=member_id, kind="folder", limit=10)
    assert page.total == 2 and [item.name for item in page.items] == ["zzz0", "zzz1"]
    page = service.list_assets(pid, user_id=member_id, kind="file", limit=10)
    assert page.total == 5
    assert page.limit == 10 and page.offset == 0
    with pytest.raises(ValueError):
        service.list_assets(pid, user_id=member_id, limit=0)
    with pytest.raises(ValueError):
        service.list_assets(pid, user_id=member_id, limit=101)
    with pytest.raises(ValueError):
        service.list_assets(pid, user_id=member_id, offset=-1)
    with pytest.raises(ValueError):
        service.list_assets(pid, user_id=member_id, kind="bogus")


def test_service_two_members_share_views_and_bytes(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
    other_member_id: int,
) -> None:
    data = b"shared-bytes"
    mine = _upload(service, pid, member_id, name="mine.bin", data=data)
    theirs = _upload(service, pid, other_member_id, name="theirs.bin", data=data)
    assert mine.version.sha256 == theirs.version.sha256 == hashlib.sha256(data).hexdigest()
    assert mine.version.version_id != theirs.version.version_id
    # Each member sees both nodes after refresh, and downloads the same bytes.
    for user_id in (member_id, other_member_id):
        page = service.list_assets(pid, user_id=user_id)
        assert {item.name for item in page.items} == {"mine.bin", "theirs.bin"}
        for node_id in (mine.node.node_id, theirs.node.node_id):
            view = service.prepare_download(pid, user_id=user_id, node_id=node_id)
            assert view.path.read_bytes() == data
    # Two distinct physical objects exist (no dedup aliasing in 023A).
    assert len(_files_under(storage.root)) == 2


def test_service_folder_download_is_404(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    folder = service.create_folder(pid, user_id=member_id, name="资料")
    with pytest.raises(OctopError) as excinfo:
        service.prepare_download(pid, user_id=member_id, node_id=folder.node_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as excinfo:
        service.prepare_download(pid, user_id=member_id, node_id=new_ulid())
    assert excinfo.value.code == ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Service: restricted cleaner on the first operation after restart
# ---------------------------------------------------------------------------


def test_cleaner_runs_once_on_first_operation(
    service: ProjectAssetService,
    repo: ProjectAssetRepo,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
) -> None:
    storage.ensure_root()
    orphan_key_dir = storage.root / pid
    orphan_key_dir.mkdir(parents=True, exist_ok=True)
    orphan = orphan_key_dir / new_ulid()
    orphan.write_bytes(b"orphan-from-a-crash")
    _age(orphan, RECLAIM_GRACE_SECONDS + 60)
    old_temp = storage.temp_path(new_ulid())
    old_temp.write_bytes(b"stale-temp")
    _age(old_temp, RECLAIM_GRACE_SECONDS + 60)
    assert storage.reclaim_done is False

    service.list_assets(pid, user_id=member_id)
    assert storage.reclaim_done is True
    assert not orphan.exists() and not old_temp.exists()

    # Referenced objects created by real uploads survive later cleanups, and
    # the once-per-process flag means a second operation does not rescan.
    uploaded = _upload(service, pid, member_id, name="keep.txt", data=b"keep")
    fresh_orphan = orphan_key_dir / new_ulid()
    fresh_orphan.write_bytes(b"new-orphan")
    _age(fresh_orphan, RECLAIM_GRACE_SECONDS + 60)
    service.get_usage(pid, user_id=member_id)
    assert fresh_orphan.exists()  # cleaner already ran in this process
    view = service.prepare_download(pid, user_id=member_id, node_id=uploaded.node.node_id)
    assert view.path.read_bytes() == b"keep"
    assert repo.all_object_keys()


def test_cleaner_failure_never_blocks_operations(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "reclaim_orphans", _boom)
    page = service.list_assets(pid, user_id=member_id)
    assert page.total == 0
    assert storage.reclaim_done is True
    uploaded = _upload(service, pid, member_id, name="照常.txt", data=b"ok")
    assert uploaded.node.name == "照常.txt"


def test_cleaner_grace_protects_new_objects(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
) -> None:
    storage.ensure_root()
    fresh_dir = storage.root / pid
    fresh_dir.mkdir(parents=True, exist_ok=True)
    fresh_orphan = fresh_dir / new_ulid()
    fresh_orphan.write_bytes(b"just-crashed")  # mtime = now: inside the grace
    fresh_temp = storage.temp_path(new_ulid())
    fresh_temp.write_bytes(b"in-flight")
    service.list_assets(pid, user_id=member_id)
    assert fresh_orphan.exists()
    assert fresh_temp.exists()


def test_service_search_normalizes_nfd_on_both_sides(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    """NFD input names are stored NFC; NFD queries fold onto the same key."""
    uploaded = _upload(service, pid, member_id, name="café-menu.pdf", data=b"c")
    composed = unicodedata.normalize("NFC", "café-menu.pdf")
    assert uploaded.node.name == composed
    # An NFD-decomposed query finds the NFC-stored name.
    page = service.list_assets(pid, user_id=member_id, q="café-menu")
    assert [item.name for item in page.items] == [composed]
    # The stored display name and key are both NFC-normalized.
    assert unicodedata.is_normalized("NFC", uploaded.node.name)
    assert asset_name_key(uploaded.node.name) == unicodedata.normalize(
        "NFC", asset_name_key("café-menu.pdf")
    )


def test_service_search_normalizes_casefold_expansion(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    uploaded = _upload(service, pid, member_id, name="ǰ.txt", data=b"j")
    page = service.list_assets(pid, user_id=member_id, q="ǰ")
    assert [item.node_id for item in page.items] == [uploaded.node.node_id]


def test_service_search_casefold_expansion_too_long_is_422(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    with pytest.raises(OctopError) as excinfo:
        service.list_assets(pid, user_id=member_id, q="İ" * 120)
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_INVALID
    assert excinfo.value.status == 422


# ---------------------------------------------------------------------------
# PS-06B-1 helpers: version listing / new-version upload / restore
# ---------------------------------------------------------------------------


def _seed_version_at(
    db: SqlitePool,
    *,
    project_id: str,
    node_id: str,
    size_bytes: int = 10,
    sha256: str = "0" * 64,
    created_at: int = 0,
    is_current: int = 0,
    uploaded_by: int | None = None,
    media_type: str | None = None,
) -> str:
    """Seed one immutable version row with an explicit created_at/sha."""
    version_id = new_ulid()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_asset_versions("
            "version_id, project_id, node_id, object_key, size_bytes, sha256, "
            "media_type, uploaded_by, created_at, is_current"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                project_id,
                node_id,
                make_object_key(project_id, version_id),
                size_bytes,
                sha256,
                media_type,
                uploaded_by,
                created_at,
                is_current,
            ),
        )
    return version_id


def _commit_new_version(
    repo: ProjectAssetRepo,
    project_id: str,
    actor_user_id: int,
    node_id: str,
    *,
    size_bytes: int = 5,
    sha256: str | None = None,
    media_type: str | None = "text/plain",
) -> Any:
    version_id = repo.new_version_id()
    return repo.commit_new_version(
        project_id=project_id,
        actor_user_id=actor_user_id,
        node_id=node_id,
        media_type=media_type,
        version_id=version_id,
        object_key=make_object_key(project_id, version_id),
        size_bytes=size_bytes,
        sha256=sha256 or hashlib.sha256(b"hello").hexdigest(),
    )


def _current_versions(db: SqlitePool, project_id: str, node_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT version_id FROM project_asset_versions "
            "WHERE project_id = ? AND node_id = ? AND is_current = 1",
            (project_id, node_id),
        ).fetchall()


def _upload_new_version(
    service: ProjectAssetService,
    project_id: str,
    user_id: int,
    node_id: str,
    *,
    data: bytes = b"second",
    max_bytes: int = _MAX_BYTES,
) -> Any:
    return service.upload_new_version(
        project_id,
        user_id=user_id,
        node_id=node_id,
        stream=io.BytesIO(data),
        max_bytes=max_bytes,
    )


# ---------------------------------------------------------------------------
# PS-06B-1 repo: version listing
# ---------------------------------------------------------------------------


def test_list_versions_single_023a_version(
    repo: ProjectAssetRepo, pid: str, member_id: int
) -> None:
    """A 023A single-version file lists its one version with safe metadata."""
    root = repo.ensure_root(pid)
    assert root is not None
    commit = _commit_file(repo, pid, member_id, root, name="a.txt", size_bytes=5)
    assert commit.outcome == "committed" and commit.node is not None and commit.version
    listed = repo.list_versions(
        pid, user_id=member_id, node_id=commit.node.node_id, limit=50, offset=0
    )
    assert listed is not None
    rows, total = listed
    assert total == 1 and len(rows) == 1
    row = rows[0]
    assert row.version_id == commit.version.version_id
    assert row.size_bytes == 5
    assert row.sha256 == commit.version.sha256
    assert row.media_type == "text/plain"
    assert row.uploaded_by == member_id
    assert row.is_current is True
    assert row.created_at >= 0
    # The listing row never carries the private object key.
    assert not hasattr(row, "object_key")


def test_list_versions_uniform_none_sentinels(
    repo: ProjectAssetRepo,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    commit = _commit_file(repo, pid, member_id, root, name="a.txt")
    assert commit.node is not None
    node_id = commit.node.node_id
    folder = repo.create_folder(
        project_id=pid, actor_user_id=member_id, parent_node_id=root, name="资料", name_key="资料"
    )
    assert folder.row is not None

    def _list(project_id: str, user_id: int, target: str) -> Any:
        return repo.list_versions(project_id, user_id=user_id, node_id=target, limit=50, offset=0)

    # Non-member, unknown project, folder node, hidden root, unknown node.
    assert _list(pid, outsider_id, node_id) is None
    assert _list("ghost-project", member_id, node_id) is None
    assert _list(pid, member_id, folder.row.node_id) is None
    assert _list(pid, member_id, root) is None
    assert _list(pid, member_id, new_ulid()) is None
    # A node id from another project never lists under this one, even for a
    # member of both projects.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目V")
    projects.add_member(other.project_id, member_id, role="member")
    other_root = repo.ensure_root(other.project_id)
    assert other_root is not None
    other_commit = _commit_file(repo, other.project_id, owner_id, other_root, name="b.txt")
    assert other_commit.node is not None
    assert _list(pid, member_id, other_commit.node.node_id) is None
    assert _list(other.project_id, member_id, node_id) is None
    # The member still lists their own project's node.
    assert _list(pid, member_id, node_id) is not None


def test_list_versions_total_order_and_stable_pagination(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int
) -> None:
    """(created_at DESC, version_id DESC) is a full order: same-second rows
    page without duplicates or skips."""
    root = _seed_root(db, pid)
    file_node = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="a.txt")
    # All seeded with created_at = 0 → the version_id tiebreak decides.
    ids = [
        _seed_version_at(db, project_id=pid, node_id=file_node, size_bytes=i) for i in range(1, 6)
    ]
    newest = _seed_version_at(
        db, project_id=pid, node_id=file_node, size_bytes=99, created_at=1000, is_current=1
    )
    expected = [newest, *sorted(ids, reverse=True)]

    listed = repo.list_versions(pid, user_id=member_id, node_id=file_node, limit=50, offset=0)
    assert listed is not None
    rows, total = listed
    assert total == 6
    assert [r.version_id for r in rows] == expected

    seen: list[str] = []
    offset = 0
    while True:
        listed = repo.list_versions(
            pid, user_id=member_id, node_id=file_node, limit=2, offset=offset
        )
        assert listed is not None
        page_rows, page_total = listed
        assert page_total == 6
        seen.extend(r.version_id for r in page_rows)
        if len(page_rows) < 2:
            break
        offset += 2
    assert seen == expected
    # Beyond the end: empty page, stable total.
    listed = repo.list_versions(pid, user_id=member_id, node_id=file_node, limit=50, offset=10)
    assert listed is not None
    assert listed[0] == [] and listed[1] == 6


# ---------------------------------------------------------------------------
# PS-06B-1 repo: historical download + restore pre-read containment
# ---------------------------------------------------------------------------


def test_get_version_download_triple_containment(
    repo: ProjectAssetRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    root = _seed_root(db, pid)
    node_a = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="a.txt")
    node_b = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="b.txt")
    folder = _seed_node(db, project_id=pid, parent_node_id=root)
    v_a1 = _seed_version_at(db, project_id=pid, node_id=node_a, size_bytes=1)
    v_a2 = _seed_version_at(db, project_id=pid, node_id=node_a, size_bytes=2, is_current=1)
    v_b = _seed_version_at(db, project_id=pid, node_id=node_b, size_bytes=3, is_current=1)

    row = repo.get_version_download(pid, user_id=member_id, node_id=node_a, version_id=v_a1)
    assert row is not None
    assert row.name == "a.txt"
    assert row.version_id == v_a1
    assert row.size_bytes == 1
    assert row.object_key == make_object_key(pid, v_a1)
    # The current version is downloadable through the same path.
    assert (
        repo.get_version_download(pid, user_id=member_id, node_id=node_a, version_id=v_a2)
        is not None
    )
    # Same-project version belonging to ANOTHER node → uniform None (404).
    assert repo.get_version_download(pid, user_id=member_id, node_id=node_a, version_id=v_b) is None
    assert (
        repo.get_version_download(pid, user_id=member_id, node_id=node_b, version_id=v_a1) is None
    )
    # Folder nodes, unknown versions/nodes, non-members, unknown projects.
    assert (
        repo.get_version_download(pid, user_id=member_id, node_id=folder, version_id=v_a1) is None
    )
    assert (
        repo.get_version_download(pid, user_id=member_id, node_id=node_a, version_id=new_ulid())
        is None
    )
    assert (
        repo.get_version_download(pid, user_id=member_id, node_id=new_ulid(), version_id=v_a1)
        is None
    )
    assert (
        repo.get_version_download(pid, user_id=outsider_id, node_id=node_a, version_id=v_a1) is None
    )
    ghost_row = repo.get_version_download(
        "ghost-project", user_id=member_id, node_id=node_a, version_id=v_a1
    )
    assert ghost_row is None
    # Cross-project: a node/version pair from another project never resolves.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目D")
    projects.add_member(other.project_id, member_id, role="member")
    other_root = _seed_root(db, other.project_id)
    other_node = _seed_node(
        db, project_id=other.project_id, parent_node_id=other_root, kind="file", name="c.txt"
    )
    other_v = _seed_version_at(db, project_id=other.project_id, node_id=other_node, is_current=1)
    cross_row = repo.get_version_download(
        other.project_id, user_id=member_id, node_id=node_a, version_id=v_a1
    )
    assert cross_row is None
    assert (
        repo.get_version_download(pid, user_id=member_id, node_id=other_node, version_id=other_v)
        is None
    )


def test_get_node_version_pair_and_containment(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int, outsider_id: int
) -> None:
    root = _seed_root(db, pid)
    node_a = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="a.txt")
    node_b = _seed_node(db, project_id=pid, parent_node_id=root, kind="file", name="b.txt")
    folder = _seed_node(db, project_id=pid, parent_node_id=root)
    v_a = _seed_version_at(db, project_id=pid, node_id=node_a, size_bytes=7, is_current=1)
    v_b = _seed_version_at(db, project_id=pid, node_id=node_b, is_current=1)

    pair = repo.get_node_version(pid, user_id=member_id, node_id=node_a, version_id=v_a)
    assert pair is not None
    node, version = pair
    assert node.node_id == node_a and node.kind == "file"
    assert version.version_id == v_a and version.node_id == node_a
    assert version.size_bytes == 7 and version.is_current is True
    assert version.object_key == make_object_key(pid, v_a)
    # Same-project other-node version, folder, unknowns, outsider → None.
    assert repo.get_node_version(pid, user_id=member_id, node_id=node_a, version_id=v_b) is None
    assert repo.get_node_version(pid, user_id=member_id, node_id=folder, version_id=v_a) is None
    assert (
        repo.get_node_version(pid, user_id=member_id, node_id=node_a, version_id=new_ulid()) is None
    )
    assert repo.get_node_version(pid, user_id=member_id, node_id=new_ulid(), version_id=v_a) is None
    assert repo.get_node_version(pid, user_id=outsider_id, node_id=node_a, version_id=v_a) is None


# ---------------------------------------------------------------------------
# PS-06B-1 repo: current-version switch writes
# ---------------------------------------------------------------------------


def test_commit_new_version_flips_current_and_preserves_old(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int, other_member_id: int
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    first = _commit_file(repo, pid, member_id, root, name="a.txt", size_bytes=5)
    assert first.outcome == "committed" and first.node is not None and first.version is not None
    node_id = first.node.node_id
    old_version_id = first.version.version_id
    old_object_key = first.version.object_key
    old_sha = first.version.sha256

    new_sha = hashlib.sha256(b"world").hexdigest()
    second = _commit_new_version(repo, pid, other_member_id, node_id, size_bytes=5, sha256=new_sha)
    assert second.outcome == "committed"
    assert second.node is not None and second.version is not None
    assert second.node.node_id == node_id
    assert second.node.name == "a.txt"  # the node display name never changed
    assert second.version.version_id != old_version_id
    assert second.version.is_current is True
    assert second.version.sha256 == new_sha
    assert second.version.uploaded_by == other_member_id
    assert second.node.updated_at >= first.node.updated_at

    versions = _version_rows(db, pid)
    assert len(versions) == 2  # exactly two immutable versions
    assert len(_current_versions(db, pid, node_id)) == 1
    by_id = {r["version_id"]: r for r in versions}
    # The old row is untouched except for the current pointer.
    assert by_id[old_version_id]["object_key"] == old_object_key
    assert by_id[old_version_id]["sha256"] == old_sha
    assert by_id[old_version_id]["is_current"] == 0
    assert by_id[second.version.version_id]["is_current"] == 1
    # Current-version readers follow the new version.
    download = repo.get_download(pid, user_id=member_id, node_id=node_id)
    assert download is not None and download.version_id == second.version.version_id
    assert repo.usage(pid, user_id=member_id) == (1, 5)


def test_commit_new_version_rejections_write_nothing(
    repo: ProjectAssetRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    first = _commit_file(repo, pid, member_id, root, name="a.txt", size_bytes=5)
    assert first.node is not None and first.version is not None
    node_id = first.node.node_id
    folder = repo.create_folder(
        project_id=pid, actor_user_id=member_id, parent_node_id=root, name="资料", name_key="资料"
    )
    assert folder.row is not None

    # Non-member → not_member, nothing written.
    assert _commit_new_version(repo, pid, 999999, node_id).outcome == "not_member"
    assert outsider_id != member_id
    assert _commit_new_version(repo, pid, outsider_id, node_id).outcome == "not_member"
    # Folder node / unknown node / cross-project node → node_missing.
    assert _commit_new_version(repo, pid, member_id, folder.row.node_id).outcome == "node_missing"
    assert _commit_new_version(repo, pid, member_id, new_ulid()).outcome == "node_missing"
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目N")
    other_root = repo.ensure_root(other.project_id)
    assert other_root is not None
    other_commit = _commit_file(repo, other.project_id, owner_id, other_root, name="b.txt")
    assert other_commit.node is not None
    projects.add_member(other.project_id, member_id, role="member")
    assert (
        _commit_new_version(repo, pid, member_id, other_commit.node.node_id).outcome
        == "node_missing"
    )
    # Still exactly one committed current version.
    assert len(_version_rows(db, pid)) == 1
    assert len(_current_versions(db, pid, node_id)) == 1

    # Archived project → archived, nothing written.
    _archive(db, pid)
    assert _commit_new_version(repo, pid, member_id, node_id).outcome == "archived"
    assert len(_version_rows(db, pid)) == 1
    assert _current_versions(db, pid, node_id)[0]["version_id"] == first.version.version_id


def test_restore_version_switches_without_new_row(
    repo: ProjectAssetRepo, db: SqlitePool, pid: str, member_id: int
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    first = _commit_file(repo, pid, member_id, root, name="a.txt", size_bytes=5)
    assert first.node is not None and first.version is not None
    node_id = first.node.node_id
    v1 = first.version.version_id
    second = _commit_new_version(repo, pid, member_id, node_id, size_bytes=6)
    assert second.outcome == "committed" and second.version is not None
    v2 = second.version.version_id

    restored = repo.restore_version(
        project_id=pid, actor_user_id=member_id, node_id=node_id, version_id=v1
    )
    assert restored.outcome == "committed"
    assert restored.node is not None and restored.version is not None
    assert restored.version.version_id == v1
    assert restored.version.is_current is True
    assert restored.version.object_key == make_object_key(pid, v1)
    assert second.node is not None
    assert restored.node.updated_at >= second.node.updated_at
    # No new rows, no copied bytes: still exactly two versions, one current.
    assert len(_version_rows(db, pid)) == 2
    current = _current_versions(db, pid, node_id)
    assert [r["version_id"] for r in current] == [v1]
    download = repo.get_download(pid, user_id=member_id, node_id=node_id)
    assert download is not None and download.version_id == v1

    # Repeat restore is idempotent at the repo level.
    again = repo.restore_version(
        project_id=pid, actor_user_id=member_id, node_id=node_id, version_id=v1
    )
    assert again.outcome == "committed"
    assert len(_version_rows(db, pid)) == 2
    assert [r["version_id"] for r in _current_versions(db, pid, node_id)] == [v1]
    # Restoring forward to v2 flips again.
    forward = repo.restore_version(
        project_id=pid, actor_user_id=member_id, node_id=node_id, version_id=v2
    )
    assert forward.outcome == "committed"
    assert [r["version_id"] for r in _current_versions(db, pid, node_id)] == [v2]
    assert len(_version_rows(db, pid)) == 2


def test_restore_version_rejections_and_rollback(
    repo: ProjectAssetRepo,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    root = repo.ensure_root(pid)
    assert root is not None
    first = _commit_file(repo, pid, member_id, root, name="a.txt", size_bytes=5)
    assert first.node is not None and first.version is not None
    node_id = first.node.node_id
    v1 = first.version.version_id
    second = _commit_new_version(repo, pid, member_id, node_id, size_bytes=6)
    assert second.outcome == "committed" and second.version is not None
    other_file = _commit_file(repo, pid, member_id, root, name="b.txt", size_bytes=3)
    assert other_file.node is not None and other_file.version is not None
    v_other = other_file.version.version_id
    folder = repo.create_folder(
        project_id=pid, actor_user_id=member_id, parent_node_id=root, name="资料", name_key="资料"
    )
    assert folder.row is not None

    def _restore(project_id: str, user_id: int, node: str, version: str) -> str:
        return repo.restore_version(
            project_id=project_id, actor_user_id=user_id, node_id=node, version_id=version
        ).outcome

    # Non-member / outsider.
    assert _restore(pid, 999999, node_id, v1) == "not_member"
    assert _restore(pid, outsider_id, node_id, v1) == "not_member"
    # Folder node, unknown node, unknown version.
    assert _restore(pid, member_id, folder.row.node_id, v1) == "node_missing"
    assert _restore(pid, member_id, new_ulid(), v1) == "node_missing"
    assert _restore(pid, member_id, node_id, new_ulid()) == "version_missing"
    # Same-project version belonging to ANOTHER file node.
    assert _restore(pid, member_id, node_id, v_other) == "version_missing"
    # Cross-project version id.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目R")
    other_root = repo.ensure_root(other.project_id)
    assert other_root is not None
    other_commit = _commit_file(repo, other.project_id, owner_id, other_root, name="c.txt")
    assert other_commit.node is not None and other_commit.version is not None
    projects.add_member(other.project_id, member_id, role="member")
    assert _restore(pid, member_id, node_id, other_commit.version.version_id) == "version_missing"
    assert _restore(other.project_id, member_id, other_commit.node.node_id, v1) == "version_missing"

    # Every rejection left the current pointer on v2 (the second version).
    current_ids = [r["version_id"] for r in _current_versions(db, pid, node_id)]
    assert current_ids == [second.version.version_id]
    assert len(_version_rows(db, pid)) == 3  # a.txt v1 + v2, b.txt v1

    # Archived project → archived, pointer untouched.
    _archive(db, pid)
    assert _restore(pid, member_id, node_id, v1) == "archived"
    still_current = [r["version_id"] for r in _current_versions(db, pid, node_id)]
    assert still_current == [second.version.version_id]


# ---------------------------------------------------------------------------
# PS-06B-1 repo: dual-dialect lock order (static; no live PostgreSQL)
# ---------------------------------------------------------------------------


class _SwitchCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return [self._row] if self._row is not None else []


class _SwitchConn:
    """Fake connection answering the version-switch SELECTs with fixed rows."""

    def __init__(self, node_row: dict[str, Any], version_row: dict[str, Any]) -> None:
        self.statements: list[str] = []
        self._node_row = node_row
        self._version_row = version_row

    def execute(self, sql: str, params: Any = None) -> _SwitchCursor:
        self.statements.append(sql)
        if "FROM project_members" in sql:
            return _SwitchCursor({"role": "member"})
        if "FROM project_spaces" in sql:
            return _SwitchCursor({"archived": 0})
        if "FROM project_asset_nodes" in sql:
            return _SwitchCursor(self._node_row)
        if "FROM project_asset_versions" in sql:
            return _SwitchCursor(self._version_row)
        return _SwitchCursor(None)


class _SwitchTxn:
    def __init__(self, conn: _SwitchConn) -> None:
        self._conn = conn

    def __enter__(self) -> _SwitchConn:
        return self._conn

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _SwitchPool:
    def __init__(self, dialect: str, conn: _SwitchConn) -> None:
        self.dialect = dialect
        self.conn = conn

    def transaction(self) -> _SwitchTxn:
        return _SwitchTxn(self.conn)

    def connect(self) -> _SwitchTxn:
        return _SwitchTxn(self.conn)

    def close(self) -> None:
        pass


_FAKE_PID = new_ulid()
_FAKE_NODE = new_ulid()
_FAKE_VERSION = new_ulid()
_FAKE_NODE_ROW: dict[str, Any] = {
    "node_id": _FAKE_NODE,
    "project_id": _FAKE_PID,
    "parent_node_id": new_ulid(),
    "kind": "file",
    "name": "a.txt",
    "name_key": "a.txt",
    "created_by": None,
    "created_at": 0,
    "updated_at": 0,
}
_FAKE_VERSION_ROW: dict[str, Any] = {
    "version_id": _FAKE_VERSION,
    "project_id": _FAKE_PID,
    "node_id": _FAKE_NODE,
    "object_key": f"{_FAKE_PID}/{_FAKE_VERSION}",
    "size_bytes": 5,
    "sha256": "0" * 64,
    "media_type": None,
    "uploaded_by": None,
    "created_at": 0,
    "is_current": 1,
}


def _fake_repo(dialect: str) -> tuple[ProjectAssetRepo, _SwitchConn]:
    conn = _SwitchConn(dict(_FAKE_NODE_ROW), dict(_FAKE_VERSION_ROW))
    pool = _SwitchPool(dialect, conn)
    return ProjectAssetRepo(cast(Any, pool)), conn


def _first_index(statements: list[str], needle: str) -> int:
    return next(i for i, s in enumerate(statements) if needle in s)


def test_postgres_new_version_lock_order_member_then_node_for_update() -> None:
    """PG: member row FOR SHARE → node row FOR UPDATE → archived check →
    reset old current → insert new current → touch node.

    NOTE: static dual-dialect assertion only — no live PostgreSQL was run.
    """
    repo, conn = _fake_repo("postgresql")
    result = repo.commit_new_version(
        project_id=_FAKE_PID,
        actor_user_id=7,
        node_id=_FAKE_NODE,
        media_type=None,
        version_id=_FAKE_VERSION,
        object_key=f"{_FAKE_PID}/{_FAKE_VERSION}",
        size_bytes=5,
        sha256="0" * 64,
    )
    assert result.outcome == "committed"
    stmts = conn.statements
    i_member = _first_index(stmts, "FROM project_members")
    i_node = _first_index(stmts, "FROM project_asset_nodes")
    i_archived = _first_index(stmts, "FROM project_spaces")
    i_reset = _first_index(stmts, "SET is_current = 0")
    i_insert = _first_index(stmts, "INSERT INTO project_asset_versions")
    i_touch = _first_index(stmts, "SET updated_at")
    assert stmts[i_member].endswith("FOR SHARE")
    assert stmts[i_node].endswith("FOR UPDATE")
    # Lock order: member row first, then the node row.
    assert i_member < i_node
    assert i_node < i_archived < i_reset < i_insert < i_touch


def test_postgres_restore_lock_order_and_triple_containment() -> None:
    """PG restore: same member→node lock order; the target version is fetched
    with the full (project_id, node_id, version_id) triple before any write."""
    repo, conn = _fake_repo("postgresql")
    conn._version_row["is_current"] = 0
    result = repo.restore_version(
        project_id=_FAKE_PID, actor_user_id=7, node_id=_FAKE_NODE, version_id=_FAKE_VERSION
    )
    assert result.outcome == "committed"
    stmts = conn.statements
    i_member = _first_index(stmts, "FROM project_members")
    i_node = _first_index(stmts, "FROM project_asset_nodes")
    i_target = _first_index(stmts, "node_id = ? AND version_id = ?")
    i_reset = _first_index(stmts, "SET is_current = 0")
    i_mark = _first_index(stmts, "SET is_current = 1")
    i_touch = _first_index(stmts, "SET updated_at")
    assert stmts[i_member].endswith("FOR SHARE")
    assert stmts[i_node].endswith("FOR UPDATE")
    assert i_member < i_node < i_target < i_reset < i_mark < i_touch
    # The containment read is triple-scoped.
    assert "WHERE project_id = ? AND node_id = ? AND version_id = ?" in stmts[i_target]


def test_postgres_restore_current_rechecks_gates_without_writes() -> None:
    """An idempotent restore still locks authorization and archive state."""
    repo, conn = _fake_repo("postgresql")
    result = repo.restore_version(
        project_id=_FAKE_PID, actor_user_id=7, node_id=_FAKE_NODE, version_id=_FAKE_VERSION
    )
    assert result.outcome == "committed"
    stmts = conn.statements
    assert _first_index(stmts, "FROM project_members") < _first_index(stmts, "FROM project_spaces")
    assert _first_index(stmts, "FROM project_spaces") < _first_index(
        stmts, "node_id = ? AND version_id = ?"
    )
    assert all("SET is_current" not in stmt and "SET updated_at" not in stmt for stmt in stmts)


def test_sqlite_version_switch_uses_no_row_lock_clauses() -> None:
    """SQLite serializes via BEGIN IMMEDIATE — no FOR SHARE/FOR UPDATE text."""
    for run, restoring in (
        (
            lambda r: r.commit_new_version(
                project_id=_FAKE_PID,
                actor_user_id=7,
                node_id=_FAKE_NODE,
                media_type=None,
                version_id=_FAKE_VERSION,
                object_key=f"{_FAKE_PID}/{_FAKE_VERSION}",
                size_bytes=5,
                sha256="0" * 64,
            ),
            False,
        ),
        (
            lambda r: r.restore_version(
                project_id=_FAKE_PID, actor_user_id=7, node_id=_FAKE_NODE, version_id=_FAKE_VERSION
            ),
            True,
        ),
    ):
        repo, conn = _fake_repo("sqlite")
        if restoring:
            conn._version_row["is_current"] = 0
        assert run(repo).outcome == "committed"
        assert all(not s.endswith("FOR SHARE") for s in conn.statements)
        assert all(not s.endswith("FOR UPDATE") for s in conn.statements)
        assert any("SET is_current = 0" in s for s in conn.statements)


# ---------------------------------------------------------------------------
# PS-06B-1 service: listing + historical download
# ---------------------------------------------------------------------------


def test_service_list_versions_page_shape_and_validation(
    service: ProjectAssetService, pid: str, member_id: int
) -> None:
    first = _upload(service, pid, member_id, name="报告.txt", data=b"hello")
    page = service.list_versions(pid, user_id=member_id, node_id=first.node.node_id)
    assert page.total == 1 and page.limit == 50 and page.offset == 0
    assert page.has_more is False
    item = page.items[0]
    assert item.version_id == first.version.version_id
    assert item.size_bytes == 5
    assert item.sha256 == first.version.sha256
    assert item.media_type == "text/plain"
    assert item.uploaded_by == member_id
    assert item.is_current is True
    # No private storage details ever reach the view.
    assert not hasattr(item, "object_key")
    assert str(service._storage.root) not in repr(page)

    with pytest.raises(ValueError):
        service.list_versions(pid, user_id=member_id, node_id=first.node.node_id, limit=0)
    with pytest.raises(ValueError):
        service.list_versions(pid, user_id=member_id, node_id=first.node.node_id, limit=101)
    with pytest.raises(ValueError):
        service.list_versions(pid, user_id=member_id, node_id=first.node.node_id, offset=-1)


def test_service_list_versions_uniform_404s(
    service: ProjectAssetService,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"x")
    folder = service.create_folder(pid, user_id=member_id, name="资料")

    with pytest.raises(OctopError) as excinfo:
        service.list_versions(pid, user_id=outsider_id, node_id=first.node.node_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND and excinfo.value.status == 404
    with pytest.raises(OctopError) as excinfo:
        service.list_versions("ghost-project", user_id=member_id, node_id=first.node.node_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    for bad_node in (folder.node_id, new_ulid()):
        with pytest.raises(OctopError) as excinfo:
            service.list_versions(pid, user_id=member_id, node_id=bad_node)
        assert excinfo.value.code == ErrorCode.NOT_FOUND
        assert excinfo.value.message == "asset not found"
    # Cross-project node id → uniform 404.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目L")
    projects.add_member(other.project_id, member_id, role="member")
    other_file = _upload(service, other.project_id, owner_id, name="b.txt", data=b"y")
    with pytest.raises(OctopError) as excinfo:
        service.list_versions(pid, user_id=member_id, node_id=other_file.node.node_id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_service_prepare_version_download_current_and_history(
    service: ProjectAssetService, pid: str, member_id: int, outsider_id: int
) -> None:
    first = _upload(service, pid, member_id, name="报告.txt", data=b"first-bytes")
    second = _upload_new_version(service, pid, member_id, first.node.node_id, data=b"222")

    view = service.prepare_version_download(
        pid, user_id=member_id, node_id=first.node.node_id, version_id=first.version.version_id
    )
    assert view.filename == "报告.txt"  # the CURRENT node name
    assert view.size_bytes == len(b"first-bytes")
    assert view.path.read_bytes() == b"first-bytes"
    current = service.prepare_version_download(
        pid, user_id=member_id, node_id=first.node.node_id, version_id=second.version.version_id
    )
    assert current.path.read_bytes() == b"222"
    # The view never carries the object key or absolute root path fields.
    assert str(service._storage.root) not in repr(view.filename)

    with pytest.raises(OctopError) as excinfo:
        service.prepare_version_download(
            pid,
            user_id=outsider_id,
            node_id=first.node.node_id,
            version_id=first.version.version_id,
        )
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    # Unknown version → uniform 404.
    with pytest.raises(OctopError) as excinfo:
        service.prepare_version_download(
            pid, user_id=member_id, node_id=first.node.node_id, version_id=new_ulid()
        )
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    # Missing object on disk → uniform 404.
    view.path.unlink()
    with pytest.raises(OctopError) as excinfo:
        service.prepare_version_download(
            pid, user_id=member_id, node_id=first.node.node_id, version_id=first.version.version_id
        )
    assert excinfo.value.code == ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# PS-06B-1 service: new-version upload
# ---------------------------------------------------------------------------


def test_service_upload_new_version_full_flow(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    pid: str,
    member_id: int,
    other_member_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="报告.txt", data=b"first-bytes")
    node_id = first.node.node_id
    v1 = first.version.version_id

    # The multipart filename never renames the node or selects a target path.
    view = service.upload_new_version(
        pid,
        user_id=other_member_id,
        node_id=node_id,
        stream=io.BytesIO(b"second-bytes-longer"),
        max_bytes=_MAX_BYTES,
    )
    assert view.node.node_id == node_id
    assert view.node.name == "报告.txt"
    assert view.node.kind == "file"
    assert view.node.size_bytes == len(b"second-bytes-longer")
    assert view.node.media_type == "text/plain"  # inferred from the NODE name
    assert view.version.version_id != v1
    assert view.version.size_bytes == len(b"second-bytes-longer")
    assert view.version.sha256 == hashlib.sha256(b"second-bytes-longer").hexdigest()
    assert not hasattr(view.node, "object_key") and not hasattr(view.version, "object_key")

    # Exactly two immutable version rows, exactly one current.
    assert len(_version_rows(db, pid)) == 2
    assert len(_current_versions(db, pid, node_id)) == 1
    # Old bytes survive untouched at their own object.
    old_path = storage.final_path(pid, v1)
    assert old_path.read_bytes() == b"first-bytes"
    assert len(_files_under(storage.root)) == 2
    # Current list/download/usage follow the new version.
    page = service.list_assets(pid, user_id=member_id)
    assert page.items[0].size_bytes == len(b"second-bytes-longer")
    usage = service.get_usage(pid, user_id=member_id)
    assert (usage.file_count, usage.total_bytes) == (1, len(b"second-bytes-longer"))
    current = service.prepare_download(pid, user_id=member_id, node_id=node_id)
    assert current.path.read_bytes() == b"second-bytes-longer"
    listed = service.list_versions(pid, user_id=member_id, node_id=node_id)
    assert listed.total == 2
    assert [i.version_id for i in listed.items] == [view.version.version_id, v1]
    assert [i.is_current for i in listed.items] == [True, False]
    # Historical download still serves the old bytes.
    old_view = service.prepare_version_download(
        pid, user_id=member_id, node_id=node_id, version_id=v1
    )
    assert old_view.path.read_bytes() == b"first-bytes"


def test_service_upload_new_version_rejections(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    node_id = first.node.node_id
    folder = service.create_folder(pid, user_id=member_id, name="资料")

    def _expect_404(user_id: int, target: str, project_id: str = pid) -> None:
        with pytest.raises(OctopError) as excinfo:
            _upload_new_version(service, project_id, user_id, target, data=b"x")
        assert excinfo.value.code == ErrorCode.NOT_FOUND

    _expect_404(outsider_id, node_id)
    _expect_404(member_id, node_id, project_id="ghost-project")
    _expect_404(member_id, folder.node_id)
    _expect_404(member_id, new_ulid())
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目U")
    projects.add_member(other.project_id, member_id, role="member")
    other_file = _upload(service, other.project_id, owner_id, name="b.txt", data=b"y")
    _expect_404(member_id, other_file.node.node_id)
    prior_objects = set(_files_under(storage.root))

    # Oversize → 413, nothing new anywhere.
    with pytest.raises(OctopError) as excinfo:
        _upload_new_version(service, pid, member_id, node_id, data=b"z" * 200, max_bytes=100)
    assert excinfo.value.code == ErrorCode.PROJECT_ASSET_TOO_LARGE
    assert excinfo.value.status == 413

    assert len(_version_rows(db, pid)) == 1
    assert _current_versions(db, pid, node_id)[0]["version_id"] == first.version.version_id
    assert set(_files_under(storage.root)) == prior_objects

    # Removed member → uniform 404.
    removed = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert removed.outcome == "removed"
    with pytest.raises(OctopError) as excinfo:
        _upload_new_version(service, pid, member_id, node_id, data=b"late")
    assert excinfo.value.code == ErrorCode.NOT_FOUND

    # Archived project → 403 for the remaining owner.
    _archive(db, pid)
    with pytest.raises(OctopError) as excinfo:
        _upload_new_version(service, pid, owner_id, node_id, data=b"late")
    assert excinfo.value.code == ErrorCode.FORBIDDEN and excinfo.value.status == 403
    assert len(_version_rows(db, pid)) == 1
    assert set(_files_under(storage.root)) == prior_objects


def test_service_upload_new_version_failure_windows(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    node_id = first.node.node_id
    v1 = first.version.version_id
    v1_path = storage.final_path(pid, v1)

    # Publish fails → temp discarded, no new final, nothing changed.
    def _boom_publish(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("injected publish failure")

    monkeypatch.setattr(storage, "publish", _boom_publish)
    with pytest.raises(RuntimeError, match="injected publish failure"):
        _upload_new_version(service, pid, member_id, node_id, data=b"second")
    assert len(_version_rows(db, pid)) == 1
    assert _current_versions(db, pid, node_id)[0]["version_id"] == v1
    assert _files_under(storage.root) == [v1_path]
    monkeypatch.undo()

    # W3 analog: publish succeeded, DB commit fails → only the NEW final is
    # cleaned; the old version stays current and downloadable.
    monkeypatch.setattr("octop.infra.db.repos.project_assets._insert_asset_version", _boom)
    with pytest.raises(RuntimeError, match="injected failure"):
        _upload_new_version(service, pid, member_id, node_id, data=b"second")
    assert len(_version_rows(db, pid)) == 1
    assert _current_versions(db, pid, node_id)[0]["version_id"] == v1
    assert _files_under(storage.root) == [v1_path]
    assert v1_path.read_bytes() == b"first"
    view = service.prepare_download(pid, user_id=member_id, node_id=node_id)
    assert view.path.read_bytes() == b"first"
    monkeypatch.undo()

    # After the injected failures clear, the second version commits normally.
    second = _upload_new_version(service, pid, member_id, node_id, data=b"second")
    assert len(_version_rows(db, pid)) == 2
    assert _current_versions(db, pid, node_id)[0]["version_id"] == second.version.version_id
    assert len(_files_under(storage.root)) == 2


# ---------------------------------------------------------------------------
# PS-06B-1 service: restore
# ---------------------------------------------------------------------------


def test_service_restore_version_full_flow(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    pid: str,
    member_id: int,
    other_member_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="报告.txt", data=b"first-bytes")
    node_id = first.node.node_id
    v1 = first.version.version_id
    second = _upload_new_version(service, pid, member_id, node_id, data=b"second-bytes-longer")
    v2 = second.version.version_id

    restored = service.restore_version(pid, user_id=other_member_id, node_id=node_id, version_id=v1)
    # Same safe DTO shape as the new-version upload response.
    assert restored.node.node_id == node_id
    assert restored.node.name == "报告.txt"
    assert restored.node.size_bytes == len(b"first-bytes")
    assert restored.node.media_type == "text/plain"
    assert restored.version.version_id == v1
    assert restored.version.sha256 == hashlib.sha256(b"first-bytes").hexdigest()
    # No new rows, no copied bytes.
    assert len(_version_rows(db, pid)) == 2
    assert len(_files_under(storage.root)) == 2
    assert _current_versions(db, pid, node_id)[0]["version_id"] == v1
    # Current download/usage/list follow the restored version.
    assert (
        service.prepare_download(pid, user_id=member_id, node_id=node_id).path.read_bytes()
        == b"first-bytes"
    )
    usage = service.get_usage(pid, user_id=member_id)
    assert usage.total_bytes == len(b"first-bytes")
    listed = service.list_versions(pid, user_id=member_id, node_id=node_id)
    flags = {i.version_id: i.is_current for i in listed.items}
    assert flags == {v1: True, v2: False}

    # Repeat restore of the current version is an idempotent success.
    before = [tuple(r) for r in _version_rows(db, pid)]
    again = service.restore_version(pid, user_id=member_id, node_id=node_id, version_id=v1)
    assert again.version.version_id == v1
    assert [tuple(r) for r in _version_rows(db, pid)] == before
    assert len(_files_under(storage.root)) == 2
    # Restoring forward flips back to v2 without new rows either.
    forward = service.restore_version(pid, user_id=member_id, node_id=node_id, version_id=v2)
    assert forward.version.version_id == v2
    assert len(_version_rows(db, pid)) == 2
    assert _current_versions(db, pid, node_id)[0]["version_id"] == v2


def test_service_restore_version_uniform_404s_and_403(
    service: ProjectAssetService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    member_id: int,
    other_member_id: int,
    outsider_id: int,
    owner_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    node_id = first.node.node_id
    v1 = first.version.version_id
    second = _upload_new_version(service, pid, member_id, node_id, data=b"second")
    other_file = _upload(service, pid, member_id, name="b.txt", data=b"other")
    folder = service.create_folder(pid, user_id=member_id, name="资料")

    def _expect_404(user_id: int, node: str, version: str, project_id: str = pid) -> None:
        with pytest.raises(OctopError) as excinfo:
            service.restore_version(project_id, user_id=user_id, node_id=node, version_id=version)
        assert excinfo.value.code == ErrorCode.NOT_FOUND

    _expect_404(outsider_id, node_id, v1)
    _expect_404(member_id, node_id, v1, project_id="ghost-project")
    _expect_404(member_id, folder.node_id, v1)
    _expect_404(member_id, new_ulid(), v1)
    _expect_404(member_id, node_id, new_ulid())
    # Same-project version belonging to another node.
    _expect_404(member_id, node_id, other_file.version.version_id)
    _expect_404(member_id, other_file.node.node_id, v1)
    # Cross-project node/version.
    other = projects.create_with_owner(creator_user_id=owner_id, name="别的项目S")
    projects.add_member(other.project_id, member_id, role="member")
    other_upload = _upload(service, other.project_id, owner_id, name="c.txt", data=b"z")
    _expect_404(member_id, node_id, other_upload.version.version_id)
    _expect_404(member_id, other_upload.node.node_id, v1, project_id=other.project_id)

    # Removed member → uniform 404 on restore (and the pointer never moved).
    removed = projects.remove_member(
        project_id=pid, user_id=other_member_id, actor_user_id=owner_id
    )
    assert removed.outcome == "removed"
    _expect_404(other_member_id, node_id, v1)
    assert _current_versions(db, pid, node_id)[0]["version_id"] == second.version.version_id

    # Archived project → 403 for write ops; reads keep working. 403 precedes
    # the idempotent success: restoring the ALREADY-CURRENT version is 403 too.
    _archive(db, pid)
    with pytest.raises(OctopError) as excinfo:
        service.restore_version(pid, user_id=member_id, node_id=node_id, version_id=v1)
    assert excinfo.value.code == ErrorCode.FORBIDDEN and excinfo.value.status == 403
    with pytest.raises(OctopError) as excinfo:
        service.restore_version(
            pid, user_id=member_id, node_id=node_id, version_id=second.version.version_id
        )
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    page = service.list_versions(pid, user_id=member_id, node_id=node_id)
    assert page.total == 2
    view = service.prepare_version_download(pid, user_id=member_id, node_id=node_id, version_id=v1)
    assert view.path.read_bytes() == b"first"
    assert _current_versions(db, pid, node_id)[0]["version_id"] == second.version.version_id


def test_service_restore_missing_or_symlinked_object_never_becomes_current(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    pid: str,
    member_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    node_id = first.node.node_id
    v1 = first.version.version_id
    second = _upload_new_version(service, pid, member_id, node_id, data=b"second")
    v1_path = storage.final_path(pid, v1)

    # Missing object at check time → uniform 404, current pointer untouched.
    v1_path.unlink()
    with pytest.raises(OctopError) as excinfo:
        service.restore_version(pid, user_id=member_id, node_id=node_id, version_id=v1)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert _current_versions(db, pid, node_id)[0]["version_id"] == second.version.version_id
    # The still-current version downloads fine.
    assert (
        service.prepare_download(pid, user_id=member_id, node_id=node_id).path.read_bytes()
        == b"second"
    )


def test_restore_current_rechecks_archive_after_the_initial_read(
    service: ProjectAssetService,
    repo: ProjectAssetRepo,
    db: SqlitePool,
    pid: str,
    member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    original_read = repo.get_node_version

    def read_then_archive(*args: Any, **kwargs: Any) -> Any:
        pair = original_read(*args, **kwargs)
        _archive(db, pid)
        return pair

    monkeypatch.setattr(repo, "get_node_version", read_then_archive)
    with pytest.raises(OctopError) as excinfo:
        service.restore_version(
            pid,
            user_id=member_id,
            node_id=first.node.node_id,
            version_id=first.version.version_id,
        )
    assert excinfo.value.code == ErrorCode.FORBIDDEN


def test_restore_rejects_a_truncated_historical_object(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    pid: str,
    member_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    second = _upload_new_version(service, pid, member_id, first.node.node_id, data=b"second")
    storage.final_path(pid, first.version.version_id).write_bytes(b"x")

    with pytest.raises(OctopError) as excinfo:
        service.restore_version(
            pid,
            user_id=member_id,
            node_id=first.node.node_id,
            version_id=first.version.version_id,
        )
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert (
        _current_versions(db, pid, first.node.node_id)[0]["version_id"] == second.version.version_id
    )


def test_missing_version_object_does_not_log_a_private_path(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    storage.final_path(pid, first.version.version_id).unlink()
    with pytest.raises(OctopError):
        service.prepare_version_download(
            pid,
            user_id=member_id,
            node_id=first.node.node_id,
            version_id=first.version.version_id,
        )
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.skipif(os.name != "posix", reason="symlink creation is POSIX-only here")
def test_service_restore_symlinked_object_never_becomes_current(
    service: ProjectAssetService,
    storage: ProjectAssetStorage,
    db: SqlitePool,
    tmp_path: Path,
    pid: str,
    member_id: int,
) -> None:
    first = _upload(service, pid, member_id, name="a.txt", data=b"first")
    node_id = first.node.node_id
    v1 = first.version.version_id
    second = _upload_new_version(service, pid, member_id, node_id, data=b"second")
    v1_path = storage.final_path(pid, v1)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"TOP-SECRET")
    v1_path.unlink()
    os.symlink(secret, v1_path)

    with pytest.raises(OctopError) as excinfo:
        service.restore_version(pid, user_id=member_id, node_id=node_id, version_id=v1)
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert _current_versions(db, pid, node_id)[0]["version_id"] == second.version.version_id
    # Historical download of the tampered object also fails closed.
    with pytest.raises(OctopError) as excinfo:
        service.prepare_version_download(pid, user_id=member_id, node_id=node_id, version_id=v1)
    assert excinfo.value.code == ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# PS-06B-1: concurrency, task-share boundary
# ---------------------------------------------------------------------------


def test_concurrent_new_version_and_restore_leave_exactly_one_current(
    service: ProjectAssetService,
    repo: ProjectAssetRepo,
    db: SqlitePool,
    storage: ProjectAssetStorage,
    pid: str,
    member_id: int,
    other_member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _upload(service, pid, member_id, name="race.txt", data=b"v1")
    node_id = first.node.node_id
    v1 = first.version.version_id
    # v2 becomes current so the racing restore of v1 takes the real switch
    # path (and not the side-effect-free idempotent one).
    _upload_new_version(service, pid, member_id, node_id, data=b"v2")
    barrier = Barrier(2, timeout=15)
    original_commit = repo.commit_new_version
    original_restore = repo.restore_version

    def synced_commit(**kwargs: Any) -> Any:
        barrier.wait()
        return original_commit(**kwargs)

    def synced_restore(**kwargs: Any) -> Any:
        barrier.wait()
        return original_restore(**kwargs)

    monkeypatch.setattr(repo, "commit_new_version", synced_commit)
    monkeypatch.setattr(repo, "restore_version", synced_restore)

    def upload_side() -> Any:
        return _upload_new_version(service, pid, member_id, node_id, data=b"v3-concurrent")

    def restore_side() -> Any:
        return service.restore_version(pid, user_id=other_member_id, node_id=node_id, version_id=v1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(upload_side), executor.submit(restore_side)]
        results = [future.result(timeout=30) for future in futures]

    assert all(not isinstance(item, Exception) for item in results)
    versions = _version_rows(db, pid)
    assert len(versions) == 3  # the upload added exactly one row; restore none
    current = _current_versions(db, pid, node_id)
    assert len(current) == 1
    assert current[0]["version_id"] in {v1, results[0].version.version_id}
    # All three physical objects survive whichever interleaving won.
    assert len(_files_under(storage.root)) == 3


def test_concurrent_new_version_uploads_serialize(
    service: ProjectAssetService,
    repo: ProjectAssetRepo,
    db: SqlitePool,
    pid: str,
    member_id: int,
    other_member_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _upload(service, pid, member_id, name="race2.txt", data=b"v1")
    node_id = first.node.node_id
    barrier = Barrier(2, timeout=15)
    original_commit = repo.commit_new_version

    def synced_commit(**kwargs: Any) -> Any:
        barrier.wait()
        return original_commit(**kwargs)

    monkeypatch.setattr(repo, "commit_new_version", synced_commit)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_upload_new_version, service, pid, member_id, node_id, data=b"v2a"),
            executor.submit(
                _upload_new_version, service, pid, other_member_id, node_id, data=b"v2b"
            ),
        ]
        results = [future.result(timeout=30) for future in futures]

    assert len(_version_rows(db, pid)) == 3
    current = _current_versions(db, pid, node_id)
    assert len(current) == 1
    assert current[0]["version_id"] in {r.version.version_id for r in results}


def _seed_task_share(db: SqlitePool, *, project_id: str, owner_id: int, grantee_id: int) -> str:
    """Seed agent + thread + task link + 024 card share + 025 text grant."""
    agent_id = new_ulid()
    thread_id = new_ulid()
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agents(agent_id, user_id, name, created_at, updated_at) "
            "VALUES (?, ?, 'bot', 0, 0)",
            (agent_id, owner_id),
        )
        conn.execute(
            "INSERT INTO threads("
            "thread_id, agent_id, user_id, channel_type, session_key, last_active, created_at"
            ") VALUES (?, ?, ?, 'dashboard', ?, 0, 0)",
            (thread_id, agent_id, owner_id, f"{agent_id}:dashboard:{owner_id}:dm"),
        )
        conn.execute(
            "INSERT INTO project_task_links("
            "project_id, thread_id, owner_user_id, source, created_at"
            ") VALUES (?, ?, ?, 'manual', 0)",
            (project_id, thread_id, owner_id),
        )
        conn.execute(
            "INSERT INTO project_task_shares("
            "project_id, thread_id, grantee_user_id, granted_by_user_id, role, granted_at"
            ") VALUES (?, ?, ?, ?, 'reader', 0)",
            (project_id, thread_id, grantee_id, owner_id),
        )
        conn.execute(
            "INSERT INTO project_task_content_grants("
            "project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at"
            ") VALUES (?, ?, ?, ?, 0)",
            (project_id, thread_id, grantee_id, owner_id),
        )
    return thread_id


def test_task_shares_never_authorize_asset_versions(
    service: ProjectAssetService,
    db: SqlitePool,
    projects: ProjectRepo,
    pid: str,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    """024 card / 025 text grants are not an asset ACL: version endpoints read
    ONLY the current project_members row."""
    first = _upload(service, pid, owner_id, name="共享任务.txt", data=b"v1")
    node_id = first.node.node_id
    v1 = first.version.version_id
    thread_id = _seed_task_share(db, project_id=pid, owner_id=owner_id, grantee_id=member_id)

    # The grantee's access is identical to plain membership (no extra reach).
    page = service.list_versions(pid, user_id=member_id, node_id=node_id)
    assert page.total == 1
    # An outsider can never hold a share row (composite FK to project_members).
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_task_shares("
            "project_id, thread_id, grantee_user_id, granted_by_user_id, role, granted_at"
            ") VALUES (?, ?, ?, ?, 'reader', 0)",
            (pid, thread_id, outsider_id, owner_id),
        )
    # Removing the membership cascades the share/grant away, and every asset
    # version operation for the removed grantee becomes a uniform 404 even
    # though the grants existed moments ago.
    removed = projects.remove_member(project_id=pid, user_id=member_id, actor_user_id=owner_id)
    assert removed.outcome == "removed"
    with db.connect() as conn:
        shares = conn.execute(
            "SELECT COUNT(*) AS n FROM project_task_shares WHERE project_id = ?", (pid,)
        ).fetchone()
        grants = conn.execute(
            "SELECT COUNT(*) AS n FROM project_task_content_grants WHERE project_id = ?", (pid,)
        ).fetchone()
    assert int(shares["n"]) == 0 and int(grants["n"]) == 0
    for op in (
        lambda: service.list_versions(pid, user_id=member_id, node_id=node_id),
        lambda: service.prepare_version_download(
            pid, user_id=member_id, node_id=node_id, version_id=v1
        ),
        lambda: service.restore_version(pid, user_id=member_id, node_id=node_id, version_id=v1),
        lambda: _upload_new_version(service, pid, member_id, node_id, data=b"late"),
    ):
        with pytest.raises(OctopError) as excinfo:
            op()
        assert excinfo.value.code == ErrorCode.NOT_FOUND
    # The owner keeps full access.
    assert service.list_versions(pid, user_id=owner_id, node_id=node_id).total == 1
