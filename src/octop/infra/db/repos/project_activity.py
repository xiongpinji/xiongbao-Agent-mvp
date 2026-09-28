"""Project activity + messages — SQL-only repository for PS-03A.

Authorization policy lives in ``octop.infra.projects.activity``; this module
only enforces what must be race-safe and what must be server-side to prevent
leaks. Both read paths apply the strict event whitelist and the members|related
scoping in SQL *before* the ``(created_at, id)`` seek pagination, so excluded
events (invites, join requests, unknown/future kinds) never reach Python and a
full page is delivered even when unrelated events interleave. Message rows and
their ``project.message_created`` events are written in one transaction after
locking the membership row (``FOR SHARE`` on PostgreSQL, lock order member-row
→ message/event rows matching ``ProjectRepo.remove_member``; SQLite's
``BEGIN IMMEDIATE`` writer already serializes writes), so a failure mid-write
leaves neither row. Message bodies live only in ``project_messages`` — the
event ``payload_json`` stays ``{}`` — and list rows never carry payloads.

Related-scope user matching compares ``str(user_id)`` uniformly: member events
via ``object_id``, todo events via *integer-typed* JSON payload fields only
(a JSON string or null never matches). Malformed historical JSON on a
whitelisted todo event makes the related listing fail closed with a database
error; the payload text never surfaces in the exception.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, sql_in_placeholders
from octop.infra.db.repos.project_todo_catalog import EVENT_TODO_CATALOG_UPDATED
from octop.infra.db.repos.project_todo_comments import EVENT_TODO_COMMENT_CREATED
from octop.infra.db.repos.project_todo_views import EVENT_TODO_VIEW_UPDATED
from octop.infra.db.repos.project_todos import (
    EVENT_TODO_CREATED,
    EVENT_TODO_DELETED,
    EVENT_TODO_UPDATED,
)
from octop.infra.db.repos.projects import (
    EVENT_CREATED,
    EVENT_EXPERTS_UPDATED,
    EVENT_MEMBER_JOINED,
    EVENT_MEMBER_REMOVED,
    EVENT_MEMBER_ROLE_CHANGED,
    EVENT_UPDATED,
)
from octop.infra.utils.ulid import new_ulid

EVENT_MESSAGE_CREATED = "project.message_created"

# The only event kinds the activity feed may ever return (contract whitelist).
# Invite lifecycle, join requests, and unknown/future events are excluded in
# SQL on every read path.
ACTIVITY_EVENT_TYPES: tuple[str, ...] = (
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_EXPERTS_UPDATED,
    EVENT_MEMBER_JOINED,
    EVENT_MEMBER_ROLE_CHANGED,
    EVENT_MEMBER_REMOVED,
    EVENT_TODO_CREATED,
    EVENT_TODO_UPDATED,
    EVENT_TODO_DELETED,
    EVENT_TODO_COMMENT_CREATED,
    EVENT_TODO_CATALOG_UPDATED,
    EVENT_TODO_VIEW_UPDATED,
    EVENT_MESSAGE_CREATED,
)

ACTIVITY_SCOPES: tuple[str, ...] = ("members", "related")

_MEMBER_EVENT_TYPES: tuple[str, ...] = (
    EVENT_MEMBER_JOINED,
    EVENT_MEMBER_ROLE_CHANGED,
    EVENT_MEMBER_REMOVED,
)
_TODO_EVENT_TYPES: tuple[str, ...] = (
    EVENT_TODO_CREATED,
    EVENT_TODO_UPDATED,
    EVENT_TODO_DELETED,
    EVENT_TODO_COMMENT_CREATED,
)

TODO_FIELD_NAMES = (
    "title",
    "description",
    "description_format",
    "status",
    "assignee_user_id",
    "start_date",
    "due_date",
    "priority_id",
    "tag_ids",
)
CATALOG_FIELD_NAMES = ("name", "color", "order", "archived")
VIEW_FIELD_NAMES = ("name", "type", "definition", "order", "archived", "default_view_id")
VIEW_ACTION_NAMES = ("created", "updated", "ordered", "archived", "restored", "default_changed")

# SELECT list shared by the timeline and the post-create message read-back.
# object_kind is derived from the event type; message bodies come from the
# joined project_messages row (never from payload_json); actor_name from the
# LEFT-joined users row so deleted authors degrade to safe nulls.
_ACTIVITY_SELECT = (
    "SELECT e.id AS event_id, e.event_type AS event_type, "
    "e.actor_user_id AS actor_user_id, u.username AS actor_name, "
    "CASE "
    "WHEN e.event_type IN (?, ?, ?) THEN 'member' "
    "WHEN e.event_type IN (?, ?, ?, ?) THEN 'todo' "
    "WHEN e.event_type = ? THEN 'message' "
    "ELSE 'project' END AS object_kind, "
    "e.object_id AS object_id, m.body AS message_body, e.created_at AS created_at "
    "FROM project_events e "
    "LEFT JOIN users u ON u.id = e.actor_user_id "
    "LEFT JOIN project_messages m ON m.message_id = e.object_id "
    "AND m.project_id = e.project_id AND e.event_type = ? "
)
_SELECT_PARAMS: tuple[str, ...] = (
    *_MEMBER_EVENT_TYPES,
    *_TODO_EVENT_TYPES,
    EVENT_MESSAGE_CREATED,
    EVENT_MESSAGE_CREATED,
)


@dataclass(frozen=True)
class ActivityRow:
    """One whitelisted timeline entry — never carries ``payload_json``."""

    event_id: int
    event_type: str
    actor_user_id: int | None
    actor_name: str | None
    object_kind: str
    object_id: str | None
    message_body: str | None
    created_at: int
    fields: tuple[str, ...] = ()
    catalog_revision: int | None = None
    catalog_kind: str | None = None
    option_id: str | None = None
    action: str | None = None
    view_id: str | None = None
    version: int | None = None
    collection_revision: int | None = None

    @classmethod
    def from_row(cls, row: DbRow) -> ActivityRow:
        actor = row["actor_user_id"]
        name = row["actor_name"]
        object_id = row["object_id"]
        body = row["message_body"]
        keys = row.keys()
        fields = (
            tuple(json.loads(str(row["safe_fields_json"]))) if "safe_fields_json" in keys else ()
        )
        allowed = (
            VIEW_FIELD_NAMES
            if row["event_type"] == EVENT_TODO_VIEW_UPDATED
            else CATALOG_FIELD_NAMES
            if row["event_type"] == EVENT_TODO_CATALOG_UPDATED
            else TODO_FIELD_NAMES
        )
        safe_fields = tuple(
            sorted({value for value in fields if isinstance(value, str) and value in allowed})
        )
        option_id = row["safe_option_id"] if "safe_option_id" in keys else None
        if (
            option_id is not None
            and re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", str(option_id)) is None
        ):
            option_id = None
        return cls(
            event_id=int(row["event_id"]),
            event_type=str(row["event_type"]),
            actor_user_id=None if actor is None else int(actor),
            actor_name=None if name is None else str(name),
            object_kind=str(row["object_kind"]),
            object_id=None if object_id is None else str(object_id),
            message_body=None if body is None else str(body),
            created_at=int(row["created_at"]),
            fields=safe_fields,
            catalog_revision=row["safe_catalog_revision"]
            if "safe_catalog_revision" in keys
            else None,
            catalog_kind=row["safe_catalog_kind"] if "safe_catalog_kind" in keys else None,
            option_id=option_id,
            action=row["safe_action"] if "safe_action" in keys else None,
            view_id=row["safe_view_id"] if "safe_view_id" in keys else None,
            version=row["safe_view_version"] if "safe_view_version" in keys else None,
            collection_revision=row["safe_view_revision"] if "safe_view_revision" in keys else None,
        )


@dataclass(frozen=True)
class MessageCreation:
    """Outcome of :meth:`ProjectActivityRepo.create_message`.

    ``outcome`` is one of: created, not_member. ``row`` carries the fresh
    activity view row on success only.
    """

    outcome: str
    row: ActivityRow | None = None


class _MessageConflict(Exception):
    """Internal signal to roll the write transaction back cleanly."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


