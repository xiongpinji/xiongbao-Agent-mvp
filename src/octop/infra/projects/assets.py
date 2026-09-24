"""Project asset service — policy layer for PS-06A / 023A + PS-06B-1.

Owns everything between the router and the repo/storage engines:

* **Authorization** — every operation derives the caller from the session
  user id and re-checks *current* membership; outsiders (including instance
  admins), unknown projects, and foreign node ids all collapse into the same
  uniform 404, so ids and object keys are never authorization credentials.
  024 task-card shares and 025 text grants never authorize asset access.
* **Naming** — display names are NFC-normalized and trimmed; ``name_key`` is
  the casefolded name, so ``Foo``/``foo`` collide under one parent (the DB
  unique constraint is the arbiter, answered as 409). Path separators,
  Windows-reserved stems, and control characters are rejected with 422.
* **Publish order** — chunked temp write (cap + SHA-256) → close →
  ``os.replace`` → one DB transaction → success. Any non-committed exit
  unlinks this request's artifacts, so failures never leave a visible
  half-asset; the crash windows are documented in ``asset_storage``.
  New-version uploads (PS-06B-1) reuse this exact order and ignore the
  request filename: it never renames the node nor selects a path.
* **Versions** — the current-version switch (upload-new-version, restore) is
  a repo-side pointer flip in one locked transaction; restores copy no bytes
  and check the private object on disk BEFORE switching. Restoring the
  already-current version is an idempotent success, but only after the
  membership/archived gates — archived projects answer 403 even then.
* **Cleaner** — the first asset operation after a restart runs the restricted
  orphan reclaim exactly once per process; its failure never blocks traffic.
* **Archived projects** — reads and downloads keep working; writes answer 403.

All public methods are synchronous (disk + sqlite work) and must be called
from routers via ``run_in_executor``. Views never carry object keys, disk
paths, or credentials. Asset slices write no ``project_events``.
"""

from __future__ import annotations

import logging
import mimetypes
import os
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
    open_regular_file_no_follow,
)
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT

logger = logging.getLogger(__name__)

#: Display-name cap (characters, after NFC normalization and trimming).
ASSET_NAME_MAX_LENGTH = 120

#: Version-listing default page size (contract: default 50, range 1–100).
DEFAULT_VERSION_PAGE_LIMIT = 50

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
class AssetVersionItemView:
    """One immutable version's safe metadata (PS-06B-1).

    ``uploaded_by`` is a nullable opaque user id — never a username, never
    an object key.
    """

    version_id: str
    size_bytes: int
    sha256: str
    media_type: str | None
    uploaded_by: int | None
    created_at: int
    is_current: bool


