"""Project assets — SQL-only repository for PS-06A / 023A + PS-06B-1.

Authorization policy lives in ``octop.infra.projects.assets``; this module
only enforces what must be race-safe and what must be server-side to prevent
leaks or cross-project access:

* every read and write runs inside one transaction that first locks the
  caller's membership row (``FOR SHARE`` on PostgreSQL, lock order
  member-row → asset rows matching ``ProjectRepo.remove_member``; SQLite's
  ``BEGIN IMMEDIATE`` writer already serializes writes), and non-members —
  including instance admins — get the ``None``/``not_member`` sentinel the
  service maps to the uniform 404;
* all queries are scoped by ``(project_id, node_id)`` pairs, and the 023
  composite child FKs make cross-project parenting impossible at the schema
  level too;
* version queries (PS-06B-1) are scoped by the full
  ``(project_id, node_id, version_id)`` triple — a same-project version
  belonging to another file node is indistinguishable from an unknown one;
* folder/file writes go through one transaction: node row plus (for files)
  its first current version commit together or not at all, so a failed
  publish never leaves a bodyless node or an unreferenced current version;
* the current-version switch writes (new version upload, restore) share one
  lock order — member row (PG ``FOR SHARE``) then file node row (PG ``FOR
  UPDATE``) — reset the old ``is_current`` pointer before inserting/marking
  the target, and touch the node timestamp, all in one transaction;
* same-parent name conflicts (``name_key`` = casefolded NFC name) are
  enforced by the DB unique constraint and surfaced as ``name_conflict``;
* node and version ids are server-generated ULIDs — request data never
  chooses an id or an ``object_key``;
* this repo writes no ``project_events`` rows (asset slices are event-silent).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

_ROOT_SELECT = (
    "SELECT node_id FROM project_asset_nodes WHERE project_id = ? AND parent_node_id IS NULL"
)
_NODE_SELECT = (
    "SELECT node_id, project_id, parent_node_id, kind, name, name_key, "
    "created_by, created_at, updated_at "
    "FROM project_asset_nodes WHERE project_id = ? AND node_id = ?"
)
_VERSION_SELECT = (
    "SELECT version_id, project_id, node_id, object_key, size_bytes, sha256, "
    "media_type, uploaded_by, created_at, is_current "
    "FROM project_asset_versions WHERE project_id = ? AND version_id = ?"
)
# PS-06B-1: every version action filters on the full triple so a same-project
# version of ANOTHER file node answers exactly like an unknown id (None).
_NODE_VERSION_SELECT = (
    "SELECT version_id, project_id, node_id, object_key, size_bytes, sha256, "
    "media_type, uploaded_by, created_at, is_current "
    "FROM project_asset_versions "
    "WHERE project_id = ? AND node_id = ? AND version_id = ?"
)
_ROOT_UPSERT = (
    "INSERT INTO project_asset_nodes("
    "node_id, project_id, parent_node_id, kind, name, name_key, "
    "created_by, created_at, updated_at"
    ") VALUES (?, ?, NULL, 'folder', '', '', NULL, ?, ?) "
    "ON CONFLICT (project_id) WHERE parent_node_id IS NULL DO NOTHING"
)


@dataclass(frozen=True)
class AssetNodeRow:
    """One folder/file node. Never carries object keys or disk paths."""

    node_id: str
    project_id: str
    parent_node_id: str | None
    kind: str
    name: str
    name_key: str
    created_by: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AssetNodeRow:
        parent = row["parent_node_id"]
        creator = row["created_by"]
        return cls(
            node_id=str(row["node_id"]),
            project_id=str(row["project_id"]),
            parent_node_id=None if parent is None else str(parent),
            kind=str(row["kind"]),
            name=str(row["name"]),
            name_key=str(row["name_key"]),
            created_by=None if creator is None else int(creator),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class AssetVersionRow:
    """One immutable file version, including its private ``object_key``."""

    version_id: str
    project_id: str
    node_id: str
    object_key: str
    size_bytes: int
    sha256: str
    media_type: str | None
    uploaded_by: int | None
    created_at: int
    is_current: bool

    @classmethod
    def from_row(cls, row: DbRow) -> AssetVersionRow:
        media = row["media_type"]
        uploader = row["uploaded_by"]
        return cls(
            version_id=str(row["version_id"]),
            project_id=str(row["project_id"]),
            node_id=str(row["node_id"]),
            object_key=str(row["object_key"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            media_type=None if media is None else str(media),
            uploaded_by=None if uploader is None else int(uploader),
            created_at=int(row["created_at"]),
            is_current=bool(int(row["is_current"])),
        )


@dataclass(frozen=True)
class AssetListRow:
    """Listing row: node fields plus the current version's display metadata."""

    node_id: str
    parent_node_id: str | None
    kind: str
    name: str
    size_bytes: int | None
    media_type: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AssetListRow:
        parent = row["parent_node_id"]
        size = row["size_bytes"]
        media = row["media_type"]
        return cls(
            node_id=str(row["node_id"]),
            parent_node_id=None if parent is None else str(parent),
            kind=str(row["kind"]),
            name=str(row["name"]),
            size_bytes=None if size is None else int(size),
            media_type=None if media is None else str(media),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class AssetDownloadRow:
    """Download row: the display name plus the current version's object ref."""

    node_id: str
    name: str
    version_id: str
    object_key: str
    size_bytes: int
    sha256: str
    media_type: str | None

    @classmethod
    def from_row(cls, row: DbRow) -> AssetDownloadRow:
        media = row["media_type"]
        return cls(
            node_id=str(row["node_id"]),
            name=str(row["name"]),
            version_id=str(row["version_id"]),
            object_key=str(row["object_key"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            media_type=None if media is None else str(media),
        )


@dataclass(frozen=True)
class AssetVersionListRow:
    """Version-listing row (PS-06B-1): safe metadata only, never object keys."""

    version_id: str
    size_bytes: int
    sha256: str
    media_type: str | None
    uploaded_by: int | None
    created_at: int
    is_current: bool

    @classmethod
    def from_row(cls, row: DbRow) -> AssetVersionListRow:
        media = row["media_type"]
        uploader = row["uploaded_by"]
        return cls(
            version_id=str(row["version_id"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            media_type=None if media is None else str(media),
            uploaded_by=None if uploader is None else int(uploader),
            created_at=int(row["created_at"]),
            is_current=bool(int(row["is_current"])),
        )


@dataclass(frozen=True)
class FolderCreation:
    """Outcome of :meth:`ProjectAssetRepo.create_folder`.

    ``outcome`` is one of: created, not_member, archived, parent_missing,
    parent_not_folder, name_conflict. ``row`` carries the fresh node on
    success only.
    """

    outcome: str
    row: AssetNodeRow | None = None


@dataclass(frozen=True)
class FileCommit:
    """Outcome of :meth:`ProjectAssetRepo.commit_file`.

    ``outcome`` is one of: committed, not_member, archived, parent_missing,
    parent_not_folder, name_conflict. ``node``/``version`` carry the fresh
    rows on success only.
    """

    outcome: str
    node: AssetNodeRow | None = None
    version: AssetVersionRow | None = None


@dataclass(frozen=True)
class VersionCommit:
    """Outcome of the current-version switch writes (PS-06B-1).

    ``outcome`` is one of: committed, not_member, archived, node_missing,
    version_missing. ``node``/``version`` carry the post-switch rows on
    success only.
    """

    outcome: str
    node: AssetNodeRow | None = None
    version: AssetVersionRow | None = None


class _AssetConflict(Exception):
    """Internal signal to roll the write transaction back cleanly."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


def _is_unique_violation(exc: BaseException) -> bool:
    """True for dialect-specific unique-constraint failures.

    Walks the MRO by name so sqlite3.IntegrityError ("UNIQUE constraint
    failed: ...") and psycopg's UniqueViolation both match without importing
    psycopg here. Foreign-key failures — which must never be reported as name
    conflicts — fall through to a re-raise.
    """
    message = str(exc).upper()
    for klass in type(exc).__mro__:
        if klass.__name__ == "UniqueViolation":
            return True
        if klass.__name__ == "IntegrityError":
            return "UNIQUE" in message
    return False


def _insert_asset_node(
    conn: Any,
    *,
    node_id: str,
    project_id: str,
    parent_node_id: str | None,
    kind: str,
    name: str,
    name_key: str,
    created_by: int | None,
    ts: int,
) -> None:
    """Insert one node row.

    Module-level so atomicity tests can inject a failure at exactly this
    step; callers invoke it through the module namespace inside the write
    transaction, so an injected failure rolls the whole commit back.
    """
    conn.execute(
        "INSERT INTO project_asset_nodes("
        "node_id, project_id, parent_node_id, kind, name, name_key, "
        "created_by, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (node_id, project_id, parent_node_id, kind, name, name_key, created_by, ts, ts),
    )


def _insert_asset_version(
    conn: Any,
    *,
    version_id: str,
    project_id: str,
    node_id: str,
    object_key: str,
    size_bytes: int,
    sha256: str,
    media_type: str | None,
    uploaded_by: int | None,
    ts: int,
) -> None:
    """Insert a version row as the node's single current version.

    Module-level for the same fault-injection reason as
    :func:`_insert_asset_node`: an injected failure here must roll the file
    node back with it (crash window W3 leaves only the orphan object, which
    the service unlinks and the restart cleaner would reclaim).
    """
    conn.execute(
        "INSERT INTO project_asset_versions("
        "version_id, project_id, node_id, object_key, size_bytes, sha256, "
        "media_type, uploaded_by, created_at, is_current"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            version_id,
            project_id,
            node_id,
            object_key,
            size_bytes,
            sha256,
            media_type,
            uploaded_by,
            ts,
        ),
    )


def _clear_current_version(conn: Any, *, project_id: str, node_id: str) -> None:
    """Reset the node's current pointer — step 1 of every switch (PS-06B-1).

    Module-level like the inserts so tests can observe the fixed statement
    order; runs inside the caller's locked write transaction.
    """
    conn.execute(
        "UPDATE project_asset_versions SET is_current = 0 "
        "WHERE project_id = ? AND node_id = ? AND is_current = 1",
        (project_id, node_id),
    )


def _mark_version_current(conn: Any, *, project_id: str, node_id: str, version_id: str) -> None:
    """Point the node at one of its existing versions — restore step 2.

    Triple-scoped: a version id belonging to another node (or project)
    matches nothing and the switch silently no-ops instead of escaping its
    containment; callers pre-verified the row inside the same transaction.
    """
    conn.execute(
        "UPDATE project_asset_versions SET is_current = 1 "
        "WHERE project_id = ? AND node_id = ? AND version_id = ?",
        (project_id, node_id, version_id),
    )


def _touch_node_updated(conn: Any, *, project_id: str, node_id: str, ts: int) -> None:
    """Bump the node's ``updated_at`` after a current-version switch."""
    conn.execute(
        "UPDATE project_asset_nodes SET updated_at = ? WHERE project_id = ? AND node_id = ?",
        (ts, project_id, node_id),
    )


class ProjectAssetRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------ helpers

    def _member_share_lock(self) -> str:
        # Contract: PostgreSQL validates membership with SELECT ... FOR SHARE
        # so a concurrent remove_member DELETE cannot interleave between the
        # check and the asset write. SQLite writers hold the database lock.
        return " FOR SHARE" if self._db.dialect == "postgresql" else ""

    def _member_role_locked(self, conn: Any, project_id: str, user_id: int) -> str | None:
        """Read the caller's role, locking the membership row first."""
        row = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
            + self._member_share_lock(),
            (project_id, user_id),
        ).fetchone()
        return None if row is None else str(row["role"])

    def _node_update_lock(self) -> str:
        # Contract (PS-06B-1): PostgreSQL locks the target file node row
        # FOR UPDATE so concurrent new-version uploads and restores of the
        # SAME file serialize; the lock order is always member row (FOR
        # SHARE) → node row (FOR UPDATE), matching remove_member and the
        # 023A write paths. SQLite's BEGIN IMMEDIATE writer serializes the
        # whole transaction instead.
        return " FOR UPDATE" if self._db.dialect == "postgresql" else ""

    def _project_archived(self, conn: Any, project_id: str) -> bool | None:
        """``None`` for unknown projects — they answer like non-memberships."""
        row = conn.execute(
            "SELECT archived FROM project_spaces WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        return bool(row["archived"])

    def _project_exists(self, conn: Any, project_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM project_spaces WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row is not None

    def _allocate_node_id(self, project_id: str) -> str:
        for _ in range(16):
            node_id = new_ulid()
            with self._db.connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM project_asset_nodes WHERE project_id = ? AND node_id = ?",
                    (project_id, node_id),
                ).fetchone()
            if exists is None:
                return node_id
        raise RuntimeError("failed to allocate unique asset node id")

    def _allocate_node_id_locked(self, conn: Any, project_id: str) -> str:
        for _ in range(16):
            node_id = new_ulid()
            exists = conn.execute(
                "SELECT 1 FROM project_asset_nodes WHERE project_id = ? AND node_id = ?",
                (project_id, node_id),
            ).fetchone()
            if exists is None:
                return node_id
        raise RuntimeError("failed to allocate unique asset node id")

    def new_version_id(self) -> str:
        """Server-generated unguessable id for the next uploaded version."""
        for _ in range(16):
            version_id = new_ulid()
            with self._db.connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM project_asset_versions WHERE version_id = ?",
                    (version_id,),
                ).fetchone()
            if exists is None:
                return version_id
        raise RuntimeError("failed to allocate unique asset version id")

    def _ensure_root_locked(self, conn: Any, project_id: str) -> str | None:
        """Idempotent hidden-root init inside an open transaction.

        Covers old projects created before 023: the first asset operation
        inserts the root folder (empty display name, NULL parent). The
        partial unique index plus ``ON CONFLICT DO NOTHING`` make concurrent
        inits converge on one row.
        """
        row = conn.execute(_ROOT_SELECT, (project_id,)).fetchone()
        if row is not None:
            return str(row["node_id"])
        ts = now_ts()
        node_id = self._allocate_node_id_locked(conn, project_id)
        conn.execute(_ROOT_UPSERT, (node_id, project_id, ts, ts))
        row = conn.execute(_ROOT_SELECT, (project_id,)).fetchone()
        return None if row is None else str(row["node_id"])

    def _resolve_parent_locked(self, conn: Any, project_id: str, parent_node_id: str | None) -> str:
        """Resolve the write parent or raise the matching conflict outcome.

        ``None`` resolves (and lazily creates) the hidden root. Any explicit
        parent must exist *inside this project* — the (project_id, node_id)
        scope makes foreign ids indistinguishable from unknown ones — and
        must be a folder.
        """
        if parent_node_id is None:
            root = self._ensure_root_locked(conn, project_id)
            if root is None:
                raise _AssetConflict("parent_missing")
            return root
        row = conn.execute(
            "SELECT kind FROM project_asset_nodes WHERE project_id = ? AND node_id = ?",
            (project_id, parent_node_id),
        ).fetchone()
        if row is None:
            raise _AssetConflict("parent_missing")
        if str(row["kind"]) != "folder":
            raise _AssetConflict("parent_not_folder")
        return parent_node_id

    def _node_row(self, conn: Any, project_id: str, node_id: str) -> AssetNodeRow | None:
        row = conn.execute(_NODE_SELECT, (project_id, node_id)).fetchone()
        return AssetNodeRow.from_row(row) if row is not None else None

    def _version_row(self, conn: Any, project_id: str, version_id: str) -> AssetVersionRow | None:
        row = conn.execute(_VERSION_SELECT, (project_id, version_id)).fetchone()
        return AssetVersionRow.from_row(row) if row is not None else None

    def _node_locked(self, conn: Any, project_id: str, node_id: str) -> AssetNodeRow | None:
        """Read the node row, taking the writer lock on PostgreSQL."""
        row = conn.execute(
            _NODE_SELECT + self._node_update_lock(), (project_id, node_id)
        ).fetchone()
        return AssetNodeRow.from_row(row) if row is not None else None

    def _node_version_row(
        self, conn: Any, project_id: str, node_id: str, version_id: str
    ) -> AssetVersionRow | None:
        """Triple-scoped version read: a same-project version belonging to
        another node is indistinguishable from an unknown one (uniform 404).
        """
        row = conn.execute(_NODE_VERSION_SELECT, (project_id, node_id, version_id)).fetchone()
        return AssetVersionRow.from_row(row) if row is not None else None

    def _lock_switch_target(
        self, conn: Any, *, project_id: str, actor_user_id: int, node_id: str
    ) -> AssetNodeRow:
        """Shared lock/check prefix for the current-version switch writes.

        Fixed order (contract): member row first (PG ``FOR SHARE``), then the
        file node row (PG ``FOR UPDATE``), then the archived/unknown-project
        check, then the file-kind containment check. Every rejection raises
        :class:`_AssetConflict` so the caller's transaction rolls back with
        nothing written; upload-new-version and restore share this exact
        order so they serialize against each other and against member
        removal.
        """
        if self._member_role_locked(conn, project_id, actor_user_id) is None:
            raise _AssetConflict("not_member")
        node = self._node_locked(conn, project_id, node_id)
        archived = self._project_archived(conn, project_id)
        if archived is None:
            raise _AssetConflict("not_member")
        if archived:
            raise _AssetConflict("archived")
        if node is None or node.kind != "file":
            raise _AssetConflict("node_missing")
        return node

    # ------------------------------------------------------------ root + lookups

    def ensure_root(self, project_id: str) -> str | None:
        """Return the project's hidden root folder id, creating it if needed.

        ``None`` for unknown projects. Safe to call repeatedly; concurrent
        callers converge on the same row.
        """
        with self._db.transaction() as conn:
            if not self._project_exists(conn, project_id):
                return None
            return self._ensure_root_locked(conn, project_id)

    def get_root(self, project_id: str) -> str | None:
        """Read-only root lookup; never writes (reads must stay side-free)."""
        with self._db.connect() as conn:
            row = conn.execute(_ROOT_SELECT, (project_id,)).fetchone()
        return None if row is None else str(row["node_id"])

    def get_node(self, project_id: str, node_id: str) -> AssetNodeRow | None:
        """Project-scoped node lookup; foreign ids answer ``None``."""
        with self._db.connect() as conn:
            row = conn.execute(_NODE_SELECT, (project_id, node_id)).fetchone()
        return AssetNodeRow.from_row(row) if row is not None else None

    def all_object_keys(self) -> set[str]:
        """Every referenced object key — the restart cleaner's keep-set."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT object_key FROM project_asset_versions").fetchall()
        return {str(row["object_key"]) for row in rows}

    # ------------------------------------------------------------ mutations

    def create_folder(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        parent_node_id: str | None,
        name: str,
        name_key: str,
    ) -> FolderCreation:
        """Insert one folder node in a single transaction.

        Race-safe re-check (the service has already authorized): the actor
        must still be a member and the project must still be unarchived — a
        concurrent removal or archive makes this return ``not_member`` /
        ``archived`` with nothing written. Same-parent name conflicts are
        raised by the DB unique constraint and returned as ``name_conflict``.
        """
        ts = now_ts()
        node_id = self._allocate_node_id(project_id)
        try:
            with self._db.transaction() as conn:
                if self._member_role_locked(conn, project_id, actor_user_id) is None:
                    raise _AssetConflict("not_member")
                archived = self._project_archived(conn, project_id)
                if archived is None:
                    raise _AssetConflict("not_member")
                if archived:
                    raise _AssetConflict("archived")
                parent = self._resolve_parent_locked(conn, project_id, parent_node_id)
                try:
                    _insert_asset_node(
                        conn,
                        node_id=node_id,
                        project_id=project_id,
                        parent_node_id=parent,
                        kind="folder",
                        name=name,
                        name_key=name_key,
                        created_by=actor_user_id,
                        ts=ts,
                    )
                except Exception as exc:
                    if _is_unique_violation(exc):
                        raise _AssetConflict("name_conflict") from exc
                    raise
                row = self._node_row(conn, project_id, node_id)
                if row is None:
                    raise RuntimeError("folder row vanished inside the write transaction")
                return FolderCreation(outcome="created", row=row)
        except _AssetConflict as conflict:
            return FolderCreation(outcome=conflict.outcome)

    def commit_file(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        parent_node_id: str | None,
        name: str,
        name_key: str,
        media_type: str | None,
        version_id: str,
        object_key: str,
        size_bytes: int,
        sha256: str,
    ) -> FileCommit:
        """Commit one finished upload: node row + first current version.

        This is the *only* DB step of the publish protocol and runs after the
        bytes are already on their final private path (``os.replace`` done):
        both rows commit together or not at all, so a failure here leaves no
        visible half-asset — just an orphan object the caller unlinks (W3).
        The version id and object key are server-generated upstream; nothing
        request-derived touches the physical layout.
        """
        ts = now_ts()
        node_id = self._allocate_node_id(project_id)
        try:
            with self._db.transaction() as conn:
                if self._member_role_locked(conn, project_id, actor_user_id) is None:
                    raise _AssetConflict("not_member")
                archived = self._project_archived(conn, project_id)
                if archived is None:
                    raise _AssetConflict("not_member")
                if archived:
                    raise _AssetConflict("archived")
                parent = self._resolve_parent_locked(conn, project_id, parent_node_id)
                try:
                    _insert_asset_node(
                        conn,
                        node_id=node_id,
                        project_id=project_id,
                        parent_node_id=parent,
                        kind="file",
                        name=name,
                        name_key=name_key,
                        created_by=actor_user_id,
                        ts=ts,
                    )
                except Exception as exc:
                    if _is_unique_violation(exc):
                        raise _AssetConflict("name_conflict") from exc
                    raise
                _insert_asset_version(
                    conn,
                    version_id=version_id,
                    project_id=project_id,
                    node_id=node_id,
                    object_key=object_key,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    media_type=media_type,
                    uploaded_by=actor_user_id,
                    ts=ts,
                )
                node = self._node_row(conn, project_id, node_id)
                version = self._version_row(conn, project_id, version_id)
                if node is None or version is None:
                    raise RuntimeError("asset rows vanished inside the write transaction")
                return FileCommit(outcome="committed", node=node, version=version)
        except _AssetConflict as conflict:
            return FileCommit(outcome=conflict.outcome)

    def commit_new_version(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        node_id: str,
        media_type: str | None,
        version_id: str,
        object_key: str,
        size_bytes: int,
        sha256: str,
    ) -> VersionCommit:
        """Insert the next immutable version and switch the current pointer.

        PS-06B-1: runs after the new bytes are already published on their
        final private path (the 023A publish order is unchanged). One
        transaction: member row lock → node row lock → archived/file checks →
        reset the old ``is_current`` → insert the new current version → touch
        the node. Any rejection or failure rolls everything back, so the old
        current version survives untouched and the caller only has to unlink
        this request's fresh orphan object (crash window W3). The node display
        name never changes here — request filenames are not an input.
        """
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                self._lock_switch_target(
                    conn, project_id=project_id, actor_user_id=actor_user_id, node_id=node_id
                )
                _clear_current_version(conn, project_id=project_id, node_id=node_id)
                _insert_asset_version(
                    conn,
                    version_id=version_id,
                    project_id=project_id,
                    node_id=node_id,
                    object_key=object_key,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    media_type=media_type,
                    uploaded_by=actor_user_id,
                    ts=ts,
                )
                _touch_node_updated(conn, project_id=project_id, node_id=node_id, ts=ts)
                node = self._node_row(conn, project_id, node_id)
                version = self._version_row(conn, project_id, version_id)
                if node is None or version is None:
                    raise RuntimeError("version rows vanished inside the write transaction")
                if version.node_id != node_id:
                    # Belt-and-braces: _VERSION_SELECT is (project, version)
                    # scoped, so the contract demands this containment
                    # assertion whenever it is reused for version actions.
                    raise RuntimeError("committed version escaped its node containment")
                return VersionCommit(outcome="committed", node=node, version=version)
        except _AssetConflict as conflict:
            return VersionCommit(outcome=conflict.outcome)

    def restore_version(
        self, *, project_id: str, actor_user_id: int, node_id: str, version_id: str
    ) -> VersionCommit:
        """Switch the current pointer to one of the node's existing versions.

        No new row and no copied bytes (PS-06B-1): member row lock → node row
        lock → archived/file checks → triple-scoped containment read of the
        target → reset the old pointer → mark the target current → touch the
        node, all in one transaction. Restoring the already-current version
        still passes the locked gates, then returns without any SQL writes.
        The disk safety check on the target object happens in the service
        BEFORE this call; a failed transaction never moves the pointer.
        """
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                self._lock_switch_target(
                    conn, project_id=project_id, actor_user_id=actor_user_id, node_id=node_id
                )
                target = self._node_version_row(conn, project_id, node_id, version_id)
                if target is None:
                    raise _AssetConflict("version_missing")
                if target.is_current:
                    # Idempotence still goes through the locked member,
                    # archived and node checks above. A concurrent revoke or
                    # archive must never be bypassed by the service pre-read.
                    node = self._node_row(conn, project_id, node_id)
                    if node is None:
                        raise RuntimeError("file node vanished inside the write transaction")
                    return VersionCommit(outcome="committed", node=node, version=target)
                _clear_current_version(conn, project_id=project_id, node_id=node_id)
                _mark_version_current(
                    conn, project_id=project_id, node_id=node_id, version_id=version_id
                )
                _touch_node_updated(conn, project_id=project_id, node_id=node_id, ts=ts)
                node = self._node_row(conn, project_id, node_id)
                version = self._node_version_row(conn, project_id, node_id, version_id)
                if node is None or version is None:
                    raise RuntimeError("version rows vanished inside the write transaction")
                return VersionCommit(outcome="committed", node=node, version=version)
        except _AssetConflict as conflict:
            return VersionCommit(outcome=conflict.outcome)

    # ------------------------------------------------------------ read paths

    def list_nodes(
        self,
        project_id: str,
        *,
        user_id: int,
        parent_node_id: str,
        kind: str | None,
        name_like: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AssetListRow], int] | None:
        """One folder's page of children; ``None`` for non-members.

        Filters (same project, current parent, kind, pre-folded pre-escaped
        LIKE pattern) run in SQL before the full deterministic
        ``(kind, name_key, node_id)`` ordering and LIMIT/OFFSET, so paging
        never duplicates or skips. ``total`` counts the same filtered set.
        The hidden root itself never appears (its parent is NULL).
        """
        where = "WHERE n.project_id = ? AND n.parent_node_id = ?"
        params: list[Any] = [project_id, parent_node_id]
        if kind is not None:
            where += " AND n.kind = ?"
            params.append(kind)
        if name_like is not None:
            where += " AND n.name_key LIKE ? ESCAPE '\\'"
            params.append(name_like)
        # SQLite's BEGIN IMMEDIATE deliberately serializes this short,
        # bounded read with cross-process member removal; PostgreSQL holds
        # FOR SHARE on the member row instead.
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                # The service maps this sentinel to the uniform 404.
                return None
            total_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM project_asset_nodes n {where}",
                tuple(params),
            ).fetchone()
            rows = conn.execute(
                "SELECT n.node_id, n.parent_node_id, n.kind, n.name, "
                "v.size_bytes AS size_bytes, v.media_type AS media_type, "
                "n.created_at, n.updated_at "
                "FROM project_asset_nodes n "
                "LEFT JOIN project_asset_versions v "
                "ON v.project_id = n.project_id AND v.node_id = n.node_id "
                "AND v.is_current = 1 "
                f"{where} "
                "ORDER BY n.kind, n.name_key, n.node_id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        total = int(total_row["n"]) if total_row is not None else 0
        return map_rows(rows, AssetListRow), total

    def usage(self, project_id: str, *, user_id: int) -> tuple[int, int] | None:
        """Real usage: count + bytes of committed current file versions only.

        Folders and non-current (historical) versions never contribute;
        ``None`` for non-members.
        """
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                return None
            row = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(v.size_bytes), 0) AS total "
                "FROM project_asset_nodes n "
                "JOIN project_asset_versions v "
                "ON v.project_id = n.project_id AND v.node_id = n.node_id "
                "AND v.is_current = 1 "
                "WHERE n.project_id = ? AND n.kind = 'file'",
                (project_id,),
            ).fetchone()
        if row is None:
            return 0, 0
        return int(row["n"]), int(row["total"])

    def get_download(
        self, project_id: str, *, user_id: int, node_id: str
    ) -> AssetDownloadRow | None:
        """Current-version download reference for one *file* node.

        ``None`` for non-members, unknown/foreign node ids, folder nodes, and
        the hidden root — all indistinguishable, all mapped to the uniform
        404. The returned object_key is a private storage locator, never an
        authorization credential and never exposed to clients.
        """
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                return None
            row = conn.execute(
                "SELECT n.node_id, n.name, v.version_id, v.object_key, "
                "v.size_bytes, v.sha256, v.media_type "
                "FROM project_asset_nodes n "
                "JOIN project_asset_versions v "
                "ON v.project_id = n.project_id AND v.node_id = n.node_id "
                "AND v.is_current = 1 "
                "WHERE n.project_id = ? AND n.node_id = ? AND n.kind = 'file'",
                (project_id, node_id),
            ).fetchone()
        return AssetDownloadRow.from_row(row) if row is not None else None

    def list_versions(
        self,
        project_id: str,
        *,
        user_id: int,
        node_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[AssetVersionListRow], int] | None:
        """One file node's version page (PS-06B-1); ``None`` for every 404
        sentinel: non-members, unknown projects, folder/root nodes, and
        unknown or cross-project node ids.

        The membership lock, the file-kind check, the count, and the page all
        run inside the SAME transaction, so a concurrent member removal can
        never authorize a stale snapshot. The full deterministic
        ``(created_at DESC, version_id DESC)`` order makes same-second
        uploads page without duplicates or skips. Listing rows never carry
        object keys.
        """
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                return None
            node = self._node_row(conn, project_id, node_id)
            if node is None or node.kind != "file":
                return None
            total_row = conn.execute(
                "SELECT COUNT(*) AS n FROM project_asset_versions "
                "WHERE project_id = ? AND node_id = ?",
                (project_id, node_id),
            ).fetchone()
            rows = conn.execute(
                "SELECT version_id, size_bytes, sha256, media_type, uploaded_by, "
                "created_at, is_current "
                "FROM project_asset_versions "
                "WHERE project_id = ? AND node_id = ? "
                "ORDER BY created_at DESC, version_id DESC LIMIT ? OFFSET ?",
                (project_id, node_id, limit, offset),
            ).fetchall()
        total = int(total_row["n"]) if total_row is not None else 0
        return map_rows(rows, AssetVersionListRow), total

    def get_version_download(
        self, project_id: str, *, user_id: int, node_id: str, version_id: str
    ) -> AssetDownloadRow | None:
        """Historical-version download reference (PS-06B-1).

        The JOIN enforces the full ``(project_id, node_id, version_id)``
        triple: a same-project version belonging to another file node answers
        ``None`` exactly like unknown ids, folder nodes, and non-members —
        all mapped to the uniform 404. ``name`` is the CURRENT node name (the
        contract's download filename); ``object_key`` is the private storage
        locator and never leaves the server.
        """
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                return None
            row = conn.execute(
                "SELECT n.node_id, n.name, v.version_id, v.object_key, "
                "v.size_bytes, v.sha256, v.media_type "
                "FROM project_asset_nodes n "
                "JOIN project_asset_versions v "
                "ON v.project_id = n.project_id AND v.node_id = n.node_id "
                "WHERE n.project_id = ? AND n.node_id = ? AND n.kind = 'file' "
                "AND v.version_id = ?",
                (project_id, node_id, version_id),
            ).fetchone()
        return AssetDownloadRow.from_row(row) if row is not None else None

    def get_node_version(
        self, project_id: str, *, user_id: int, node_id: str, version_id: str
    ) -> tuple[AssetNodeRow, AssetVersionRow] | None:
        """Restore pre-read: the authorized ``(node, version)`` pair or None.

        One membership-locked transaction answers every uniform-404 sentinel
        (non-member, unknown project/node/version, folder node, same-project
        other-node version). The version row carries the private object_key
        for the service's fail-closed disk safety check only — the current
        pointer is NOT switched here; that is :meth:`restore_version`'s job
        under the node writer lock.
        """
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                return None
            node = self._node_row(conn, project_id, node_id)
            if node is None or node.kind != "file":
                return None
            version = self._node_version_row(conn, project_id, node_id, version_id)
            if version is None:
                return None
            return node, version
