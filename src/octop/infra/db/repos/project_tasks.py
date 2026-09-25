"""Project task links — SQL-only repository for PS-05 private attribution.

Authorization policy lives in ``octop.infra.projects.tasks``; this module only
enforces what must be race-safe. :meth:`ProjectTaskRepo.attach` re-validates
membership, ``threads.user_id`` ownership, the Dashboard ``:dm`` session
binding, and ``UNIQUE(thread_id)`` inside one write transaction, so a
concurrent member removal or competing attach can never leave a stale or
foreign attribution. :meth:`ProjectTaskRepo.create_with_context` goes further
for the 028 create flow: it inserts the new thread, history projection,
``source='project'`` link, and the immutable instruction snapshot in the SAME
transaction, after re-validating membership, the unarchived project, the
expert agent kind, and the caller-confirmed instruction digest. On PostgreSQL
the membership row is locked ``FOR SHARE`` before the project row (lock order:
member-row → project-row → link-row, matching ``ProjectRepo.remove_member``);
SQLite's ``BEGIN IMMEDIATE`` writer already serializes writes. Neither write
path rebinds the active session or writes ``project_events`` — a private
``thread_id`` must never appear in a member-readable project summary.
"""

from __future__ import annotations

import hashlib
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
    can_read_text: bool = True

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