@dataclass(frozen=True)
class AssetVersionPageView:
    items: list[AssetVersionItemView]
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

    def _raise_for_version_outcome(self, outcome: str) -> NoReturn:
        """Map the repo's version-switch sentinels (PS-06B-1).

        Unknown nodes and missing/foreign versions stay indistinguishable —
        both answer the uniform asset 404.
        """
        if outcome == "not_member":
            raise self._project_not_found()
        if outcome == "archived":
            raise self._archived_error()
        if outcome in ("node_missing", "version_missing"):
            raise self._asset_not_found()
        raise OctopError(ErrorCode.INTERNAL_ERROR, f"unexpected version outcome: {outcome}")

    def _uploaded_view(self, node: Any, version: Any) -> UploadedAssetView:
        """Safe node+version DTO shared by upload and version-switch routes.

        The node's displayed size/media always come from the version the
        switch just made current — never from request data.
        """
        return UploadedAssetView(
            node=AssetNodeView(
                node_id=str(node.node_id),
                parent_node_id=None if node.parent_node_id is None else str(node.parent_node_id),
                kind=str(node.kind),
                name=str(node.name),
                size_bytes=int(version.size_bytes),
                media_type=None if version.media_type is None else str(version.media_type),
                created_at=int(node.created_at),
                updated_at=int(node.updated_at),
            ),
            version=AssetVersionView(
                version_id=str(version.version_id),
                size_bytes=int(version.size_bytes),
                sha256=str(version.sha256),
                media_type=None if version.media_type is None else str(version.media_type),
            ),
        )

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
        except AssetStorageError:
            logger.warning("asset object unavailable for download")
            raise self._asset_not_found() from None
        return AssetDownloadView(
            path=path,
            filename=row.name,
            media_type=row.media_type,
            size_bytes=row.size_bytes,
        )

    def list_versions(
        self,
        project_id: str,
        *,
        user_id: int,
        node_id: str,
        limit: int = DEFAULT_VERSION_PAGE_LIMIT,
        offset: int = 0,
    ) -> AssetVersionPageView:
        """One file node's immutable version page (PS-06B-1).

        Membership is re-checked per request; the repo answers count and page
        from the SAME membership-locked transaction in the full
        ``(created_at DESC, version_id DESC)`` order. Non-members, folder
        nodes, and unknown/foreign ids all share the uniform 404.
        """
        if limit < 1 or limit > MAX_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_LIMIT}")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        self._require_membership(project_id, user_id)
        self._ensure_reclaim()
        listed = self._repo.list_versions(
            project_id, user_id=user_id, node_id=node_id, limit=limit, offset=offset
        )
        if listed is None:
            raise self._asset_not_found()
        rows, total = listed
        items = [
            AssetVersionItemView(
                version_id=row.version_id,
                size_bytes=row.size_bytes,
                sha256=row.sha256,
                media_type=row.media_type,
                uploaded_by=row.uploaded_by,
                created_at=row.created_at,
                is_current=row.is_current,
            )
            for row in rows
        ]
        return AssetVersionPageView(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(rows) < total,
        )

    def prepare_version_download(
        self, project_id: str, *, user_id: int, node_id: str, version_id: str
    ) -> AssetDownloadView:
        """Fail-closed historical-version download (PS-06B-1).

        The repo enforces the full ``(project, node, version)`` triple under
        the membership lock; the disk check (``open_download``) then rejects
        missing objects, symlinks, directories, and root escapes with the
        same uniform 404. The filename is always the CURRENT node name.
        """
        self._require_membership(project_id, user_id)
        self._ensure_reclaim()
        row = self._repo.get_version_download(
            project_id, user_id=user_id, node_id=node_id, version_id=version_id
        )
        if row is None:
            raise self._asset_not_found()
        try:
            path = self._storage.open_download(row.object_key)
        except AssetStorageError:
            logger.warning("asset version object unavailable for download")
            raise self._asset_not_found() from None
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

    def upload_new_version(
        self,
        project_id: str,
        *,
        user_id: int,
        node_id: str,
        stream: BinaryIO,
        max_bytes: int,
    ) -> UploadedAssetView:
        """Upload the next immutable version of an existing file node.

        PS-06B-1: the request filename is NOT an input — it never renames the
        node and never selects a target path; MIME is guessed conservatively
        from the existing node name. The 023A publish order is unchanged
        (chunked temp → cap + SHA-256 → atomic rename → one DB switch
        transaction → success), and any non-committed exit unlinks ONLY this
        request's fresh object — existing versions are never touched. The
        old current version stays downloadable through the historical route.
        """
        self._require_writable(project_id, user_id)
        self._ensure_reclaim()
        node = self._repo.get_node(project_id, node_id)
        if node is None or node.kind != "file":
            # Uniform 404 before a single byte is read: folder nodes, hidden
            # root, unknown, foreign, and cross-project ids are identical.
            raise self._asset_not_found()
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
            media_type = mimetypes.guess_type(node.name)[0]
            commit = self._repo.commit_new_version(
                project_id=project_id,
                actor_user_id=user_id,
                node_id=node_id,
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
            self._raise_for_version_outcome(commit.outcome)
        return self._uploaded_view(commit.node, commit.version)

    def restore_version(
        self,
        project_id: str,
        *,
        user_id: int,
        node_id: str,
        version_id: str,
    ) -> UploadedAssetView:
        """Restore one existing version as current: pointer switch only.

        PS-06B-1 order (contract): membership + archived write gates run
        FIRST, so an archived project answers 403 even when the target is
        already current. Every target gets a fail-closed disk check before
        the repo transaction: missing, symlinked, and truncated objects all
        answer a uniform 404. The repo rechecks membership and archive state
        under its write locks, then treats an already-current target as an
        idempotent no-op. External tampering after the disk check is still
        possible; the download probe at stream time is the second net.
        """
        self._require_writable(project_id, user_id)
        self._ensure_reclaim()
        pair = self._repo.get_node_version(
            project_id, user_id=user_id, node_id=node_id, version_id=version_id
        )
        if pair is None:
            raise self._asset_not_found()
        _, version = pair
        try:
            path = self._storage.open_download(version.object_key)
            with open_regular_file_no_follow(path) as source:
                if os.fstat(source.fileno()).st_size != version.size_bytes:
                    raise AssetStorageError("object size mismatch")
        except (AssetStorageError, OSError):
            logger.warning("asset version object unavailable for restore")
            raise self._asset_not_found() from None
        commit = self._repo.restore_version(
            project_id=project_id,
            actor_user_id=user_id,
            node_id=node_id,
            version_id=version_id,
        )
        if commit.outcome != "committed" or commit.node is None or commit.version is None:
            self._raise_for_version_outcome(commit.outcome)
        return self._uploaded_view(commit.node, commit.version)
