"""Full-system backup and restore (database + local agent workspaces + config)."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

from octop import __version__
from octop.config import DatabaseConfig
from octop.infra.agents.workspace_dir import (
    system_files_path_from_config,
    workspace_dir_from_config_json,
)
from octop.infra.backup.chats import (
    CHAT_TABLES_CHILD_FIRST,
    capture_chat_tables,
    is_chat_workspace_rel,
    restore_preserved_chats,
    strip_chat_tables_from_sqlite_file,
)
from octop.infra.backup.manifest import MANIFEST_VERSION, AgentBackupEntry, BackupManifest
from octop.infra.backup.pg_dump import _require_tool, dump_postgres, restore_postgres
from octop.infra.backup.snapshot import (
    capture_jwt_secret_from_pool,
    capture_users_from_pool,
    infer_owner_user_id,
    prune_users_not_in,
    remap_ownership_to_user,
    repair_project_plan_keys_in_connection,
    restore_jwt_secret_into_pool,
    restore_sqlite_into_pool,
    snapshot_sqlite_file,
    upsert_users_into_pool,
)
from octop.infra.db.migrate import (
    _current_version,
    _max_discovered_version,
    run_migrations,
    validate_project_todo_d1_in_connection,
)
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.asset_storage import (
    AssetStorageError,
    make_object_key,
    open_regular_file_no_follow,
)
from octop.infra.utils.env_file import env_file_path
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.safe_dirs import (
    SafeDirectoryError,
    assert_plain_directory_chain,
    ensure_plain_directory_chain,
    is_reparse_point,
)

logger = logging.getLogger(__name__)

_CONFIG_DIR = "config"
_DB_DIR = "db"
_WORKSPACES_DIR = "workspaces"
_SKILL_PACKAGES_DIR = "skill-packages"
_PLUGINS_DIR = "plugins"
_KNOWLEDGE_DIR = "knowledge"
_TODO_COMMENT_IMAGES_DIR = "project-todo-comment-images"
_TODO_COMMENT_IMAGE_INDEX = "project-todo-comment-image-index.json"
_MANIFEST_NAME = "manifest.json"
_SQLITE_DB_ARC = f"{_DB_DIR}/octop.db"
_PG_DUMP_ARC = f"{_DB_DIR}/octop.dump"
_MIGRATION_VERSION_SUFFIX = "-migrated-from-lightclaw"

# Align with workspace zip export; keep backups smaller / faster.
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".next",
        ".turbo",
        "dist",
        "build",
    }
)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def suggested_backup_filename() -> str:
    """Canonical basename for a newly created manual backup archive."""
    return f"octop-backup-{_timestamp()}.tar.gz"


def _should_skip_path(
    rel: Path,
    *,
    skip_chats: bool,
    system_files_path: str = "",
) -> bool:
    if any(part in _SKIP_DIR_NAMES for part in rel.parts):
        return True
    return skip_chats and is_chat_workspace_rel(
        rel,
        system_files_path=system_files_path,
    )


def _add_dir(
    tf: tarfile.TarFile,
    src: Path,
    arc_root: str,
    *,
    skip_chats: bool,
    system_files_path: str = "",
) -> None:
    if not src.is_dir():
        return
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if _should_skip_path(
            rel,
            skip_chats=skip_chats,
            system_files_path=system_files_path,
        ):
            continue
        tf.add(path, arcname=f"{arc_root}/{rel.as_posix()}")


def _private_comment_image_files(root: Path, *, allow_temp: bool) -> list[tuple[str, Path]]:
    """Enumerate only plain ``<project ULID>/<image ULID>`` object files.

    The private tree is unlike a workspace: following a planted link here
    could copy unrelated secrets into an archive or restore them as images.
    """
    try:
        os.lstat(root)
    except FileNotFoundError:
        return []
    assert_plain_directory_chain(root)
    objects: list[tuple[str, Path]] = []
    for project_dir in sorted(root.iterdir()):
        if project_dir.name == "_tmp" and allow_temp:
            assert_plain_directory_chain(project_dir)
            continue  # In-flight .part files are not committed comment images.
        if project_dir.name == "_tmp":
            raise SafeDirectoryError("temp files do not belong in a comment image archive")
        assert_plain_directory_chain(project_dir)
        for image_path in sorted(project_dir.iterdir()):
            object_key = make_object_key(project_dir.name, image_path.name)
            st = os.lstat(image_path)
            if not stat.S_ISREG(st.st_mode) or is_reparse_point(st) or st.st_nlink != 1:
                raise SafeDirectoryError(f"private comment image is not a plain file: {image_path}")
            objects.append((object_key, image_path))
    return objects


class _HashingReader:
    """Digest the exact bytes consumed by :meth:`TarFile.addfile`."""

    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        self._digest = hashlib.sha256()
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        self._digest.update(chunk)
        self.count += len(chunk)
        return chunk

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _comment_image_metadata(
    pool: DatabasePool, *, sqlite_snapshot: Path | None, schema_version: int
) -> dict[str, tuple[int, str]]:
    """Read object expectations from the database captured for this archive."""
    if schema_version < 33:
        return {}
    query = "SELECT object_key, size_bytes, sha256 FROM project_todo_comment_images"
    if sqlite_snapshot is not None:
        # sqlite3.Connection.__exit__ commits; it does not close the handle.
        # A leaked handle prevents the temporary snapshot's removal on Windows.
        with closing(sqlite3.connect(sqlite_snapshot)) as conn:
            rows = conn.execute(query).fetchall()
    else:
        # pg_dump is a separate snapshot: callers must quiesce writes for a
        # multi-resource backup, as with the existing workspace content.
        with pool.connect() as conn:
            rows = conn.execute(query).fetchall()
    return {str(row[0]): (int(row[1]), str(row[2])) for row in rows}


def _pg_dump_comment_image_metadata(
    dump_file: Path, scratch_dir: Path
) -> dict[str, tuple[int, str]]:
    """Inspect a custom PG dump's image COPY without connecting to a database.

    ``--table`` must be the bare relation name: pg_restore can silently emit
    no rows when given ``public.project_todo_comment_images``. The required
    COPY section is parsed strictly, so an absent or changed table fails before
    the live database or private image root is modified.
    """
    output = scratch_dir / "project-todo-comment-images.copy.sql"
    proc = subprocess.run(
        [
            _require_tool("pg_restore"),
            "--data-only",
            "--table=project_todo_comment_images",
            "--file",
            str(output),
            str(dump_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not output.is_file():
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "cannot inspect comment image database dump")
    try:
        return _parse_pg_comment_image_copy(output)
    except (OSError, UnicodeError, ValueError, AssetStorageError) as exc:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "invalid comment image database dump") from exc


def _parse_pg_comment_image_copy(path: Path) -> dict[str, tuple[int, str]]:
    """Parse exactly one pg_restore COPY section for three ASCII metadata fields."""
    expected: dict[str, tuple[int, str]] = {}
    columns: list[str] | None = None
    seen_copy = False
    with path.open("r", encoding="utf-8") as sql:
        for raw_line in sql:
            line = raw_line.rstrip("\r\n")
            if columns is not None:
                if line == r"\.":
                    columns = None
                    continue
                cells = line.split("\t")
                if len(cells) != len(columns):
                    raise ValueError("invalid comment image COPY row width")
                key = cells[columns.index("object_key")]
                ids = key.split("/")
                if len(ids) != 2 or make_object_key(ids[0], ids[1]) != key:
                    raise ValueError("invalid comment image object key")
                size_text = cells[columns.index("size_bytes")]
                digest = cells[columns.index("sha256")]
                if not size_text.isascii() or not size_text.isdecimal():
                    raise ValueError("invalid comment image size")
                size = int(size_text)
                if not (0 < size <= 8 * 1024 * 1024):
                    raise ValueError("comment image size outside limit")
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    raise ValueError("invalid comment image digest")
                if key in expected:
                    raise ValueError("duplicate comment image object key")
                expected[key] = (size, digest)
                continue
            if not line.startswith("COPY "):
                continue
            if seen_copy or not line.endswith(") FROM stdin;") or " (" not in line:
                raise ValueError("unexpected comment image COPY section")
            relation, fields = line[5:].split(" (", 1)
            if relation.rsplit(".", 1)[-1].strip('"') != "project_todo_comment_images":
                raise ValueError("unexpected copied relation")
            columns = [
                field.strip().strip('"') for field in fields[: -len(") FROM stdin;")].split(",")
            ]
            if len(columns) != len(set(columns)) or not {
                "object_key",
                "size_bytes",
                "sha256",
            }.issubset(columns):
                raise ValueError("comment image COPY columns changed")
            seen_copy = True
    if not seen_copy or columns is not None:
        raise ValueError("comment image COPY section is missing or unterminated")
    return expected


def _add_private_comment_images(
    tf: tarfile.TarFile, root: Path, expected: dict[str, tuple[int, str]]
) -> None:
    files = dict(_private_comment_image_files(root, allow_temp=True))
    if expected.keys() - files.keys():
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "comment image bytes are missing")
    for object_key in sorted(expected):
        path = files[object_key]
        expected_size, expected_sha256 = expected[object_key]
        # Asset storage opens root/project/object using no-follow descriptors;
        # stream from that verified inode instead of reopening a pathname.
        with open_regular_file_no_follow(path) as image:
            st = os.fstat(image.fileno())
            if st.st_nlink != 1:
                raise SafeDirectoryError(f"private comment image is hardlinked: {path}")
            if st.st_size != expected_size:
                raise OctopError(
                    ErrorCode.SLASH_BAD_ARGS, "comment image size differs from database"
                )
            info = tarfile.TarInfo(f"{_TODO_COMMENT_IMAGES_DIR}/{object_key}")
            info.size = st.st_size
            info.mtime = int(st.st_mtime)
            info.mode = 0o600
            reader = _HashingReader(image)
            tf.addfile(info, cast(BinaryIO, reader))
            if reader.count != expected_size or reader.sha256 != expected_sha256:
                raise OctopError(
                    ErrorCode.SLASH_BAD_ARGS, "comment image digest differs from database"
                )


def _add_private_comment_image_index(
    tf: tarfile.TarFile, expected: dict[str, tuple[int, str]]
) -> None:
    payload = {
        "version": 1,
        "objects": {
            key: {"size_bytes": size, "sha256": digest}
            for key, (size, digest) in sorted(expected.items())
        },
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    info = tarfile.TarInfo(_TODO_COMMENT_IMAGE_INDEX)
    info.size = len(data)
    info.mode = 0o600
    tf.addfile(info, io.BytesIO(data))


def _read_private_comment_image_index(extracted: Path) -> dict[str, tuple[int, str]]:
    index_path = extracted / _TODO_COMMENT_IMAGE_INDEX
    if not index_path.is_file():
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "comment image archive index is missing")
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("unsupported index version")
        objects = raw.get("objects")
        if not isinstance(objects, dict):
            raise ValueError("index objects must be a mapping")
        expected: dict[str, tuple[int, str]] = {}
        for key, value in objects.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError("invalid indexed image")
            ids = key.split("/")
            if len(ids) != 2 or make_object_key(ids[0], ids[1]) != key:
                raise ValueError("invalid indexed object key")
            size, digest = value.get("size_bytes"), value.get("sha256")
            if type(size) is not int or not (0 < size <= 8 * 1024 * 1024):
                raise ValueError("invalid indexed image size")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("invalid indexed image digest")
            expected[key] = size, digest
        return expected
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, AssetStorageError) as exc:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "invalid comment image archive index") from exc


def _plain_image_root_if_present(root: Path) -> bool:
    try:
        os.lstat(root)
    except FileNotFoundError:
        return False
    assert_plain_directory_chain(root)
    return True


def _stage_private_comment_images(
    extracted: Path,
    dest: Path,
    stage: Path,
    expected: dict[str, tuple[int, str]],
) -> int:
    """Validate both trees and copy extracted objects before changing the DB."""
    assert_plain_directory_chain(dest.parent)
    _plain_image_root_if_present(dest)
    ensure_plain_directory_chain(stage, private_from=stage)
    objects = stage / "objects"
    ensure_plain_directory_chain(objects, private_from=stage)
    files = dict(
        _private_comment_image_files(extracted / _TODO_COMMENT_IMAGES_DIR, allow_temp=False)
    )
    if files.keys() != expected.keys():
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "comment image archive objects are incomplete")
    for object_key, src in sorted(files.items()):
        out = objects / object_key
        ensure_plain_directory_chain(out.parent, private_from=stage)
        digest = hashlib.sha256()
        size = 0
        with open_regular_file_no_follow(src) as image, out.open("xb") as target:
            while chunk := image.read(256 * 1024):
                target.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if (size, digest.hexdigest()) != expected[object_key]:
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS, "comment image archive bytes differ from index"
            )
        if os.name == "posix":
            os.chmod(out, 0o600)
    return len(files)


def _install_private_comment_images(stage: Path, dest: Path) -> None:
    """Swap the complete private tree on one filesystem, rolling back on error."""
    previous = stage / "previous"
    had_previous = _plain_image_root_if_present(dest)
    try:
        if had_previous:
            os.replace(dest, previous)
        os.replace(stage / "objects", dest)
    except BaseException:
        _rollback_private_comment_images(stage, dest)
        raise


def _rollback_private_comment_images(stage: Path, dest: Path) -> None:
    """Undo actual swap state, including interrupted transitions and repeated rollback."""
    assert_plain_directory_chain(stage)
    assert_plain_directory_chain(dest.parent)
    previous = stage / "previous"
    objects = stage / "objects"
    has_previous = _plain_image_root_if_present(previous)
    has_objects = _plain_image_root_if_present(objects)
    has_dest = _plain_image_root_if_present(dest)
    if has_previous:
        if has_dest:
            if has_objects:
                raise SafeDirectoryError("private image rollback state is inconsistent")
            os.replace(dest, objects)
        os.replace(previous, dest)
    elif not has_objects and has_dest:
        os.replace(dest, objects)
    # Existing objects with no previous tree mean no install occurred, or the
    # original tree has already been restored. Never move that original again.


def _system_files_path_from_row(row: Any) -> str:
    raw = getattr(row, "config_json", None)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return system_files_path_from_config(parsed if isinstance(parsed, dict) else None)


def _build_manifest(
    *,
    paths: PathLayout,
    agent_rows: list[Any],
    pool: DatabasePool,
    db_arc: str,
    database_driver: str,
    database_dump_format: str,
    env_path: Path,
    include_config: bool,
    include_workspaces: bool,
    include_skill_packages: bool,
    include_plugins: bool,
    include_knowledge: bool,
    include_chats: bool,
) -> BackupManifest:
    try:
        schema_version = _current_version(pool)
    except Exception:
        schema_version = 0
    agents = [
        AgentBackupEntry(
            agent_id=str(row.agent_id),
            name=str(row.name),
            workspace_included=include_workspaces,
        )
        for row in agent_rows
    ]
    return BackupManifest(
        manifest_version=MANIFEST_VERSION,
        octop_version=__version__,
        schema_version=schema_version,
        created_at=datetime.now(UTC).isoformat(),
        home=str(paths.root),
        db_file=db_arc,
        database_driver=database_driver,
        database_dump_format=database_dump_format,
        agents=agents,
        includes_config=include_config and paths.config.is_file(),
        includes_env=include_config and env_path.is_file(),
        includes_skill_packages=include_skill_packages,
        includes_plugins=include_plugins,
        includes_knowledge=include_knowledge,
        includes_chats=include_chats,
        includes_project_todo_comment_images=schema_version >= 33,
    )


def create_system_backup(
    *,
    paths: PathLayout,
    agent_rows: list[Any],
    pool: DatabasePool,
    db_config: DatabaseConfig,
    dest: Path,
    include_config: bool = True,
    include_workspaces: bool = True,
    include_skill_packages: bool = True,
    include_plugins: bool = True,
    include_knowledge: bool = True,
    include_chats: bool = False,
) -> str:
    """Write a ``.tar.gz`` archive to *dest* (streamed to disk).

    Configuration, agent workspaces, skill packages, plugins, and knowledge
    files can be omitted independently. ``include_chats`` defaults to False
    because thread/session history usually dominates archive size. Restore
    keeps live chats and omitted directories when the manifest says they were
    not packed.

    Returns the suggested basename (``octop-backup-….tar.gz``). Callers that
    need a different name should rename/move *dest* afterward.
    """
    if pool.dialect == "postgresql":
        db_arc = _PG_DUMP_ARC
        database_driver = "postgresql"
        database_dump_format = "pg_custom"
    else:
        db_arc = _SQLITE_DB_ARC
        database_driver = "sqlite"
        database_dump_format = "sqlite_file"
        if not isinstance(pool, SqlitePool):
            raise OctopError(ErrorCode.INTERNAL_ERROR, "sqlite backup requires SqlitePool")
        if not pool.path.is_file():
            raise OctopError(ErrorCode.NOT_FOUND, f"database not found: {pool.path}")

    env_path = env_file_path(paths.root)
    manifest = _build_manifest(
        paths=paths,
        agent_rows=agent_rows,
        pool=pool,
        db_arc=db_arc,
        database_driver=database_driver,
        database_dump_format=database_dump_format,
        env_path=env_path,
        include_config=include_config,
        include_workspaces=include_workspaces,
        include_skill_packages=include_skill_packages,
        include_plugins=include_plugins,
        include_knowledge=include_knowledge,
        include_chats=include_chats,
    )
    filename = suggested_backup_filename()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dest = root / db_arc
            history_path = paths.root / "history_v2.sqlite"
            history_dest = root / "history_v2.sqlite"
            marker = history_path.with_suffix(".required")
            if include_chats and marker.exists() and not history_path.exists():
                raise FileNotFoundError("Required history archive is missing")
            if include_chats and history_path.exists():
                # Snapshot the child archive before the main thread registry.
                # A fully consistent multi-file restore still requires quiescence.
                snapshot_sqlite_file(history_path, history_dest)
            if pool.dialect == "postgresql":
                dump_postgres(
                    db_config.postgresql_conninfo(),
                    db_dest,
                    exclude_table_data=() if include_chats else CHAT_TABLES_CHILD_FIRST,
                )
            else:
                assert isinstance(pool, SqlitePool)
                snapshot_sqlite_file(pool.path, db_dest)
                if not include_chats:
                    strip_chat_tables_from_sqlite_file(db_dest)

            manifest_path = root / _MANIFEST_NAME
            manifest_path.write_text(manifest.to_json(), encoding="utf-8")

            if include_config and paths.config.is_file():
                cfg_dir = root / _CONFIG_DIR
                cfg_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(paths.config, cfg_dir / "config.json")
            if include_config and env_path.is_file():
                cfg_dir = root / _CONFIG_DIR
                cfg_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(env_path, cfg_dir / "env")

            with tarfile.open(partial, mode="w:gz") as tf:
                tf.add(manifest_path, arcname=_MANIFEST_NAME)
                tf.add(db_dest, arcname=db_arc)
                if history_dest.exists():
                    tf.add(history_dest, arcname="history/history_v2.sqlite")
                    if marker.exists():
                        tf.add(marker, arcname="history/history_v2.required")
                if include_config and paths.config.is_file():
                    tf.add(
                        root / _CONFIG_DIR / "config.json",
                        arcname=f"{_CONFIG_DIR}/config.json",
                    )
                if include_config and env_path.is_file():
                    tf.add(root / _CONFIG_DIR / "env", arcname=f"{_CONFIG_DIR}/env")
                if include_workspaces:
                    for row in agent_rows:
                        agent_id = str(row.agent_id)
                        try:
                            ws = workspace_dir_from_config_json(
                                getattr(row, "config_json", None),
                                paths=paths,
                                agent_id=agent_id,
                                ensure=False,
                            )
                            if not ws.is_dir():
                                continue
                            _add_dir(
                                tf,
                                ws,
                                f"{_WORKSPACES_DIR}/{agent_id}",
                                skip_chats=not include_chats,
                                system_files_path=_system_files_path_from_row(row),
                            )
                        except OSError:
                            logger.warning(
                                "skipping workspace for agent %s",
                                agent_id,
                                exc_info=True,
                            )
                if include_skill_packages and paths.skill_packages_dir.is_dir():
                    _add_dir(
                        tf,
                        paths.skill_packages_dir,
                        _SKILL_PACKAGES_DIR,
                        skip_chats=False,
                    )
                if include_plugins and paths.plugins_dir.is_dir():
                    _add_dir(
                        tf,
                        paths.plugins_dir,
                        _PLUGINS_DIR,
                        skip_chats=False,
                    )
                if include_knowledge and paths.knowledge_dir.is_dir():
                    _add_dir(
                        tf,
                        paths.knowledge_dir,
                        _KNOWLEDGE_DIR,
                        skip_chats=False,
                    )
                if manifest.includes_project_todo_comment_images:
                    expected_images = _comment_image_metadata(
                        pool,
                        sqlite_snapshot=db_dest if isinstance(pool, SqlitePool) else None,
                        schema_version=manifest.schema_version,
                    )
                    if pool.dialect == "postgresql" and expected_images != (
                        _pg_dump_comment_image_metadata(db_dest, root)
                    ):
                        raise OctopError(
                            ErrorCode.SLASH_BAD_ARGS,
                            "comment image dump differs from database",
                        )
                    _add_private_comment_images(
                        tf, paths.project_todo_comment_images, expected_images
                    )
                    _add_private_comment_image_index(tf, expected_images)

        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return filename


def _extract_manifest_from_dir(extracted: Path) -> BackupManifest:
    manifest_path = extracted / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "backup archive missing manifest.json")
    try:
        manifest = BackupManifest.load_text(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, f"invalid manifest: {exc}") from exc
    if manifest.manifest_version != MANIFEST_VERSION:
        raise OctopError(
            ErrorCode.SLASH_BAD_ARGS,
            f"unsupported manifest version {manifest.manifest_version}",
        )
    return manifest


def _extract_archive(source: Path | bytes, dest_dir: Path) -> None:
    """Extract *source* into *dest_dir* without holding the whole archive in a dict."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(source, bytes):
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:*") as tf:
            _validate_private_comment_image_members(tf)
            # Python 3.12+: refuse path traversal / special files.
            tf.extractall(dest_dir, filter=tarfile.data_filter)
        return
    if not Path(source).is_file():
        raise OctopError(ErrorCode.NOT_FOUND, f"backup not found: {source}")
    with tarfile.open(source, mode="r:*") as tf:
        _validate_private_comment_image_members(tf)
        tf.extractall(dest_dir, filter=tarfile.data_filter)


