"""Private on-disk object storage for project assets (PS-06A / 023A).

Layout under the private root (``~/.octop/project-assets/`` — never served
statically, never inside the workspace or knowledge dirs)::

    <root>/<project ULID>/<version ULID>      committed object (final)
    <root>/_tmp/<version ULID>.part           in-flight upload (temp)

Physical paths are derived **only** from server-generated ULIDs; display
filenames never appear on disk, so a hostile name cannot steer the layout.
Object keys (``<project ULID>/<version ULID>``) come from the DB after a
membership-gated query — they are locators, not credentials, and path
validation here is defense in depth on top of that authorization, never the
boundary itself (the resolved-path containment check is a second belt only).

Publish protocol (fixed order, owned by the service):

1. chunked temp write with cumulative byte cap + incremental SHA-256;
2. close (+fsync);
3. ``os.replace(temp, final)`` — atomic rename inside the root;
4. one DB transaction committing node + current version;
5. HTTP 201 only after the commit.

Crash windows: W1/W2 (mid-stream or pre-rename) leave at most a ``.part``
temp; W3 (rename done, commit failed) leaves an orphan final — the service
unlinks it on any non-committed exit; W4 (committed) is consistent, a retry
answers 409. The restricted restart cleaner reclaims server-generated temps
and unreferenced finals older than a 24h grace period.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 256 * 1024
# Server-generated ids are ULIDs: 26 uppercase Crockford base32 characters.
_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_TMP_DIR_NAME = "_tmp"
_TMP_SUFFIX = ".part"

#: Objects younger than this are never reclaimed: an upload in flight (or a
#: crash that just happened) must not lose its bytes to a concurrent cleaner.
RECLAIM_GRACE_SECONDS = 24 * 60 * 60


class AssetStorageError(Exception):
    """Storage-level failure. Messages never carry disk paths to clients."""


class AssetUploadTooLarge(AssetStorageError):
    """The stream crossed the byte cap; the temp has already been discarded."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__("upload exceeds the size cap")
        self.max_bytes = max_bytes


@dataclass(frozen=True)
class StoredUpload:
    """What the chunked temp write observed: exact bytes and their digest."""

    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ReclaimReport:
    temps_removed: int
    finals_removed: int


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or _ID_RE.match(value) is None:
        raise AssetStorageError("invalid server-generated id")
    return value


