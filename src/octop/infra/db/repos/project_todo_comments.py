"""SQL-only storage for project todo comments.

Membership is locked before the todo row, matching todo deletion and member
removal. The comment and its safe activity event commit in one transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts

EVENT_TODO_COMMENT_CREATED = "project.todo_comment_created"

_COMMENT_SELECT = (
    "SELECT c.comment_id, c.todo_id, c.author_user_id, "
    "COALESCE(u.username, '已删除用户') AS author_name, "
    "c.body_text, c.created_at "
    "FROM project_todo_comments c "
    "LEFT JOIN users u ON u.id = c.author_user_id "
)


@dataclass(frozen=True)
class ProjectTodoCommentRow:
    comment_id: str
    todo_id: str
    author_user_id: int | None
    author_name: str
    body: str
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> ProjectTodoCommentRow:
        author = row["author_user_id"]
        return cls(
            comment_id=str(row["comment_id"]),
            todo_id=str(row["todo_id"]),
            author_user_id=None if author is None else int(author),
            author_name=str(row["author_name"]),
            body=str(row["body_text"]),
            created_at=int(row["created_at"]),
        )


@dataclass(frozen=True)
class CommentCreation:
    """Outcome: created, reused, conflict, or missing."""

    outcome: str
    row: ProjectTodoCommentRow | None = None


def _body_fingerprint(body: str) -> str:
    """Hash the B1 canonical payload, including its empty image sequence."""
    payload = json.dumps({"body": body, "images": []}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _append_comment_event(
    conn: Any, project_id: str, author_user_id: int, todo_id: str, ts: int
) -> None:
    """Write only the todo id; comment content never enters project events."""
    conn.execute(
        "INSERT INTO project_events("
        "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, '{}', ?)",
        (project_id, author_user_id, EVENT_TODO_COMMENT_CREATED, todo_id, ts),
    )


class ProjectTodoCommentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def _member_lock(self) -> str:
        return " FOR SHARE" if self._db.dialect == "postgresql" else ""

    def _todo_lock(self, *, write: bool) -> str:
        if self._db.dialect != "postgresql":
            return ""
        return " FOR UPDATE" if write else " FOR SHARE"

    def _has_access(
        self, conn: Any, project_id: str, todo_id: str, user_id: int, *, write: bool
    ) -> bool:
        member = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
            + self._member_lock(),
            (project_id, user_id),
        ).fetchone()
        if member is None:
            return False
        todo = conn.execute(
            "SELECT todo_id FROM project_todos "
            "WHERE project_id = ? AND todo_id = ? AND deleted_at IS NULL"
            + self._todo_lock(write=write),
            (project_id, todo_id),
        ).fetchone()
        return todo is not None

    def has_access(self, project_id: str, todo_id: str, user_id: int) -> bool:
        """Current member and live, same-project todo; no existence leak."""
        with self._db.transaction() as conn:
            return self._has_access(conn, project_id, todo_id, user_id, write=False)

    def _comment_row(self, conn: Any, comment_id: str) -> ProjectTodoCommentRow:
        row = conn.execute(_COMMENT_SELECT + "WHERE c.comment_id = ?", (comment_id,)).fetchone()
        if row is None:  # pragma: no cover - only called for a just-created or existing id
            raise RuntimeError("comment row missing")
        return ProjectTodoCommentRow.from_row(row)

    def list_comments(
        self,
        project_id: str,
        todo_id: str,
        *,
        user_id: int,
        limit: int,
        before: tuple[int, str] | None = None,
    ) -> list[ProjectTodoCommentRow] | None:
        """Newest-first seek page with authorization in the same transaction."""
        where = "WHERE c.todo_id = ?"
        params: list[Any] = [todo_id]
        if before is not None:
            where += " AND (c.created_at < ? OR (c.created_at = ? AND c.comment_id < ?))"
            params.extend((before[0], before[0], before[1]))
        with self._db.transaction() as conn:
            if not self._has_access(conn, project_id, todo_id, user_id, write=False):
                return None
            rows = conn.execute(
                _COMMENT_SELECT + where + " ORDER BY c.created_at DESC, c.comment_id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
        return map_rows(rows, ProjectTodoCommentRow)

    def create_comment(
        self,
        *,
        project_id: str,
        todo_id: str,
        author_user_id: int,
        body: str,
        client_request_id: str,
        ts: int | None = None,
    ) -> CommentCreation:
        """Create once per author/todo/request id, with event in the same commit."""
        stamp = now_ts() if ts is None else ts
        fingerprint = _body_fingerprint(body)
        with self._db.transaction() as conn:
            if not self._has_access(conn, project_id, todo_id, author_user_id, write=True):
                return CommentCreation("missing")
            prior = conn.execute(
                "SELECT comment_id, request_fingerprint FROM project_todo_comments "
                "WHERE todo_id = ? AND author_user_id = ? AND client_request_id = ?",
                (todo_id, author_user_id, client_request_id),
            ).fetchone()
            if prior is not None:
                if str(prior["request_fingerprint"]) != fingerprint:
                    return CommentCreation("conflict")
                return CommentCreation("reused", self._comment_row(conn, str(prior["comment_id"])))

            comment_id = str(uuid4())
            conn.execute(
                "INSERT INTO project_todo_comments("
                "comment_id, todo_id, author_user_id, body_text, client_request_id, "
                "request_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    comment_id,
                    todo_id,
                    author_user_id,
                    body,
                    client_request_id,
                    fingerprint,
                    stamp,
                ),
            )
            _append_comment_event(conn, project_id, author_user_id, todo_id, stamp)
            return CommentCreation("created", self._comment_row(conn, comment_id))
