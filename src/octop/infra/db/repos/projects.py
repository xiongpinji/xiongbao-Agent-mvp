"""Project spaces — projects, memberships, and append-only project events."""

from __future__ import annotations

import json
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    map_rows,
    now_ts,
    partial_updates,
)
from octop.infra.utils.ulid import new_ulid

EVENT_CREATED = "project.created"
EVENT_UPDATED = "project.updated"


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
