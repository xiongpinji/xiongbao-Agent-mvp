"""SQL-only shared todo view snapshots and versioned configuration writes."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos import project_plan_locks as locks
from octop.infra.db.repos._base import UNSET, now_ts
from octop.infra.utils.project_plan_keys import normalize_project_plan_key
from octop.infra.utils.ulid import new_ulid

EVENT_TODO_VIEW_UPDATED = "project.todo_view_updated"
_COLUMNS = (
    "view_id,project_id,name,name_key,view_type,definition_json,version,position,"
    "archived_at,created_at,updated_at"
)


@dataclass(frozen=True)
class StoredViewRow:
    view_id: str
    project_id: str
    name: str
    name_key: str
    view_type: str
    definition_json: str
    version: int
    position: int
    archived_at: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: Any) -> StoredViewRow:
        return cls(
            view_id=str(row["view_id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            name_key=str(row["name_key"]),
            view_type=str(row["view_type"]),
            definition_json=str(row["definition_json"]),
            version=int(row["version"]),
            position=int(row["position"]),
            archived_at=None if row["archived_at"] is None else int(row["archived_at"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class PlanReadSnapshot:
    project_id: str
    revision: int
    default_view_id: str
    catalog_revision: int
    items: list[StoredViewRow]
    member_roles: dict[int, str]
    priorities: list[Any] = field(default_factory=list)
    tags: list[Any] = field(default_factory=list)
    view: StoredViewRow | None = None
    project_archived: bool = False


class ViewTransactionConflict(Exception):
    """A retryable database conflict, without raw database details."""


@dataclass(frozen=True)
class ViewDefinition:
    """Already parsed, pure SQL data; the repo does not import the parser."""

    compatible_types: tuple[str, ...]
    definition_json: str
    assignee_ids: tuple[int, ...] = ()
    priority_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ViewMutation:
    outcome: str
    revision: int = 0
    default_view_id: str = ""
    item: StoredViewRow | None = None
    items: list[StoredViewRow] = field(default_factory=list)


class _ViewConflict(Exception):
    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


def _insert_event_in_connection(
    conn: Any,
    project_id: str,
    actor_user_id: int,
    view_id: str,
    version: int,
    revision: int,
    action: str,
    fields: list[str],
    ts: int,
) -> None:
    payload = {
        "view_id": view_id,
        "action": action,
        "version": version,
        "collection_revision": revision,
        "fields": fields,
    }
    conn.execute(
        "INSERT INTO project_events(project_id,actor_user_id,event_type,object_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        (project_id, actor_user_id, EVENT_TODO_VIEW_UPDATED, view_id, json.dumps(payload), ts),
    )


class ProjectTodoViewRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @property
    def max_integer(self) -> int:
        return 2**31 - 1 if self._db.dialect == "postgresql" else 2**63 - 1

    @contextmanager
    def _transaction(self, *, read: bool = False) -> Iterator[Any]:
        with self._db.transaction() as conn:
            if read and self._db.dialect == "postgresql":
                # This must precede even an observer's SELECT/PID query.
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            try:
                yield conn
            except BaseException as error:
                # SqlitePool handles Exception; interrupts need explicit rollback.
                if self._db.dialect == "sqlite" and not isinstance(error, Exception):
                    conn.rollback()
                raise

    def peek_view_in_connection(
        self, conn: Any, project_id: str, view_id: str
    ) -> StoredViewRow | None:
        """Internal only: no connection, transaction, ACL claim or HTTP response."""
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM project_todo_views WHERE project_id=? AND view_id=?",
            (project_id, view_id),
        ).fetchone()
        return None if row is None else StoredViewRow.from_row(row)

    def _snapshot_in_connection(
        self,
        conn: Any,
        project_id: str,
        actor_user_id: int,
        member_ids: Sequence[int],
        *,
        write: bool,
        view_id: str | None = None,
    ) -> PlanReadSnapshot | None:
        if type(actor_user_id) is not int or not 1 <= actor_user_id <= self.max_integer:
            return None
        if any(
            type(value) is not int or not 1 <= value <= self.max_integer for value in member_ids
        ):
            raise ValueError("invalid stored membership identifier")
        roles = locks.member_roles(
            self._db, conn, project_id, sorted(set(member_ids) | {actor_user_id}), write=write
        )
        if actor_user_id not in roles:
            return None
        project = locks.project_row(self._db, conn, project_id, write=write)
        if project is None:
            return None
        catalog_revision = locks.catalog_revision(self._db, conn, project_id, write=write)
        suffix = locks.lock_suffix(self._db, write=write)
        priorities = conn.execute(
            "SELECT * FROM project_todo_priorities WHERE project_id=? ORDER BY priority_id"
            + suffix,
            (project_id,),
        ).fetchall()
        tags = conn.execute(
            "SELECT * FROM project_todo_tags WHERE project_id=? ORDER BY tag_id" + suffix,
            (project_id,),
        ).fetchall()
        state = conn.execute(
            "SELECT revision,default_view_id FROM project_todo_view_state WHERE project_id=?"
            + suffix,
            (project_id,),
        ).fetchone()
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM project_todo_views WHERE project_id=? ORDER BY view_id"
            + suffix,
            (project_id,),
        ).fetchall()
        items = [StoredViewRow.from_row(row) for row in rows]
        active = [item for item in items if item.archived_at is None]
        if state is None or not active or len(active) > 30 or len(items) > 100:
            raise RuntimeError("project todo view state is incomplete")
        default = str(state["default_view_id"] or "")
        if default not in {item.view_id for item in active}:
            raise RuntimeError("project todo view default is invalid")
        items.sort(key=lambda item: (item.archived_at is not None, item.position, item.view_id))
        selected = next((item for item in items if item.view_id == view_id), None)
        return PlanReadSnapshot(
            project_id,
            int(state["revision"]),
            default,
            catalog_revision,
            items,
            roles,
            priorities,
            tags,
            selected,
            bool(project["archived"]),
        )

    def read_plan_snapshot_in_connection(
        self,
        conn: Any,
        project_id: str,
        actor_user_id: int,
        member_ids: Sequence[int],
        view_id: str | None = None,
    ) -> PlanReadSnapshot | None:
        """ACL/config/ref snapshot inside the caller's existing RR transaction."""
        return self._snapshot_in_connection(
            conn, project_id, actor_user_id, member_ids, write=False, view_id=view_id
        )

    def list_views(self, project_id: str, *, user_id: int) -> PlanReadSnapshot | None:
        try:
            with self._transaction(read=True) as conn:
                return self.read_plan_snapshot_in_connection(conn, project_id, user_id, ())
        except Exception as error:
            if getattr(error, "sqlstate", None) in ("40001", "40P01", "55P03"):
                raise ViewTransactionConflict from None
            raise

    def get_view(self, project_id: str, view_id: str, *, user_id: int) -> StoredViewRow | None:
        try:
            with self._transaction(read=True) as conn:
                snapshot = self.read_plan_snapshot_in_connection(
                    conn, project_id, user_id, (), view_id
                )
                return None if snapshot is None else snapshot.view
        except Exception as error:
            if getattr(error, "sqlstate", None) in ("40001", "40P01", "55P03"):
                raise ViewTransactionConflict from None
            raise

    def _run_write(
        self,
        actor_user_id: int,
        member_ids: Sequence[int],
        operation: Callable[[Any], ViewMutation],
    ) -> ViewMutation:
        # Check storage bounds before opening a transaction or binding any ID.
        if type(actor_user_id) is not int or not 1 <= actor_user_id <= self.max_integer:
            return ViewMutation("not_member")
        if any(
            type(value) is not int or not 1 <= value <= self.max_integer for value in member_ids
        ):
            return ViewMutation("invalid_assignee")
        try:
            with self._transaction() as conn:
                return operation(conn)
        except _ViewConflict as conflict:
            return ViewMutation(conflict.outcome)
        except Exception as error:
            if getattr(error, "sqlstate", None) in ("40001", "40P01", "55P03"):
                return ViewMutation("transaction_conflict")
            raise

    def _write_snapshot(
        self,
        conn: Any,
        project_id: str,
        actor_user_id: int,
        member_ids: Sequence[int],
    ) -> PlanReadSnapshot:
        snapshot = self._snapshot_in_connection(
            conn, project_id, actor_user_id, member_ids, write=True
        )
        if snapshot is None:
            raise _ViewConflict("not_member")
        return snapshot

    @staticmethod
    def _target(snapshot: PlanReadSnapshot, view_id: str) -> StoredViewRow:
        row = next((item for item in snapshot.items if item.view_id == view_id), None)
        if row is None:
            raise _ViewConflict("missing")
        return row

    @staticmethod
    def _authorize(snapshot: PlanReadSnapshot, actor_user_id: int) -> None:
        if snapshot.member_roles[actor_user_id] not in ("owner", "admin"):
            raise _ViewConflict("forbidden")
        if snapshot.project_archived:
            raise _ViewConflict("project_archived")

    @staticmethod
    def _check_name(snapshot: PlanReadSnapshot, key: str, view_id: str = "") -> None:
        if any(
            item.archived_at is None and item.name_key == key and item.view_id != view_id
            for item in snapshot.items
        ):
            raise _ViewConflict("name_conflict")

    @staticmethod
    def _check_references(
        snapshot: PlanReadSnapshot,
        definition: ViewDefinition,
        expected_catalog_revision: int | None,
    ) -> None:
        if snapshot.catalog_revision != expected_catalog_revision:
            raise _ViewConflict("catalog_revision_conflict")
        if any(user_id not in snapshot.member_roles for user_id in definition.assignee_ids):
            raise _ViewConflict("invalid_assignee")
        if not set(definition.priority_ids) <= {
            str(row["priority_id"]) for row in snapshot.priorities
        }:
            raise _ViewConflict("invalid_catalog_reference")
        if not set(definition.tag_ids) <= {str(row["tag_id"]) for row in snapshot.tags}:
            raise _ViewConflict("invalid_catalog_reference")

    def _next_integer(self, value: int) -> int:
        if value >= self.max_integer:
            raise _ViewConflict("transaction_conflict")
        return value + 1

    def _bump_state(
        self,
        conn: Any,
        snapshot: PlanReadSnapshot,
        stamp: int,
        default_view_id: str | None = None,
    ) -> tuple[int, str]:
        revision = self._next_integer(snapshot.revision)
        default = snapshot.default_view_id if default_view_id is None else default_view_id
        result = conn.execute(
            "UPDATE project_todo_view_state SET revision=?,default_view_id=?,updated_at=? WHERE project_id=? AND revision=?",
            (revision, default, stamp, snapshot.project_id, snapshot.revision),
        )
        if result.rowcount != 1:
            raise _ViewConflict("transaction_conflict")
        return revision, default

    def _item(self, conn: Any, project_id: str, view_id: str) -> StoredViewRow:
        item = self.peek_view_in_connection(conn, project_id, view_id)
        if item is None:
            raise RuntimeError("written view is missing")
        return item

    def create_view(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        expected_catalog_revision: int,
        name: str,
        view_type: str,
        definition: ViewDefinition,
        ts: int | None = None,
    ) -> ViewMutation:
        stamp = now_ts() if ts is None else ts

        def operation(conn: Any) -> ViewMutation:
            snapshot = self._write_snapshot(
                conn, project_id, actor_user_id, definition.assignee_ids
            )
            self._authorize(snapshot, actor_user_id)
            if snapshot.revision != expected_revision:
                raise _ViewConflict("view_revision_conflict")
            if view_type not in definition.compatible_types:
                raise _ViewConflict("invalid_definition")
            self._check_references(snapshot, definition, expected_catalog_revision)
            active = [item for item in snapshot.items if item.archived_at is None]
            if len(active) >= 30:
                raise _ViewConflict("active_limit")
            if len(snapshot.items) >= 100:
                raise _ViewConflict("total_limit")
            key = normalize_project_plan_key(name)
            self._check_name(snapshot, key)
            view_id = new_ulid()
            position = self._next_integer(max(item.position for item in active))
            conn.execute(
                "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,definition_json,version,position,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?)",
                (
                    view_id,
                    project_id,
                    name,
                    key,
                    view_type,
                    definition.definition_json,
                    position,
                    stamp,
                    stamp,
                ),
            )
            revision, default = self._bump_state(conn, snapshot, stamp)
            _insert_event_in_connection(
                conn,
                project_id,
                actor_user_id,
                view_id,
                1,
                revision,
                "created",
                ["name", "type", "definition"],
                stamp,
            )
            return ViewMutation("created", revision, default, self._item(conn, project_id, view_id))

        return self._run_write(actor_user_id, definition.assignee_ids, operation)

    def update_view(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        name: object = UNSET,
        view_type: object = UNSET,
        definition: ViewDefinition | None = None,
        expected_catalog_revision: int | None = None,
        ts: int | None = None,
    ) -> ViewMutation:
        stamp = now_ts() if ts is None else ts
        member_ids = () if definition is None else definition.assignee_ids

        def operation(conn: Any) -> ViewMutation:
            snapshot = self._write_snapshot(conn, project_id, actor_user_id, member_ids)
            current = self._target(snapshot, view_id)
            self._authorize(snapshot, actor_user_id)
            if current.version != expected_version:
                raise _ViewConflict("view_version_conflict")
            kind = current.view_type if view_type is UNSET else str(view_type)
            if kind != current.view_type and definition is None:
                raise _ViewConflict("invalid_definition")
            changes: dict[str, Any] = {}
            fields = []
            if name is not UNSET and name != current.name:
                key = normalize_project_plan_key(str(name))
                if current.archived_at is None:
                    self._check_name(snapshot, key, view_id)
                changes.update(name=name, name_key=key)
                fields.append("name")
            if kind != current.view_type:
                changes["view_type"] = kind
                fields.append("type")
            if definition is not None:
                if kind not in definition.compatible_types:
                    raise _ViewConflict("invalid_definition")
                self._check_references(snapshot, definition, expected_catalog_revision)
                if json.loads(current.definition_json) != json.loads(definition.definition_json):
                    changes["definition_json"] = definition.definition_json
                    fields.append("definition")
            if not changes:
                raise _ViewConflict("no_change")
            version = self._next_integer(current.version)
            changes.update(version=version, updated_at=stamp)
            conn.execute(
                "UPDATE project_todo_views SET "
                + ",".join(column + "=?" for column in changes)
                + " WHERE project_id=? AND view_id=?",
                [*changes.values(), project_id, view_id],
            )
            revision, default = self._bump_state(conn, snapshot, stamp)
            _insert_event_in_connection(
                conn,
                project_id,
                actor_user_id,
                view_id,
                version,
                revision,
                "updated",
                fields,
                stamp,
            )
            return ViewMutation("updated", revision, default, self._item(conn, project_id, view_id))

        return self._run_write(actor_user_id, member_ids, operation)

    def order_views(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        view_ids: list[str],
        ts: int | None = None,
    ) -> ViewMutation:
        stamp = now_ts() if ts is None else ts

        def operation(conn: Any) -> ViewMutation:
            snapshot = self._write_snapshot(conn, project_id, actor_user_id, ())
            self._authorize(snapshot, actor_user_id)
            if snapshot.revision != expected_revision:
                raise _ViewConflict("view_revision_conflict")
            active = {item.view_id: item for item in snapshot.items if item.archived_at is None}
            if len(view_ids) != len(set(view_ids)) or set(view_ids) != set(active):
                raise _ViewConflict("invalid_order")
            changed = [
                (index, active[value])
                for index, value in enumerate(view_ids)
                if active[value].position != index
            ]
            if not changed:
                raise _ViewConflict("no_change")
            for position, item in changed:
                conn.execute(
                    "UPDATE project_todo_views SET position=?,version=?,updated_at=? WHERE project_id=? AND view_id=?",
                    (position, self._next_integer(item.version), stamp, project_id, item.view_id),
                )
            revision, default = self._bump_state(conn, snapshot, stamp)
            for _, item in changed:
                _insert_event_in_connection(
                    conn,
                    project_id,
                    actor_user_id,
                    item.view_id,
                    item.version + 1,
                    revision,
                    "ordered",
                    ["order"],
                    stamp,
                )
            items = [self._item(conn, project_id, value) for value in view_ids]
            return ViewMutation("ordered", revision, default, items=items)

        return self._run_write(actor_user_id, (), operation)

    def set_default_view(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        view_id: str,
        ts: int | None = None,
    ) -> ViewMutation:
        stamp = now_ts() if ts is None else ts

        def operation(conn: Any) -> ViewMutation:
            snapshot = self._write_snapshot(conn, project_id, actor_user_id, ())
            current = self._target(snapshot, view_id)
            self._authorize(snapshot, actor_user_id)
            if snapshot.revision != expected_revision:
                raise _ViewConflict("view_revision_conflict")
            if current.archived_at is not None:
                raise _ViewConflict("invalid_lifecycle")
            if view_id == snapshot.default_view_id:
                raise _ViewConflict("no_change")
            revision, default = self._bump_state(conn, snapshot, stamp, view_id)
            _insert_event_in_connection(
                conn,
                project_id,
                actor_user_id,
                view_id,
                current.version,
                revision,
                "default_changed",
                ["default_view_id"],
                stamp,
            )
            return ViewMutation("default_changed", revision, default)

        return self._run_write(actor_user_id, (), operation)

    def _set_archived(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        archived: bool,
        ts: int | None,
    ) -> ViewMutation:
        stamp = now_ts() if ts is None else ts
        action = "archived" if archived else "restored"

        def operation(conn: Any) -> ViewMutation:
            snapshot = self._write_snapshot(conn, project_id, actor_user_id, ())
            current = self._target(snapshot, view_id)
            self._authorize(snapshot, actor_user_id)
            if current.version != expected_version:
                raise _ViewConflict("view_version_conflict")
            if (current.archived_at is not None) == archived:
                raise _ViewConflict("invalid_lifecycle")
            active = [item for item in snapshot.items if item.archived_at is None]
            default, fields = snapshot.default_view_id, ["archived"]
            position = current.position
            if archived:
                remaining = [item for item in active if item.view_id != view_id]
                if not remaining:
                    raise _ViewConflict("last_active_view")
                if default == view_id:
                    default = min(remaining, key=lambda item: (item.position, item.view_id)).view_id
                    fields.append("default_view_id")
            else:
                if len(active) >= 30:
                    raise _ViewConflict("active_limit")
                self._check_name(snapshot, current.name_key, view_id)
                position = self._next_integer(max(item.position for item in active))
            version = self._next_integer(current.version)
            conn.execute(
                "UPDATE project_todo_views SET archived_at=?,position=?,version=?,updated_at=? WHERE project_id=? AND view_id=?",
                (stamp if archived else None, position, version, stamp, project_id, view_id),
            )
            revision, default = self._bump_state(conn, snapshot, stamp, default)
            _insert_event_in_connection(
                conn, project_id, actor_user_id, view_id, version, revision, action, fields, stamp
            )
            return ViewMutation(action, revision, default, self._item(conn, project_id, view_id))

        return self._run_write(actor_user_id, (), operation)

    def archive_view(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        ts: int | None = None,
    ) -> ViewMutation:
        return self._set_archived(
            project_id,
            view_id,
            actor_user_id=actor_user_id,
            expected_version=expected_version,
            archived=True,
            ts=ts,
        )

    def restore_view(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        ts: int | None = None,
    ) -> ViewMutation:
        return self._set_archived(
            project_id,
            view_id,
            actor_user_id=actor_user_id,
            expected_version=expected_version,
            archived=False,
            ts=ts,
        )
