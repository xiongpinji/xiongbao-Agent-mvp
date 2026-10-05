"""SQL-only, bounded project plan queries in the caller's read snapshot."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos import project_plan_locks as locks
from octop.infra.db.repos.project_todo_views import StoredViewRow
from octop.infra.db.repos.project_todos import ProjectTodoRow
from octop.infra.utils.project_plan_keys import normalize_project_plan_key


@dataclass(frozen=True)
class MemberMetadata:
    user_id: int
    display_name: str
    sort_key: str


@dataclass(frozen=True)
class QueryPage:
    rows: list[ProjectTodoRow]
    total: int
    matched_total: int
    unscheduled_total: int | None
    groups: list[dict[str, Any]]
    has_more: bool


class QueryAnchorChanged(Exception):
    """The compact anchor no longer belongs to this exact SQL query."""


@dataclass(frozen=True)
class _SortPart:
    expression: str
    direction: str


def _nullable_predicate(column: str, op: str, values: Sequence[Any]) -> tuple[str, list[Any]]:
    nonnull = [value for value in values if value is not None]
    clauses = []
    if nonnull:
        clauses.append(f"{column} IN ({','.join('?' for _ in nonnull)})")
    if None in values:
        clauses.append(f"{column} IS NULL")
    # COALESCE converts SQL UNKNOWN for NULL into false before complementing.
    predicate = "COALESCE((" + " OR ".join(clauses) + "), FALSE)"
    return (f"NOT ({predicate})" if op == "not_in" else predicate), nonnull


class ProjectPlanQueryRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @property
    def max_integer(self) -> int:
        return 2**31 - 1 if self._db.dialect == "postgresql" else 2**63 - 1

    @contextmanager
    def read_transaction(self) -> Iterator[Any]:
        with self._db.transaction() as conn:
            if self._db.dialect == "postgresql":
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            try:
                yield conn
            except BaseException as error:
                if self._db.dialect == "sqlite" and not isinstance(error, Exception):
                    conn.rollback()
                raise

    def bootstrap_in_connection(
        self, conn: Any, project_id: str, actor_user_id: int, view_id: str
    ) -> StoredViewRow | None:
        row = conn.execute(
            "SELECT v.* FROM project_todo_views v "
            "JOIN project_spaces p ON p.project_id=v.project_id "
            "JOIN project_members m ON m.project_id=p.project_id AND m.user_id=? "
            "WHERE v.project_id=? AND v.view_id=? AND v.archived_at IS NULL",
            (actor_user_id, project_id, view_id),
        ).fetchone()
        return None if row is None else StoredViewRow.from_row(row)

    def members_in_connection(self, conn: Any, project_id: str) -> list[MemberMetadata]:
        rows = conn.execute(
            "SELECT m.user_id, COALESCE(NULLIF(u.display_name,''),u.username) AS display_name, "
            "u.project_plan_display_sort_key AS sort_key "
            "FROM project_members m JOIN users u ON u.id=m.user_id "
            "WHERE m.project_id=? ORDER BY m.user_id",
            (project_id,),
        ).fetchall()
        return [
            MemberMetadata(int(row["user_id"]), str(row["display_name"]), str(row["sort_key"]))
            for row in rows
        ]

    def can_read_project(self, project_id: str, actor_user_id: int) -> bool:
        # This is the sole fresh, ACL-only read after the failed query rolled back.
        with self._db.transaction() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM project_spaces p JOIN project_members m ON m.project_id=p.project_id "
                    "WHERE p.project_id=? AND m.user_id=?",
                    (project_id, actor_user_id),
                ).fetchone()
                is not None
            )

    def _filters(self, conditions: Sequence[dict[str, Any]], today: str) -> tuple[str, list[Any]]:
        predicates, params = [], []
        for condition in conditions:
            field, op = condition["field"], condition["op"]
            if field in ("assignee", "priority"):
                column = "t.assignee_user_id" if field == "assignee" else "t.priority_id"
                predicate, values = _nullable_predicate(column, op, condition["values"])
            elif field == "tags" and op in ("any", "all", "none_of"):
                values = condition["values"]
                exists = (
                    "EXISTS (SELECT 1 FROM project_todo_tag_links ref "
                    "WHERE ref.project_id=t.project_id AND ref.todo_id=t.todo_id AND "
                )
                if op in ("any", "none_of"):
                    predicate = exists + f"ref.tag_id IN ({','.join('?' for _ in values)}))"
                    if op == "none_of":
                        predicate = "NOT " + predicate
                else:
                    predicate = " AND ".join(exists + "ref.tag_id=?)" for _ in values)
            elif field == "tags":
                predicate = (
                    "EXISTS (SELECT 1 FROM project_todo_tag_links ref "
                    "WHERE ref.project_id=t.project_id AND ref.todo_id=t.todo_id)"
                )
                if op == "is_empty":
                    predicate = "NOT " + predicate
                values = []
            elif field == "title":
                key = normalize_project_plan_key(condition["value"])
                if "\x00" in key:
                    # SQLite LIKE truncates its pattern at NUL; instr does not.
                    # PostgreSQL TEXT cannot contain NUL, so no text bind is needed.
                    if self._db.dialect == "postgresql":
                        predicate, values = ("TRUE" if op == "not_contains" else "FALSE"), []
                    else:
                        predicate = "instr(t.title_search_key,?)" + (
                            "=0" if op == "not_contains" else ">0"
                        )
                        values = [key]
                else:
                    literal = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    column = "t.title_search_key"
                    if self._db.dialect == "postgresql":
                        column += ' COLLATE "C"'
                    predicate = column + (
                        " NOT LIKE ? ESCAPE '\\'" if op == "not_contains" else " LIKE ? ESCAPE '\\'"
                    )
                    values = ["%" + literal + "%"]
            elif field == "status":
                predicate, values = _nullable_predicate("t.status", op, condition["values"])
            elif field == "source":
                predicate, values = ("TRUE" if op == "in" else "FALSE"), []
            elif field in ("start_date", "due_date"):
                column = "t." + field
                if op == "overdue":
                    predicate = "COALESCE((t.due_date < ? AND t.status <> 'done'), FALSE)"
                    if condition["value"] is False:
                        predicate = "NOT (" + predicate + ")"
                    values = [today]
                elif op in ("is_empty", "not_empty"):
                    predicate = column + (" IS NULL" if op == "is_empty" else " IS NOT NULL")
                    values = []
                elif op == "between":
                    predicate, values = column + " BETWEEN ? AND ?", condition["values"]
                else:
                    predicate = column + {"on": " = ?", "before": " < ?", "after": " > ?"}[op]
                    values = [condition["value"]]
            else:
                raise NotImplementedError("project plan filter is not implemented yet")
            predicates.append("(" + predicate + ")")
            params.extend(values)
        return (" AND ".join(predicates) if predicates else "TRUE"), params

    @staticmethod
    def _group_predicate(group_key: dict[str, Any] | None) -> tuple[str, list[Any]]:
        if group_key is None or group_key["kind"] == "source":
            return "TRUE", []
        kind, value = group_key["kind"], group_key["id"]
        if kind == "tag":
            exists = (
                "EXISTS (SELECT 1 FROM project_todo_tag_links g "
                "WHERE g.project_id=t.project_id AND g.todo_id=t.todo_id"
            )
            return (
                ("NOT " + exists + ")", [])
                if value is None
                else (exists + " AND g.tag_id=?)", [value])
            )
        column = {
            "status": "t.status",
            "assignee": "t.assignee_user_id",
            "priority": "t.priority_id",
        }[kind]
        if value is None:
            return column + " IS NULL", []
        return column + "=?", [int(value) if kind == "assignee" else value]

    def _groups_in_connection(
        self,
        conn: Any,
        where: str,
        params: list[Any],
        group_by: str | None,
        *,
        members: Sequence[MemberMetadata],
        priorities: Sequence[Any],
        tags: Sequence[Any],
        total: int,
    ) -> list[dict[str, Any]]:
        if group_by is None:
            return []
        if group_by == "source":
            return [{"key": {"kind": "source", "id": "manual"}, "count": total}]
        if group_by == "tag":
            counts = conn.execute(
                "SELECT r.tag_id AS group_id,COUNT(DISTINCT t.todo_id) AS n "
                "FROM project_todos t JOIN project_todo_tag_links r "
                "ON r.project_id=t.project_id AND r.todo_id=t.todo_id WHERE "
                + where
                + " GROUP BY r.tag_id",
                params,
            ).fetchall()
            empty = conn.execute(
                "SELECT COUNT(*) FROM project_todos t WHERE "
                + where
                + " AND NOT EXISTS (SELECT 1 FROM project_todo_tag_links r "
                "WHERE r.project_id=t.project_id AND r.todo_id=t.todo_id)",
                params,
            ).fetchone()[0]
        else:
            column = {
                "status": "t.status",
                "assignee": "t.assignee_user_id",
                "priority": "t.priority_id",
            }[group_by]
            counts = conn.execute(
                "SELECT " + column + " AS group_id,COUNT(*) AS n "
                "FROM project_todos t WHERE " + where + " GROUP BY " + column,
                params,
            ).fetchall()
            empty = 0
        mapped = {
            None if row["group_id"] is None else str(row["group_id"]): int(row["n"])
            for row in counts
        }
        if group_by == "status":
            ids: list[str | None] = ["todo", "in_progress", "done"]
        elif group_by == "assignee":
            ids = [str(member.user_id) for member in members] + [None]
        else:
            catalog, column = (
                (priorities, "priority_id") if group_by == "priority" else (tags, "tag_id")
            )
            ordered = sorted(
                catalog,
                key=lambda row: (
                    row["archived_at"] is not None,
                    int(row["position"]) if group_by == "priority" else str(row["name_key"]),
                    str(row[column]),
                ),
            )
            ids = [
                str(row[column])
                for row in ordered
                if row["archived_at"] is None or mapped.get(str(row[column]), 0) > 0
            ] + [None]
        if group_by == "tag":
            mapped[None] = int(empty)
        return [
            {"key": {"kind": group_by, "id": value}, "count": mapped.get(value, 0)} for value in ids
        ]

    @staticmethod
    def _date_predicate(
        view_type: str,
        definition: dict[str, Any],
        window: dict[str, Any] | None,
        bucket: str | None,
    ) -> tuple[str, list[Any], str | None]:
        if view_type not in ("calendar", "gantt"):
            return "TRUE", [], None
        if window is None:
            raise ValueError("query date window is missing")
        if view_type == "calendar":
            column = {"start_date": "t.start_date", "due_date": "t.due_date"}[
                definition["calendar"]["date_basis"]
            ]
            unscheduled = column + " IS NULL"
            scheduled = column + " BETWEEN ? AND ?"
            values = [window["start_date"], window["end_date"]]
        else:
            unscheduled = "(t.start_date IS NULL AND t.due_date IS NULL)"
            scheduled = "(COALESCE(t.start_date,t.due_date) <= ? AND COALESCE(t.due_date,t.start_date) >= ?)"
            values = [window["end_date"], window["start_date"]]
        return (
            (unscheduled, [], unscheduled)
            if bucket == "unscheduled"
            else (scheduled, values, unscheduled)
        )

    def _sort_parts(self, sorts: Sequence[dict[str, Any]]) -> list[_SortPart]:
        collation = '"C"' if self._db.dialect == "postgresql" else "BINARY"

        def text(expression: str) -> str:
            return expression + " COLLATE " + collation

        parts = []
        for item in sorts or [{"field": "updated_at", "direction": "desc"}]:
            field, direction = item["field"], {"asc": "ASC", "desc": "DESC"}[item["direction"]]
            if field == "title":
                parts.append(_SortPart(text("t.title_search_key"), direction))
            elif field == "status":
                parts.append(
                    _SortPart(
                        "CASE t.status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END",
                        direction,
                    )
                )
            elif field == "assignee":
                parts.extend(
                    [
                        _SortPart("CASE WHEN t.assignee_user_id IS NULL THEN 1 ELSE 0 END", "ASC"),
                        _SortPart(text("COALESCE(u.project_plan_display_sort_key,'')"), direction),
                        _SortPart("COALESCE(t.assignee_user_id,0)", "ASC"),
                    ]
                )
            elif field == "priority":
                parts.extend(
                    [
                        _SortPart(
                            "CASE WHEN t.priority_id IS NULL THEN 2 WHEN p.archived_at IS NULL THEN 0 ELSE 1 END",
                            "ASC",
                        ),
                        _SortPart("COALESCE(p.position,0)", direction),
                        _SortPart(text("COALESCE(p.name_key,'')"), direction),
                        _SortPart(text("COALESCE(t.priority_id,'')"), "ASC"),
                    ]
                )
            elif field in ("start_date", "due_date"):
                column = {"start_date": "t.start_date", "due_date": "t.due_date"}[field]
                parts.extend(
                    [
                        _SortPart(f"CASE WHEN {column} IS NULL THEN 1 ELSE 0 END", "ASC"),
                        _SortPart(text(f"COALESCE({column},'')"), direction),
                    ]
                )
            else:
                parts.append(
                    _SortPart(
                        {"created_at": "t.created_at", "updated_at": "t.updated_at"}[field],
                        direction,
                    )
                )
        parts.append(_SortPart(text("t.todo_id"), "ASC"))
        return parts

    @staticmethod
    def _seek(parts: Sequence[_SortPart], anchor_values: Sequence[Any]) -> tuple[str, list[Any]]:
        # ORDER BY and seek use these identical complete expressions, including
        # fixed null/category flags and stable inner IDs; no key enters the cursor.
        branches: list[str] = []
        params: list[Any] = []
        for index, part in enumerate(parts):
            clauses = [previous.expression + "=?" for previous in parts[:index]]
            clauses.append(part.expression + (">?" if part.direction == "ASC" else "<?"))
            branches.append("(" + " AND ".join(clauses) + ")")
            params.extend(anchor_values[: index + 1])
        return "(" + " OR ".join(branches) + ")", params

    def materialize_in_connection(
        self,
        conn: Any,
        project_id: str,
        *,
        definition: dict[str, Any],
        catalog_revision: int,
        today: str,
        limit: int,
        members: Sequence[MemberMetadata] = (),
        priorities: Sequence[Any] = (),
        tags: Sequence[Any] = (),
        group_key: dict[str, Any] | None = None,
        view_type: str = "table",
        window: dict[str, Any] | None = None,
        bucket: str | None = None,
        anchor: tuple[str, int] | None = None,
    ) -> QueryPage:
        predicate, params = self._filters(definition["filters"], today)
        where = (
            "t.project_id=? AND t.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM project_todo_children r WHERE r.project_id=t.project_id AND r.child_todo_id=t.todo_id) AND "
            + predicate
        )
        parameters = [project_id, *params]
        # Count every authorized matching todo, including corrupt missing state.
        # Fail the snapshot instead of filtering such rows out of totals/items.
        bad_state = conn.execute(
            "SELECT 1 FROM project_todos t LEFT JOIN project_todo_display_state d "
            "ON d.project_id=t.project_id AND d.todo_id=t.todo_id "
            "LEFT JOIN project_todo_children_state c ON c.project_id=t.project_id AND c.todo_id=t.todo_id WHERE "
            + where
            + " AND (d.revision IS NULL OR d.revision<1 OR d.revision>? OR c.revision IS NULL OR c.revision<1 OR c.revision>?) LIMIT 1",
            [*parameters, locks.DISPLAY_REVISION_MAX, locks.DISPLAY_REVISION_MAX],
        ).fetchone()
        if bad_state is not None:
            raise RuntimeError("todo display state is missing or outside its range")
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM project_todos t WHERE " + where, parameters
            ).fetchone()[0]
        )
        groups = self._groups_in_connection(
            conn,
            where,
            parameters,
            definition["group_by"],
            members=members,
            priorities=priorities,
            tags=tags,
            total=total,
        )
        date_predicate, date_params, unscheduled = self._date_predicate(
            view_type, definition, window, bucket
        )
        unscheduled_total = (
            None
            if unscheduled is None
            else int(
                conn.execute(
                    "SELECT COUNT(*) FROM project_todos t WHERE " + where + " AND " + unscheduled,
                    parameters,
                ).fetchone()[0]
            )
        )
        matched_total = (
            total
            if unscheduled is None
            else int(
                conn.execute(
                    "SELECT COUNT(*) FROM project_todos t WHERE "
                    + where
                    + " AND "
                    + date_predicate,
                    [*parameters, *date_params],
                ).fetchone()[0]
            )
        )
        group_predicate, group_values = self._group_predicate(group_key)
        scoped_where = where + " AND (" + date_predicate + ") AND (" + group_predicate + ")"
        scoped_params = [*parameters, *date_params, *group_values]
        source = (
            " FROM project_todos t LEFT JOIN project_todo_display_state d ON d.project_id=t.project_id AND d.todo_id=t.todo_id LEFT JOIN users u ON u.id=t.assignee_user_id "
            "LEFT JOIN project_todo_priorities p ON p.project_id=t.project_id AND p.priority_id=t.priority_id"
        )
        parts = self._sort_parts(definition["sort"])
        seek = "TRUE"
        seek_params: list[Any] = []
        if anchor is not None:
            keys = ",".join(
                f"{part.expression} AS __plan_sort_{index}" for index, part in enumerate(parts)
            )
            row = conn.execute(
                "SELECT d.revision AS display_revision,"
                + keys
                + source
                + " WHERE "
                + scoped_where
                + " AND t.todo_id=?",
                [*scoped_params, anchor[0]],
            ).fetchone()
            if row is None or locks.checked_display_revision(row["display_revision"]) != anchor[1]:
                raise QueryAnchorChanged("plan query anchor changed")
            seek, seek_params = self._seek(
                parts, [row[f"__plan_sort_{index}"] for index in range(len(parts))]
            )
        order = ",".join(part.expression + " " + part.direction for part in parts)
        rows = conn.execute(
            "SELECT t.*,d.revision AS display_revision"
            + locks.RELATIONSHIP_PAGE_COLUMNS
            + source
            + locks.relationship_page_joins("t")
            + " WHERE "
            + scoped_where
            + " AND "
            + seek
            + " ORDER BY "
            + order
            + " LIMIT ?",
            [project_id, *scoped_params, *seek_params, limit + 1],
        ).fetchall()
        page_tags: dict[str, list[str]] = {str(row["todo_id"]): [] for row in rows}
        if page_tags:
            refs = conn.execute(
                "SELECT todo_id,tag_id FROM project_todo_tag_links WHERE project_id=? AND todo_id IN ("
                + ",".join("?" for _ in page_tags)
                + ") ORDER BY todo_id,tag_id",
                [project_id, *page_tags],
            ).fetchall()
            for ref in refs:
                page_tags[str(ref["todo_id"])].append(str(ref["tag_id"]))
        payloads = [
            ProjectTodoRow.from_row(
                row,
                tag_ids=page_tags[str(row["todo_id"])],
                catalog_revision=catalog_revision,
                relationship=locks.relationship_from_page_row(row),
                display_revision=locks.checked_display_revision(row["display_revision"]),
            )
            for row in rows
        ]
        return QueryPage(
            payloads[:limit], total, matched_total, unscheduled_total, groups, len(rows) > limit
        )
