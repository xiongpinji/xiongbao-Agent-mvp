"""Project public connector SQL, qualification, CAS and atomic key bootstrap."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from psycopg import Error as PostgresError

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts

MAX_REVISION = 9007199254740991
PURPOSE = "octop.project_public_connector.credentials.v1"
KEY_REF = "project_public_connectors_fernet_v1"
KIND = "http_mcp_static_bearer"
SAFE_COLUMNS = (
    "connector_id,kind,display_name,description,state,grant_revision,created_at,updated_at"
)


class PublicConnectorFailure(Exception):
    """Stable internal reason only, never request, credential or driver text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def revision(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise PublicConnectorFailure("invalid_revision")
    return value


@dataclass(frozen=True)
class PublicConnectorView:
    connector_id: str
    kind: str
    display_name: str
    description: str
    state: str
    grant_revision: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class PublicConnectorCollection:
    project_id: str
    public_connectors_revision: int
    items: list[PublicConnectorView]


def _collection(conn: Any, project_id: str, current: int) -> PublicConnectorCollection:
    rows = conn.execute(
        f"SELECT {SAFE_COLUMNS} FROM project_public_connectors WHERE project_id=? ORDER BY connector_id",
        (project_id,),
    ).fetchall()
    return PublicConnectorCollection(
        project_id,
        revision(current),
        [
            PublicConnectorView(**{**dict(row), "grant_revision": revision(row["grant_revision"])})
            for row in rows
        ],
    )


@dataclass
class PublicConnectorWrite:
    """Internal, valid only while the repository context holds its connection."""

    conn: Any
    dialect: str
    project_id: str
    actor_id: int
    project_revision: int
    resource: dict[str, Any] | None

    def collection(self) -> PublicConnectorCollection:
        return _collection(self.conn, self.project_id, self.project_revision)

    def locked_key(self, *, candidate: bytes | None = None) -> bytes:
        lock = " FOR UPDATE" if self.dialect == "postgresql" else ""
        guard = self.conn.execute(
            "SELECT id,purpose,initialized FROM project_public_connector_key_state WHERE id=1"
            + lock
        ).fetchone()
        if guard is None or guard["purpose"] != PURPOSE or guard["initialized"] not in (0, 1):
            raise PublicConnectorFailure("key_state_invalid")
        row = self.conn.execute("SELECT v FROM secrets WHERE k=?" + lock, (KEY_REF,)).fetchone()
        if guard["initialized"] == 1:
            if row is None:
                raise PublicConnectorFailure("key_missing")
            if not isinstance(row["v"], (bytes, bytearray, memoryview)):
                raise PublicConnectorFailure("credential_unavailable")
            return bytes(row["v"])
        if (
            row is not None
            or self.conn.execute(
                "SELECT 1 FROM project_public_connectors WHERE state='active' LIMIT 1"
            ).fetchone()
        ):
            raise PublicConnectorFailure("key_state_invalid")
        if candidate is None:
            raise PublicConnectorFailure("key_missing")
        self.conn.execute(
            "INSERT INTO secrets(k,v,created_at,rotated_at) VALUES(?,?,?,NULL)",
            (KEY_REF, candidate, now_ts()),
        )
        self.conn.execute(
            "UPDATE project_public_connector_key_state SET initialized=1 WHERE id=1 AND initialized=0"
        )
        return candidate

    def apply(
        self,
        action: str,
        connector_id: str,
        *,
        display_name: str = "",
        description: str = "",
        blob: bytes | None = None,
        changed_fields: tuple[str, ...] = (),
    ) -> PublicConnectorCollection:
        old = self.resource
        grant = 1 if old is None else revision(old["grant_revision"])
        if self.project_revision == MAX_REVISION or (
            action in ("credentials_replaced", "revoked") and grant == MAX_REVISION
        ):
            raise PublicConnectorFailure("revision_exhausted")
        timestamp = max(now_ts(), int(old["updated_at"]) if old else 0)
        if action == "created":
            self.conn.execute(
                "INSERT INTO project_public_connector_ids(connector_id,reserved_at) VALUES(?,?)",
                (connector_id, timestamp),
            )
            self.conn.execute(
                "INSERT INTO project_public_connectors(connector_id,project_id,kind,display_name,description,state,grant_revision,credential_blob,key_ref,creator_user_id,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,'active',1,?,?,?,?,?,?)",
                (
                    connector_id,
                    self.project_id,
                    KIND,
                    display_name,
                    description,
                    blob,
                    KEY_REF,
                    self.actor_id,
                    self.actor_id,
                    timestamp,
                    timestamp,
                ),
            )
        else:
            assert old is not None
            params: tuple[object, ...]
            if action == "renamed":
                clause, params = (
                    "display_name=?,description=?,updated_by=?,updated_at=?",
                    (display_name, description, self.actor_id, timestamp),
                )
            elif action == "credentials_replaced":
                clause, params = (
                    "credential_blob=?,grant_revision=?,updated_by=?,updated_at=?",
                    (blob, grant + 1, self.actor_id, timestamp),
                )
            elif action == "revoked":
                clause, params = (
                    "state='revoked',credential_blob=NULL,key_ref=NULL,grant_revision=?,revoked_at=?,revoked_by=?,updated_by=?,updated_at=?",
                    (grant + 1, timestamp, self.actor_id, self.actor_id, timestamp),
                )
            else:
                raise PublicConnectorFailure("invalid_action")
            if (
                self.conn.execute(
                    "UPDATE project_public_connectors SET "
                    + clause
                    + " WHERE project_id=? AND connector_id=? AND grant_revision=?",
                    (*params, self.project_id, connector_id, grant),
                ).rowcount
                != 1
            ):
                raise PublicConnectorFailure("stale_revision")
            if action != "renamed":
                grant += 1
        if (
            self.conn.execute(
                "UPDATE project_spaces SET public_connectors_revision=? WHERE project_id=? AND public_connectors_revision=?",
                (self.project_revision + 1, self.project_id, self.project_revision),
            ).rowcount
            != 1
        ):
            raise PublicConnectorFailure("stale_revision")
        self.project_revision += 1
        payload = json.dumps(
            {
                "action": action,
                "project_revision": self.project_revision,
                "grant_revision": grant,
                "changed_fields": list(changed_fields),
            },
            separators=(",", ":"),
        )
        self.conn.execute(
            "INSERT INTO project_events(project_id,actor_user_id,event_type,object_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (
                self.project_id,
                self.actor_id,
                "public_connector_" + action,
                connector_id,
                payload,
                timestamp,
            ),
        )
        return self.collection()


class ProjectPublicConnectorRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        if self._db.dialect == "postgresql":
            try:
                with self._db.transaction() as conn:
                    yield conn
            except PostgresError:
                raise PublicConnectorFailure("storage_failure") from None
            return
        # Own this one transaction, so every throwable has exactly one rollback
        # attempt (including true BaseException) and never a second pool rollback.
        with self._db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException as primary:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                except BaseException as cleanup:
                    primary.add_note("public_connector_cleanup_UNKNOWN:" + type(cleanup).__name__)
                if isinstance(primary, sqlite3.DatabaseError):
                    failure = PublicConnectorFailure("storage_failure")
                    for note in getattr(primary, "__notes__", ()):
                        failure.add_note(note)
                    raise failure from None
                raise

    def _qualify(
        self,
        conn: Any,
        project_id: str,
        actor_id: int,
        *,
        write: bool,
        connector_id: str | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        share = " FOR SHARE" if self._db.dialect == "postgresql" else ""
        update = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        member = conn.execute(
            "SELECT role FROM project_members WHERE project_id=? AND user_id=?" + share,
            (project_id, actor_id),
        ).fetchone()
        if member is None:
            raise PublicConnectorFailure("not_found")
        project = conn.execute(
            "SELECT archived,public_connectors_revision FROM project_spaces WHERE project_id=?"
            + (update if write else share),
            (project_id,),
        ).fetchone()
        resource = None
        if connector_id is not None:
            row = conn.execute(
                "SELECT * FROM project_public_connectors WHERE project_id=? AND connector_id=?"
                + update,
                (project_id, connector_id),
            ).fetchone()
            resource = dict(row) if row else None
        user = conn.execute("SELECT disabled FROM users WHERE id=?" + share, (actor_id,)).fetchone()
        if (
            member is None
            or project is None
            or user is None
            or user["disabled"] != 0
            or (connector_id is not None and resource is None)
        ):
            raise PublicConnectorFailure("not_found")
        if member["role"] not in ("owner", "admin", "member"):
            raise PublicConnectorFailure("not_found")
        if write and member["role"] not in ("owner", "admin"):
            raise PublicConnectorFailure("forbidden")
        return project, resource

    def list_safe(self, project_id: str, actor_id: int) -> PublicConnectorCollection:
        with self._transaction() as conn:
            project, _ = self._qualify(conn, project_id, actor_id, write=False)
            return _collection(conn, project_id, project["public_connectors_revision"])

    @contextmanager
    def mutation(
        self,
        project_id: str,
        actor_id: int,
        *,
        expected_project_revision: int,
        connector_id: str | None = None,
        expected_grant_revision: int | None = None,
        allow_archived: bool = False,
    ) -> Iterator[PublicConnectorWrite]:
        with self._transaction() as conn:
            project, resource = self._qualify(
                conn, project_id, actor_id, write=True, connector_id=connector_id
            )
            current = revision(project["public_connectors_revision"])
            if revision(expected_project_revision) != current or (
                resource is not None
                and revision(expected_grant_revision) != revision(resource["grant_revision"])
            ):
                raise PublicConnectorFailure("stale_revision")
            if project["archived"] and not allow_archived:
                raise PublicConnectorFailure("project_archived")
            yield PublicConnectorWrite(
                conn, self._db.dialect, project_id, actor_id, current, resource
            )
