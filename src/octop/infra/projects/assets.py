"""Project asset service — policy layer for PS-06A / 023A.

Owns everything between the router and the repo/storage engines:

* **Authorization** — every operation derives the caller from the session
  user id and re-checks *current* membership; outsiders (including instance
  admins), unknown projects, and foreign node ids all collapse into the same
  uniform 404, so ids and object keys are never authorization credentials.
* **Naming** — display names are NFC-normalized and trimmed; ``name_key`` is
  the casefolded name, so ``Foo``/``foo`` collide under one parent (the DB
  unique constraint is the arbiter, answered as 409). Path separators,
  Windows-reserved stems, and control characters are rejected with 422.
* **Publish order** — chunked temp write (cap + SHA-256) → close →
  ``os.replace`` → one DB transaction → success. Any non-committed exit
  unlinks this request's artifacts, so failures never leave a visible
  half-asset; the crash windows are documented in ``asset_storage``.
* **Cleaner** — the first asset operation after a restart runs the restricted
  orphan reclaim exactly once per process; its failure never blocks traffic.
* **Archived projects** — reads and downloads keep working; writes answer 403.

All public methods are synchronous (disk + sqlite work) and must be called
from routers via ``run_in_executor``. Views never carry object keys, disk
paths, or credentials. 023A writes no ``project_events``.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, NoReturn

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.asset_storage import (
    AssetStorageError,
    AssetUploadTooLarge,
    ProjectAssetStorage,
    make_object_key,
)
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT

logger = logging.getLogger(__name__)

#: Display-name cap (characters, after NFC normalization and trimming).
ASSET_NAME_MAX_LENGTH = 120

ASSET_KINDS: tuple[str, ...] = ("file", "folder")

# Path separators, Windows-reserved metacharacters, and control characters
# are rejected outright; names never reach the disk layout anyway (objects
# are stored under server-generated ULID keys), but this keeps names safe for
# Content-Disposition, future exports, and Windows-mounted shares.
_FORBIDDEN_NAME_CHARS = frozenset('/\\:*?"<>|')
_RESERVED_STEMS = (
    frozenset({"CON", "PRN", "AUX", "NUL"})
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def validate_asset_name(raw: object) -> str:
    """NFC-normalize, trim, and enforce the naming rules; raise ValueError."""
    if not isinstance(raw, str):
        raise ValueError("name must be a string")
    name = unicodedata.normalize("NFC", raw).strip()
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > ASSET_NAME_MAX_LENGTH:
        raise ValueError(f"name must be at most {ASSET_NAME_MAX_LENGTH} characters")
    if name in (".", ".."):
        raise ValueError("name must not be a path segment")
    for ch in name:
        if ch in _FORBIDDEN_NAME_CHARS or ord(ch) < 32 or ord(ch) == 127:
            raise ValueError("name contains forbidden characters")
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_STEMS:
        raise ValueError("name uses a reserved device stem")
    return name


def asset_name_key(name: str) -> str:
    """Canonical conflict key, including callers with decomposed Unicode."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def sanitize_upload_filename(raw: object) -> str:
    """Reduce an upload filename to its final segment before validation."""
    if not isinstance(raw, str):
        raise ValueError("filename must be a string")
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not basename:
        raise ValueError("filename must not be empty")
    return basename


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Safe response views — no object keys, no disk paths, no actor ids
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetNodeView:
    node_id: str
    parent_node_id: str | None
    kind: str
    name: str
    size_bytes: int | None
    media_type: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class AssetVersionView:
    version_id: str
    size_bytes: int
    sha256: str
    media_type: str | None


@dataclass(frozen=True)
class UploadedAssetView:
    node: AssetNodeView
    version: AssetVersionView


@dataclass(frozen=True)
class AssetPage:
    items: list[AssetNodeView]
    total: int
    limit: int
    offset: int
    has_more: bool


@dataclass(frozen=True)
class AssetUsageView:
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class AssetDownloadView:
    #: Private on-disk path — the router streams it as an attachment and
    #: never renders it into a response body or header.
    path: Path
    filename: str
    media_type: str | None
    size_bytes: int


# One storage instance per physical root so the once-per-process cleaner flag
# survives per-request service construction.
_STORAGE_INSTANCES: dict[str, ProjectAssetStorage] = {}
_STORAGE_LOCK = threading.Lock()


def storage_for_root(root: Path) -> ProjectAssetStorage:
    key = str(root)
    with _STORAGE_LOCK:
        storage = _STORAGE_INSTANCES.get(key)
        if storage is None:
            storage = ProjectAssetStorage(root)
            _STORAGE_INSTANCES[key] = storage
        return storage


