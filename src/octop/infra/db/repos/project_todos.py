"""Project todos — SQL-only repository for PS-04 planning items.

Authorization policy lives in ``octop.infra.projects.todos``; this module only
enforces what must be race-safe. Every mutation re-checks membership/role and
the optimistic ``version`` inside the same transaction, so a stale role or a
concurrent edit can never win. On PostgreSQL the membership/todo rows are
locked with ``FOR UPDATE`` (memberships → project → catalog → sorted todos,
matching ``ProjectRepo.remove_member``); SQLite's ``BEGIN IMMEDIATE`` writer already
serializes writes. Events carry ids, changed field names, and status/assignee
values only — never title/description text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos import project_plan_locks as locks
from octop.infra.db.repos._base import UNSET, DbRow, now_ts
from octop.infra.utils.project_plan_keys import normalize_project_plan_key
from octop.infra.utils.ulid import new_ulid

EVENT_TODO_CREATED = "project.todo_created"
EVENT_TODO_UPDATED = "project.todo_updated"
EVENT_TODO_DELETED = "project.todo_deleted"

MANAGER_ROLES = ("owner", "admin")

_TODO_COLUMNS = (
    "todo_id, project_id, creator_user_id, assignee_user_id, title, description, "
    "description_format, status, version, created_at, updated_at, deleted_at, "
    "start_date, due_date, priority_id"
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
    description_format: str
    status: str
    version: int
    created_at: int
    updated_at: int
    deleted_at: int | None
    start_date: str | None
    due_date: str | None
    priority_id: str | None
    tag_ids: list[str]
    catalog_revision: int
    display_revision: int
    parent_todo_id: str | None
    children_count: int
    done_children_count: int
    children_revision: int | None

    @classmethod
    def from_row(
        cls,
        row: DbRow,
        *,
        tag_ids: list[str],
        catalog_revision: int,
        display_revision: int,
        relationship: tuple[str | None, int, int, int | None],
    ) -> ProjectTodoRow:
        return cls(
            todo_id=str(row["todo_id"]),
            project_id=str(row["project_id"]),
            creator_user_id=int(row["creator_user_id"]),
            assignee_user_id=(
                None if row["assignee_user_id"] is None else int(row["assignee_user_id"])
            ),
            title=str(row["title"]),
            description=str(row["description"]),
            description_format=str(row["description_format"]),
            status=str(row["status"]),
            version=int(row["version"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            deleted_at=None if row["deleted_at"] is None else int(row["deleted_at"]),
            start_date=None if row["start_date"] is None else str(row["start_date"]),
            due_date=None if row["due_date"] is None else str(row["due_date"]),
            priority_id=None if row["priority_id"] is None else str(row["priority_id"]),
            tag_ids=tag_ids,
            catalog_revision=catalog_revision,
            display_revision=locks.checked_display_revision(display_revision),
            parent_todo_id=relationship[0],
            children_count=relationship[1],
            done_children_count=relationship[2],
            children_revision=relationship[3],
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


@dataclass(frozen=True)
class RelationshipMutation:
    outcome: str
    data: dict[str, Any] = field(default_factory=dict)


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
        return locks.member_roles(self._db, conn, project_id, user_ids, write=True)

    def _select_todo(
        self, conn: Any, project_id: str, todo_id: str, *, lock: bool, catalog_revision: int
    ) -> ProjectTodoRow | None:
        row = conn.execute(
            _TODO_SELECT
            + "WHERE project_id = ? AND todo_id = ?"
            + (self._lock_suffix() if lock else ""),
            (project_id, todo_id),
        ).fetchone()
        return self._row_with_refs(conn, row, catalog_revision) if row is not None else None

    def _row_with_refs(self, conn: Any, row: DbRow, revision: int) -> ProjectTodoRow:
        tags = conn.execute(
            "SELECT tag_id FROM project_todo_tag_links WHERE project_id = ? AND todo_id = ? "
            "ORDER BY tag_id",
            (row["project_id"], row["todo_id"]),
        ).fetchall()
        return ProjectTodoRow.from_row(
            row,
            tag_ids=[str(tag["tag_id"]) for tag in tags],
            catalog_revision=revision,
            relationship=locks.relationship_projection(
                conn, str(row["project_id"]), str(row["todo_id"])
            ),
            display_revision=locks.display_revision(
                self._db, conn, str(row["project_id"]), str(row["todo_id"]), write=False
            ),
        )

    def _read_revision(self, conn: Any, project_id: str, user_id: int | None) -> int | None:
        if user_id is not None and user_id not in locks.member_roles(
            self._db, conn, project_id, [user_id], write=False
        ):
            return None
        if locks.project_row(self._db, conn, project_id, write=False) is None:
            return None
        return locks.catalog_revision(self._db, conn, project_id, write=False)

    def _catalog_locked(self, conn: Any, project_id: str) -> tuple[int, list[Any], list[Any]]:
        if locks.project_row(self._db, conn, project_id, write=True) is None:
            raise _TodoConflict("not_member")
        revision = locks.catalog_revision(self._db, conn, project_id, write=True)
        priorities, tags = locks.catalog_items(self._db, conn, project_id)
        return revision, priorities, tags

    @staticmethod
    def _check_catalog_revision(revision: int, expected: Any) -> None:
        if expected is UNSET:
            return
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise _TodoConflict("invalid_catalog_revision")
        if expected != revision:
            raise _TodoConflict("catalog_stale")

    @staticmethod
    def _check_references(
        priorities: list[Any],
        tags: list[Any],
        priority_id: Any,
        tag_ids: Any,
        current: ProjectTodoRow | None = None,
    ) -> list[str]:
        if priority_id is not UNSET and priority_id is not None:
            priority = next((row for row in priorities if row["priority_id"] == priority_id), None)
            if priority is None or (
                priority["archived_at"] is not None
                and (current is None or current.priority_id != priority_id)
            ):
                raise _TodoConflict("invalid_priority")
        if tag_ids is UNSET:
            return [] if current is None else current.tag_ids
        if not isinstance(tag_ids, (list, tuple)) or any(
            not isinstance(value, str) or not value or len(value) > 64 for value in tag_ids
        ):
            raise _TodoConflict("invalid_tags")
        requested = sorted(set(tag_ids))
        if len(requested) > 20:
            raise _TodoConflict("invalid_tags")
        existing = set(current.tag_ids) if current is not None else set()
        options = {str(row["tag_id"]): row for row in tags}
        for tag_id in requested:
            option = options.get(tag_id)
            if option is None or (option["archived_at"] is not None and tag_id not in existing):
                raise _TodoConflict("invalid_tags")
        return requested

    @staticmethod
    def _check_dates(start: Any, due: Any, current_due: str | None, timezone: str) -> None:
        for value in (start, due):
            if value is None:
                continue
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
            ):
                raise _TodoConflict("invalid_dates")
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise _TodoConflict("invalid_dates") from None
            if parsed.year < 1900:
                raise _TodoConflict("invalid_dates")
        if start is not None and due is not None and start > due:
            raise _TodoConflict("invalid_dates")
        if due is not None and due != current_due and due < locks.server_today(timezone):
            raise _TodoConflict("invalid_dates")

    @staticmethod
    def _replace_tags(conn: Any, project_id: str, todo_id: str, tag_ids: list[str]) -> None:
        conn.execute(
            "DELETE FROM project_todo_tag_links WHERE project_id = ? AND todo_id = ?",
            (project_id, todo_id),
        )
        for tag_id in tag_ids:
            conn.execute(
                "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
                (project_id, todo_id, tag_id),
            )

    def _diagnose(self, conn: Any, project_id: str, todo_id: str) -> str:
        """Classify a failed guarded UPDATE: gone/deleted → missing, else stale."""
        todo = conn.execute(
            "SELECT deleted_at FROM project_todos WHERE project_id = ? AND todo_id = ?",
            (project_id, todo_id),
        ).fetchone()
        if todo is None or todo["deleted_at"] is not None:
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

    def _lock_relationships(self, conn: Any, project_id: str, todo_ids: Sequence[str]) -> None:
        ids = sorted(set(todo_ids))
        marks = ",".join("?" for _ in ids)
        relations = conn.execute(
            "SELECT parent_todo_id,child_todo_id FROM project_todo_children WHERE project_id=? AND (parent_todo_id IN ("
            + marks
            + ") OR child_todo_id IN ("
            + marks
            + "))",
            (project_id, *ids, *ids),
        ).fetchall()
        targets = sorted(
            set(ids)
            | {str(row[key]) for row in relations for key in ("parent_todo_id", "child_todo_id")}
        )
        existing = []
        for tid in targets:
            if conn.execute(
                "SELECT todo_id FROM project_todos WHERE project_id=? AND todo_id=?"
                + self._lock_suffix(),
                (project_id, tid),
            ).fetchone():
                existing.append(tid)
        for tid in existing:
            locks.display_revision(self._db, conn, project_id, tid, write=True)
        conn.execute(
            "SELECT parent_todo_id,child_todo_id FROM project_todo_children WHERE project_id=? AND (parent_todo_id IN ("
            + marks
            + ") OR child_todo_id IN ("
            + marks
            + ")) ORDER BY parent_todo_id,child_todo_id"
            + self._lock_suffix(),
            (project_id, *ids, *ids),
        ).fetchall()
        for tid in existing:
            row = conn.execute(
                "SELECT revision FROM project_todo_children_state WHERE project_id=? AND todo_id=?"
                + self._lock_suffix(),
                (project_id, tid),
            ).fetchone()
            locks.checked_display_revision(None if row is None else row["revision"])
        row = conn.execute(
            "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?"
            + self._lock_suffix(),
            (project_id,),
        ).fetchone()
        locks.checked_display_revision(None if row is None else row["revision"])

    @staticmethod
    def _active_parent(conn: Any, project_id: str, todo_id: str) -> str | None:
        row = conn.execute(
            "SELECT r.parent_todo_id FROM project_todo_children r JOIN project_todos p ON p.project_id=r.project_id AND p.todo_id=r.parent_todo_id JOIN project_todos c ON c.project_id=r.project_id AND c.todo_id=r.child_todo_id WHERE r.project_id=? AND r.child_todo_id=? AND p.deleted_at IS NULL AND c.deleted_at IS NULL",
            (project_id, todo_id),
        ).fetchone()
        return None if row is None else str(row["parent_todo_id"])

    # ------------------------------------------------------------ read paths

    def get(
        self, project_id: str, todo_id: str, *, user_id: int | None = None
    ) -> ProjectTodoRow | None:
        """One undeleted todo; deleted, foreign, and unknown ids return None."""
        with self._db.transaction() as conn:
            revision = self._read_revision(conn, project_id, user_id)
            if revision is None:
                return None
            row = conn.execute(
                _TODO_SELECT + "WHERE project_id = ? AND todo_id = ? AND deleted_at IS NULL",
                (project_id, todo_id),
            ).fetchone()
            return self._row_with_refs(conn, row, revision) if row is not None else None

    def list_todos(
        self,
        project_id: str,
        *,
        user_id: int | None = None,
        q: str = "",
        status: str | None = None,
        assignee_user_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ProjectTodoRow] | None:
        """Undeleted todos ordered ``updated_at DESC, todo_id DESC``.

        Returns at most ``limit + 1`` rows so the caller can detect
        ``has_more``. ``q`` matches the title only, with LIKE wildcards
        escaped so user text stays literal.
        """
        where = [
            "project_id = ?",
            "deleted_at IS NULL",
            "NOT EXISTS (SELECT 1 FROM project_todo_children r WHERE r.project_id=project_todos.project_id AND r.child_todo_id=project_todos.todo_id)",
        ]
        params: list[object] = [project_id]
        needle = normalize_project_plan_key(q.strip())
        if needle:
            where.append("title_search_key LIKE ? ESCAPE '\\'")
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
        sql = (
            "SELECT page.*, display.revision AS display_revision"
            + locks.RELATIONSHIP_PAGE_COLUMNS
            + " FROM ("
            + sql
            + ") AS page LEFT JOIN project_todo_display_state AS display "
            "ON display.project_id=page.project_id AND display.todo_id=page.todo_id "
            + locks.relationship_page_joins("page")
            + " ORDER BY page.updated_at DESC,page.todo_id DESC"
        )
        with self._db.transaction() as conn:
            revision = self._read_revision(conn, project_id, user_id)
            if revision is None:
                return None
            rows = conn.execute(sql, [*params, project_id]).fetchall()
            if not rows:
                return []
            tag_ids_by_todo: dict[str, list[str]] = {str(row["todo_id"]): [] for row in rows}
            links = conn.execute(
                "SELECT todo_id, tag_id FROM project_todo_tag_links WHERE project_id = ? "
                "AND todo_id IN (" + ",".join("?" for _ in tag_ids_by_todo) + ") "
                "ORDER BY todo_id, tag_id",
                (project_id, *tag_ids_by_todo),
            ).fetchall()
            for link in links:
                tag_ids_by_todo[str(link["todo_id"])].append(str(link["tag_id"]))
            return [
                ProjectTodoRow.from_row(
                    row,
                    tag_ids=tag_ids_by_todo[str(row["todo_id"])],
                    catalog_revision=revision,
                    relationship=locks.relationship_from_page_row(row),
                    display_revision=locks.checked_display_revision(row["display_revision"]),
                )
                for row in rows
            ]

    # ------------------------------------------------------------ mutations

    def _insert_in_connection(
        self,
        conn: Any,
        *,
        project_id: str,
        todo_id: str,
        creator_user_id: int,
        assignee_user_id: int | None,
        title: str,
        description: str,
        description_format: str,
        status: str,
        stamp: int,
        start_date: str | None,
        due_date: str | None,
        priority_id: str | None,
        requested_tags: list[str],
        revision: int,
        emit_event: bool = True,
    ) -> ProjectTodoRow:
        conn.execute(
            "INSERT INTO project_todos("
            "todo_id, project_id, creator_user_id, assignee_user_id, title, "
            "description, description_format, status, version, created_at, updated_at, "
            "start_date, due_date, priority_id, title_search_key"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (
                todo_id,
                project_id,
                creator_user_id,
                assignee_user_id,
                title,
                description,
                description_format,
                status,
                stamp,
                stamp,
                start_date,
                due_date,
                priority_id,
                normalize_project_plan_key(title),
            ),
        )
        for state in ("project_todo_display_state", "project_todo_attachment_state"):
            conn.execute(
                "INSERT INTO "
                + state
                + "(project_id,todo_id,revision,updated_at) VALUES (?,?,1,?)",
                (project_id, todo_id, stamp),
            )
        self._replace_tags(conn, project_id, todo_id, requested_tags)
        payload = json.dumps(
            {
                "creator_user_id": creator_user_id,
                "assignee_user_id": assignee_user_id,
            }
        )
        if emit_event:
            _append_todo_event(
                conn, project_id, creator_user_id, EVENT_TODO_CREATED, todo_id, payload, stamp
            )
        row = self._select_todo(conn, project_id, todo_id, lock=False, catalog_revision=revision)
        if row is None:
            raise RuntimeError("inserted todo is missing")
        return row

    def create(
        self,
        *,
        project_id: str,
        creator_user_id: int,
        title: str,
        description: str = "",
        description_format: str = "plain",
        assignee_user_id: int | None = None,
        status: str = "todo",
        start_date: str | None = None,
        due_date: str | None = None,
        priority_id: str | None = None,
        tag_ids: Sequence[str] = (),
        expected_catalog_revision: Any = UNSET,
        timezone: str = "UTC",
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
                revision, priorities, tags = self._catalog_locked(conn, project_id)
                if (priority_id is not None or tag_ids) and expected_catalog_revision is UNSET:
                    raise _TodoConflict("invalid_catalog_revision")
                self._check_catalog_revision(revision, expected_catalog_revision)
                requested_tags = self._check_references(priorities, tags, priority_id, tag_ids)
                self._check_dates(start_date, due_date, None, timezone)
                row = self._insert_in_connection(
                    conn,
                    project_id=project_id,
                    todo_id=todo_id,
                    creator_user_id=creator_user_id,
                    assignee_user_id=assignee_user_id,
                    title=title,
                    description=description,
                    description_format=description_format,
                    status=status,
                    stamp=stamp,
                    start_date=start_date,
                    due_date=due_date,
                    priority_id=priority_id,
                    requested_tags=requested_tags,
                    revision=revision,
                )
        except _TodoConflict as conflict:
            return TodoMutation(outcome=conflict.outcome)
        return TodoMutation(outcome="created", row=row)

    def update(
        self,
        *,
        project_id: str,
        todo_id: str,
        actor_user_id: int,
        expected_version: int,
        title: Any = UNSET,
        description: Any = UNSET,
        description_format: Any = UNSET,
        status: Any = UNSET,
        assignee_user_id: Any = UNSET,
        start_date: Any = UNSET,
        due_date: Any = UNSET,
        priority_id: Any = UNSET,
        tag_ids: Any = UNSET,
        expected_catalog_revision: Any = UNSET,
        timezone: str = "UTC",
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
                revision, priorities, tags = self._catalog_locked(conn, project_id)
                self._lock_relationships(conn, project_id, [todo_id])
                todo = self._select_todo(
                    conn, project_id, todo_id, lock=True, catalog_revision=revision
                )
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
                has_refs = priority_id is not UNSET or tag_ids is not UNSET
                if has_refs != (expected_catalog_revision is not UNSET):
                    raise _TodoConflict("invalid_catalog_revision")
                self._check_catalog_revision(revision, expected_catalog_revision)
                requested_tags = self._check_references(
                    priorities, tags, priority_id, tag_ids, todo
                )
                merged_start = todo.start_date if start_date is UNSET else start_date
                merged_due = todo.due_date if due_date is UNSET else due_date
                self._check_dates(merged_start, merged_due, todo.due_date, timezone)
                # Keys below are the only ones interpolated into SET clauses.
                changes: dict[str, Any] = {}
                if title is not UNSET and str(title) != todo.title:
                    changes["title"] = str(title)
                if description is not UNSET and str(description) != todo.description:
                    changes["description"] = str(description)
                if description is not UNSET:
                    requested_format = (
                        "plain" if description_format is UNSET else str(description_format)
                    )
                    if requested_format != todo.description_format:
                        changes["description_format"] = requested_format
                if status is not UNSET and str(status) != todo.status:
                    changes["status"] = str(status)
                if assignee_user_id is not UNSET:
                    requested = requested_assignee
                    if requested != todo.assignee_user_id:
                        changes["assignee_user_id"] = requested
                for key, value, current_value in (
                    ("start_date", start_date, todo.start_date),
                    ("due_date", due_date, todo.due_date),
                    ("priority_id", priority_id, todo.priority_id),
                ):
                    if value is not UNSET and value != current_value:
                        changes[key] = value
                tags_changed = tag_ids is not UNSET and requested_tags != todo.tag_ids
                if not changes and not tags_changed:
                    raise _TodoConflict("no_change")

                count_parent = (
                    self._active_parent(conn, project_id, todo_id)
                    if "status" in changes
                    and ((todo.status == "done") != (changes["status"] == "done"))
                    else None
                )
                stored_changes = dict(changes)
                if "title" in changes:
                    stored_changes["title_search_key"] = normalize_project_plan_key(
                        changes["title"]
                    )
                set_sql = "".join(f"{col} = ?, " for col in stored_changes)
                updated = conn.execute(
                    f"UPDATE project_todos SET {set_sql}version = version + 1, updated_at = ? "
                    "WHERE project_id = ? AND todo_id = ? AND version = ? AND deleted_at IS NULL",
                    (*stored_changes.values(), stamp, project_id, todo_id, expected_version),
                )
                if getattr(updated, "rowcount", 1) != 1:
                    raise _TodoConflict(self._diagnose(conn, project_id, todo_id))
                locks.bump_display(conn, project_id, todo_id, stamp)
                if count_parent is not None:
                    locks.bump_display(conn, project_id, count_parent, stamp)
                if tags_changed:
                    self._replace_tags(conn, project_id, todo_id, requested_tags)
                payload = json.dumps(
                    {
                        "fields": sorted([*changes, *(["tag_ids"] if tags_changed else [])]),
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
                row = self._select_todo(
                    conn, project_id, todo_id, lock=False, catalog_revision=revision
                )
        except _TodoConflict as conflict:
            return TodoMutation(outcome=conflict.outcome)
        return TodoMutation(outcome="updated", row=row)

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
                revision, _priorities, _tags = self._catalog_locked(conn, project_id)
                self._lock_relationships(conn, project_id, [todo_id])
                todo = self._select_todo(
                    conn, project_id, todo_id, lock=True, catalog_revision=revision
                )
                if todo is None or todo.deleted_at is not None:
                    raise _TodoConflict("missing")
                if todo.version != expected_version:
                    raise _TodoConflict("stale")
                if role not in MANAGER_ROLES and todo.creator_user_id != actor_user_id:
                    raise _TodoConflict("forbidden")
                if todo.parent_todo_id is None and todo.children_count:
                    raise _TodoConflict("children_confirmation_required")
                count_parent = self._active_parent(conn, project_id, todo_id)
                deleted = conn.execute(
                    "UPDATE project_todos "
                    "SET deleted_at = ?, version = version + 1, updated_at = ? "
                    "WHERE project_id = ? AND todo_id = ? AND version = ? AND deleted_at IS NULL",
                    (stamp, stamp, project_id, todo_id, expected_version),
                )
                if getattr(deleted, "rowcount", 1) != 1:
                    raise _TodoConflict(self._diagnose(conn, project_id, todo_id))
                locks.bump_display(conn, project_id, todo_id, stamp)
                if count_parent is not None:
                    locks.bump_children(conn, project_id, count_parent, stamp)
                    locks.bump_hierarchy(conn, project_id, stamp)
                    locks.bump_display(conn, project_id, count_parent, stamp)
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
                revision, _priorities, _tags = self._catalog_locked(conn, project_id)
                fields_changed: list[str] = []
                if status is not UNSET:
                    fields_changed.append("status")
                if new_assignee is not UNSET:
                    fields_changed.append("assignee_user_id")
                event_fields = sorted(fields_changed)

                self._lock_relationships(conn, project_id, [item[0] for item in items])
                locked_todos: dict[str, ProjectTodoRow] = {}
                locked_rows: list[DbRow] = []
                for item_todo_id, expected_version in sorted(items):
                    raw = conn.execute(
                        _TODO_SELECT + "WHERE project_id = ? AND todo_id = ?" + self._lock_suffix(),
                        (project_id, item_todo_id),
                    ).fetchone()
                    if raw is None or raw["deleted_at"] is not None:
                        raise _TodoConflict("missing", item_todo_id)
                    if int(raw["version"]) != expected_version:
                        raise _TodoConflict("stale", item_todo_id)
                    locked_rows.append(raw)
                for raw in locked_rows:
                    todo = self._row_with_refs(conn, raw, revision)
                    locked_todos[todo.todo_id] = todo
                count_parents = {
                    parent
                    for todo in locked_todos.values()
                    if status is not UNSET
                    and ((todo.status == "done") != (status == "done"))
                    and (parent := self._active_parent(conn, project_id, todo.todo_id)) is not None
                }
                for item_todo_id, expected_version in sorted(items):
                    todo = locked_todos[item_todo_id]
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
                    locks.bump_display(conn, project_id, item_todo_id, stamp)
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
                for parent in sorted(count_parents - set(locked_todos)):
                    locks.bump_display(conn, project_id, parent, stamp)
                # Re-read inside the transaction so the response reflects it.
                for item_todo_id, _expected in items:
                    row = self._select_todo(
                        conn, project_id, item_todo_id, lock=False, catalog_revision=revision
                    )
                    if row is None:
                        raise _TodoConflict("missing", item_todo_id)
                    rows.append(row)
        except _TodoConflict as conflict:
            return BulkTodoMutation(outcome=conflict.outcome, todo_id=conflict.todo_id)
        return BulkTodoMutation(outcome="updated", rows=rows)

    @staticmethod
    def _collection_receipt(parent: ProjectTodoRow) -> dict[str, Any]:
        return {
            "children_revision": parent.children_revision,
            "parent_display_revision": parent.display_revision,
            "active_count": parent.children_count,
            "done_count": parent.done_children_count,
        }

    def _relationship_parent(
        self, conn: Any, project_id: str, parent_id: str, revision: int
    ) -> ProjectTodoRow:
        parent = self._select_todo(
            conn, project_id, parent_id, lock=False, catalog_revision=revision
        )
        if parent is None or parent.deleted_at is not None or parent.parent_todo_id is not None:
            raise _TodoConflict("missing")
        return parent

    def create_child(
        self,
        *,
        project_id: str,
        parent_todo_id: str,
        actor_user_id: int,
        expected_children_revision: int,
        client_request_id: str,
        request_fingerprint: str,
        fields: dict[str, Any],
        timezone: str = "UTC",
    ) -> RelationshipMutation:
        stamp = now_ts()
        try:
            with self._db.transaction() as conn:
                assignee = fields.get("assignee_user_id")
                roles = self._locked_member_roles(
                    conn, project_id, [actor_user_id] + ([] if assignee is None else [assignee])
                )
                role = roles.get(actor_user_id)
                if role is None:
                    raise _TodoConflict("not_member")
                revision, priorities, tags = self._catalog_locked(conn, project_id)
                self._lock_relationships(conn, project_id, [parent_todo_id])
                parent = self._relationship_parent(conn, project_id, parent_todo_id, revision)
                if role not in MANAGER_ROLES and actor_user_id not in (
                    parent.creator_user_id,
                    parent.assignee_user_id,
                ):
                    raise _TodoConflict("forbidden")
                if locks.project_row(self._db, conn, project_id, write=True)["archived"]:
                    raise _TodoConflict("project_archived")
                previous = conn.execute(
                    "SELECT * FROM project_todo_child_create_requests WHERE project_id=? AND parent_todo_id=? AND actor_user_id=? AND client_request_id=?"
                    + self._lock_suffix(),
                    (project_id, parent_todo_id, actor_user_id, client_request_id),
                ).fetchone()
                if previous is not None:
                    if previous["request_fingerprint"] != request_fingerprint:
                        raise _TodoConflict("idempotency_conflict")
                    child = self._select_todo(
                        conn,
                        project_id,
                        str(previous["child_todo_id"]),
                        lock=False,
                        catalog_revision=revision,
                    )
                    if (
                        previous["result_state"] != "recorded"
                        or child is None
                        or child.deleted_at is not None
                        or child.parent_todo_id != parent_todo_id
                    ):
                        raise _TodoConflict("child_result_invalidated")
                    return RelationshipMutation(
                        "replayed",
                        {**self._collection_receipt(parent), "item": child, "replayed": True},
                    )
                if parent.children_revision != expected_children_revision:
                    raise _TodoConflict("children_revision_conflict")
                retained = conn.execute(
                    "SELECT COUNT(*) FROM project_todo_children WHERE project_id=? AND parent_todo_id=?",
                    (project_id, parent_todo_id),
                ).fetchone()[0]
                if parent.children_count >= 100:
                    raise _TodoConflict("children_active_limit")
                if retained >= 500:
                    raise _TodoConflict("children_retained_limit")
                if assignee is not None:
                    if role not in MANAGER_ROLES and assignee != actor_user_id:
                        raise _TodoConflict("forbidden")
                    if assignee not in roles:
                        raise _TodoConflict("invalid_assignee")
                expected_catalog = fields.get("expected_catalog_revision", UNSET)
                if (
                    fields.get("priority_id") is not None or fields.get("tag_ids")
                ) and expected_catalog is UNSET:
                    raise _TodoConflict("invalid_catalog_revision")
                self._check_catalog_revision(revision, expected_catalog)
                requested_tags = self._check_references(
                    priorities, tags, fields.get("priority_id"), fields.get("tag_ids", [])
                )
                self._check_dates(fields.get("start_date"), fields.get("due_date"), None, timezone)
                child_id = new_ulid()
                self._insert_in_connection(
                    conn,
                    project_id=project_id,
                    todo_id=child_id,
                    creator_user_id=actor_user_id,
                    assignee_user_id=assignee,
                    title=fields["title"],
                    description=fields.get("description", ""),
                    description_format=fields.get("description_format", "plain"),
                    status=fields.get("status", "todo"),
                    stamp=stamp,
                    start_date=fields.get("start_date"),
                    due_date=fields.get("due_date"),
                    priority_id=fields.get("priority_id"),
                    requested_tags=requested_tags,
                    revision=revision,
                    emit_event=False,
                )
                conn.execute(
                    "INSERT INTO project_todo_children(project_id,parent_todo_id,child_todo_id,created_by,created_at) VALUES(?,?,?,?,?)",
                    (project_id, parent_todo_id, child_id, actor_user_id, stamp),
                )
                conn.execute(
                    "INSERT INTO project_todo_child_create_requests(project_id,parent_todo_id,actor_user_id,client_request_id,request_fingerprint,child_todo_id,created_at,result_state) VALUES(?,?,?,?,?,?,?,'recorded')",
                    (
                        project_id,
                        parent_todo_id,
                        actor_user_id,
                        client_request_id,
                        request_fingerprint,
                        child_id,
                        stamp,
                    ),
                )
                locks.bump_children(conn, project_id, parent_todo_id, stamp)
                locks.bump_hierarchy(conn, project_id, stamp)
                locks.bump_display(conn, project_id, parent_todo_id, stamp)
                _append_todo_event(
                    conn,
                    project_id,
                    actor_user_id,
                    EVENT_TODO_CREATED,
                    child_id,
                    json.dumps(
                        {
                            "creator_user_id": actor_user_id,
                            "assignee_user_id": assignee,
                            "parent_todo_id": parent_todo_id,
                        }
                    ),
                    stamp,
                )
                parent = self._relationship_parent(conn, project_id, parent_todo_id, revision)
                child = self._select_todo(
                    conn, project_id, child_id, lock=False, catalog_revision=revision
                )
                return RelationshipMutation(
                    "created",
                    {**self._collection_receipt(parent), "item": child, "replayed": False},
                )
        except _TodoConflict as error:
            return RelationshipMutation(error.outcome)

    def list_children(
        self,
        *,
        project_id: str,
        parent_todo_id: str,
        actor_user_id: int,
        limit: int,
        cursor: dict[str, Any] | None,
        invalid_cursor: bool = False,
    ) -> RelationshipMutation:
        try:
            with self._db.transaction() as conn:
                revision = self._read_revision(conn, project_id, actor_user_id)
                if revision is None:
                    raise _TodoConflict("not_member")
                parent = self._relationship_parent(conn, project_id, parent_todo_id, revision)
                if invalid_cursor:
                    raise _TodoConflict("invalid_cursor")
                params: list[Any] = [project_id, parent_todo_id]
                seek = ""
                if cursor is not None:
                    if (
                        cursor["project_id"] != project_id
                        or cursor["parent_todo_id"] != parent_todo_id
                        or cursor["children_revision"] != parent.children_revision
                        or cursor["parent_display_revision"] != parent.display_revision
                    ):
                        raise _TodoConflict("query_changed")
                    anchor = conn.execute(
                        "SELECT t.created_at FROM project_todos t JOIN project_todo_children r ON r.project_id=t.project_id AND r.child_todo_id=t.todo_id WHERE r.project_id=? AND r.parent_todo_id=? AND t.todo_id=? AND t.deleted_at IS NULL",
                        (project_id, parent_todo_id, cursor["last_todo_id"]),
                    ).fetchone()
                    if anchor is None or anchor["created_at"] != cursor["last_created_at"]:
                        raise _TodoConflict("query_changed")
                    seek = " AND (t.created_at>? OR (t.created_at=? AND t.todo_id>?))"
                    params.extend(
                        [
                            cursor["last_created_at"],
                            cursor["last_created_at"],
                            cursor["last_todo_id"],
                        ]
                    )
                raws = conn.execute(
                    "SELECT t.* FROM project_todos t JOIN project_todo_children r ON r.project_id=t.project_id AND r.child_todo_id=t.todo_id WHERE r.project_id=? AND r.parent_todo_id=? AND t.deleted_at IS NULL"
                    + seek
                    + " ORDER BY t.created_at,t.todo_id LIMIT ?",
                    [*params, limit + 1],
                ).fetchall()
                items = [self._row_with_refs(conn, row, revision) for row in raws[:limit]]
                next_value = None
                if len(raws) > limit:
                    last = items[-1]
                    next_value = {
                        "v": 1,
                        "project_id": project_id,
                        "parent_todo_id": parent_todo_id,
                        "last_created_at": last.created_at,
                        "last_todo_id": last.todo_id,
                        "children_revision": parent.children_revision,
                        "parent_display_revision": parent.display_revision,
                    }
                return RelationshipMutation(
                    "listed",
                    {
                        **self._collection_receipt(parent),
                        "items": items,
                        "limit": limit,
                        "has_more": len(raws) > limit,
                        "next_cursor": next_value,
                    },
                )
        except _TodoConflict as error:
            return RelationshipMutation(error.outcome)

    def delete_tree(
        self,
        *,
        project_id: str,
        parent_todo_id: str,
        actor_user_id: int,
        expected_version: int,
        expected_children_revision: int,
        children: Sequence[tuple[str, int]],
    ) -> RelationshipMutation:
        if len(children) > 100 or len({tid for tid, _ in children}) != len(children):
            raise ValueError("invalid child set")
        stamp = now_ts()
        try:
            with self._db.transaction() as conn:
                role = self._locked_member_roles(conn, project_id, [actor_user_id]).get(
                    actor_user_id
                )
                if role is None:
                    raise _TodoConflict("not_member")
                revision, _, _ = self._catalog_locked(conn, project_id)
                self._lock_relationships(conn, project_id, [parent_todo_id])
                parent = self._relationship_parent(conn, project_id, parent_todo_id, revision)
                if locks.project_row(self._db, conn, project_id, write=True)["archived"]:
                    raise _TodoConflict("project_archived")
                if parent.version != expected_version:
                    raise _TodoConflict("stale")
                if parent.children_revision != expected_children_revision:
                    raise _TodoConflict("children_revision_conflict")
                raw_children = conn.execute(
                    "SELECT c.* FROM project_todo_children r JOIN project_todos c ON c.project_id=r.project_id AND c.todo_id=r.child_todo_id WHERE r.project_id=? AND r.parent_todo_id=? AND c.deleted_at IS NULL ORDER BY c.todo_id",
                    (project_id, parent_todo_id),
                ).fetchall()
                if {str(row["todo_id"]) for row in raw_children} != {tid for tid, _ in children}:
                    raise _TodoConflict("children_revision_conflict")
                expected = dict(children)
                if any(
                    int(row["version"]) != expected[str(row["todo_id"])] for row in raw_children
                ):
                    raise _TodoConflict("stale")
                if role not in MANAGER_ROLES and (
                    parent.creator_user_id != actor_user_id
                    or any(row["creator_user_id"] != actor_user_id for row in raw_children)
                ):
                    raise _TodoConflict("forbidden")
                ids = [parent_todo_id, *[str(row["todo_id"]) for row in raw_children]]
                for tid in ids:
                    changed = conn.execute(
                        "UPDATE project_todos SET deleted_at=?,updated_at=?,version=version+1 WHERE project_id=? AND todo_id=? AND deleted_at IS NULL",
                        (stamp, stamp, project_id, tid),
                    )
                    if changed.rowcount != 1:
                        raise _TodoConflict("stale")
                    locks.bump_display(conn, project_id, tid, stamp)
                    _append_todo_event(
                        conn,
                        project_id,
                        actor_user_id,
                        EVENT_TODO_DELETED,
                        tid,
                        json.dumps({"fields": ["deleted_at"]}),
                        stamp,
                    )
                if raw_children:
                    locks.bump_children(conn, project_id, parent_todo_id, stamp)
                    locks.bump_hierarchy(conn, project_id, stamp)
                return RelationshipMutation(
                    "deleted",
                    {
                        "deleted_todo_ids": ids,
                        "children_revision": locks.relationship_projection(
                            conn, project_id, parent_todo_id
                        )[3],
                        "hierarchy_revision": locks.hierarchy_revision(conn, project_id),
                    },
                )
        except _TodoConflict as error:
            return RelationshipMutation(error.outcome)