def make_object_key(project_id: str, version_id: str) -> str:
    """Server-side object key: ``<project ULID>/<version ULID>``."""
    return f"{_safe_id(project_id)}/{_safe_id(version_id)}"


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync so the rename survives power loss."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # Windows and some filesystems cannot open directories.
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _ensure_private_dir(directory: Path) -> None:
    """Create a dedicated asset directory and tighten existing POSIX modes."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "posix":
        if (
            not stat.S_ISDIR(directory.lstat().st_mode)
            or directory.is_symlink()
            or getattr(directory, "is_junction", lambda: False)()
        ):
            raise AssetStorageError("asset directory is not a plain directory")
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        raise AssetStorageError("asset directory unavailable") from exc
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise AssetStorageError("asset directory is not a directory")
        if os.name == "posix":
            os.fchmod(fd, 0o700)  # type: ignore[attr-defined]  # POSIX-only API
    finally:
        os.close(fd)


def _open_posix_object_no_follow(path: Path, flags: int) -> int:
    """Walk root/project/object with no-follow descriptors, never a re-opened path."""
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    no_follow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not no_follow_flag:
        raise AssetStorageError("no-follow open unavailable")
    directory_flags = os.O_RDONLY | directory_flag | no_follow_flag
    root_fd = os.open(path.parent.parent, directory_flags)
    try:
        project_fd = os.open(path.parent.name, directory_flags, dir_fd=root_fd)
        try:
            return os.open(path.name, flags, dir_fd=project_fd)
        finally:
            os.close(project_fd)
    finally:
        os.close(root_fd)


def open_regular_file_no_follow(path: Path) -> BinaryIO:
    """Open the validated download target without following a swapped link.

    The inode check rejects a regular-file replacement between ``lstat`` and
    ``open``. POSIX opens the root, project directory, and object using
    ``O_NOFOLLOW`` and directory descriptors, closing both final and
    intermediate-component symlink races. Callers must first authorize and
    contain the path under the private asset root. The returned descriptor
    owns the bytes until the response finishes.
    """
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise AssetStorageError("object is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = (
            _open_posix_object_no_follow(path, flags)
            if os.name == "posix"
            else os.open(path, flags)
        )
    except OSError as exc:
        raise AssetStorageError("object unavailable") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise AssetStorageError("object changed during open")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


class ProjectAssetStorage:
    """Chunked-write / atomic-publish / restricted-clean storage engine."""

    def __init__(self, root: Path) -> None:
        self.root = root
        #: Set once the restart cleaner has run for this root (per process).
        self.reclaim_done = False
        self.reclaim_lock = threading.Lock()

    # ------------------------------------------------------------ paths

    def ensure_root(self) -> Path:
        _ensure_private_dir(self.root)
        _ensure_private_dir(self.root / _TMP_DIR_NAME)
        return self.root

    def temp_path(self, version_id: str) -> Path:
        """``<root>/_tmp/<version ULID>.part`` — the filename is never used."""
        return self.root / _TMP_DIR_NAME / f"{_safe_id(version_id)}{_TMP_SUFFIX}"

    def final_path(self, project_id: str, version_id: str) -> Path:
        """``<root>/<project ULID>/<version ULID>`` — ids only, no filenames."""
        return self.root / _safe_id(project_id) / _safe_id(version_id)

    def object_path(self, object_key: str) -> Path:
        parts = object_key.split("/")
        if len(parts) != 2:
            raise AssetStorageError("malformed object key")
        return self.final_path(parts[0], parts[1])

    # ------------------------------------------------------------ publish

    def write_temp(
        self, version_id: str, stream: BinaryIO, *, max_bytes: int
    ) -> tuple[Path, StoredUpload]:
        """Chunk the stream to a temp file with a cumulative cap and SHA-256.

        The cap is enforced on *actual* bytes as they arrive, so a lying
        Content-Length cannot overshoot. On any failure — cap crossed, stream
        crash, disk error — this request's temp is unlinked before the error
        propagates (crash window W1 leaves nothing behind in-process).
        """
        _safe_id(version_id)
        self.ensure_root()
        temp = self.temp_path(version_id)
        digest = hashlib.sha256()
        total = 0
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temp, flags, 0o600)
            created = True
            with os.fdopen(fd, "wb") as out:
                if os.name == "posix":
                    os.fchmod(out.fileno(), 0o600)  # type: ignore[attr-defined]  # POSIX-only API
                while True:
                    chunk = stream.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise AssetUploadTooLarge(max_bytes)
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            if created:
                self.discard(temp)
            raise
        return temp, StoredUpload(size_bytes=total, sha256=digest.hexdigest())

    def publish(self, temp: Path, project_id: str, version_id: str) -> Path:
        """Atomically move the finished temp onto its final private path."""
        final = self.final_path(project_id, version_id)
        _ensure_private_dir(final.parent)
        os.replace(temp, final)
        _fsync_dir(final.parent)
        return final

    def discard(self, path: Path | None) -> None:
        """Unlink one artifact of *this* request; missing files are fine."""
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # The exception often embeds the private object path; keep logs generic.
            logger.warning("failed to discard asset object")

    # ------------------------------------------------------------ download

    def open_download(self, object_key: str) -> Path:
        """Resolve a committed object key to a plain regular file, fail closed.

        Rejects malformed keys, missing objects, symlinks, and anything that
        is not a regular file. The containment check on the resolved path is
        defense in depth only: authorization already happened via the
        membership-gated DB query that produced the key.
        """
        path = self.object_path(object_key)
        try:
            st = os.lstat(path)
        except OSError as exc:
            raise AssetStorageError("object missing") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise AssetStorageError("object is not a regular file")
        try:
            root_resolved = self.root.resolve()
            if not path.resolve().is_relative_to(root_resolved):
                raise AssetStorageError("object escapes the asset root")
        except OSError as exc:
            raise AssetStorageError("object unreadable") from exc
        return path

    # ------------------------------------------------------------ cleaner

    def reclaim_orphans(
        self,
        known_object_keys: set[str],
        *,
        now: float | None = None,
        grace_seconds: float = RECLAIM_GRACE_SECONDS,
    ) -> ReclaimReport:
        """Restricted restart cleaner (runs once per process, first asset op).

        Reclaims only:

        * ``_tmp/<ULID>.part`` temps whose mtime is older than the grace
          period (in-flight uploads and fresh crash residue stay), and
        * ``<project ULID>/<version ULID>`` finals that are older than the
          grace period *and* absent from ``known_object_keys`` (the committed
          set read from the DB inside the same first operation).

        It never deletes recursively, never removes directories, and never
        touches names that do not match the server-generated shapes — foreign
        material under the root survives untouched.
        """
        moment = time.time() if now is None else now
        temps_removed = 0
        finals_removed = 0
        try:
            with os.scandir(self.root) as it:
                entries = list(it)
        except OSError:
            return ReclaimReport(temps_removed=0, finals_removed=0)
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue  # root-level stray files are never ours to judge
                if entry.name == _TMP_DIR_NAME:
                    temps_removed += self._reclaim_temps(entry.path, moment, grace_seconds)
                elif _ID_RE.match(entry.name):
                    finals_removed += self._reclaim_finals(
                        entry.path, entry.name, known_object_keys, moment, grace_seconds
                    )
            except OSError:
                # Static message only: no exc_info, no path/exception text —
                # OSError messages carry the private on-disk path.
                logger.warning("asset reclaim skipped an entry")
        return ReclaimReport(temps_removed=temps_removed, finals_removed=finals_removed)

    def _reclaim_temps(self, tmp_dir: str, moment: float, grace: float) -> int:
        removed = 0
        with os.scandir(tmp_dir) as it:
            for entry in it:
                name = entry.name
                if not name.endswith(_TMP_SUFFIX):
                    continue
                if _ID_RE.match(name[: -len(_TMP_SUFFIX)]) is None:
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if moment - entry.stat(follow_symlinks=False).st_mtime <= grace:
                    continue
                try:
                    os.unlink(entry.path)
                    removed += 1
                    logger.info(
                        "project asset temp reclaimed: version_id=%s result=removed",
                        name[: -len(_TMP_SUFFIX)],
                    )
                except OSError:
                    pass
        return removed

    def _reclaim_finals(
        self,
        project_dir: str,
        project_id: str,
        known_object_keys: set[str],
        moment: float,
        grace: float,
    ) -> int:
        removed = 0
        with os.scandir(project_dir) as it:
            for entry in it:
                name = entry.name
                if _ID_RE.match(name) is None:
                    continue  # foreign material: never touched
                if not entry.is_file(follow_symlinks=False):
                    continue
                if f"{project_id}/{name}" in known_object_keys:
                    continue  # referenced by a committed version
                if moment - entry.stat(follow_symlinks=False).st_mtime <= grace:
                    continue  # inside the grace window (W3 retry may follow)
                try:
                    os.unlink(entry.path)
                    removed += 1
                    logger.info(
                        "project asset object reclaimed: project_id=%s version_id=%s result=removed",
                        project_id,
                        name,
                    )
                except OSError:
                    pass
        return removed
