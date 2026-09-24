"""Project task card shares — SQL-only repository for PS-05B slice 1.

Authorization policy lives in ``octop.infra.projects.tasks``; this module
only enforces what must be race-safe. :meth:`ProjectTaskShareRepo.grant`
re-validates the actor's membership, task ownership, the thread's live
Dashboard ``:dm`` binding, the recipient's membership, and the project's
archive state inside one write transaction. On PostgreSQL both membership
rows are locked ``FOR SHARE`` in ascending user-id order before any share
row is touched — deadlock-free against opposite-direction grants and
``ProjectRepo.remove_member``; SQLite's ``BEGIN IMMEDIATE`` writer already
serializes writes. The ``UNIQUE(project_id, thread_id, grantee_user_id)``
constraint and the composite foreign keys resolve the remaining races, so a
share can never outlive its task link or the recipient's membership. Reads
apply membership and active-grant state inside SQL. This slice writes no
``project_events`` rows — a private ``thread_id`` must never appear in a
member-readable project feed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.db.repos.project_tasks import ProjectTaskSummary

# Same safe-summary column set as ProjectTaskRepo (the card, nothing more);
# reader queries append a constant ``access`` discriminator.
_SUMMARY_COLUMNS = (
    "l.project_id, l.thread_id, l.owner_user_id, l.source, "
    "t.agent_id, t.title, t.last_active, t.created_at"
)
# Reader branch: an ACTIVE grant joined to its task link, thread, and the
# recipient's current membership — membership is enforced inside the SQL,
# not only by the service gate.
_READER_FROM = (
    "FROM project_task_shares s "
    "JOIN project_task_links l ON l.project_id = s.project_id AND l.thread_id = s.thread_id "
    "JOIN threads t ON t.thread_id = l.thread_id "
    "JOIN project_members pm ON pm.project_id = s.project_id AND pm.user_id = s.grantee_user_id"
)
# Same activity sentinel as ProjectTaskRepo._ORDER_BY: last_active=0 means
# "no turns yet", so brand-new threads sort by created_at instead of sinking.
_ACTIVITY_ORDER = (
    "CASE WHEN t.last_active > 0 THEN t.last_active ELSE t.created_at END DESC, t.thread_id DESC"
)


def _escape_like(value: str) -> str:
    # Same escaping as ProjectTaskRepo: %, _, and \ stay literal.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class VisibleTaskSummary(ProjectTaskSummary):
    """Safe task summary plus how the caller may see the card.

    ``access`` is ``owner`` for the task owner and ``reader`` for an active
    grant recipient — the only field this slice adds to the 021 summary.
    """

    access: str = "owner"

    @classmethod
    def from_row(cls, row: DbRow) -> VisibleTaskSummary:
        base = ProjectTaskSummary.from_row(row)
        return cls(
            project_id=base.project_id,
            thread_id=base.thread_id,
            owner_user_id=base.owner_user_id,
            agent_id=base.agent_id,
            title=base.title,
            source=base.source,
            last_active=base.last_active,
            created_at=base.created_at,
            access=str(row["access"]),
        )


@dataclass(frozen=True)
class TaskShareRow:
    """Grant metadata — grantee id, role, and grant time only.

    Never carries the grantee's profile, the task title, or any thread
    content; this is everything the owner's share list may expose.
    """

    user_id: int
    role: str
    granted_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> TaskShareRow:
        return cls(
            user_id=int(row["grantee_user_id"]),
            role=str(row["role"]),
            granted_at=int(row["granted_at"]),
        )


@dataclass(frozen=True)
class ShareGrantMutation:
    """Outcome of :meth:`ProjectTaskShareRepo.grant`.

    ``outcome`` is one of: created, duplicate, regranted, not_member,
    archived, invalid_task, invalid_recipient. ``invalid_task`` uniformly
    covers unknown, cross-project, foreign, and no-longer-:dm targets;
    ``invalid_recipient`` covers self-grants and non-member recipients —
    no existence or ownership signal leaks through the distinction.
    """

    outcome: str
    share: TaskShareRow | None = None


class ProjectTaskShareRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------ helpers

    def _member_share_lock(self) -> str:
        # Contract: PostgreSQL validates membership with SELECT ... FOR SHARE
        # so a concurrent remove_member DELETE cannot interleave between the
        # check and the share write. SQLite writers hold the database lock.
        return " FOR SHARE" if self._db.dialect == "postgresql" else ""

    def _members_locked(
        self, conn: Any, project_id: str, user_ids: Iterable[int]
    ) -> dict[int, str]:
        """Lock membership rows in ascending user-id order; return found roles.

        The fixed ascending order keeps concurrent grants (which lock the
        actor's and the recipient's rows) deadlock-free, and matches the
        member-row-before-share-row lock order of ``ProjectRepo.remove_member``.
        """
        roles: dict[int, str] = {}
        for uid in sorted(set(user_ids)):
            row = conn.execute(
                "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
                + self._member_share_lock(),
                (project_id, uid),
            ).fetchone()
            if row is not None:
                roles[uid] = str(row["role"])
        return roles

    def _member_role_locked(self, conn: Any, project_id: str, user_id: int) -> str | None:
        """Read one member's role, locking the membership row first."""
        row = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
            + self._member_share_lock(),
            (project_id, user_id),
        ).fetchone()
        return None if row is None else str(row["role"])

    def _project_archived(self, conn: Any, project_id: str) -> bool | None:
        """``None`` for unknown projects — they answer like non-memberships."""
        row = conn.execute(
            "SELECT archived FROM project_spaces WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        return bool(row["archived"])

    def _own_task_ok(self, conn: Any, project_id: str, thread_id: str, user_id: int) -> bool:
        """The actor must own the link in this project AND the thread must
        still be their Dashboard :dm conversation — re-validated inside every
        share transaction, so post-attach mutations cannot widen access."""
        row = conn.execute(
            "SELECT 1 FROM project_task_links l "
            "JOIN threads t ON t.thread_id = l.thread_id "
            "WHERE l.project_id = ? AND l.thread_id = ? AND l.owner_user_id = ? "
            "AND t.user_id = ? AND t.channel_type = 'dashboard' "
            "AND t.session_key = t.agent_id || ':dashboard:' || ? || ':dm'",
            (project_id, thread_id, user_id, user_id, str(user_id)),
        ).fetchone()
        return row is not None

    def _share_row(
        self, conn: Any, project_id: str, thread_id: str, grantee_user_id: int
    ) -> TaskShareRow | None:
        row = conn.execute(
            "SELECT grantee_user_id, role, granted_at FROM project_task_shares "
            "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?",
            (project_id, thread_id, grantee_user_id),
        ).fetchone()
        return TaskShareRow.from_row(row) if row is not None else None

    @staticmethod
    def _apply_q(sql: str, params: list[object], q: str) -> tuple[str, list[object]]:
        needle = q.strip().lower()
        if needle:
            # LOWER() + ESCAPE keeps behavior identical on SQLite and
            # PostgreSQL; %, _, and \ stay literal.
            sql += " AND LOWER(t.title) LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(needle)}%")
        return sql, params

    # ------------------------------------------------------------ writes

    def grant(
        self, *, project_id: str, thread_id: str, actor_user_id: int, grantee_user_id: int
    ) -> ShareGrantMutation:
        """Grant (or reactivate) reader access to one task card.

        First grant is ``created``; an active duplicate is ``duplicate`` with
        zero row updates; a revoked row is reactivated in place as
        ``regranted`` (same row id). ``ON CONFLICT DO NOTHING`` plus the
        guarded reactivation UPDATE make concurrent grants converge on one
        active row.
        """
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                # Lock order: member rows (ascending id) before share rows.
                roles = self._members_locked(conn, project_id, (actor_user_id, grantee_user_id))
                if actor_user_id not in roles:
                    return ShareGrantMutation(outcome="not_member")
                archived = self._project_archived(conn, project_id)
                if archived is None:
                    return ShareGrantMutation(outcome="not_member")
                if archived:
                    # Archive state is checked inside the write transaction:
                    # new and re- grants are refused, reads/revokes are not.
                    return ShareGrantMutation(outcome="archived")
                if not self._own_task_ok(conn, project_id, thread_id, actor_user_id):
                    return ShareGrantMutation(outcome="invalid_task")
                if grantee_user_id == actor_user_id or grantee_user_id not in roles:
                    return ShareGrantMutation(outcome="invalid_recipient")

                existing = conn.execute(
                    "SELECT role, granted_at, revoked_at FROM project_task_shares "
                    "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ?",
                    (project_id, thread_id, grantee_user_id),
                ).fetchone()
                if existing is not None and existing["revoked_at"] is None:
                    # Active duplicate: 200 semantics — no write at all, so
                    # granted_at keeps its original value.
                    return ShareGrantMutation(
                        outcome="duplicate",
                        share=TaskShareRow(
                            user_id=grantee_user_id,
                            role=str(existing["role"]),
                            granted_at=int(existing["granted_at"]),
                        ),
                    )
                if existing is not None:
                    updated = conn.execute(
                        "UPDATE project_task_shares "
                        "SET granted_by_user_id = ?, granted_at = ?, revoked_at = NULL "
                        "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ? "
                        "AND revoked_at IS NOT NULL "
                        "RETURNING grantee_user_id, role, granted_at",
                        (actor_user_id, ts, project_id, thread_id, grantee_user_id),
                    ).fetchone()
                    if updated is not None:
                        return ShareGrantMutation(
                            outcome="regranted", share=TaskShareRow.from_row(updated)
                        )
                    # A concurrent regrant won the row: answer like a duplicate.
                    raced = self._share_row(conn, project_id, thread_id, grantee_user_id)
                    if raced is None:
                        return ShareGrantMutation(outcome="invalid_task")
                    return ShareGrantMutation(outcome="duplicate", share=raced)
                inserted = conn.execute(
                    "INSERT INTO project_task_shares("
                    "project_id, thread_id, grantee_user_id, granted_by_user_id, "
                    "role, granted_at) VALUES (?, ?, ?, ?, 'reader', ?) "
                    "ON CONFLICT (project_id, thread_id, grantee_user_id) DO NOTHING "
                    "RETURNING grantee_user_id, role, granted_at",
                    (project_id, thread_id, grantee_user_id, actor_user_id, ts),
                ).fetchone()
                if inserted is not None:
                    return ShareGrantMutation(
                        outcome="created", share=TaskShareRow.from_row(inserted)
                    )
                # ON CONFLICT lost to a concurrent grant: read the winner's row.
                raced = self._share_row(conn, project_id, thread_id, grantee_user_id)
                if raced is None:
                    # Link or membership vanished mid-write: uniform outcome.
                    return ShareGrantMutation(outcome="invalid_task")
                return ShareGrantMutation(outcome="duplicate", share=raced)
        except Exception as exc:
            # A concurrent link/member/thread/project delete can lose the race
            # with the composite FK checks; classify exactly like an invalid
            # target (uniform 404). sqlite3 raises IntegrityError, psycopg
            # raises errors with sqlstate 23503 (foreign_key_violation).
            foreign_key_race = isinstance(exc, sqlite3.IntegrityError) or (
                getattr(exc, "sqlstate", None) == "23503"
            )
            if foreign_key_race:
                return ShareGrantMutation(outcome="invalid_task")
            raise

    def revoke(
        self, *, project_id: str, thread_id: str, actor_user_id: int, grantee_user_id: int
    ) -> str:
        """Stamp ``revoked_at`` on one active grant; the row survives.

        Outcomes: revoked | noop | not_member | missing_task. Repeated
        revokes and never-granted recipients are ``noop`` (204 either way).
        Allowed on archived projects — the archive gate blocks granting
        access, never tightening it.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            if self._member_role_locked(conn, project_id, actor_user_id) is None:
                return "not_member"
            if not self._own_task_ok(conn, project_id, thread_id, actor_user_id):
                return "missing_task"
            updated = conn.execute(
                "UPDATE project_task_shares SET revoked_at = ? "
                "WHERE project_id = ? AND thread_id = ? AND grantee_user_id = ? "
                "AND revoked_at IS NULL",
                (ts, project_id, thread_id, grantee_user_id),
            )
            if getattr(updated, "rowcount", 1) == 1:
                return "revoked"
            return "noop"

    # ------------------------------------------------------------ reads

    def list_shares(
        self, project_id: str, thread_id: str, *, actor_user_id: int
    ) -> list[TaskShareRow] | None:
        """Active grant metadata for one own task, oldest grant first.

        ``None`` when the caller does not own the task — upstream answers
        with the same uniform 404 as for unknown tasks, so neither task
        existence nor ownership leaks to members, admins, or outsiders.
        """
        with self._db.connect() as conn:
            if not self._own_task_ok(conn, project_id, thread_id, actor_user_id):
                return None
            rows = conn.execute(
                "SELECT grantee_user_id, role, granted_at FROM project_task_shares "
                "WHERE project_id = ? AND thread_id = ? AND revoked_at IS NULL "
                "ORDER BY granted_at, grantee_user_id",
                (project_id, thread_id),
            ).fetchall()
        return map_rows(rows, TaskShareRow)

    def list_shared(
        self,
        project_id: str,
        *,
        user_id: int,
        q: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> list[VisibleTaskSummary]:
        """Cards actively shared with the caller, newest activity first.

        Membership and active-grant state are applied inside the SQL.
        Returns up to ``limit + 1`` rows so the caller derives ``has_more``
        without a COUNT; title search is literal and case-insensitive.
        """
        sql = (
            f"SELECT {_SUMMARY_COLUMNS}, 'reader' AS access {_READER_FROM} "
            "WHERE s.project_id = ? AND s.grantee_user_id = ? AND s.revoked_at IS NULL"
        )
        params: list[object] = [project_id, user_id]
        sql, params = self._apply_q(sql, params, q)
        sql += f" ORDER BY {_ACTIVITY_ORDER} LIMIT ? OFFSET ?"
        params.extend([limit + 1, offset])
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, VisibleTaskSummary)

    def list_all(
        self,
        project_id: str,
        *,
        user_id: int,
        q: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> list[VisibleTaskSummary]:
        """Own and shared cards in ONE SQL statement.

        Contract: a single UNION subquery with one final ORDER BY / LIMIT /
        OFFSET — never two paged queries merged in Python, which would break
        global ordering and ``has_more`` at the own/shared boundary. The
        branches are disjoint (self-grants are impossible), so UNION dedup is
        pure safety. Returns up to ``limit + 1`` rows.
        """
        own_sql = (
            f"SELECT {_SUMMARY_COLUMNS}, 'owner' AS access "
            "FROM project_task_links l "
            "JOIN threads t ON t.thread_id = l.thread_id "
            "JOIN project_members pm ON pm.project_id = l.project_id "
            "AND pm.user_id = l.owner_user_id "
            "WHERE l.project_id = ? AND l.owner_user_id = ?"
        )
        reader_sql = (
            f"SELECT {_SUMMARY_COLUMNS}, 'reader' AS access {_READER_FROM} "
            "WHERE s.project_id = ? AND s.grantee_user_id = ? AND s.revoked_at IS NULL"
        )
        own_sql, own_params = self._apply_q(own_sql, [project_id, user_id], q)
        reader_sql, reader_params = self._apply_q(reader_sql, [project_id, user_id], q)
        sql = (
            f"SELECT * FROM ({own_sql} UNION {reader_sql}) visible "
            "ORDER BY CASE WHEN visible.last_active > 0 THEN visible.last_active "
            "ELSE visible.created_at END DESC, visible.thread_id DESC "
            "LIMIT ? OFFSET ?"
        )
        params: list[object] = [*own_params, *reader_params, limit + 1, offset]
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, VisibleTaskSummary)

    def get_shared_summary(
        self, project_id: str, thread_id: str, *, user_id: int
    ) -> VisibleTaskSummary | None:
        """One actively shared card for the caller.

        ``None`` for revoked, never-granted, unknown, and cross-project
        targets — upstream collapses all of them into the same 404 as a
        foreign task, so no title or existence leaks.
        """
        sql = (
            f"SELECT {_SUMMARY_COLUMNS}, 'reader' AS access {_READER_FROM} "
            "WHERE s.project_id = ? AND s.thread_id = ? AND s.grantee_user_id = ? "
            "AND s.revoked_at IS NULL"
        )
        with self._db.connect() as conn:
            row = conn.execute(sql, (project_id, thread_id, user_id)).fetchone()
        return VisibleTaskSummary.from_row(row) if row is not None else None
