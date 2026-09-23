"""Project spaces — projects, memberships, invites, join requests, and events.

Invite tokens are stored only as SHA-256 hashes; event payloads carry ids and
role/action labels, never token material or project content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    bool_int,
    insert_returning_id,
    map_rows,
    now_ts,
    partial_updates,
)
from octop.infra.db.repos.project_todos import EVENT_TODO_UPDATED
from octop.infra.utils.ulid import new_ulid

EVENT_CREATED = "project.created"
EVENT_UPDATED = "project.updated"
EVENT_INVITE_CREATED = "project.invite_created"
EVENT_INVITE_REVOKED = "project.invite_revoked"
EVENT_JOIN_REQUESTED = "project.join_requested"
EVENT_JOIN_APPROVED = "project.join_approved"
EVENT_JOIN_REJECTED = "project.join_rejected"
EVENT_MEMBER_JOINED = "project.member_joined"
EVENT_MEMBER_ROLE_CHANGED = "project.member_role_changed"
EVENT_MEMBER_REMOVED = "project.member_removed"


@dataclass(frozen=True)
class ProjectRow:
    project_id: str
    pk: int
    creator_user_id: int
    name: str
    description: str
    instructions: str
    archived: bool
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectRow:
        return cls(
            project_id=str(r["project_id"]),
            pk=int(r["id"]),
            creator_user_id=int(r["creator_user_id"]),
            name=str(r["name"]),
            description=str(r["description"]),
            instructions=str(r["instructions"]),
            archived=bool(int(r["archived"])),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


@dataclass(frozen=True)
class ProjectMemberRow:
    project_id: str
    user_id: int
    role: str
    joined_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectMemberRow:
        return cls(
            project_id=str(r["project_id"]),
            user_id=int(r["user_id"]),
            role=str(r["role"]),
            joined_at=int(r["joined_at"]),
        )


@dataclass(frozen=True)
class ProjectMemberUserRow:
    user_id: int
    username: str
    role: str
    joined_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectMemberUserRow:
        return cls(
            user_id=int(r["user_id"]),
            username=str(r["username"]),
            role=str(r["role"]),
            joined_at=int(r["joined_at"]),
        )


@dataclass(frozen=True)
class ProjectListItemRow:
    project_id: str
    name: str
    description: str
    my_role: str
    member_count: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectListItemRow:
        return cls(
            project_id=str(r["project_id"]),
            name=str(r["name"]),
            description=str(r["description"]),
            my_role=str(r["my_role"]),
            member_count=int(r["member_count"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


@dataclass(frozen=True)
class ProjectInviteRow:
    invite_id: str
    project_id: str
    token_hash: str
    created_by: int
    role: str
    requires_approval: bool
    created_at: int
    expires_at: int
    revoked_at: int | None
    consumed_at: int | None
    consumed_by_user_id: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectInviteRow:
        return cls(
            invite_id=str(r["invite_id"]),
            project_id=str(r["project_id"]),
            token_hash=str(r["token_hash"]),
            created_by=int(r["created_by"]),
            role=str(r["role"]),
            requires_approval=bool(int(r["requires_approval"])),
            created_at=int(r["created_at"]),
            expires_at=int(r["expires_at"]),
            revoked_at=_optional_int(r["revoked_at"]),
            consumed_at=_optional_int(r["consumed_at"]),
            consumed_by_user_id=_optional_int(r["consumed_by_user_id"]),
        )

    def status(self, now: int | None = None) -> str:
        """revoked > used > expired > pending (mirrors the user-invite order)."""
        ts = now_ts() if now is None else now
        if self.revoked_at is not None:
            return "revoked"
        if self.consumed_at is not None:
            return "used"
        if self.expires_at <= ts:
            return "expired"
        return "pending"


@dataclass(frozen=True)
class ProjectJoinRequestRow:
    request_id: str
    project_id: str
    invite_id: str | None
    user_id: int
    username: str
    status: str
    requested_at: int
    resolved_at: int | None
    resolved_by: int | None
    invite_role: str

    @classmethod
    def from_row(cls, r: DbRow) -> ProjectJoinRequestRow:
        return cls(
            request_id=str(r["request_id"]),
            project_id=str(r["project_id"]),
            invite_id=(None if r["invite_id"] is None else str(r["invite_id"])),
            user_id=int(r["user_id"]),
            username=str(r["username"]),
            status=str(r["status"]),
            requested_at=int(r["requested_at"]),
            resolved_at=_optional_int(r["resolved_at"]),
            resolved_by=_optional_int(r["resolved_by"]),
            invite_role=str(r["invite_role"]),
        )


@dataclass(frozen=True)
class InviteRedemption:
    """Outcome of ``redeem_invite``.

    ``outcome`` is one of: joined, requested, invalid, revoked, used, expired,
    already_member, pending_exists.
    """

    outcome: str
    project_id: str = ""
    role: str = ""
    invite_id: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class JoinRequestResolution:
    """Outcome of ``resolve_join_request``.

    ``outcome`` is one of: approved, rejected, missing, resolved,
    already_member.
    """

    outcome: str
    request: ProjectJoinRequestRow | None = None


@dataclass(frozen=True)
class MemberMutation:
    """Outcome of ``set_member_role``/``remove_member``.

    ``outcome`` is one of: changed, noop, removed, missing, forbidden_role.
    """

    outcome: str
    role: str = ""


class _RedeemConflict(Exception):
    """Raised inside the redemption transaction to force a clean rollback."""

    def __init__(
        self, outcome: str, *, project_id: str = "", invite_id: str = "", request_id: str = ""
    ) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.project_id = project_id
        self.invite_id = invite_id
        self.request_id = request_id


class _RequestConflict(Exception):
    """Raised inside the resolution transaction to force a clean rollback."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


