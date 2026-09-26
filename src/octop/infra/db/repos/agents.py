"""Agent table access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import UNSET, DbRow, bool_int, map_rows, now_ts, optional_updates

RUNTIME_KIND_STANDARD = "standard"
RUNTIME_KIND_PROJECT_TASK_FILES = "project_task_files"
_VALID_RUNTIME_KINDS = frozenset({RUNTIME_KIND_STANDARD, RUNTIME_KIND_PROJECT_TASK_FILES})

# 030A B1 fixed quota for internal project-task file runtimes: at most 8
# registered runtime rows per owner and 64 per instance. Disabled or detached
# runtimes still count as registered until cleanup deletes them. The limits
# are contract-fixed and never configurable.
PROJECT_TASK_FILES_OWNER_LIMIT = 8
PROJECT_TASK_FILES_GLOBAL_LIMIT = 64

# Single fixed PostgreSQL transaction-level advisory lock serializing runtime
# registration across server processes. The quota transaction acquires this
# lock first and takes no other lock, so no lock-order inversion with other
# flows is possible; it is released automatically at commit/rollback. SQLite
# needs no key: BEGIN IMMEDIATE already serializes writers.
_PG_RUNTIME_QUOTA_LOCK_KEY = 828_030


class ProjectTaskFilesQuotaError(RuntimeError):
    """Internal runtime registration refused at a fixed quota boundary.

    ``scope`` names the limit that was hit (``"owner"`` or ``"global"``);
    ``limit`` is the fixed threshold and ``current`` the number of registered
    internal runtime rows counted inside the serialized transaction. Services
    map this to the 030A ``PROJECT_TASK_FILES_QUOTA`` API error; the repo
    layer itself stays SQL-only.
    """

    def __init__(self, scope: Literal["owner", "global"], *, limit: int, current: int) -> None:
        super().__init__(
            f"project_task_files runtime quota reached (scope={scope}: {current}/{limit})"
        )
        self.scope = scope
        self.limit = limit
        self.current = current


class _ProjectTaskRowsGuard(RuntimeError):
    """Invariant violation inside the 030A full-delete transaction (B4).

    Raised when the runtime owns threads besides the delete target, when the
    thread DELETE affects an unexpected rowcount, or when a thread reappears
    mid-transaction. Propagating out of ``db.transaction()`` rolls EVERY child
    delete back, so a mismatch can never commit partially deleted metadata;
    the manager maps it to the retryable failed-cleanup 503 path. Pre-check
    binding failures keep returning False (zero writes) instead.
    """


def _count_project_task_runtimes(conn: Any, *, user_id: int | None = None) -> int:
    """Count registered ``project_task_files`` rows, optionally for one owner.

    The count is driven by the database-trusted ``runtime_kind`` column;
    ``config_json`` contents can never influence it.
    """
    sql = "SELECT COUNT(*) AS n FROM agents WHERE runtime_kind = 'project_task_files'"
    params: tuple[object, ...] = ()
    if user_id is not None:
        sql += " AND user_id = ?"
        params = (user_id,)
    row = conn.execute(sql, params).fetchone()
    return 0 if row is None else int(row["n"])


def _opt_str(r: DbRow, key: str) -> str | None:
    try:
        value = r[key]
    except (KeyError, IndexError):
        return None
    if value is None:
        return None
    text = str(value)
    return text if text else None


@dataclass(frozen=True)
class AgentRow:
    id: int
    agent_id: str
    user_id: int | None
    name: str
    description: str | None
    persona_mbti: str | None
    default_model: str | None
    system_prompt: str | None
    enabled: int
    config_json: str | None
    last_state: str | None
    last_error: str | None
    created_at: int
    updated_at: int
    icon: str | None = None
    template_name: str | None = None
    is_shared: int = 0
    color: str | None = None
    icon_name: str | None = None
    icon_url: str | None = None
    skill_package_ids: str | None = None
    published_expert_id: str | None = None
    welcome_message: str | None = None
    knowledge_base_ids: str | None = None
    mcp_servers: str | None = None
    kind: str = "expert"
    runtime_kind: str = RUNTIME_KIND_STANDARD

    @classmethod
    def from_row(cls, r: DbRow) -> AgentRow:
        try:
            is_shared = int(r["is_shared"])
        except KeyError:
            is_shared = 0
        try:
            kind_raw = r["kind"]
        except (KeyError, IndexError):
            kind_raw = None
        kind = str(kind_raw).strip() if kind_raw else "expert"
        if kind not in {"expert", "team"}:
            kind = "expert"
        try:
            runtime_kind_raw = r["runtime_kind"]
        except (KeyError, IndexError):
            runtime_kind_raw = None
        runtime_kind = str(runtime_kind_raw).strip() if runtime_kind_raw else RUNTIME_KIND_STANDARD
        if runtime_kind not in _VALID_RUNTIME_KINDS:
            runtime_kind = RUNTIME_KIND_STANDARD
        return cls(
            id=r["id"],
            agent_id=r["agent_id"],
            user_id=r["user_id"],
            name=r["name"],
            description=r["description"],
            persona_mbti=r["persona_mbti"],
            default_model=r["default_model"],
            system_prompt=r["system_prompt"],
            enabled=r["enabled"],
            config_json=r["config_json"],
            last_state=r["last_state"],
            last_error=r["last_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            icon=r["icon"],
            template_name=r["template_name"],
            is_shared=is_shared,
            color=_opt_str(r, "color"),
            icon_name=_opt_str(r, "icon_name"),
            icon_url=_opt_str(r, "icon_url"),
            skill_package_ids=_opt_str(r, "skill_package_ids"),
            published_expert_id=_opt_str(r, "published_expert_id"),
            welcome_message=_opt_str(r, "welcome_message"),
            knowledge_base_ids=_opt_str(r, "knowledge_base_ids"),
            mcp_servers=_opt_str(r, "mcp_servers"),
            kind=kind,
            runtime_kind=runtime_kind,
        )


class AgentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        agent_id: str,
        user_id: int | None,
        name: str,
        description: str | None = None,
        persona_mbti: str | None = None,
        default_model: str | None = None,
        system_prompt: str | None = None,
        config_json: str | None = None,
        icon: str | None = None,
        template_name: str | None = None,
        color: str | None = None,
        icon_name: str | None = None,
        icon_url: str | None = None,
        skill_package_ids: str | None = None,
        published_expert_id: str | None = None,
        welcome_message: str | None = None,
        knowledge_base_ids: str | None = None,
        mcp_servers: str | None = None,
        kind: str = "expert",
    ) -> str:
        ts = now_ts()
        agent_kind = kind if kind in {"expert", "team"} else "expert"
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO agents(agent_id, user_id, name, description, "
                "persona_mbti, default_model, system_prompt, enabled, config_json, icon, "
                "template_name, color, icon_name, icon_url, skill_package_ids, "
                "published_expert_id, welcome_message, knowledge_base_ids, mcp_servers, "
                "kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    user_id,
                    name,
                    description,
                    persona_mbti,
                    default_model,
                    system_prompt,
                    config_json,
                    icon,
                    template_name,
                    color,
                    icon_name,
                    icon_url,
                    skill_package_ids,
                    published_expert_id,
                    welcome_message,
                    knowledge_base_ids,
                    mcp_servers,
                    agent_kind,
                    ts,
                    ts,
                ),
            )
        return agent_id

    def create_project_task_runtime_with_quota(
        self,
        *,
        user_id: int,
        agent_id: str,
        name: str,
        default_model: str | None = None,
        system_prompt: str | None = None,
        icon_name: str | None = None,
        color: str | None = None,
    ) -> str:
        """Register one internal ``project_task_files`` runtime Agent (030A B1).

        The only insertion path allowed to write ``runtime_kind =
        'project_task_files'``; the public :meth:`create` and
        :meth:`update_config` never touch the column. Everything happens in
        ONE write transaction that serializes concurrent registrations —
        SQLite ``BEGIN IMMEDIATE``, PostgreSQL a fixed transaction-level
        advisory lock acquired before any count — then counts the owner's
        registered internal rows, counts the instance-wide internal rows,
        and inserts exactly one row. Fixed limits:
        ``PROJECT_TASK_FILES_OWNER_LIMIT`` per owner and
        ``PROJECT_TASK_FILES_GLOBAL_LIMIT`` per instance.

        On refusal raises :class:`ProjectTaskFilesQuotaError` carrying the
        hit scope; on SQL failure (e.g. duplicate ``agent_id`` or duplicate
        per-user ``name``) the driver exception propagates. Either way the
        transaction rolls back and no partial row is left behind. ``user_id``
        must be non-null because every internal runtime is owner-scoped, and
        ``name`` is expected to be a random non-colliding display name that
        carries no project id or task title.
        """
        if user_id is None:
            raise ValueError("project-task runtime requires an owner")
        ts = now_ts()
        with self._db.transaction() as conn:
            if self._db.dialect == "postgresql":
                conn.execute("SELECT pg_advisory_xact_lock(?)", (_PG_RUNTIME_QUOTA_LOCK_KEY,))
            owner_count = _count_project_task_runtimes(conn, user_id=user_id)
            if owner_count >= PROJECT_TASK_FILES_OWNER_LIMIT:
                raise ProjectTaskFilesQuotaError(
                    "owner", limit=PROJECT_TASK_FILES_OWNER_LIMIT, current=owner_count
                )
            global_count = _count_project_task_runtimes(conn)
            if global_count >= PROJECT_TASK_FILES_GLOBAL_LIMIT:
                raise ProjectTaskFilesQuotaError(
                    "global", limit=PROJECT_TASK_FILES_GLOBAL_LIMIT, current=global_count
                )
            conn.execute(
                "INSERT INTO agents(agent_id, user_id, name, default_model, system_prompt, "
                "icon_name, color, runtime_kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'project_task_files', ?, ?)",
                (
                    agent_id,
                    user_id,
                    name,
                    default_model,
                    system_prompt,
                    icon_name,
                    color,
                    ts,
                    ts,
                ),
            )
        return agent_id

    def list_unreferenced_project_task_runtimes(
        self, *, created_before: int, limit: int
    ) -> list[AgentRow]:
        """Internal runtimes with no thread reference older than *created_before*.

        Narrow recovery query for the bounded boot cleanup (030A B2): selects
        only DB-marked ``project_task_files`` rows whose ``created_at`` is
        strictly older than the cutoff and that have NO ``threads`` reference —
        a runtime with even one private thread is never selectable, even when
        every project link was detached. Standard rows are never selectable at
        any age. Oldest first, capped by *limit*; the caller re-checks both
        conditions per row before deleting anything.
        """
        sql = (
            "SELECT * FROM agents a "
            "WHERE a.runtime_kind = ? AND a.created_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM threads t WHERE t.agent_id = a.agent_id) "
            "ORDER BY a.created_at ASC, a.id ASC LIMIT ?"
        )
        with self._db.connect() as conn:
            rows = conn.execute(
                sql, (RUNTIME_KIND_PROJECT_TASK_FILES, created_before, limit)
            ).fetchall()
        return map_rows(rows, AgentRow)

    def count_project_task_runtimes(self, *, user_id: int | None = None) -> int:
        """Registered internal runtimes, instance-wide or for one owner.

        Read-only count driven by the DB-trusted ``runtime_kind`` marker, used
        by the 030A pre-check; the authoritative quota decision stays in
        :meth:`create_project_task_runtime_with_quota`.
        """
        with self._db.connect() as conn:
            return _count_project_task_runtimes(conn, user_id=user_id)

    def delete_project_task_runtime_if_unreferenced(self, agent_id: str) -> bool:
        """Delete one DB-marked internal runtime only while it owns no thread.

        Used by 030A compensation after a failed project-task transaction.
        Returns True only when the row existed, is an internal runtime, has no
        ``threads`` reference, and was deleted. Unknown ids, standard rows and
        linked runtimes return False with no write.
        """
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT runtime_kind FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None or str(row["runtime_kind"]) != RUNTIME_KIND_PROJECT_TASK_FILES:
                return False
            referenced = conn.execute(
                "SELECT 1 FROM threads WHERE agent_id = ? LIMIT 1", (agent_id,)
            ).fetchone()
            if referenced is not None:
                return False
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            return True

    def delete_project_task_file_task_rows(
        self, *, agent_id: str, thread_id: str, owner_user_id: int
    ) -> bool:
        """Delete every metadata row of one owned private file task, atomically.

        Guarded on the exact thread↔runtime↔owner binding: the thread must
        belong to *owner_user_id*, its ``agent_id`` must equal *agent_id*, and
        that Agent row must be a DB-marked ``project_task_files`` runtime owned
        by the same user. Only then are the session, share/content-grant,
        context, projection, link, and thread rows removed in one transaction;
        the runtime Agent row is deleted too when this was its last thread.
        Any binding guard failure returns False with zero writes, so a
        dedicated full-task delete can never remove another user's data or
        detach a standard Agent.

        030A B4: the transaction additionally requires that *thread_id* is the
        runtime's ONLY thread and that the thread DELETE affects exactly one
        row with no thread remaining afterwards. A violated invariant raises
        :class:`_ProjectTaskRowsGuard`, rolling every child delete back — an
        unexpected rowcount can never commit partially deleted metadata.
        """
        with self._db.transaction() as conn:
            thread = conn.execute(
                "SELECT agent_id, user_id FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                return False
            if str(thread["agent_id"]) != agent_id or int(thread["user_id"]) != owner_user_id:
                return False
            row = conn.execute(
                "SELECT runtime_kind, user_id FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if (
                row is None
                or str(row["runtime_kind"]) != RUNTIME_KIND_PROJECT_TASK_FILES
                or int(row["user_id"]) != owner_user_id
            ):
                return False
            siblings = conn.execute(
                "SELECT COUNT(*) AS n FROM threads WHERE agent_id = ? AND thread_id <> ?",
                (agent_id, thread_id),
            ).fetchone()
            if siblings is None or int(siblings["n"]) != 0:
                msg = f"runtime {agent_id!r} owns threads besides {thread_id!r}"
                raise _ProjectTaskRowsGuard(msg)
            # Children before parents: content grants reference shares,
            # shares/contexts reference the link, the link references the
            # thread. Sessions carry no FK to threads, so they are explicit.
            conn.execute("DELETE FROM sessions WHERE thread_id = ?", (thread_id,))
            conn.execute(
                "DELETE FROM project_task_content_grants WHERE thread_id = ?", (thread_id,)
            )
            conn.execute("DELETE FROM project_task_shares WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM project_task_contexts WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM thread_history_projection WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM project_task_links WHERE thread_id = ?", (thread_id,))
            deleted = conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
            if getattr(deleted, "rowcount", 1) != 1:
                msg = f"thread {thread_id!r} delete affected an unexpected rowcount"
                raise _ProjectTaskRowsGuard(msg)
            remaining = conn.execute(
                "SELECT 1 FROM threads WHERE agent_id = ? LIMIT 1", (agent_id,)
            ).fetchone()
            if remaining is not None:
                msg = f"runtime {agent_id!r} gained a thread mid-delete"
                raise _ProjectTaskRowsGuard(msg)
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            return True

    def get(self, agent_id: str) -> AgentRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return AgentRow.from_row(r) if r else None

    def list_by_user(self, user_id: int, *, include_disabled: bool = True) -> list[AgentRow]:
        sql = "SELECT * FROM agents WHERE user_id = ?"
        if not include_disabled:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, (user_id,)).fetchall()
        return map_rows(rows, AgentRow)

    def list_all(self, *, include_disabled: bool = True) -> list[AgentRow]:
        sql = "SELECT * FROM agents"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return map_rows(rows, AgentRow)

    def set_enabled(self, agent_id: str, enabled: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET enabled = ?, updated_at = ? WHERE agent_id = ?",
                (bool_int(enabled), now_ts(), agent_id),
            )

    def set_shared(self, agent_id: str, shared: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET is_shared = ?, updated_at = ? WHERE agent_id = ?",
                (bool_int(shared), now_ts(), agent_id),
            )

    def list_shared(self, *, exclude_user_id: int | None = None) -> list[AgentRow]:
        sql = "SELECT * FROM agents WHERE is_shared = 1 AND enabled = 1"
        params: list[object] = []
        if exclude_user_id is not None:
            sql += " AND user_id != ?"
            params.append(exclude_user_id)
        sql += " ORDER BY created_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, AgentRow)

    def set_state(self, agent_id: str, state: str, *, error: str | None = None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET last_state = ?, last_error = ?, updated_at = ? "
                "WHERE agent_id = ?",
                (state, error, now_ts(), agent_id),
            )

    def update_config(
        self,
        agent_id: str,
        *,
        name: str | None | object = UNSET,
        description: str | None | object = UNSET,
        persona_mbti: str | None | object = UNSET,
        default_model: str | None | object = UNSET,
        system_prompt: str | None | object = UNSET,
        config_json: str | None | object = UNSET,
        icon: str | None | object = UNSET,
        template_name: str | None | object = UNSET,
        color: str | None | object = UNSET,
        icon_name: str | None | object = UNSET,
        icon_url: str | None | object = UNSET,
        skill_package_ids: str | None | object = UNSET,
        published_expert_id: str | None | object = UNSET,
        welcome_message: str | None | object = UNSET,
        knowledge_base_ids: str | None | object = UNSET,
        mcp_servers: str | None | object = UNSET,
    ) -> None:
        fields, params = optional_updates(
            [
                ("name", name),
                ("description", description),
                ("persona_mbti", persona_mbti),
                ("default_model", default_model),
                ("system_prompt", system_prompt),
                ("config_json", config_json),
                ("icon", icon),
                ("template_name", template_name),
                ("color", color),
                ("icon_name", icon_name),
                ("icon_url", icon_url),
                ("skill_package_ids", skill_package_ids),
                ("published_expert_id", published_expert_id),
                ("welcome_message", welcome_message),
                ("knowledge_base_ids", knowledge_base_ids),
                ("mcp_servers", mcp_servers),
            ]
        )
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_ts())
        params.append(agent_id)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE agents SET {', '.join(fields)} WHERE agent_id = ?", params)

    def delete(self, agent_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
