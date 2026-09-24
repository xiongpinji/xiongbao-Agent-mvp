"""Explicit project-task text grants and snapshot-bound projection reads.

This repository never opens owner history or checkpoints. A read first proves
current project membership, task binding, card share and the separate text
grant in one transaction, then obtains only the existing projection rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.thread_messages import ThreadMessageRow


@dataclass(frozen=True)
class TextGrantRow:
    user_id: int
    granted_at: int


@dataclass(frozen=True)
class TextGrantMutation:
    outcome: str
    grant: TextGrantRow | None = None


@dataclass(frozen=True)
class RawTextPage:
    status: str
    rows: list[ThreadMessageRow]
    has_more: bool = False
    next_before_seq: int | None = None


class ProjectTaskContentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def _share_lock(self) -> str:
        return " FOR SHARE" if self._db.dialect == "postgresql" else ""

    def _members_locked(self, conn: Any, project_id: str, user_ids: Iterable[int]) -> set[int]:
        found: set[int] = set()
        for user_id in sorted(set(user_ids)):
            row = conn.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
                + self._share_lock(),
                (project_id, user_id),
            ).fetchone()
            if row is not None:
                found.add(user_id)
        return found

    @staticmethod
    def _own_task_ok(conn: Any, project_id: str, thread_id: str, user_id: int) -> bool:
        row = conn.execute(
            "SELECT 1 FROM project_task_links l "
            "JOIN threads t ON t.thread_id = l.thread_id "
            "WHERE l.project_id = ? AND l.thread_id = ? AND l.owner_user_id = ? "
            "AND t.user_id = ? AND t.channel_type = 'dashboard' "
            "AND t.session_key = t.agent_id || ':dashboard:' || ? || ':dm'",
            (project_id, thread_id, user_id, user_id, str(user_id)),
        ).fetchone()
        return row is not None

    def grant(
        self, *, project_id: str, thread_id: str, actor_user_id: int, grantee_user_id: int
    ) -> TextGrantMutation:
        """Add the second, text-specific grant for an active card recipient."""
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                members = self._members_locked(conn, project_id, (actor_user_id, grantee_user_id))
                if actor_user_id not in members:
                    return TextGrantMutation("not_member")
                project = conn.execute(
                    "SELECT archived FROM project_spaces WHERE project_id = ?", (project_id,)
                ).fetchone()
                if project is None:
                    return TextGrantMutation("not_member")
                if bool(project["archived"]):
                    return TextGrantMutation("archived")
                if not self._own_task_ok(conn, project_id, thread_id, actor_user_id):
                    return TextGrantMutation("invalid_task")
                if grantee_user_id == actor_user_id or grantee_user_id not in members:
                    return TextGrantMutation("invalid_recipient")
                card = conn.execute(
                    "SELECT revoked_at FROM project_task_shares "
                    "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?"
                    + self._share_lock(),
                    (project_id, thread_id, grantee_user_id),
                ).fetchone()
                if card is None or card["revoked_at"] is not None:
                    return TextGrantMutation("invalid_recipient")
                inserted = conn.execute(
                    "INSERT INTO project_task_content_grants("
                    "project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (project_id, thread_id, grantee_user_id) DO NOTHING "
                    "RETURNING grantee_user_id, granted_at",
                    (project_id, thread_id, grantee_user_id, actor_user_id, ts),
                ).fetchone()
                if inserted is not None:
                    return TextGrantMutation(
                        "created",
                        TextGrantRow(int(inserted["grantee_user_id"]), int(inserted["granted_at"])),
                    )
                existing = conn.execute(
                    "SELECT grantee_user_id, granted_at FROM project_task_content_grants "
                    "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?",
                    (project_id, thread_id, grantee_user_id),
                ).fetchone()
                if existing is None:
                    return TextGrantMutation("invalid_recipient")
                return TextGrantMutation(
                    "duplicate",
                    TextGrantRow(int(existing["grantee_user_id"]), int(existing["granted_at"])),
                )
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23503":
                return TextGrantMutation("invalid_recipient")
            raise

    def revoke(
        self, *, project_id: str, thread_id: str, actor_user_id: int, grantee_user_id: int
    ) -> str:
        """Delete a text grant without changing the card, also when archived."""
        with self._db.transaction() as conn:
            members = self._members_locked(conn, project_id, (actor_user_id, grantee_user_id))
            if actor_user_id not in members:
                return "not_member"
            if not self._own_task_ok(conn, project_id, thread_id, actor_user_id):
                return "missing_task"
            conn.execute(
                "SELECT 1 FROM project_task_shares "
                "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?"
                + self._share_lock(),
                (project_id, thread_id, grantee_user_id),
            ).fetchone()
            deleted = conn.execute(
                "DELETE FROM project_task_content_grants "
                "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?",
                (project_id, thread_id, grantee_user_id),
            )
            return "revoked" if getattr(deleted, "rowcount", 0) else "noop"

    def read_page(
        self,
        *,
        project_id: str,
        thread_id: str,
        actor_user_id: int,
        limit: int,
        before_seq: int | None = None,
        projection_enabled: bool = True,
    ) -> RawTextPage | None:
        """Authorize and fetch a raw page in one transaction.

        ``None`` is deliberately uniform for unknown/foreign/unshared tasks.
        Parsing and text projection happen only after this transaction ends.
        """
        with self._db.transaction() as conn:
            if actor_user_id not in self._members_locked(conn, project_id, (actor_user_id,)):
                return None
            owner = self._own_task_ok(conn, project_id, thread_id, actor_user_id)
            if not owner:
                card = conn.execute(
                    "SELECT 1 FROM project_task_shares s "
                    "JOIN project_task_links l ON l.project_id = s.project_id "
                    "AND l.thread_id = s.thread_id "
                    "JOIN threads t ON t.thread_id = l.thread_id "
                    "WHERE s.project_id = ? AND s.thread_id = ? "
                    "AND s.grantee_user_id = ? AND s.revoked_at IS NULL "
                    "AND t.user_id = l.owner_user_id AND t.channel_type = 'dashboard' "
                    "AND t.session_key = t.agent_id || ':dashboard:' || l.owner_user_id || ':dm'"
                    + self._share_lock(),
                    (project_id, thread_id, actor_user_id),
                ).fetchone()
                if card is None:
                    return None
                grant = conn.execute(
                    "SELECT 1 FROM project_task_content_grants "
                    "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?"
                    + self._share_lock(),
                    (project_id, thread_id, actor_user_id),
                ).fetchone()
                if grant is None:
                    return None
            if not projection_enabled:
                return RawTextPage(status="pending", rows=[])
            projection = conn.execute(
                "SELECT status FROM thread_history_projection WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if projection is None or projection["status"] != "ready":
                return RawTextPage(status="pending", rows=[])
            sql = (
                "SELECT seq, message_id, role, message_json, created_at "
                "FROM thread_messages WHERE thread_id = ?"
            )
            params: list[object] = [thread_id]
            if before_seq is not None:
                sql += " AND seq < ?"
                params.append(before_seq)
            sql += " ORDER BY seq DESC LIMIT ?"
            params.append(limit + 1)
            fetched = conn.execute(sql, tuple(params)).fetchall()
            raw_rows = [ThreadMessageRow.from_row(row) for row in fetched[:limit]]
        has_more = len(fetched) > limit
        return RawTextPage(
            status="ready",
            rows=raw_rows,
            has_more=has_more,
            next_before_seq=raw_rows[-1].seq if has_more and raw_rows else None,
        )
