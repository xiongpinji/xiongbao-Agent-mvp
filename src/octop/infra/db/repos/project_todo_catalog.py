"""Project-owned todo catalogs, optimistic revisions and atomic safe events."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos import project_plan_locks as locks
from octop.infra.db.repos._base import UNSET, now_ts
from octop.infra.utils.ulid import new_ulid

EVENT_TODO_CATALOG_UPDATED = "project.todo_catalog_updated"
_TABLES = {
    "priority": ("project_todo_priorities", "priority_id"),
    "tag": ("project_todo_tags", "tag_id"),
}
_LIMITS = {"priority": (32, 128), "tag": (100, 500)}


@dataclass(frozen=True)
class CatalogOptionRow:
    option_id: str
    name: str
    name_key: str
    color: str
    position: int | None
    archived_at: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: Any, kind: str) -> CatalogOptionRow:
        return cls(
            option_id=str(row[_TABLES[kind][1]]),
            name=str(row["name"]),
            name_key=str(row["name_key"]),
            color=str(row["color"]),
            position=int(row["position"]) if kind == "priority" else None,
            archived_at=None if row["archived_at"] is None else int(row["archived_at"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class CatalogSnapshot:
    project_id: str
    revision: int
    priorities: list[CatalogOptionRow]
    tags: list[CatalogOptionRow]


@dataclass(frozen=True)
class CatalogMutation:
    outcome: str
    revision: int = 0
    item: CatalogOptionRow | None = None
    priorities: list[CatalogOptionRow] = field(default_factory=list)


class _CatalogConflict(Exception):
    def __init__(self, outcome: str, revision: int = 0) -> None:
        super().__init__(outcome)
        self.outcome, self.revision = outcome, revision


def _append_catalog_event(
    conn: Any,
    project_id: str,
    actor_user_id: int,
    revision: int,
    kind: str,
    option_id: str | None,
    action: str,
    fields: list[str],
    ts: int,
) -> None:
    payload: dict[str, Any] = {
        "catalog_revision": revision,
        "catalog_kind": kind,
        "action": action,
        "fields": fields,
    }
    if option_id is not None:
        payload["option_id"] = option_id
    conn.execute(
        "INSERT INTO project_events(project_id,actor_user_id,event_type,object_id,payload_json,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            project_id,
            actor_user_id,
            EVENT_TODO_CATALOG_UPDATED,
            option_id or project_id,
            json.dumps(payload),
            ts,
        ),
    )


class ProjectTodoCatalogRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @staticmethod
    def _sort(items: list[CatalogOptionRow], kind: str) -> list[CatalogOptionRow]:
        return sorted(
            items,
            key=lambda item: (
                item.archived_at is not None,
                item.position if kind == "priority" else item.name_key,
                item.option_id,
            ),
        )

    def get_catalog(self, project_id: str, *, user_id: int) -> CatalogSnapshot | None:
        with self._db.transaction() as conn:
            if user_id not in locks.member_roles(
                self._db, conn, project_id, [user_id], write=False
            ):
                return None
            if locks.project_row(self._db, conn, project_id, write=False) is None:
                return None
            revision = locks.catalog_revision(self._db, conn, project_id, write=False)
            priorities = conn.execute(
                "SELECT * FROM project_todo_priorities WHERE project_id = ?", (project_id,)
            ).fetchall()
            tags = conn.execute(
                "SELECT * FROM project_todo_tags WHERE project_id = ?", (project_id,)
            ).fetchall()
            return CatalogSnapshot(
                project_id=project_id,
                revision=revision,
                priorities=self._sort(
                    [CatalogOptionRow.from_row(row, "priority") for row in priorities], "priority"
                ),
                tags=self._sort([CatalogOptionRow.from_row(row, "tag") for row in tags], "tag"),
            )

    def _prepare(
        self,
        conn: Any,
        project_id: str,
        actor_user_id: int,
        expected_revision: int,
    ) -> tuple[int, dict[str, list[CatalogOptionRow]]]:
        role = locks.member_roles(self._db, conn, project_id, [actor_user_id], write=True).get(
            actor_user_id
        )
        if role is None:
            raise _CatalogConflict("not_member")
        if role not in ("owner", "admin"):
            raise _CatalogConflict("forbidden")
        project = locks.project_row(self._db, conn, project_id, write=True)
        if project is None:
            raise _CatalogConflict("not_member")
        if int(project["archived"]):
            raise _CatalogConflict("project_archived")
        revision = locks.catalog_revision(self._db, conn, project_id, write=True)
        if revision != expected_revision:
            raise _CatalogConflict("stale", revision)
        priorities, tags = locks.catalog_items(self._db, conn, project_id)
        return revision, {
            "priority": self._sort(
                [CatalogOptionRow.from_row(row, "priority") for row in priorities], "priority"
            ),
            "tag": self._sort([CatalogOptionRow.from_row(row, "tag") for row in tags], "tag"),
        }

    @staticmethod
    def _find(items: list[CatalogOptionRow], option_id: str) -> CatalogOptionRow:
        for item in items:
            if item.option_id == option_id:
                return item
        raise _CatalogConflict("missing")

    @staticmethod
    def _check_name(items: list[CatalogOptionRow], name_key: str, option_id: str = "") -> None:
        if any(
            item.archived_at is None and item.name_key == name_key and item.option_id != option_id
            for item in items
        ):
            raise _CatalogConflict("name_conflict")

    @staticmethod
    def _check_limit(items: list[CatalogOptionRow], kind: str, *, creating: bool) -> None:
        active_limit, total_limit = _LIMITS[kind]
        if creating and len(items) >= total_limit:
            raise _CatalogConflict("total_limit")
        if sum(item.archived_at is None for item in items) >= active_limit:
            raise _CatalogConflict("active_limit")

    @staticmethod
    def _bump(conn: Any, project_id: str, revision: int, ts: int) -> int:
        conn.execute(
            "UPDATE project_todo_catalog_state SET revision = ?, updated_at = ? WHERE project_id = ?",
            (revision + 1, ts, project_id),
        )
        return revision + 1

    @staticmethod
    def _read_item(conn: Any, project_id: str, kind: str, option_id: str) -> CatalogOptionRow:
        table, id_column = _TABLES[kind]
        row = conn.execute(
            f"SELECT * FROM {table} WHERE project_id = ? AND {id_column} = ?",
            (project_id, option_id),
        ).fetchone()
        return CatalogOptionRow.from_row(row, kind)

    def create_option(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        expected_revision: int,
        kind: str,
        name: str,
        color: str,
        ts: int | None = None,
    ) -> CatalogMutation:
        table, id_column = _TABLES[kind]
        stamp, option_id = now_ts() if ts is None else ts, new_ulid()
        name_key = unicodedata.normalize("NFKC", name).casefold()
        try:
            with self._db.transaction() as conn:
                revision, catalog = self._prepare(
                    conn, project_id, actor_user_id, expected_revision
                )
                items = catalog[kind]
                self._check_limit(items, kind, creating=True)
                self._check_name(items, name_key)
                columns = f"{id_column},project_id,name,name_key,color,created_at,updated_at"
                values: list[Any] = [option_id, project_id, name, name_key, color, stamp, stamp]
                if kind == "priority":
                    columns += ",position"
                    values.append(sum(item.archived_at is None for item in items))
                conn.execute(
                    f"INSERT INTO {table}({columns}) VALUES ({','.join('?' for _ in values)})",
                    values,
                )
                revision = self._bump(conn, project_id, revision, stamp)
                _append_catalog_event(
                    conn,
                    project_id,
                    actor_user_id,
                    revision,
                    kind,
                    option_id,
                    "created",
                    ["name", "color"],
                    stamp,
                )
                item = self._read_item(conn, project_id, kind, option_id)
            return CatalogMutation("created", revision, item)
        except _CatalogConflict as conflict:
            return CatalogMutation(conflict.outcome, conflict.revision)

    def update_option(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        expected_revision: int,
        kind: str,
        option_id: str,
        name: Any = UNSET,
        color: Any = UNSET,
        ts: int | None = None,
    ) -> CatalogMutation:
        table, id_column = _TABLES[kind]
        stamp = now_ts() if ts is None else ts
        try:
            with self._db.transaction() as conn:
                revision, catalog = self._prepare(
                    conn, project_id, actor_user_id, expected_revision
                )
                current = self._find(catalog[kind], option_id)
                changes: dict[str, Any] = {}
                if name is not UNSET and name != current.name:
                    changes["name"] = name
                    changes["name_key"] = unicodedata.normalize("NFKC", name).casefold()
                    if current.archived_at is None:
                        self._check_name(catalog[kind], changes["name_key"], option_id)
                if color is not UNSET and color != current.color:
                    changes["color"] = color
                if not changes:
                    raise _CatalogConflict("no_change")
                conn.execute(
                    f"UPDATE {table} SET {', '.join(key + ' = ?' for key in changes)},updated_at = ? WHERE project_id = ? AND {id_column} = ?",
                    (*changes.values(), stamp, project_id, option_id),
                )
                revision = self._bump(conn, project_id, revision, stamp)
                _append_catalog_event(
                    conn,
                    project_id,
                    actor_user_id,
                    revision,
                    kind,
                    option_id,
                    "updated",
                    sorted(key for key in changes if key != "name_key"),
                    stamp,
                )
                item = self._read_item(conn, project_id, kind, option_id)
            return CatalogMutation("updated", revision, item)
        except _CatalogConflict as conflict:
            return CatalogMutation(conflict.outcome, conflict.revision)

    def set_archived(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        expected_revision: int,
        kind: str,
        option_id: str,
        archived: bool,
        ts: int | None = None,
    ) -> CatalogMutation:
        table, id_column = _TABLES[kind]
        stamp = now_ts() if ts is None else ts
        try:
            with self._db.transaction() as conn:
                revision, catalog = self._prepare(
                    conn, project_id, actor_user_id, expected_revision
                )
                current = self._find(catalog[kind], option_id)
                if (current.archived_at is not None) == archived:
                    raise _CatalogConflict("invalid_lifecycle")
                if not archived:
                    self._check_limit(catalog[kind], kind, creating=False)
                    self._check_name(catalog[kind], current.name_key, option_id)
                position = sum(item.archived_at is None for item in catalog[kind])
                extra = ",position = ?" if kind == "priority" and not archived else ""
                params: list[Any] = [stamp if archived else None, stamp]
                if extra:
                    params.append(position)
                conn.execute(
                    f"UPDATE {table} SET archived_at = ?,updated_at = ?{extra} WHERE project_id = ? AND {id_column} = ?",
                    (*params, project_id, option_id),
                )
                if kind == "priority" and archived:
                    remaining = [
                        item
                        for item in catalog[kind]
                        if item.archived_at is None and item.option_id != option_id
                    ]
                    for index, item in enumerate(remaining):
                        conn.execute(
                            "UPDATE project_todo_priorities SET position = ?,updated_at = ? WHERE project_id = ? AND priority_id = ?",
                            (index, stamp, project_id, item.option_id),
                        )
                revision = self._bump(conn, project_id, revision, stamp)
                action = "archived" if archived else "restored"
                _append_catalog_event(
                    conn,
                    project_id,
                    actor_user_id,
                    revision,
                    kind,
                    option_id,
                    action,
                    ["archived"],
                    stamp,
                )
                item = self._read_item(conn, project_id, kind, option_id)
            return CatalogMutation(action, revision, item)
        except _CatalogConflict as conflict:
            return CatalogMutation(conflict.outcome, conflict.revision)

    def order_priorities(
        self,
        *,
        project_id: str,
        actor_user_id: int,
        expected_revision: int,
        priority_ids: list[str],
        ts: int | None = None,
    ) -> CatalogMutation:
        stamp = now_ts() if ts is None else ts
        try:
            with self._db.transaction() as conn:
                revision, catalog = self._prepare(
                    conn, project_id, actor_user_id, expected_revision
                )
                active = [
                    item.option_id for item in catalog["priority"] if item.archived_at is None
                ]
                if len(priority_ids) != len(set(priority_ids)) or set(priority_ids) != set(active):
                    raise _CatalogConflict("invalid_order")
                if priority_ids == active:
                    raise _CatalogConflict("no_change")
                for index, option_id in enumerate(priority_ids):
                    conn.execute(
                        "UPDATE project_todo_priorities SET position = ?,updated_at = ? WHERE project_id = ? AND priority_id = ?",
                        (index, stamp, project_id, option_id),
                    )
                revision = self._bump(conn, project_id, revision, stamp)
                _append_catalog_event(
                    conn,
                    project_id,
                    actor_user_id,
                    revision,
                    "priority",
                    None,
                    "ordered",
                    ["order"],
                    stamp,
                )
                priorities = [
                    self._read_item(conn, project_id, "priority", option_id)
                    for option_id in priority_ids
                ]
            return CatalogMutation("ordered", revision, priorities=priorities)
        except _CatalogConflict as conflict:
            return CatalogMutation(conflict.outcome, conflict.revision)