@dataclass(frozen=True)
class TaskCreateMutation:
    """Outcome of :meth:`ProjectTaskRepo.create_with_context`.

    ``outcome`` is one of: created, not_member, archived, stale_instructions,
    stale_experts, invalid_agent, invalid_expert. Every refusal is decided
    inside the write transaction, so a conflict leaves no thread, projection,
    link, or snapshot row behind. ``invalid_expert`` is the uniform outcome for
    a nonempty 029 project expert list whose selected Agent is no longer in the
    list or is unshared/disabled/deleted; ``invalid_agent`` keeps the 028
    empty-list ACL refusal.
    """

    outcome: str
    summary: ProjectTaskSummary | None = None


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

    def _project_row_locked(self, conn: Any, project_id: str) -> Any:
        """Read instructions/archived/revision, locking the project row after membership."""
        return conn.execute(
            "SELECT instructions, archived, experts_revision FROM project_spaces "
            "WHERE project_id = ?" + self._member_share_lock(),
            (project_id,),
        ).fetchone()

    def _expert_ids(self, conn: Any, project_id: str) -> list[str]:
        """Current ordered expert rows for one project (live, never cached)."""
        rows = conn.execute(
            "SELECT agent_id FROM project_experts WHERE project_id = ? "
            "ORDER BY sort_order, agent_id",
            (project_id,),
        ).fetchall()
        return [str(row["agent_id"]) for row in rows]

    def _insert_new_thread(
        self,
        conn: Any,
        *,
        thread_id: str,
        agent_id: str,
        user_id: int,
        session_key: str,
        ts: int,
    ) -> None:
        """Insert the owner's Dashboard DM thread with the "no turns" sentinel."""
        conn.execute(
            "INSERT INTO threads(thread_id, agent_id, user_id, channel_type, session_key, "
            "title, last_active, created_at) VALUES (?, ?, ?, 'dashboard', ?, NULL, 0, ?)",
            (thread_id, agent_id, user_id, session_key, ts),
        )

    def _insert_projection(self, conn: Any, *, thread_id: str, ts: int) -> None:
        conn.execute(
            "INSERT INTO thread_history_projection(thread_id, status, updated_at, error) "
            "VALUES (?, 'ready', ?, NULL)",
            (thread_id, ts),
        )

    def _insert_link(
        self, conn: Any, *, project_id: str, thread_id: str, user_id: int, ts: int
    ) -> None:
        conn.execute(
            "INSERT INTO project_task_links("
            "project_id, thread_id, owner_user_id, source, created_at) "
            "VALUES (?, ?, ?, 'project', ?)",
            (project_id, thread_id, user_id, ts),
        )

    def _insert_snapshot(
        self,
        conn: Any,
        *,
        project_id: str,
        thread_id: str,
        user_id: int,
        instructions: str,
        digest: str,
        expert_selection_revision: int,
        ts: int,
    ) -> None:
        conn.execute(
            "INSERT INTO project_task_contexts("
            "thread_id, project_id, owner_user_id, instructions_snapshot, "
            "snapshot_version, instructions_sha256, captured_at, expert_selection_revision) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (thread_id, project_id, user_id, instructions, digest, ts, expert_selection_revision),
        )

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

    def create_with_context(
        self,
        *,
        project_id: str,
        user_id: int,
        agent_id: str,
        thread_id: str,
        session_key: str,
        expected_instructions_sha256: str,
        expected_experts_revision: int | None = None,
        is_admin: bool = False,
    ) -> TaskCreateMutation:
        """Create a project task atomically with a frozen instruction snapshot.

        One transaction inserts the new Dashboard :dm thread, its history
        projection, the ``source='project'`` link, and the 026 snapshot of the
        project instructions read HERE — after locking membership, then the
        project row (PostgreSQL ``FOR SHARE``; SQLite ``BEGIN IMMEDIATE``).
        Nothing else is touched: the active session is not rebound and no
        ``project_events`` row is written. The digest is recomputed from the
        rows just read; a mismatch returns ``stale_instructions`` before the
        first insert so a 409 can never leave a partial task behind. The
        agent's ``kind`` is re-checked in the transaction because team hosts
        are excluded from instruction inheritance in this slice.

        029 expert gate: when the project has a nonempty expert list, the
        caller must supply the current ``expected_experts_revision`` and pick a
        listed Agent that is still shared+enabled+kind expert; a mismatch is
        ``stale_experts`` and a stale/unlisted Agent is ``invalid_expert`` —
        both before any insert. An empty list keeps the 028 owner/shared ACL,
        but a supplied revision must still match. The actual revision is
        recorded on the context row for audit; the list is never an
        authorization grant and is re-read live, not trusted from its revision.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            # Lock order: membership row, then project row, then the selected
            # Agent row (PostgreSQL ``FOR SHARE``; sorted Agent locks only in
            # ProjectRepo.replace_experts), then the new rows.
            if self._member_role_locked(conn, project_id, user_id) is None:
                return TaskCreateMutation(outcome="not_member")
            project = self._project_row_locked(conn, project_id)
            if project is None:
                return TaskCreateMutation(outcome="not_member")
            if int(project["archived"]):
                return TaskCreateMutation(outcome="archived")
            expert_revision = int(project["experts_revision"])
            expert_ids = self._expert_ids(conn, project_id)
            agent = conn.execute(
                "SELECT kind, user_id, is_shared, enabled FROM agents WHERE agent_id = ?"
                + self._member_share_lock(),
                (agent_id,),
            ).fetchone()
            if expert_ids:
                if (
                    expected_experts_revision is None
                    or expected_experts_revision != expert_revision
                ):
                    return TaskCreateMutation(outcome="stale_experts")
                if (
                    agent_id not in expert_ids
                    or agent is None
                    or str(agent["kind"]) != "expert"
                    or not int(agent["enabled"])
                    or int(agent["is_shared"] or 0) != 1
                ):
                    return TaskCreateMutation(outcome="invalid_expert")
            else:
                if (
                    expected_experts_revision is not None
                    and expected_experts_revision != expert_revision
                ):
                    return TaskCreateMutation(outcome="stale_experts")
                if (
                    agent is None
                    or str(agent["kind"]) != "expert"
                    or not int(agent["enabled"])
                    or not (
                        is_admin or agent["user_id"] == user_id or int(agent["is_shared"] or 0) == 1
                    )
                ):
                    return TaskCreateMutation(outcome="invalid_agent")
            instructions = str(project["instructions"])
            digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
            if digest != expected_instructions_sha256:
                return TaskCreateMutation(outcome="stale_instructions")
            self._insert_new_thread(
                conn,
                thread_id=thread_id,
                agent_id=agent_id,
                user_id=user_id,
                session_key=session_key,
                ts=ts,
            )
            self._insert_projection(conn, thread_id=thread_id, ts=ts)
            self._insert_link(
                conn, project_id=project_id, thread_id=thread_id, user_id=user_id, ts=ts
            )
            self._insert_snapshot(
                conn,
                project_id=project_id,
                thread_id=thread_id,
                user_id=user_id,
                instructions=instructions,
                digest=digest,
                expert_selection_revision=expert_revision,
                ts=ts,
            )
            return TaskCreateMutation(
                outcome="created",
                summary=ProjectTaskSummary(
                    project_id=project_id,
                    thread_id=thread_id,
                    owner_user_id=user_id,
                    agent_id=agent_id,
                    title=None,
                    source="project",
                    last_active=0,
                    created_at=ts,
                ),
            )

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

    def active_instructions_for_thread(
        self, *, thread_id: str, owner_user_id: int, agent_id: str
    ) -> str | None:
        """Return the frozen context only for a still-authorized project task.

        The thread remains private after detaching or member removal, but it
        must stop inheriting project instructions on the next model turn.
        Manual links have no snapshot and never enter this path.
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT c.instructions_snapshot FROM project_task_contexts c "
                "JOIN project_task_links l ON l.thread_id = c.thread_id "
                "AND l.project_id = c.project_id AND l.owner_user_id = c.owner_user_id "
                "JOIN threads t ON t.thread_id = c.thread_id AND t.user_id = c.owner_user_id "
                "JOIN project_spaces p ON p.project_id = c.project_id "
                "JOIN project_members m ON m.project_id = c.project_id "
                "AND m.user_id = c.owner_user_id "
                "WHERE c.thread_id = ? AND c.owner_user_id = ? AND t.agent_id = ? "
                "AND l.source = 'project' AND p.archived = 0 "
                "AND t.channel_type = 'dashboard'",
                (thread_id, owner_user_id, agent_id),
            ).fetchone()
        return str(row["instructions_snapshot"]) if row is not None else None

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