def _validate_private_comment_image_members(tf: tarfile.TarFile) -> None:
    """Reject private-tree links before tar extraction can follow an alias."""
    for member in tf.getmembers():
        name = member.name.replace("\\", "/")
        while name.startswith("./"):
            name = name[2:]
        if name == _TODO_COMMENT_IMAGE_INDEX and not member.isfile():
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "unsafe comment image archive index")
        if name != _TODO_COMMENT_IMAGES_DIR and not name.startswith(f"{_TODO_COMMENT_IMAGES_DIR}/"):
            continue
        parts = name.split("/")
        if member.isdir() and parts[-1] == "":
            parts.pop()
        if any(part in {"", ".", ".."} for part in parts) or not (
            member.isfile() or member.isdir()
        ):
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "unsafe comment image archive member")


def _is_migration_backup(manifest: BackupManifest) -> bool:
    """Return True when the backup was produced by the LightClaw migration tool."""
    return manifest.octop_version.endswith(_MIGRATION_VERSION_SUFFIX)


def _iter_extracted_files(root: Path, prefix: str) -> list[tuple[str, Path]]:
    """Return ``(archive-relative-posix, on-disk path)`` under *prefix*."""
    base = root / prefix
    if not base.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        out.append((rel, path))
    return out


def _replace_tree_from_archive(extracted: Path, dest: Path, arc_name: str) -> int:
    """Replace *dest* with files packed under *arc_name* in the extracted archive."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    prefix = f"{arc_name}/"
    for rel, src_file in _iter_extracted_files(extracted, arc_name):
        rest = rel[len(prefix) :]
        if not rest:
            continue
        out = dest / rest
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, out)
        count += 1
    return count


def _postgres_preimage_schema_version(pool: DatabasePool) -> int:
    """Read an initialized public database's actual compensation watermark."""
    try:
        with pool.connect() as conn:
            rows = conn.execute("SELECT version FROM public._schema_version").fetchall()
    except Exception:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            "Cannot read PostgreSQL backup preimage schema version",
            details={"reason": "backup_preimage_schema_version_unavailable"},
        ) from None
    if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            "Cannot read PostgreSQL backup preimage schema version",
            details={"reason": "backup_preimage_schema_version_unavailable"},
        )
    return int(rows[0][0])


