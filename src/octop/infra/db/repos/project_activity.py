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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, sql_in_placeholders
from octop.infra.db.repos.project_todos import (
    EVENT_TODO_CREATED,
    EVENT_TODO_DELETED,
    EVENT_TODO_UPDATED,
)
from octop.infra.db.repos.projects import (
    EVENT_CREATED,
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
    EVENT_MEMBER_JOINED,
    EVENT_MEMBER_ROLE_CHANGED,
    EVENT_MEMBER_REMOVED,
    EVENT_TODO_CREATED,
    EVENT_TODO_UPDATED,
    EVENT_TODO_DELETED,
    EVENT_MESSAGE_CREATED,
)

ACTIVITY_SCOPES: tuple[str, ...] = ("members", "related")

_MEMBER_EVENT_TYPES: tuple[str, ...] = (
    EVENT_MEMBER_JOINED,
    EVENT_MEMBER_ROLE_CHANGED,
    EVENT_MEMBER_REMOVED,
)
_TODO_EVENT_TYPES: tuple[str, ...] = (EVENT_TODO_CREATED, EVENT_TODO_UPDATED, EVENT_TODO_DELETED)

# SELECT list shared by the timeline and the post-create message read-back.
# object_kind is derived from the event type; message bodies come from the
# joined project_messages row (never from payload_json); actor_name from the
# LEFT-joined users row so deleted authors degrade to safe nulls.
_ACTIVITY_SELECT = (
    "SELECT e.id AS event_id, e.event_type AS event_type, "
    "e.actor_user_id AS actor_user_id, u.username AS actor_name, "
    "CASE "
    "WHEN e.event_type IN (?, ?, ?) THEN 'member' "
    "WHEN e.event_type IN (?, ?, ?) THEN 'todo' "
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

    @classmethod
    def from_row(cls, row: DbRow) -> ActivityRow:
        actor = row["actor_user_id"]
        name = row["actor_name"]
        object_id = row["object_id"]
        body = row["message_body"]
        return cls(
            event_id=int(row["event_id"]),
            event_type=str(row["event_type"]),
            actor_user_id=None if actor is None else int(actor),
            actor_name=None if name is None else str(name),
            object_kind=str(row["object_kind"]),
            object_id=None if object_id is None else str(object_id),
            message_body=None if body is None else str(body),
            created_at=int(row["created_at"]),
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
            _ACTIVITY_SELECT
            + "WHERE "
            + " AND ".join(where)
            + " ORDER BY e.created_at DESC, e.id DESC LIMIT ?"
        )
        params.append(limit + 1)
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                # Keep authorization and the feed read in one transaction.
                # The service maps this sentinel to the uniform 404.
                return None
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ActivityRow)

    def _message_row(self, project_id: str, message_id: str) -> ActivityRow | None:
        """Read back one message's activity row through the shared SELECT."""
        sql = _ACTIVITY_SELECT + "WHERE e.project_id = ? AND e.event_type = ? AND e.object_id = ?"
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