_JOIN_REQUEST_SELECT = (
    "SELECT r.request_id, r.project_id, r.invite_id, r.user_id, u.username, r.status, "
    "r.requested_at, r.resolved_at, r.resolved_by, COALESCE(pi.role, 'member') AS invite_role "
    "FROM project_join_requests r "
    "JOIN users u ON u.id = r.user_id "
    "LEFT JOIN project_invites pi ON pi.invite_id = r.invite_id "
)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _append_event(
    conn: Any,
    project_id: str,
    actor_user_id: int,
    event_type: str,
    object_id: str,
    payload_json: str,
    ts: int,
) -> None:
    conn.execute(
        "INSERT INTO project_events("
        "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, actor_user_id, event_type, object_id, payload_json, ts),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ProjectRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def _allocate_project_id(self) -> str:
        for _ in range(16):
            project_id = new_ulid()
            if self.get_project(project_id) is None:
                return project_id
        raise RuntimeError("failed to allocate unique project id")

    def create_with_owner(
        self,
        *,
        creator_user_id: int,
        name: str,
        description: str = "",
        instructions: str = "",
    ) -> ProjectRow:
        """Insert project + owner membership + creation event in one transaction."""
        project_id = self._allocate_project_id()
        ts = now_ts()
        with self._db.transaction() as conn:
            pk = insert_returning_id(
                conn,
                "INSERT INTO project_spaces("
                "project_id, creator_user_id, name, description, instructions, archived, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (project_id, creator_user_id, name, description, instructions, ts, ts),
            )
            conn.execute(
                "INSERT INTO project_members(project_id, user_id, role, joined_at) "
                "VALUES (?, ?, 'owner', ?)",
                (project_id, creator_user_id, ts),
            )
            conn.execute(
                "INSERT INTO project_events("
                "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, '{}', ?)",
                (project_id, creator_user_id, EVENT_CREATED, project_id, ts),
            )
        return ProjectRow(
            project_id=project_id,
            pk=pk,
            creator_user_id=creator_user_id,
            name=name,
            description=description,
            instructions=instructions,
            archived=False,
            created_at=ts,
            updated_at=ts,
        )

    def get_project(self, project_id: str) -> ProjectRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_spaces WHERE project_id = ?", (project_id,)
            ).fetchone()
        return ProjectRow.from_row(row) if row is not None else None

    def get_membership(self, project_id: str, user_id: int) -> ProjectMemberRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT project_id, user_id, role, joined_at FROM project_members "
                "WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
        return ProjectMemberRow.from_row(row) if row is not None else None

    def count_members(self, project_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM project_members WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["n"])

    def add_member(
        self, project_id: str, user_id: int, *, role: str = "member"
    ) -> ProjectMemberRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO project_members(project_id, user_id, role, joined_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, user_id, role, ts),
            )
        return ProjectMemberRow(project_id=project_id, user_id=user_id, role=role, joined_at=ts)

    def list_members(self, project_id: str) -> list[ProjectMemberUserRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT pm.user_id, u.username, pm.role, pm.joined_at "
                "FROM project_members pm "
                "JOIN users u ON u.id = pm.user_id "
                "WHERE pm.project_id = ? "
                "ORDER BY CASE pm.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, "
                "pm.joined_at, pm.user_id",
                (project_id,),
            ).fetchall()
        return map_rows(rows, ProjectMemberUserRow)

    def list_for_user(
        self, user_id: int, *, q: str = "", limit: int = 20, offset: int = 0
    ) -> list[ProjectListItemRow]:
        """Projects the user is a member of, newest update first.

        Returns at most ``limit + 1`` rows so the caller can detect ``has_more``.
        """
        where = ["pm.user_id = ?"]
        params: list[object] = [user_id]
        needle = q.strip().lower()
        if needle:
            # LOWER() + ESCAPE keeps behavior identical on SQLite and PostgreSQL
            # (SQLite LIKE is case-insensitive by default, PostgreSQL is not).
            where.append("LOWER(ps.name) LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(needle)}%")
        params.extend([limit + 1, offset])
        sql = (
            "SELECT ps.project_id, ps.name, ps.description, pm.role AS my_role, "
            "(SELECT COUNT(*) FROM project_members m2 "
            "WHERE m2.project_id = ps.project_id) AS member_count, "
            "ps.created_at, ps.updated_at "
            "FROM project_spaces ps "
            "JOIN project_members pm ON pm.project_id = ps.project_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY ps.updated_at DESC, ps.project_id DESC "
            "LIMIT ? OFFSET ?"
        )
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ProjectListItemRow)

    def update_project(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
    ) -> ProjectRow | None:
        """Apply provided fields and append an update event in one transaction.

        The event records actor, time, target, and changed field names only —
        never description/instructions content. Returns ``None`` when the
        project does not exist; a no-op call writes no event.
        """
        clauses, params = partial_updates(
            [("name", name), ("description", description), ("instructions", instructions)]
        )
        if not clauses:
            return self.get_project(project_id)
        ts = now_ts()
        clauses.append("updated_at = ?")
        params.append(ts)
        params.append(project_id)
        changed = sorted(
            col
            for col, val in (
                ("name", name),
                ("description", description),
                ("instructions", instructions),
            )
            if val is not None
        )
        payload = json.dumps({"fields": changed})
        with self._db.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM project_spaces WHERE project_id = ?", (project_id,)
            ).fetchone()
            if exists is None:
                return None
            conn.execute(
                f"UPDATE project_spaces SET {', '.join(clauses)} WHERE project_id = ?",
                params,
            )
            conn.execute(
                "INSERT INTO project_events("
                "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, actor_user_id, EVENT_UPDATED, project_id, payload, ts),
            )
        return self.get_project(project_id)

    # ------------------------------------------------------------------
    # Invites
    # ------------------------------------------------------------------

    def create_invite(
        self,
        *,
        invite_id: str,
        project_id: str,
        token_hash: str,
        created_by: int,
        role: str,
        requires_approval: bool,
        expires_at: int,
        created_at: int | None = None,
    ) -> ProjectInviteRow:
        """Insert an invite plus its audit event in one transaction.

        Only the SHA-256 ``token_hash`` is persisted; the event payload holds
        the role and approval flag — never the token or its hash.
        """
        ts = now_ts() if created_at is None else created_at
        approval = bool(requires_approval)
        payload = json.dumps({"role": role, "requires_approval": approval})
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO project_invites("
                "invite_id, project_id, token_hash, created_by, role, requires_approval, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invite_id,
                    project_id,
                    token_hash,
                    created_by,
                    role,
                    bool_int(approval),
                    ts,
                    expires_at,
                ),
            )
            _append_event(
                conn, project_id, created_by, EVENT_INVITE_CREATED, invite_id, payload, ts
            )
        return ProjectInviteRow(
            invite_id=invite_id,
            project_id=project_id,
            token_hash=token_hash,
            created_by=created_by,
            role=role,
            requires_approval=approval,
            created_at=ts,
            expires_at=expires_at,
            revoked_at=None,
            consumed_at=None,
            consumed_by_user_id=None,
        )

    def get_invite(self, project_id: str, invite_id: str) -> ProjectInviteRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_invites WHERE project_id = ? AND invite_id = ?",
                (project_id, invite_id),
            ).fetchone()
        return ProjectInviteRow.from_row(row) if row is not None else None

    def list_invites(self, project_id: str) -> list[ProjectInviteRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_invites WHERE project_id = ? "
                "ORDER BY created_at DESC, id DESC",
                (project_id,),
            ).fetchall()
        return map_rows(rows, ProjectInviteRow)

    def revoke_invite(self, project_id: str, invite_id: str, *, actor_user_id: int) -> str:
        """Guarded revocation: ``revoked`` | ``missing`` | ``conflict:<status>``.

        Only a pending (unconsumed, unrevoked, unexpired) invite can be
        revoked; the guard lives in the UPDATE so concurrent consumption on
        PostgreSQL cannot be overwritten.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            updated = conn.execute(
                "UPDATE project_invites SET revoked_at = ? "
                "WHERE project_id = ? AND invite_id = ? AND revoked_at IS NULL "
                "AND consumed_at IS NULL AND expires_at > ?",
                (ts, project_id, invite_id, ts),
            )
            if getattr(updated, "rowcount", 1) == 1:
                _append_event(
                    conn, project_id, actor_user_id, EVENT_INVITE_REVOKED, invite_id, "{}", ts
                )
                return "revoked"
        row = self.get_invite(project_id, invite_id)
        if row is None:
            return "missing"
        return f"conflict:{row.status(ts)}"

    def redeem_invite(self, *, token_hash: str, user_id: int) -> InviteRedemption:
        """One-use redemption: state transition + event in a single transaction.

        Race safety: the guarded consume UPDATE plus ``ON CONFLICT DO
        NOTHING`` rowcount checks make concurrent acceptances safe on both
        dialects; any conflict raises internally so the whole transaction
        rolls back (no partial membership, invite left unconsumed).
        """
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM project_invites WHERE token_hash = ?", (token_hash,)
                ).fetchone()
                if row is None:
                    return InviteRedemption(outcome="invalid")
                invite = ProjectInviteRow.from_row(row)
                pk = int(row["id"])
                status = invite.status(ts)
                if status != "pending":
                    return InviteRedemption(
                        outcome=status, project_id=invite.project_id, invite_id=invite.invite_id
                    )
                member = conn.execute(
                    "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                    (invite.project_id, user_id),
                ).fetchone()
                if member is not None:
                    return InviteRedemption(
                        outcome="already_member",
                        project_id=invite.project_id,
                        invite_id=invite.invite_id,
                    )
                if invite.requires_approval:
                    existing = self._pending_request(conn, invite.project_id, user_id)
                    if existing is not None:
                        return InviteRedemption(
                            outcome="pending_exists",
                            project_id=invite.project_id,
                            invite_id=invite.invite_id,
                            request_id=existing,
                        )
                consumed = conn.execute(
                    "UPDATE project_invites SET consumed_at = ?, consumed_by_user_id = ? "
                    "WHERE id = ? AND consumed_at IS NULL AND revoked_at IS NULL "
                    "AND expires_at > ?",
                    (ts, user_id, pk, ts),
                )
                if getattr(consumed, "rowcount", 1) == 0:
                    fresh = conn.execute(
                        "SELECT * FROM project_invites WHERE id = ?", (pk,)
                    ).fetchone()
                    reason = (
                        ProjectInviteRow.from_row(fresh).status(ts) if fresh is not None else "used"
                    )
                    raise _RedeemConflict(
                        reason, project_id=invite.project_id, invite_id=invite.invite_id
                    )
                request_id = ""
                if invite.requires_approval:
                    request_id = new_ulid()
                    inserted = conn.execute(
                        "INSERT INTO project_join_requests("
                        "request_id, project_id, invite_id, user_id, status, requested_at"
                        ") VALUES (?, ?, ?, ?, 'pending', ?) "
                        "ON CONFLICT (project_id, user_id) WHERE status = 'pending' "
                        "DO NOTHING",
                        (request_id, invite.project_id, invite.invite_id, user_id, ts),
                    )
                    if getattr(inserted, "rowcount", 1) == 0:
                        existing = self._pending_request(conn, invite.project_id, user_id)
                        raise _RedeemConflict(
                            "pending_exists",
                            project_id=invite.project_id,
                            invite_id=invite.invite_id,
                            request_id="" if existing is None else existing,
                        )
                    payload = json.dumps({"user_id": user_id, "invite_id": invite.invite_id})
                    _append_event(
                        conn,
                        invite.project_id,
                        user_id,
                        EVENT_JOIN_REQUESTED,
                        request_id,
                        payload,
                        ts,
                    )
                    outcome, role = "requested", ""
                else:
                    inserted = conn.execute(
                        "INSERT INTO project_members(project_id, user_id, role, joined_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT (project_id, user_id) DO NOTHING",
                        (invite.project_id, user_id, invite.role, ts),
                    )
                    if getattr(inserted, "rowcount", 1) == 0:
                        raise _RedeemConflict(
                            "already_member",
                            project_id=invite.project_id,
                            invite_id=invite.invite_id,
                        )
                    payload = json.dumps(
                        {"user_id": user_id, "role": invite.role, "invite_id": invite.invite_id}
                    )
                    _append_event(
                        conn,
                        invite.project_id,
                        user_id,
                        EVENT_MEMBER_JOINED,
                        str(user_id),
                        payload,
                        ts,
                    )
                    outcome, role = "joined", invite.role
            return InviteRedemption(
                outcome=outcome,
                project_id=invite.project_id,
                role=role,
                invite_id=invite.invite_id,
                request_id=request_id,
            )
        except _RedeemConflict as conflict:
            return InviteRedemption(
                outcome=conflict.outcome,
                project_id=conflict.project_id,
                invite_id=conflict.invite_id,
                request_id=conflict.request_id,
            )

    @staticmethod
    def _pending_request(conn: Any, project_id: str, user_id: int) -> str | None:
        row = conn.execute(
            "SELECT request_id FROM project_join_requests "
            "WHERE project_id = ? AND user_id = ? AND status = 'pending'",
            (project_id, user_id),
        ).fetchone()
        return None if row is None else str(row["request_id"])

    # ------------------------------------------------------------------
    # Join requests
    # ------------------------------------------------------------------

    def list_join_requests(
        self, project_id: str, *, status: str | None = None
    ) -> list[ProjectJoinRequestRow]:
        sql = _JOIN_REQUEST_SELECT + "WHERE r.project_id = ?"
        params: list[object] = [project_id]
        if status is not None:
            sql += " AND r.status = ?"
            params.append(status)
        sql += " ORDER BY r.requested_at, r.id"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ProjectJoinRequestRow)

    def get_join_request(self, project_id: str, request_id: str) -> ProjectJoinRequestRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                _JOIN_REQUEST_SELECT + "WHERE r.project_id = ? AND r.request_id = ?",
                (project_id, request_id),
            ).fetchone()
        return ProjectJoinRequestRow.from_row(row) if row is not None else None

    def resolve_join_request(
        self,
        *,
        project_id: str,
        request_id: str,
        resolver_user_id: int,
        approve: bool,
    ) -> JoinRequestResolution:
        """Approve/reject a pending request; membership + status + event are
        committed together or not at all."""
        ts = now_ts()
        outcome = "approved" if approve else "rejected"
        try:
            with self._db.transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM project_join_requests WHERE project_id = ? AND request_id = ?",
                    (project_id, request_id),
                ).fetchone()
                if row is None:
                    return JoinRequestResolution(outcome="missing")
                if str(row["status"]) != "pending":
                    raise _RequestConflict("resolved")
                user_id = int(row["user_id"])
                if approve:
                    role = self._invite_role(conn, row["invite_id"])
                    inserted = conn.execute(
                        "INSERT INTO project_members(project_id, user_id, role, joined_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT (project_id, user_id) DO NOTHING",
                        (project_id, user_id, role, ts),
                    )
                    if getattr(inserted, "rowcount", 1) == 0:
                        raise _RequestConflict("already_member")
                    event_type = EVENT_JOIN_APPROVED
                    payload = json.dumps({"user_id": user_id, "role": role})
                else:
                    event_type = EVENT_JOIN_REJECTED
                    payload = json.dumps({"user_id": user_id})
                updated = conn.execute(
                    "UPDATE project_join_requests "
                    "SET status = ?, resolved_at = ?, resolved_by = ? "
                    "WHERE project_id = ? AND request_id = ? AND status = 'pending'",
                    (outcome, ts, resolver_user_id, project_id, request_id),
                )
                if getattr(updated, "rowcount", 1) == 0:
                    raise _RequestConflict("resolved")
                _append_event(
                    conn, project_id, resolver_user_id, event_type, request_id, payload, ts
                )
        except _RequestConflict as conflict:
            return JoinRequestResolution(
                outcome=conflict.outcome,
                request=self.get_join_request(project_id, request_id),
            )
        return JoinRequestResolution(
            outcome=outcome, request=self.get_join_request(project_id, request_id)
        )

    @staticmethod
    def _invite_role(conn: Any, invite_id: object) -> str:
        if invite_id is None:
            return "member"
        row = conn.execute(
            "SELECT role FROM project_invites WHERE invite_id = ?", (str(invite_id),)
        ).fetchone()
        return "member" if row is None else str(row["role"])

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    def get_member_user(self, project_id: str, user_id: int) -> ProjectMemberUserRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT pm.user_id, u.username, pm.role, pm.joined_at "
                "FROM project_members pm "
                "JOIN users u ON u.id = pm.user_id "
                "WHERE pm.project_id = ? AND pm.user_id = ?",
                (project_id, user_id),
            ).fetchone()
        return ProjectMemberUserRow.from_row(row) if row is not None else None

    def set_member_role(
        self,
        *,
        project_id: str,
        user_id: int,
        role: str,
        actor_user_id: int,
        forbid_roles: tuple[str, ...] = ("owner",),
    ) -> MemberMutation:
        """Change a member's role; ``forbid_roles`` targets are rejected.

        A no-op (same role) writes nothing and appends no event.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if row is None:
                return MemberMutation(outcome="missing")
            current = str(row["role"])
            if current in forbid_roles:
                return MemberMutation(outcome="forbidden_role", role=current)
            if current == role:
                return MemberMutation(outcome="noop", role=current)
            updated = conn.execute(
                "UPDATE project_members SET role = ? "
                "WHERE project_id = ? AND user_id = ? AND role = ?",
                (role, project_id, user_id, current),
            )
            if getattr(updated, "rowcount", 1) != 1:
                return MemberMutation(outcome="stale", role=current)
            payload = json.dumps({"user_id": user_id, "from_role": current, "to_role": role})
            _append_event(
                conn,
                project_id,
                actor_user_id,
                EVENT_MEMBER_ROLE_CHANGED,
                str(user_id),
                payload,
                ts,
            )
        return MemberMutation(outcome="changed", role=role)

    def remove_member(
        self,
        *,
        project_id: str,
        user_id: int,
        actor_user_id: int,
        forbid_roles: tuple[str, ...] = ("owner",),
    ) -> MemberMutation:
        """Delete a membership row plus its audit event in one transaction."""
        ts = now_ts()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if row is None:
                return MemberMutation(outcome="missing")
            current = str(row["role"])
            if current in forbid_roles:
                return MemberMutation(outcome="forbidden_role", role=current)
            deleted = conn.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ? AND role = ?",
                (project_id, user_id, current),
            )
            if getattr(deleted, "rowcount", 1) != 1:
                return MemberMutation(outcome="stale", role=current)
            # Unassign the removed member's undeleted todos in the same
            # transaction so no todo points at a user without access. The
            # version bump makes in-flight writes against the old row fail as
            # stale; events carry ids only, never title/description.
            unassigned = conn.execute(
                "UPDATE project_todos "
                "SET assignee_user_id = NULL, version = version + 1, updated_at = ? "
                "WHERE project_id = ? AND assignee_user_id = ? AND deleted_at IS NULL "
                "RETURNING todo_id",
                (ts, project_id, user_id),
            ).fetchall()
            for todo in unassigned:
                todo_payload = json.dumps(
                    {
                        "fields": ["assignee_user_id"],
                        "from_assignee_user_id": user_id,
                        "to_assignee_user_id": None,
                    }
                )
                _append_event(
                    conn,
                    project_id,
                    actor_user_id,
                    EVENT_TODO_UPDATED,
                    str(todo["todo_id"]),
                    todo_payload,
                    ts,
                )
            payload = json.dumps({"user_id": user_id, "role": current})
            _append_event(
                conn, project_id, actor_user_id, EVENT_MEMBER_REMOVED, str(user_id), payload, ts
            )
        return MemberMutation(outcome="removed", role=current)
