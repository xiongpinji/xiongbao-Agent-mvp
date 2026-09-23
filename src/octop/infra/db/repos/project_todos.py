"""Project todos — SQL-only repository for PS-04 planning items.

Authorization policy lives in ``octop.infra.projects.todos``; this module only
enforces what must be race-safe. Every mutation re-checks membership/role and
the optimistic ``version`` inside the same transaction, so a stale role or a
concurrent edit can never win. On PostgreSQL the membership/todo rows are
locked with ``FOR UPDATE`` (lock order: memberships → todos, matching
``ProjectRepo.remove_member``); SQLite's ``BEGIN IMMEDIATE`` writer already
serializes writes. Events carry ids, changed field names, and status/assignee
values only — never title/description text.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import UNSET, DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

EVENT_TODO_CREATED = "project.todo_created"
EVENT_TODO_UPDATED = "project.todo_updated"
EVENT_TODO_DELETED = "project.todo_deleted"

MANAGER_ROLES = ("owner", "admin")

_TODO_COLUMNS = (
    "todo_id, project_id, creator_user_id, assignee_user_id, title, description, "
    "status, version, created_at, updated_at, deleted_at"
)
_TODO_SELECT = f"SELECT {_TODO_COLUMNS} FROM project_todos "


@dataclass(frozen=True)
class ProjectTodoRow:
    todo_id: str
    project_id: str
    creator_user_id: int
    assignee_user_id: int | None
    title: str
    description: str
    status: str
    version: int
    created_at: int
    updated_at: int
    deleted_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> ProjectTodoRow:
        return cls(
            todo_id=str(row["todo_id"]),
            project_id=str(row["project_id"]),
            creator_user_id=int(row["creator_user_id"]),
            assignee_user_id=(
                None if row["assignee_user_id"] is None else int(row["assignee_user_id"])
            ),
            title=str(row["title"]),
            description=str(row["description"]),
            status=str(row["status"]),
            version=int(row["version"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            deleted_at=None if row["deleted_at"] is None else int(row["deleted_at"]),
        )


@dataclass(frozen=True)
class TodoMutation:
    """Outcome of a single-todo mutation plus the fresh row on success.

    ``outcome`` is one of: created, updated, deleted, missing, stale,
    not_member, forbidden, invalid_assignee, no_change.
    """

    outcome: str
    row: ProjectTodoRow | None = None


@dataclass(frozen=True)
class BulkTodoMutation:
    """Outcome of :meth:`ProjectTodoRepo.bulk_update`.

    ``outcome`` is one of: updated, missing, stale, not_member, forbidden,
    invalid_assignee. ``todo_id`` names the offending item on failure;
    ``rows`` carries every updated row (request order) on success.
    """

    outcome: str
    todo_id: str = ""
    rows: list[ProjectTodoRow] = field(default_factory=list)


class _TodoConflict(Exception):
    """Internal signal to roll the write transaction back cleanly."""

    def __init__(self, outcome: str, todo_id: str = "") -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.todo_id = todo_id


def _escape_like(value: str) -> str:
    # Same escaping as ProjectRepo.list_for_user: %, _, and \ stay literal.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _append_todo_event(
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


class ProjectTodoRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------ helpers

    def _lock_suffix(self) -> str:
        # PostgreSQL: lock rows so member removal / role changes serialize
        # with this transaction. SQLite writers already hold an exclusive lock.
        return " FOR UPDATE" if self._db.dialect == "postgresql" else ""

    def _locked_member_roles(
        self, conn: Any, project_id: str, user_ids: Sequence[int]
    ) -> dict[int, str]:
        """Lock every relevant membership before any todo, in user-id order.

        A reassignment and member removal can otherwise take the membership
        and todo locks in opposite orders on PostgreSQL. Sorting also avoids
        two writers deadlocking when each assigns a todo to the other actor.
        """
        roles: dict[int, str] = {}
        for user_id in sorted(set(user_ids)):
            row = conn.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
                + self._lock_suffix(),
                (project_id, user_id),
            ).fetchone()
            if row is not None:
                roles[user_id] = str(row["role"])
        return roles

    def _select_todo(
        self, conn: Any, project_id: str, todo_id: str, *, lock: bool
    ) -> ProjectTodoRow | None:
        row = conn.execute(
            _TODO_SELECT
            + "WHERE project_id = ? AND todo_id = ?"
            + (self._lock_suffix() if lock else ""),
            (project_id, todo_id),
        ).fetchone()
        return ProjectTodoRow.from_row(row) if row is not None else None

    def _diagnose(self, conn: Any, project_id: str, todo_id: str) -> str:
        """Classify a failed guarded UPDATE: gone/deleted → missing, else stale."""
        todo = self._select_todo(conn, project_id, todo_id, lock=False)
        if todo is None or todo.deleted_at is not None:
            return "missing"
        return "stale"

    def _allocate_todo_id(self) -> str:
        for _ in range(16):
            todo_id = new_ulid()
            with self._db.connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM project_todos WHERE todo_id = ?", (todo_id,)
                ).fetchone()
            if exists is None:
                return todo_id
        raise RuntimeError("failed to allocate unique todo id")

    # ------------------------------------------------------------ read paths

    def get(self, project_id: str, todo_id: str) -> ProjectTodoRow | None:
        """One undeleted todo; deleted, foreign, and unknown ids return None."""
        with self._db.connect() as conn:
            row = conn.execute(
                _TODO_SELECT + "WHERE project_id = ? AND todo_id = ? AND deleted_at IS NULL",
                (project_id, todo_id),
            ).fetchone()
        return ProjectTodoRow.from_row(row) if row is not None else None

    def list_todos(
        self,
        project_id: str,
        *,
        q: str = "",
        status: str | None = None,
        assignee_user_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ProjectTodoRow]:
        """Undeleted todos ordered ``updated_at DESC, todo_id DESC``.

        Returns at most ``limit + 1`` rows so the caller can detect
        ``has_more``. ``q`` matches the title only, with LIKE wildcards
        escaped so user text stays literal.
        """
        where = ["project_id = ?", "deleted_at IS NULL"]
        params: list[object] = [project_id]
        needle = q.strip().lower()
        if needle:
            # LOWER() + ESCAPE keeps behavior identical on SQLite and PostgreSQL.
            where.append("LOWER(title) LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(needle)}%")
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if assignee_user_id is not None:
            where.append("assignee_user_id = ?")
            params.append(assignee_user_id)
        params.extend([limit + 1, offset])
        sql = (
            _TODO_SELECT
            + f"WHERE {' AND '.join(where)} "
            + "ORDER BY updated_at DESC, todo_id DESC "
            + "LIMIT ? OFFSET ?"
        )
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ProjectTodoRow)

    # ------------------------------------------------------------ mutations

    def create(
        self,
        *,
        project_id: str,
        creator_user_id: int,
        title: str,
        description: str = "",
        assignee_user_id: int | None = None,
        ts: int | None = None,
    ) -> TodoMutation:
        """Insert one todo plus its creation event in a single transaction.

        Race-safe re-checks (the service has already authorized): the creator
        must still be a member (``not_member``), a non-manager creator may
        only assign themself (``forbidden``), and any assignee must be a
        current member of this project (``invalid_assignee``).
        """
        stamp = now_ts() if ts is None else ts
        todo_id = self._allocate_todo_id()
        try:
            with self._db.transaction() as conn:
                member_ids = [creator_user_id]
                if assignee_user_id is not None:
                    member_ids.append(assignee_user_id)
                roles = self._locked_member_roles(conn, project_id, member_ids)
                role = roles.get(creator_user_id)
                if role is None:
                    raise _TodoConflict("not_member")
                if assignee_user_id is not None:
                    if assignee_user_id != creator_user_id and role not in MANAGER_ROLES:
                        raise _TodoConflict("forbidden")
                    if assignee_user_id not in roles:
                        raise _TodoConflict("invalid_assignee")
                conn.execute(
                    "INSERT INTO project_todos("
                    "todo_id, project_id, creator_user_id, assignee_user_id, title, "
                    "description, status, version, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, 'todo', 1, ?, ?)",
                    (
                        todo_id,
                        project_id,
                        creator_user_id,
                        assignee_user_id,
                        title,
                        description,
                        stamp,
                        stamp,
                    ),
                )
                payload = json.dumps(
                    {
                        "creator_user_id": creator_user_id,
                        "assignee_user_id": assignee_user_id,
                    }
                )
                _append_todo_event(
                    conn, project_id, creator_user_id, EVENT_TODO_CREATED, todo_id, payload, stamp
                )
        except _TodoConflict as conflict:
            return TodoMutation(outcome=conflict.outcome)
        return TodoMutation(outcome="created", row=self.get(project_id, todo_id))

    def update(
        self,
        *,
        project_id: str,
        todo_id: str,
        actor_user_id: int,
        expected_version: int,
        title: Any = UNSET,
        description: Any = UNSET,
        status: Any = UNSET,
        assignee_user_id: Any = UNSET,
        ts: int | None = None,
    ) -> TodoMutation:
        """Apply one guarded patch; returns an outcome instead of raising.

        ``assignee_user_id`` distinguishes omitted (``UNSET``) from explicit
        null (clear). Only owner/admin may change the assignee — anyone else
        editing it gets ``forbidden``; creators and the current assignee may
        edit title/description/status. At least one actually changed field is
        required (``no_change``), and ``expected_version`` must match the
        stored row (``stale``); failures write nothing and emit no event.
        """
        stamp = now_ts() if ts is None else ts
        try:
            with self._db.transaction() as conn:
                requested_assignee = (
                    int(assignee_user_id)
                    if assignee_user_id is not UNSET and assignee_user_id is not None
                    else None
                )
                member_ids = [actor_user_id]
                if requested_assignee is not None:
                    member_ids.append(requested_assignee)
                roles = self._locked_member_roles(conn, project_id, member_ids)
                role = roles.get(actor_user_id)
                if role is None:
                    raise _TodoConflict("not_member")
                if assignee_user_id is not UNSET and role not in MANAGER_ROLES:
                    raise _TodoConflict("forbidden")
                if requested_assignee is not None and requested_assignee not in roles:
                    raise _TodoConflict("invalid_assignee")
                todo = self._select_todo(conn, project_id, todo_id, lock=True)
                if todo is None or todo.deleted_at is not None:
                    raise _TodoConflict("missing")
                if todo.version != expected_version:
                    raise _TodoConflict("stale")
                manager = role in MANAGER_ROLES
                if (
                    not manager
                    and todo.creator_user_id != actor_user_id
                    and todo.assignee_user_id != actor_user_id
                ):
                    raise _TodoConflict("forbidden")
                # Keys below are the only ones interpolated into SET clauses.
                changes: dict[str, Any] = {}
                if title is not UNSET and str(title) != todo.title:
                    changes["title"] = str(title)
                if description is not UNSET and str(description) != todo.description:
                    changes["description"] = str(description)
                if status is not UNSET and str(status) != todo.status:
                    changes["status"] = str(status)
                if assignee_user_id is not UNSET:
                    requested = requested_assignee
                    if requested != todo.assignee_user_id:
                        changes["assignee_user_id"] = requested
                if not changes:
                    raise _TodoConflict("no_change")

                set_sql = ", ".join(f"{col} = ?" for col in changes)
                updated = conn.execute(
                    f"UPDATE project_todos SET {set_sql}, version = version + 1, updated_at = ? "
                    "WHERE project_id = ? AND todo_id = ? AND version = ? AND deleted_at IS NULL",
                    (*changes.values(), stamp, project_id, todo_id, expected_version),
                )
                if getattr(updated, "rowcount", 1) != 1:
                    raise _TodoConflict(self._diagnose(conn, project_id, todo_id))
                payload = json.dumps(
                    {
                        "fields": sorted(changes),
                        "from_status": todo.status,
                        "to_status": str(changes.get("status", todo.status)),
                        "from_assignee_user_id": todo.assignee_user_id,
                        "to_assignee_user_id": changes.get(
                            "assignee_user_id", todo.assignee_user_id
                        ),
                    }
                )
                _append_todo_event(
                    conn,
                    project_id,
                    actor_user_id,
                    EVENT_TODO_UPDATED,
                    todo_id,
                    payload,
                    stamp,
                )
        except _TodoConflict as conflict:
            return TodoMutation(outcome=conflict.outcome)
        return TodoMutation(outcome="updated", row=self.get(project_id, todo_id))

    def delete(
        self,
        *,
        project_id: str,
        todo_id: str,
        actor_user_id: int,
        expected_version: int,
        ts: int | None = None,
    ) -> TodoMutation:
        """Soft-delete one todo (kept for audit, invisible to every read).

        Allowed for owner/admin or the creator; outcome: deleted / missing /
        stale / not_member / forbidden. The version bump makes any in-flight
        write against the pre-delete row fail as stale.
        """
        stamp = now_ts() if ts is None else ts
        try:
            with self._db.transaction() as conn:
                role = self._locked_member_roles(conn, project_id, [actor_user_id]).get(
                    actor_user_id
                )
                if role is None:
                    raise _TodoConflict("not_member")
                todo = self._select_todo(conn, project_id, todo_id, lock=True)
                if todo is None or todo.deleted_at is not None:
                    raise _TodoConflict("missing")
                if todo.version != expected_version:
                    raise _TodoConflict("stale")
                if role not in MANAGER_ROLES and todo.creator_user_id != actor_user_id:
                    raise _TodoConflict("forbidden")
                deleted = conn.execute(
                    "UPDATE project_todos "
                    "SET deleted_at = ?, version = version + 1, updated_at = ? "
                    "WHERE project_id = ? AND todo_id = ? AND version = ? AND deleted_at IS NULL",
                    (stamp, stamp, project_id, todo_id, expected_version),
                )
                if getattr(deleted, "rowcount", 1) != 1:
                    raise _TodoConflict(self._diagnose(conn, project_id, todo_id))
                payload = json.dumps(
                    {
                        "fields": ["deleted_at"],
                        "from_status": todo.status,
                        "from_assignee_user_id": todo.assignee_user_id,
                    }
                )
                _append_todo_event(
                    conn,
                    project_id,
                    actor_user_id,
                    EVENT_TODO_DELETED,
                    todo_id,
                    payload,
                    stamp,
                )
        except _TodoConflict as conflict:
            return TodoMutation(outcome=conflict.outcome)
        return TodoMutation(outcome="deleted")

    def bulk_update(
        self,
        *,
        project_id: str,
        items: Sequence[tuple[str, int]],
        actor_user_id: int,
        status: Any = UNSET,
        assignee_user_id: Any = UNSET,
        ts: int | None = None,
    ) -> BulkTodoMutation:
        """Apply one status/assignee patch to many todos, all-or-nothing.

        Owner/admin only. Every id must exist in *this* project, be
        undeleted, and carry its expected version; any failure rolls the
        whole batch back (``missing``/``stale`` name the offending todo_id).
        Rows are locked in todo_id order for deterministic lock ordering.
        Fields are applied even when unchanged so the batch stays atomic and
        every touched row gains a version bump and an event.
        """
        if status is UNSET and assignee_user_id is UNSET:
            raise ValueError("bulk_update requires status or assignee_user_id")
        stamp = now_ts() if ts is None else ts
        try:
            rows: list[ProjectTodoRow] = []
            with self._db.transaction() as conn:
                new_assignee: Any = UNSET
                if assignee_user_id is not UNSET:
                    new_assignee = None if assignee_user_id is None else int(assignee_user_id)
                member_ids = [actor_user_id]
                if new_assignee is not UNSET and new_assignee is not None:
                    member_ids.append(new_assignee)
                roles = self._locked_member_roles(conn, project_id, member_ids)
                role = roles.get(actor_user_id)
                if role is None:
                    raise _TodoConflict("not_member")
                if role not in MANAGER_ROLES:
                    raise _TodoConflict("forbidden")
                if (
                    new_assignee is not UNSET
                    and new_assignee is not None
                    and new_assignee not in roles
                ):
                    raise _TodoConflict("invalid_assignee")
                fields_changed: list[str] = []
                if status is not UNSET:
                    fields_changed.append("status")
                if new_assignee is not UNSET:
                    fields_changed.append("assignee_user_id")
                event_fields = sorted(fields_changed)

                for item_todo_id, expected_version in sorted(items):
                    todo = self._select_todo(conn, project_id, item_todo_id, lock=True)
                    if todo is None or todo.deleted_at is not None:
                        raise _TodoConflict("missing", item_todo_id)
                    if todo.version != expected_version:
                        raise _TodoConflict("stale", item_todo_id)
                    set_parts: list[str] = []
                    params: list[object] = []
                    if status is not UNSET:
                        set_parts.append("status = ?")
                        params.append(str(status))
                    if new_assignee is not UNSET:
                        set_parts.append("assignee_user_id = ?")
                        params.append(new_assignee)
                    updated = conn.execute(
                        f"UPDATE project_todos SET {', '.join(set_parts)}, "
                        "version = version + 1, updated_at = ? "
                        "WHERE project_id = ? AND todo_id = ? AND version = ? "
                        "AND deleted_at IS NULL",
                        (*params, stamp, project_id, item_todo_id, expected_version),
                    )
                    if getattr(updated, "rowcount", 1) != 1:
                        raise _TodoConflict(
                            self._diagnose(conn, project_id, item_todo_id), item_todo_id
                        )
                    payload = json.dumps(
                        {
                            "fields": event_fields,
                            "from_status": todo.status,
                            "to_status": str(status) if status is not UNSET else todo.status,
                            "from_assignee_user_id": todo.assignee_user_id,
                            "to_assignee_user_id": (
                                new_assignee if new_assignee is not UNSET else todo.assignee_user_id
                            ),
                        }
                    )
                    _append_todo_event(
                        conn,
                        project_id,
                        actor_user_id,
                        EVENT_TODO_UPDATED,
                        item_todo_id,
                        payload,
                        stamp,
                    )
                # Re-read inside the transaction so the response reflects it.
                for item_todo_id, _expected in items:
                    row = self._select_todo(conn, project_id, item_todo_id, lock=False)
                    if row is None:
                        raise _TodoConflict("missing", item_todo_id)
                    rows.append(row)
        except _TodoConflict as conflict:
            return BulkTodoMutation(outcome=conflict.outcome, todo_id=conflict.todo_id)
        return BulkTodoMutation(outcome="updated", rows=rows)