def restore_system_backup(
    source: Path | bytes,
    *,
    paths: PathLayout,
    pool: DatabasePool,
    db_config: DatabaseConfig,
    restore_config: bool = True,
    preserve_users: bool | None = None,
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    """Restore database, workspaces, and optional config from a tar.gz archive.

    *source* may be a filesystem path (preferred) or in-memory bytes (tests / legacy).

    ``preserve_users`` controls whether the *current* Octop instance's login
    credentials (``users`` rows + ``secrets.jwt``) are written back after the
    database is replaced:

    * ``None`` (default) — auto-detect: preserves credentials when the backup
      was produced by an external migration tool (``octop_version`` ends with
      ``"-migrated-from-lightclaw"``).
    * ``True`` — always preserve current users + JWT secret (cross-system
      import where passwords and outstanding sessions must remain valid).
    * ``False`` — restore the users/secrets tables as-is from the backup
      (normal same-instance restore).

    For LightClaw migration archives, ``owner_user_id`` (typically the admin
    performing the restore) receives all imported ``user_id`` ownership. When
    omitted, the first preserved admin (else first preserved user) is used.
    """
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as cleanup:
        extracted = Path(tmp) / "extracted"
        _extract_archive(source, extracted)
        manifest = _extract_manifest_from_dir(extracted)
        image_stage: Path | None = None
        image_file_count = 0
        if (
            (paths.root / "history_v2.required").exists()
            or (paths.root / "history_v2.sqlite").exists()
            or (extracted / "history/history_v2.sqlite").exists()
        ):
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                "Versioned history requires a coordinated offline restore; no database was changed",
            )
        is_migration = _is_migration_backup(manifest)

        # Resolve effective preserve_users flag before touching the DB.
        effective_preserve_users = is_migration if preserve_users is None else preserve_users

        archive_driver = manifest.database_driver or "sqlite"
        if archive_driver != pool.dialect:
            raise OctopError(
                ErrorCode.BACKUP_DRIVER_MISMATCH,
                f"backup database_driver={archive_driver!r} does not match "
                f"runtime dialect={pool.dialect!r}; cross-engine restore is refused",
                status=400,
                details={"archive_driver": archive_driver, "runtime_driver": pool.dialect},
            )

        runtime_schema_version = _max_discovered_version(pool.dialect)
        if manifest.schema_version > runtime_schema_version:
            details = {
                "archive_schema_version": manifest.schema_version,
                "runtime_schema_version": runtime_schema_version,
            }
            raise OctopError(
                ErrorCode.BACKUP_SCHEMA_INCOMPATIBLE,
                f"backup schema version {manifest.schema_version} is newer than "
                f"the maximum supported version {runtime_schema_version}",
                details=details,
            )

        db_path = extracted / manifest.db_file
        if not db_path.is_file():
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "backup archive missing database file")

        # Recovery preimages must outlive archive extraction if compensation
        # fails. mkdtemp has no TemporaryDirectory finalizer that could erase
        # them after the conditional ExitStack cleanup has chosen to retain.
        assert_plain_directory_chain(paths.root)
        recovery_stage = Path(
            tempfile.mkdtemp(prefix=".todo-comment-image-restore-", dir=paths.root)
        )
        keep_recovery = False

        def cleanup_recovery() -> None:
            if not keep_recovery:
                assert_plain_directory_chain(recovery_stage)
                shutil.rmtree(recovery_stage)

        cleanup.callback(cleanup_recovery)
        ensure_plain_directory_chain(recovery_stage, private_from=recovery_stage)

        if manifest.includes_project_todo_comment_images:
            expected_images = _read_private_comment_image_index(extracted)
            if archive_driver == "sqlite":
                dump_images = _comment_image_metadata(
                    pool, sqlite_snapshot=db_path, schema_version=manifest.schema_version
                )
            else:
                dump_images = _pg_dump_comment_image_metadata(db_path, Path(tmp))
            if expected_images != dump_images:
                raise OctopError(
                    ErrorCode.SLASH_BAD_ARGS, "comment image archive index differs from database"
                )
            image_stage = recovery_stage
            image_file_count = _stage_private_comment_images(
                extracted, paths.project_todo_comment_images, image_stage, expected_images
            )

        saved_chats = (
            capture_chat_tables(pool, Path(tmp) / "preserved-chats.sqlite")
            if not manifest.includes_chats
            else None
        )

        # Capture current login credentials before overwriting the DB (only when needed).
        saved_users: list[tuple[object, ...]] = []
        saved_jwt: bytes | None = None
        if effective_preserve_users and pool is not None:
            saved_users = capture_users_from_pool(pool)
            saved_jwt = capture_jwt_secret_from_pool(pool)

        effective_owner: int | None = None
        if is_migration:
            effective_owner = owner_user_id
            if effective_owner is None:
                effective_owner = infer_owner_user_id(saved_users)
            if effective_owner is not None and saved_users:
                saved_ids = {int(str(row[0])) for row in saved_users}
                if int(effective_owner) not in saved_ids:
                    raise OctopError(
                        ErrorCode.SLASH_BAD_ARGS,
                        f"owner_user_id={effective_owner} is not among current users",
                        status=400,
                    )

        # Keep the full current database before installing either resource.
        # A failed replacement/upgrade must restore this preimage, including
        # current catalog references, users and chats, together with the tree.
        previous_schema_version = (
            _postgres_preimage_schema_version(pool) if pool.dialect == "postgresql" else None
        )
        previous_database = recovery_stage / "previous-database"
        if pool.dialect == "postgresql":
            dump_postgres(db_config.postgresql_conninfo(), previous_database)
        elif isinstance(pool, SqlitePool):
            snapshot_sqlite_file(pool.path, previous_database)
        else:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "sqlite restore requires SqlitePool")

        # Install the staged image tree before database replacement. An image
        # rename failure therefore cannot leave new DB rows pointing at old
        # bytes. Keep the previous tree in the stage until the DB upgrade works.
        ownership_remap: dict[str, int] | None = None
        database_attempted = False
        try:
            # Set retention before any destructive call: an interruption can
            # arrive after the real operation but before its caller returns.
            keep_recovery = True
            if image_stage is not None:
                _install_private_comment_images(image_stage, paths.project_todo_comment_images)

            database_attempted = True
            if pool.dialect == "postgresql":
                restore_postgres(
                    db_config.postgresql_conninfo(),
                    db_path,
                    pool=pool,
                    schema_version=manifest.schema_version,
                )
            elif isinstance(pool, SqlitePool):
                restore_sqlite_into_pool(db_path, pool)
            else:
                raise OctopError(ErrorCode.INTERNAL_ERROR, "sqlite restore requires SqlitePool")

            # Upgrade before current-version repository/preservation queries.
            try:
                run_migrations(pool)
            except Exception as exc:
                details = {
                    "archive_schema_version": manifest.schema_version,
                    "runtime_schema_version": runtime_schema_version,
                }
                raise OctopError(
                    ErrorCode.BACKUP_SCHEMA_INCOMPATIBLE,
                    f"failed to upgrade backup schema version {manifest.schema_version} "
                    f"to {runtime_schema_version}: {exc}",
                    details=details,
                ) from exc
            # Migration ownership remap must run after the target owner exists and
            # before pruning backup placeholder users (avoids ON DELETE CASCADE).
            if saved_users and pool is not None:
                upsert_users_into_pool(pool, saved_users)
                if is_migration and effective_owner is not None:
                    ownership_remap = remap_ownership_to_user(pool, int(effective_owner))
                prune_users_not_in(pool, [row[0] for row in saved_users])
            with pool.transaction() as conn:
                repair_project_plan_keys_in_connection(conn)
                validate_project_todo_d1_in_connection(conn)
        except BaseException as original_error:
            failed_resources: list[str] = []
            if database_attempted:
                try:
                    if pool.dialect == "postgresql":
                        assert previous_schema_version is not None
                        restore_postgres(
                            db_config.postgresql_conninfo(),
                            previous_database,
                            pool=pool,
                            schema_version=previous_schema_version,
                        )
                    elif isinstance(pool, SqlitePool):
                        # Pool transactions only roll back Exception. End a
                        # transaction left by an interruption before restoring
                        # the preimage into this same live connection.
                        with pool.connect() as live:
                            if live.in_transaction:
                                live.rollback()
                        restore_sqlite_into_pool(previous_database, pool)
                except BaseException:
                    failed_resources.append("database")
            if image_stage is not None:
                try:
                    _rollback_private_comment_images(image_stage, paths.project_todo_comment_images)
                except BaseException:
                    failed_resources.append("private_comment_images")
            if failed_resources:
                # Do not interpolate tool/OS errors: they can carry connection
                # information or private paths. Still attempt both resources.
                raise OctopError(
                    ErrorCode.INTERNAL_ERROR,
                    "backup restore failed and rollback did not complete",
                    details={
                        "reason": "backup_restore_rollback_failed",
                        "failed_resources": failed_resources,
                    },
                ) from original_error
            keep_recovery = False
            raise

        if restore_config:
            cfg_path = extracted / _CONFIG_DIR / "config.json"
            if cfg_path.is_file():
                paths.config.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cfg_path, paths.config)
            env_blob_path = extracted / _CONFIG_DIR / "env"
            if env_blob_path.is_file():
                env_path = env_file_path(paths.root)
                env_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(env_blob_path, env_path)

        # Migration backups ship a foreign ``secrets`` table. Prefer the current
        # instance's JWT secret so outstanding sessions stay valid; only seed a
        # fresh key when this instance never had one.
        if effective_preserve_users and pool is not None:
            if saved_jwt is not None:
                restore_jwt_secret_into_pool(pool, saved_jwt)
            else:
                SecretRepo(pool).get_or_create("jwt", lambda: os.urandom(32))

        restored_workspaces = 0
        prefix = f"{_WORKSPACES_DIR}/"
        agent_repo = AgentRepo(pool)
        workspace_by_agent: dict[str, Path] = {}
        system_path_by_agent: dict[str, str] = {}
        for rel, src_file in _iter_extracted_files(extracted, _WORKSPACES_DIR):
            file_rel = rel[len(prefix) :]
            if "/" not in file_rel:
                continue
            agent_id, _, rest = file_rel.partition("/")
            if not agent_id or not rest:
                continue
            dest_root = workspace_by_agent.get(agent_id)
            if dest_root is None:
                agent_row = agent_repo.get(agent_id)
                dest_root = workspace_dir_from_config_json(
                    None if agent_row is None else agent_row.config_json,
                    paths=paths,
                    agent_id=agent_id,
                )
                workspace_by_agent[agent_id] = dest_root
                system_path_by_agent[agent_id] = (
                    _system_files_path_from_row(agent_row) if agent_row is not None else ""
                )
            dest = dest_root / rest
            if not manifest.includes_chats and is_chat_workspace_rel(
                Path(rest),
                system_files_path=system_path_by_agent.get(agent_id, ""),
            ):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            restored_workspaces += 1

        restored_skill_package_files = 0
        if manifest.includes_skill_packages:
            restored_skill_package_files = _replace_tree_from_archive(
                extracted,
                paths.skill_packages_dir,
                _SKILL_PACKAGES_DIR,
            )

        restored_plugin_files = 0
        if manifest.includes_plugins:
            restored_plugin_files = _replace_tree_from_archive(
                extracted,
                paths.plugins_dir,
                _PLUGINS_DIR,
            )

        restored_knowledge_files = 0
        if manifest.includes_knowledge:
            restored_knowledge_files = _replace_tree_from_archive(
                extracted,
                paths.knowledge_dir,
                _KNOWLEDGE_DIR,
            )

        preserved_chat_rows, skipped_chat_rows = (
            restore_preserved_chats(pool, saved_chats) if saved_chats is not None else (0, 0)
        )
        schema_version = _current_version(pool)
        keep_recovery = False

    result: dict[str, Any] = {
        "schema_version": schema_version,
        "octop_version": manifest.octop_version,
        "agents": len(manifest.agents),
        "workspace_files": restored_workspaces,
        "skill_package_files": restored_skill_package_files,
        "plugin_files": restored_plugin_files,
        "knowledge_files": restored_knowledge_files,
        "project_todo_comment_image_files": image_file_count,
        "restore_config": restore_config,
        "chats_restored": manifest.includes_chats,
        "preserved_chat_rows": preserved_chat_rows,
        "skipped_chat_rows": skipped_chat_rows,
        "users_preserved": effective_preserve_users,
        "jwt_preserved": bool(saved_jwt is not None and effective_preserve_users),
        "database_driver": archive_driver,
    }
    if ownership_remap is not None:
        result["owner_user_id"] = int(effective_owner) if effective_owner is not None else None
        result["ownership_remap"] = ownership_remap
    return result