def _append_message_event(
    conn: Any,
    project_id: str,
    actor_user_id: int,
    event_type: str,
    object_id: str,
    ts: int,
) -> None:
    """Insert the message's event row with an always-empty ``{}`` payload.

    Module-level so atomicity tests can inject a failure at exactly this step;
    callers invoke it through the module namespace inside the write
    transaction, so an injected failure rolls the message row back too.
    """
    conn.execute(
        "INSERT INTO project_events("
        "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, '{}', ?)",
        (project_id, actor_user_id, event_type, object_id, ts),
    )


class ProjectActivityRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------ helpers

    def _activity_select(self) -> str:
        """Select individual safe metadata values; never fetch payload_json."""
        todo_fields = ",".join(f"'{value}'" for value in TODO_FIELD_NAMES)
        catalog_fields = ",".join(f"'{value}'" for value in CATALOG_FIELD_NAMES)
        view_fields = ",".join(f"'{value}'" for value in VIEW_FIELD_NAMES)
        view_actions = ",".join(f"'{value}'" for value in VIEW_ACTION_NAMES)
        catalog = f"e.event_type = '{EVENT_TODO_CATALOG_UPDATED}'"
        updated = f"e.event_type = '{EVENT_TODO_UPDATED}'"
        view = f"e.event_type = '{EVENT_TODO_VIEW_UPDATED}'"
        if self._db.dialect == "postgresql":
            payload = "e.payload_json::jsonb"
            fields = (
                "CASE WHEN " + catalog + " OR " + updated + " OR " + view + " THEN "
                "(SELECT COALESCE(jsonb_agg(value), '[]'::jsonb)::text FROM "
                "jsonb_array_elements(CASE WHEN jsonb_typeof(" + payload + "->'fields') = 'array' "
                "THEN " + payload + "->'fields' ELSE '[]'::jsonb END) AS safe(value) "
                "WHERE jsonb_typeof(value) = 'string' AND (("
                + catalog
                + " AND value#>>'{}' IN ("
                + catalog_fields
                + ")) "
                "OR ("
                + updated
                + " AND value#>>'{}' IN ("
                + todo_fields
                + ")) OR ("
                + view
                + " AND value#>>'{}' IN ("
                + view_fields
                + ")))) ELSE '[]' END AS safe_fields_json"
            )
            view_id = (
                f"CASE WHEN {view} AND jsonb_typeof({payload}->'view_id') = 'string' "
                f"AND ({payload}->>'view_id') ~ '^[0-9A-HJKMNP-TV-Z]{{26}}$' "
                f"THEN {payload}->>'view_id' ELSE NULL END"
            )

            def view_integer(field: str) -> str:
                raw = f"({payload}->>'{field}')"
                return (
                    f"CASE WHEN {view} THEN CASE WHEN jsonb_typeof({payload}->'{field}') = 'number' "
                    f"AND {raw} ~ '^[1-9][0-9]{{0,18}}$' "
                    f"AND (length({raw}) < 19 OR {raw} COLLATE \"C\" <= '9223372036854775807') "
                    f"THEN {raw}::bigint ELSE NULL END ELSE NULL END"
                )

            values = (
                f"CASE WHEN {catalog} THEN CASE WHEN jsonb_typeof({payload}->'catalog_revision') = 'number' "
                f"AND ({payload}->>'catalog_revision') ~ '^[1-9][0-9]{{0,17}}$' "
                f"THEN ({payload}->>'catalog_revision')::bigint ELSE NULL END ELSE NULL END AS safe_catalog_revision, "
                f"CASE WHEN {catalog} THEN CASE WHEN {payload}->>'catalog_kind' IN ('priority','tag') "
                f"THEN {payload}->>'catalog_kind' ELSE NULL END ELSE NULL END AS safe_catalog_kind, "
                f"CASE WHEN {catalog} THEN CASE WHEN jsonb_typeof({payload}->'option_id') = 'string' "
                f"THEN {payload}->>'option_id' ELSE NULL END ELSE NULL END AS safe_option_id, "
                f"CASE WHEN {view} THEN CASE WHEN {payload}->>'action' IN ({view_actions}) "
                f"THEN {payload}->>'action' ELSE NULL END "
                f"WHEN {catalog} THEN CASE WHEN {payload}->>'action' IN ('created','updated','ordered','archived','restored') "
                f"THEN {payload}->>'action' ELSE NULL END ELSE NULL END AS safe_action"
            )
        else:
            valid = "json_valid(e.payload_json)"
            fields = (
                f"CASE WHEN ({catalog} OR {updated} OR {view}) AND {valid} THEN "
                "(SELECT json_group_array(value) FROM json_each(CASE WHEN "
                "json_type(e.payload_json,'$.fields') = 'array' THEN json_extract(e.payload_json,'$.fields') "
                "ELSE '[]' END) WHERE type = 'text' AND (("
                + catalog
                + " AND value IN ("
                + catalog_fields
                + ")) "
                "OR ("
                + updated
                + " AND value IN ("
                + todo_fields
                + ")) OR ("
                + view
                + " AND value IN ("
                + view_fields
                + ")))) ELSE '[]' END AS safe_fields_json"
            )
            view_id = (
                f"CASE WHEN {view} AND {valid} THEN CASE WHEN json_type(e.payload_json,'$.view_id') = 'text' "
                "AND length(json_extract(e.payload_json,'$.view_id')) = 26 "
                "AND json_extract(e.payload_json,'$.view_id') NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*' "
                "THEN json_extract(e.payload_json,'$.view_id') ELSE NULL END ELSE NULL END"
            )

            def view_integer(field: str) -> str:
                raw = f"json_extract(e.payload_json,'$.{field}')"
                return (
                    f"CASE WHEN {view} AND {valid} THEN CASE WHEN json_type(e.payload_json,'$.{field}') = 'integer' "
                    f"AND typeof({raw}) = 'integer' AND {raw} BETWEEN 1 AND 9223372036854775807 "
                    f"THEN {raw} ELSE NULL END ELSE NULL END"
                )

            values = (
                f"CASE WHEN {catalog} AND {valid} THEN CASE WHEN json_type(e.payload_json,'$.catalog_revision') = 'integer' "
                "AND json_extract(e.payload_json,'$.catalog_revision') > 0 THEN json_extract(e.payload_json,'$.catalog_revision') "
                "ELSE NULL END ELSE NULL END AS safe_catalog_revision, "
                f"CASE WHEN {catalog} AND {valid} THEN CASE WHEN json_extract(e.payload_json,'$.catalog_kind') IN ('priority','tag') "
                "THEN json_extract(e.payload_json,'$.catalog_kind') ELSE NULL END ELSE NULL END AS safe_catalog_kind, "
                f"CASE WHEN {catalog} AND {valid} THEN CASE WHEN json_type(e.payload_json,'$.option_id') = 'text' "
                "THEN json_extract(e.payload_json,'$.option_id') ELSE NULL END ELSE NULL END AS safe_option_id, "
                f"CASE WHEN {view} AND {valid} THEN CASE WHEN json_extract(e.payload_json,'$.action') IN ({view_actions}) "
                "THEN json_extract(e.payload_json,'$.action') ELSE NULL END "
                f"WHEN {catalog} AND {valid} THEN CASE WHEN json_extract(e.payload_json,'$.action') IN ('created','updated','ordered','archived','restored') "
                "THEN json_extract(e.payload_json,'$.action') ELSE NULL END ELSE NULL END AS safe_action"
            )
        safe_view = (
            f"{view_id} AS safe_view_id, {view_integer('version')} AS safe_view_version, "
            f"{view_integer('collection_revision')} AS safe_view_revision"
        )
        return _ACTIVITY_SELECT.replace(
            "e.object_id AS object_id",
            f"CASE WHEN {view} THEN {view_id} ELSE e.object_id END AS object_id",
        ).replace(
            "e.created_at AS created_at ",
            f"e.created_at AS created_at, {fields}, {values}, {safe_view} ",
        )

    def _member_share_lock(self) -> str:
        # Contract: PostgreSQL validates membership with SELECT ... FOR SHARE
        # so a concurrent remove_member DELETE cannot interleave between the
        # check and the message write. SQLite writers hold the database lock.
        return " FOR SHARE" if self._db.dialect == "postgresql" else ""

    def _member_role_locked(self, conn: Any, project_id: str, user_id: int) -> str | None:
        """Read the caller's role, locking the membership row first."""
        row = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
            + self._member_share_lock(),
            (project_id, user_id),
        ).fetchone()
        return None if row is None else str(row["role"])

    def _allocate_message_id(self) -> str:
        for _ in range(16):
            message_id = new_ulid()
            with self._db.connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM project_messages WHERE message_id = ?", (message_id,)
                ).fetchone()
            if exists is None:
                return message_id
        raise RuntimeError("failed to allocate unique message id")

    def _json_uid_match(self, field: str) -> str:
        """SQL boolean matching one *integer-typed* JSON payload field to ``?``.

        ``field`` is always a module constant. A JSON string, null, or missing
        key never matches; malformed JSON raises a database error, which is
        the contract's fail-closed behavior for historical rows.
        """
        if self._db.dialect == "postgresql":
            return (
                f"(jsonb_typeof(e.payload_json::jsonb -> '{field}') = 'number' "
                f"AND (e.payload_json::jsonb ->> '{field}') = ?)"
            )
        return (
            f"(json_type(e.payload_json, '$.{field}') = 'integer' "
            f"AND CAST(json_extract(e.payload_json, '$.{field}') AS TEXT) = ?)"
        )

    def _related_predicate(self, user_id: int) -> tuple[str, list[Any]]:
        """Related-scope filter: actor hits, member-event targets, and todo
        assignee (current or historical) hits. Messages only enter related via
        own authorship (the actor branch). The CASE guard keeps every JSON
        access confined to whitelisted todo events, so payloads of excluded
        events are never parsed on either dialect."""
        uid_str = str(user_id)
        else_false = "FALSE" if self._db.dialect == "postgresql" else "0"
        sql = (
            "(e.actor_user_id = ? "
            f"OR (e.event_type IN ({sql_in_placeholders(len(_MEMBER_EVENT_TYPES))}) "
            "AND e.object_id = ?) "
            "OR CASE e.event_type "
            f"WHEN ? THEN {self._json_uid_match('assignee_user_id')} "
            f"WHEN ? THEN ({self._json_uid_match('from_assignee_user_id')} "
            f"OR {self._json_uid_match('to_assignee_user_id')}) "
            f"WHEN ? THEN {self._json_uid_match('from_assignee_user_id')} "
            f"ELSE {else_false} END)"
        )
        params: list[Any] = [
            user_id,
            *_MEMBER_EVENT_TYPES,
            uid_str,
            EVENT_TODO_CREATED,
            uid_str,
            EVENT_TODO_UPDATED,
            uid_str,
            uid_str,
            EVENT_TODO_DELETED,
            uid_str,
        ]
        return sql, params

    # ------------------------------------------------------------ read paths

    def list_activity(
        self,
        project_id: str,
        *,
        user_id: int,
        scope: str,
        limit: int,
        before: Sequence[int] | None = None,
    ) -> list[ActivityRow] | None:
        """Whitelisted timeline page, newest first; ``None`` for non-members.

        The whitelist and scope filters run in SQL before the seek predicate
        and ``LIMIT``. Returns up to ``limit + 1`` rows so the caller can
        detect ``has_more`` without a second query. ``before`` is a strict
        ``(created_at, id)`` seek key: ties inside one second are broken by
        ``id DESC``, so paging never duplicates or skips committed events and
        stays stable when newer events arrive mid-walk.
        """
        if scope not in ACTIVITY_SCOPES:
            raise ValueError(f"scope must be one of {', '.join(ACTIVITY_SCOPES)}")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        params: list[Any] = list(_SELECT_PARAMS)
        where = ["e.project_id = ?"]
        params.append(project_id)
        where.append(f"e.event_type IN ({sql_in_placeholders(len(ACTIVITY_EVENT_TYPES))})")
        params.extend(ACTIVITY_EVENT_TYPES)
        if scope == "related":
            related_sql, related_params = self._related_predicate(user_id)
            where.append(related_sql)
            params.extend(related_params)
        if before is not None:
            where.append("(e.created_at < ? OR (e.created_at = ? AND e.id < ?))")
            params.extend([int(before[0]), int(before[0]), int(before[1])])
        sql = (
            self._activity_select()
            + "WHERE "
            + " AND ".join(where)
            + " ORDER BY e.created_at DESC, e.id DESC LIMIT ?"
        )
        params.append(limit + 1)
        # SQLite's BEGIN IMMEDIATE deliberately serializes this short,
        # bounded read with cross-process member removal. A deferred WAL read
        # could keep serving a pre-removal snapshot after removal commits.
        # PostgreSQL instead holds FOR SHARE on the member row below.
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                # Keep authorization and the feed read in one transaction.
                # The service maps this sentinel to the uniform 404.
                return None
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ActivityRow)

    def _message_row(self, project_id: str, message_id: str) -> ActivityRow | None:
        """Read back one message's activity row through the shared SELECT."""
        sql = (
            self._activity_select()
            + "WHERE e.project_id = ? AND e.event_type = ? AND e.object_id = ?"
        )
        params: list[Any] = [*_SELECT_PARAMS, project_id, EVENT_MESSAGE_CREATED, message_id]
        with self._db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return ActivityRow.from_row(row) if row is not None else None

    # ------------------------------------------------------------ mutations

    def create_message(
        self,
        *,
        project_id: str,
        author_user_id: int,
        body: str,
        ts: int | None = None,
    ) -> MessageCreation:
        """Insert one message plus its event in a single transaction.

        Race-safe re-check (the service has already authorized): the author
        must still be a member — a concurrent removal makes this return
        ``not_member`` with nothing written. On PostgreSQL the membership row
        is locked ``FOR SHARE`` first; the author id is always the
        server-provided operator, never request data.
        """
        stamp = now_ts() if ts is None else ts
        message_id = self._allocate_message_id()
        try:
            with self._db.transaction() as conn:
                role = self._member_role_locked(conn, project_id, author_user_id)
                if role is None:
                    raise _MessageConflict("not_member")
                conn.execute(
                    "INSERT INTO project_messages("
                    "message_id, project_id, author_user_id, body, created_at"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (message_id, project_id, author_user_id, body, stamp),
                )
                _append_message_event(
                    conn, project_id, author_user_id, EVENT_MESSAGE_CREATED, message_id, stamp
                )
        except _MessageConflict as conflict:
            return MessageCreation(outcome=conflict.outcome)
        return MessageCreation(outcome="created", row=self._message_row(project_id, message_id))
