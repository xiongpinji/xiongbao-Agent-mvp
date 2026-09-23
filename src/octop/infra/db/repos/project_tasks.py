"""Project task links — SQL-only repository for PS-05 private attribution.

Authorization policy lives in ``octop.infra.projects.tasks``; this module only
enforces what must be race-safe. :meth:`ProjectTaskRepo.attach` re-validates
membership, ``threads.user_id`` ownership, the Dashboard ``:dm`` session
binding, and ``UNIQUE(thread_id)`` inside one write transaction, so a
concurrent member removal or competing attach can never leave a stale or
foreign attribution. On PostgreSQL the membership row is locked ``FOR SHARE``
before any link row is touched (lock order: member-row → link-row, matching
``ProjectRepo.remove_member``); SQLite's ``BEGIN IMMEDIATE`` writer already
serializes writes. This slice writes no ``project_events`` rows — a private
``thread_id`` must never appear in a member-readable project summary.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts

_SUMMARY_COLUMNS = (
    "l.project_id, l.thread_id, l.owner_user_id, l.source, "
    "t.agent_id, t.title, t.last_active, t.created_at"
)
_SUMMARY_SELECT = (
    f"SELECT {_SUMMARY_COLUMNS} FROM project_task_links l "
    "JOIN threads t ON t.thread_id = l.thread_id "
)
# Same activity sentinel as the thread-list ordering: last_active=0 means
# "no turns yet", so brand-new threads sort by created_at instead of sinking.
_ORDER_BY = (
    " ORDER BY CASE WHEN t.last_active > 0 THEN t.last_active ELSE t.created_at END DESC,"
    " t.thread_id DESC"
)


def _escape_like(value: str) -> str:
    # Same escaping as ProjectRepo.list_for_user: %, _, and \ stay literal.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class ProjectTaskSummary:
    """Safe task summary — exactly the contract's public fields.

    ``agent_id``/``title``/``last_active``/``created_at`` come from the joined
    thread row (task times); the link's own ``created_at`` stays internal.
    Never carries ``session_key``, artifacts, messages, workspace paths, or
    model credentials.
    """

    project_id: str
    thread_id: str
    owner_user_id: int
    agent_id: str
    title: str | None
    source: str
    last_active: int
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> ProjectTaskSummary:
        title = row["title"]
        return cls(
            project_id=str(row["project_id"]),
            thread_id=str(row["thread_id"]),
            owner_user_id=int(row["owner_user_id"]),
            agent_id=str(row["agent_id"]),
            title=None if title is None else str(title),
            source=str(row["source"]),
            last_active=int(row["last_active"]),
            created_at=int(row["created_at"]),
        )


@dataclass(frozen=True)
class TaskLinkMutation:
    """Outcome of :meth:`ProjectTaskRepo.attach`.

    ``outcome`` is one of: created, duplicate, not_member, invalid_thread,
    conflict. ``invalid_thread`` covers unknown, deleted, foreign,
    non-dashboard, and non-DM threads with one uniform value so no ownership
    or existence signal leaks. ``conflict_project_id`` names the project
    already holding the link — always one of the caller's own links, because
    only the thread owner can attach.
    """

    outcome: str
    summary: ProjectTaskSummary | None = None
    conflict_project_id: str = ""


class ProjectTaskRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------ helpers

    def _member_share_lock(self) -> str:
        # Contract: PostgreSQL validates membership with SELECT ... FOR SHARE
        # so a concurrent remove_member DELETE cannot interleave between the
        # check and the link write. SQLite writers hold the database lock.
        return " FOR SHARE" if self._db.dialect == "postgresql" else ""

    def _member_role_locked(self, conn: Any, project_id: str, user_id: int) -> str | None:
        """Read the caller's role, locking the membership row first."""
        row = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
            + self._member_share_lock(),
            (project_id, user_id),
        ).fetchone()
        return None if row is None else str(row["role"])

    def _summary_for(
        self, conn: Any, project_id: str, thread_id: str, user_id: int
    ) -> ProjectTaskSummary | None:
        row = conn.execute(
            _SUMMARY_SELECT + "WHERE l.project_id = ? AND l.thread_id = ? AND l.owner_user_id = ?",
            (project_id, thread_id, user_id),
        ).fetchone()
        return ProjectTaskSummary.from_row(row) if row is not None else None

    # ------------------------------------------------------------ writes

    def attach(self, *, project_id: str, user_id: int, thread_id: str) -> TaskLinkMutation:
        """Link the caller's own Dashboard :dm thread to one project.

        Single validated INSERT … SELECT: the thread must exist, belong to the
        caller, be a dashboard channel, and carry exactly the caller's ``:dm``
        session key for its agent. ``UNIQUE(thread_id)`` resolves duplicate and
        cross-project races without a read-then-write window.
        """
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                # Lock order: membership row before any link row.
                if self._member_role_locked(conn, project_id, user_id) is None:
                    return TaskLinkMutation(outcome="not_member")
                inserted = conn.execute(
                    "INSERT INTO project_task_links("
                    "project_id, thread_id, owner_user_id, source, created_at) "
                    "SELECT ?, t.thread_id, t.user_id, 'manual', ? FROM threads t "
                    "WHERE t.thread_id = ? AND t.user_id = ? "
                    "AND t.channel_type = 'dashboard' "
                    "AND t.session_key = t.agent_id || ':dashboard:' || ? || ':dm' "
                    "ON CONFLICT (thread_id) DO NOTHING "
                    "RETURNING id",
                    (project_id, ts, thread_id, user_id, str(user_id)),
                ).fetchone()
                if inserted is not None:
                    return TaskLinkMutation(
                        outcome="created",
                        summary=self._summary_for(conn, project_id, thread_id, user_id),
                    )
                existing = conn.execute(
                    "SELECT l.project_id FROM project_task_links l "
                    "JOIN threads t ON t.thread_id = l.thread_id "
                    "WHERE l.thread_id = ? AND l.owner_user_id = ? AND t.user_id = ? "
                    "AND t.channel_type = 'dashboard' "
                    "AND t.session_key = t.agent_id || ':dashboard:' || ? || ':dm'",
                    (thread_id, user_id, user_id, str(user_id)),
                ).fetchone()
                if existing is None:
                    # Thread missing, foreign, deleted mid-write, or not the
                    # caller's dashboard DM — one uniform outcome, no leak.
                    return TaskLinkMutation(outcome="invalid_thread")
                if str(existing["project_id"]) == project_id:
                    return TaskLinkMutation(
                        outcome="duplicate",
                        summary=self._summary_for(conn, project_id, thread_id, user_id),
                    )
                return TaskLinkMutation(
                    outcome="conflict", conflict_project_id=str(existing["project_id"])
                )
        except Exception as exc:
            # A concurrent thread/project/user delete can lose the race with
            # the FK check; classify exactly like an invalid thread (uniform
            # 404). sqlite3 raises IntegrityError, psycopg raises errors with
            # sqlstate 23503 (foreign_key_violation).
            foreign_key_race = isinstance(exc, sqlite3.IntegrityError) or (
                getattr(exc, "sqlstate", None) == "23503"
            )
            if foreign_key_race:
                return TaskLinkMutation(outcome="invalid_thread")
            raise

    def detach(self, *, project_id: str, thread_id: str, user_id: int) -> str:
        """Remove one link row. Outcomes: removed | missing | not_member.

        Only the link owner detaches; foreign/cross-project/unknown targets all
        return ``missing`` (rowcount 0), and the original thread is untouched.
        """
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, user_id) is None:
                return "not_member"
            deleted = conn.execute(
                "DELETE FROM project_task_links "
                "WHERE project_id = ? AND thread_id = ? AND owner_user_id = ?",
                (project_id, thread_id, user_id),
            )
            if getattr(deleted, "rowcount", 1) != 1:
                return "missing"
            return "removed"

    # ------------------------------------------------------------ reads

    def list_for_owner(
        self,
        project_id: str,
        *,
        user_id: int,
        q: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> list[ProjectTaskSummary]:
        """Own links in one project, newest activity first.

        Returns up to ``limit + 1`` rows so the caller derives ``has_more``
        without a COUNT. Title search is a literal, case-insensitive substring
        on both dialects (%, _, and \\ are escaped).
        """
        sql = _SUMMARY_SELECT + "WHERE l.project_id = ? AND l.owner_user_id = ?"
        params: list[object] = [project_id, user_id]
        needle = q.strip().lower()
        if needle:
            # LOWER() + ESCAPE keeps behavior identical on SQLite and
            # PostgreSQL (SQLite LIKE is case-insensitive, PostgreSQL is not).
            sql += " AND LOWER(t.title) LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(needle)}%")
        sql += _ORDER_BY + " LIMIT ? OFFSET ?"
        params.extend([limit + 1, offset])
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ProjectTaskSummary)

    def get_for_owner(
        self, project_id: str, thread_id: str, *, user_id: int
    ) -> ProjectTaskSummary | None:
        """One own link in one project; ``None`` for foreign/cross-project."""
        with self._db.connect() as conn:
            return self._summary_for(conn, project_id, thread_id, user_id)