class ProjectAssetService:
    def __init__(self, services: Any, *, storage: ProjectAssetStorage | None = None) -> None:
        self._services = services
        if storage is not None:
            self._storage = storage
        else:
            self._storage = storage_for_root(self._paths.ensure_project_assets_dir())

    # ------------------------------------------------------------ plumbing

    @property
    def _paths(self) -> Any:
        return self._services.paths

    @property
    def _repo(self) -> Any:
        return self._services.project_asset_repo

    @property
    def _project_repo(self) -> Any:
        return self._services.project_repo

    # ------------------------------------------------------------ errors

    def _project_not_found(self) -> OctopError:
        return OctopError(ErrorCode.NOT_FOUND, "project not found")

    def _asset_not_found(self) -> OctopError:
        return OctopError(ErrorCode.NOT_FOUND, "asset not found")

    def _invalid(self, message: str) -> OctopError:
        return OctopError(ErrorCode.PROJECT_ASSET_INVALID, message)

    def _conflict(self) -> OctopError:
        return OctopError(
            ErrorCode.PROJECT_ASSET_NAME_CONFLICT,
            "asset name already exists in this folder",
        )

    def _archived_error(self) -> OctopError:
        return OctopError(ErrorCode.FORBIDDEN, "project is archived")

    def _too_large(self, max_bytes: int) -> OctopError:
        max_mb = max(1, max_bytes // (1024 * 1024))
        return OctopError(
            ErrorCode.PROJECT_ASSET_TOO_LARGE,
            f"file too large (max {max_mb}MB)",
            details={"max_mb": max_mb},
        )

    def _raise_for_outcome(self, outcome: str) -> NoReturn:
        if outcome == "not_member":
            raise self._project_not_found()
        if outcome == "archived":
            raise self._archived_error()
        if outcome == "parent_missing":
            raise self._asset_not_found()
        if outcome == "parent_not_folder":
            raise self._invalid("parent is not a folder")
        if outcome == "name_conflict":
            raise self._conflict()
        raise OctopError(ErrorCode.INTERNAL_ERROR, f"unexpected asset outcome: {outcome}")

    # ------------------------------------------------------------ gates

    def _require_membership(self, project_id: str, user_id: int) -> None:
        if self._project_repo.get_membership(project_id, user_id) is None:
            raise self._project_not_found()

    def _require_writable(self, project_id: str, user_id: int) -> None:
        """Membership + live-project gate; the repo re-checks race-safely."""
        self._require_membership(project_id, user_id)
        project = self._project_repo.get_project(project_id)
        if project is None:
            raise self._project_not_found()
        if project.archived:
            raise self._archived_error()

    def _ensure_reclaim(self) -> None:
        """Run the restricted orphan cleaner once per process, best-effort."""
        storage = self._storage
        if storage.reclaim_done:
            return
        with storage.reclaim_lock:
            if storage.reclaim_done:
                return
            try:
                known = self._repo.all_object_keys()
                report = storage.reclaim_orphans(known)
                logger.info(
                    "project asset reclaim complete: temps_removed=%d finals_removed=%d",
                    report.temps_removed,
                    report.finals_removed,
                )
            except Exception:
                # A broken cleaner must never take the asset API down; the
                # next process start retries.
                logger.warning("project asset orphan reclaim failed", exc_info=True)
            finally:
                storage.reclaim_done = True

    def _resolve_read_parent(self, project_id: str, parent_id: str | None) -> str | None:
        """Parent id to list, or ``None`` when the root does not exist yet."""
        if parent_id is None:
            root = self._repo.get_root(project_id)
            return None if root is None else str(root)
        node = self._repo.get_node(project_id, parent_id)
        if node is None:
            raise self._asset_not_found()
        if node.kind != "folder":
            raise self._invalid("parent is not a folder")
        return str(node.node_id)

    def _resolve_write_parent(self, project_id: str, parent_id: str | None) -> str | None:
        """Explicit parents are validated up front; ``None`` defers to the
        repo, which resolves (and lazily creates) the hidden root inside the
        write transaction — a failed upload must not leave DB residue."""
        if parent_id is None:
            return None
        node = self._repo.get_node(project_id, parent_id)
        if node is None:
            raise self._asset_not_found()
        if node.kind != "folder":
            raise self._invalid("parent is not a folder")
        return str(node.node_id)

    # ------------------------------------------------------------ reads

    def list_assets(
        self,
        project_id: str,
        *,
        user_id: int,
        parent_id: str | None = None,
        q: str | None = None,
        kind: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> AssetPage:
        if kind is not None and kind not in ASSET_KINDS:
            raise ValueError(f"kind must be one of {', '.join(ASSET_KINDS)}")
        if limit < 1 or limit > MAX_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_LIMIT}")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        name_like: str | None = None
        if q is not None:
            needle = asset_name_key(unicodedata.normalize("NFC", q).strip())
            if len(needle) > ASSET_NAME_MAX_LENGTH:
                raise self._invalid("search query too long")
            if needle:
                name_like = f"%{_escape_like(needle)}%"
        self._require_membership(project_id, user_id)
        self._ensure_reclaim()
        parent = self._resolve_read_parent(project_id, parent_id)
        if parent is None:
            # Nothing was ever created in this project: an empty first page,
            # and reads never write (no lazy root here).
            return AssetPage(items=[], total=0, limit=limit, offset=offset, has_more=False)
        listed = self._repo.list_nodes(
            project_id,
            user_id=user_id,
            parent_node_id=parent,
            kind=kind,
            name_like=name_like,
            limit=limit,
            offset=offset,
        )
        if listed is None:
            raise self._project_not_found()
        rows, total = listed
        items = [
            AssetNodeView(
                node_id=row.node_id,
                parent_node_id=row.parent_node_id,
                kind=row.kind,
                name=row.name,
                size_bytes=row.size_bytes,
                media_type=row.media_type,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        return AssetPage(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(rows) < total,
        )

    def get_usage(self, project_id: str, *, user_id: int) -> AssetUsageView:
        self._require_membership(project_id, user_id)
        self._ensure_reclaim()
        usage = self._repo.usage(project_id, user_id=user_id)
        if usage is None:
            raise self._project_not_found()
        file_count, total_bytes = usage
        return AssetUsageView(file_count=file_count, total_bytes=total_bytes)

    def prepare_download(self, project_id: str, *, user_id: int, node_id: str) -> AssetDownloadView:
        self._require_membership(project_id, user_id)
        self._ensure_reclaim()
        row = self._repo.get_download(project_id, user_id=user_id, node_id=node_id)
        if row is None:
            raise self._asset_not_found()
        try:
            path = self._storage.open_download(row.object_key)
        except AssetStorageError as exc:
            logger.warning("asset object unavailable for download", exc_info=True)
            raise self._asset_not_found() from exc
        return AssetDownloadView(
            path=path,
            filename=row.name,
            media_type=row.media_type,
            size_bytes=row.size_bytes,
        )

    # ------------------------------------------------------------ writes

    def create_folder(
        self,
        project_id: str,
        *,
        user_id: int,
        name: str,
        parent_id: str | None = None,
    ) -> AssetNodeView:
        try:
            clean = validate_asset_name(name)
        except ValueError as exc:
            raise self._invalid(str(exc)) from exc
        self._require_writable(project_id, user_id)
        self._ensure_reclaim()
        parent = self._resolve_write_parent(project_id, parent_id)
        created = self._repo.create_folder(
            project_id=project_id,
            actor_user_id=user_id,
            parent_node_id=parent,
            name=clean,
            name_key=asset_name_key(clean),
        )
        if created.outcome != "created" or created.row is None:
            self._raise_for_outcome(created.outcome)
        row = created.row
        return AssetNodeView(
            node_id=row.node_id,
            parent_node_id=row.parent_node_id,
            kind=row.kind,
            name=row.name,
            size_bytes=None,
            media_type=None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def upload_file(
        self,
        project_id: str,
        *,
        user_id: int,
        filename: str,
        stream: BinaryIO,
        max_bytes: int,
        parent_id: str | None = None,
    ) -> UploadedAssetView:
        """Stream → temp → atomic publish → single DB commit (fixed order)."""
        try:
            clean = validate_asset_name(sanitize_upload_filename(filename))
        except ValueError as exc:
            raise self._invalid(str(exc)) from exc
        self._require_writable(project_id, user_id)
        self._ensure_reclaim()
        parent = self._resolve_write_parent(project_id, parent_id)
        version_id = str(self._repo.new_version_id())
        object_key = make_object_key(project_id, version_id)
        try:
            temp, stored = self._storage.write_temp(version_id, stream, max_bytes=max_bytes)
        except AssetUploadTooLarge as exc:
            # Storage already discarded this request's temp.
            raise self._too_large(exc.max_bytes) from exc
        # Past this point a final object may exist on disk; every
        # non-committed exit must unlink it (crash window W3 otherwise).
        final: Path | None = None
        try:
            final = self._storage.publish(temp, project_id, version_id)
            media_type = mimetypes.guess_type(clean)[0]
            commit = self._repo.commit_file(
                project_id=project_id,
                actor_user_id=user_id,
                parent_node_id=parent,
                name=clean,
                name_key=asset_name_key(clean),
                media_type=media_type,
                version_id=version_id,
                object_key=object_key,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
            )
        except BaseException:
            self._storage.discard(final if final is not None else temp)
            raise
        if commit.outcome != "committed" or commit.node is None or commit.version is None:
            self._storage.discard(final)
            self._raise_for_outcome(commit.outcome)
        node, version = commit.node, commit.version
        return UploadedAssetView(
            node=AssetNodeView(
                node_id=node.node_id,
                parent_node_id=node.parent_node_id,
                kind=node.kind,
                name=node.name,
                size_bytes=version.size_bytes,
                media_type=version.media_type,
                created_at=node.created_at,
                updated_at=node.updated_at,
            ),
            version=AssetVersionView(
                version_id=version.version_id,
                size_bytes=version.size_bytes,
                sha256=version.sha256,
                media_type=version.media_type,
            ),
        )
