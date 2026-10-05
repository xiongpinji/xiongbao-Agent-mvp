"""Threads table — conversation metadata for history listing and /resume."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, bool_int, map_rows, now_ts
from octop.infra.utils.project_plan_keys import normalize_project_plan_key

MAX_THREAD_ARTIFACTS = 200
MAX_ARCHIVE_TIMESTAMP = 9007199254740991


def parse_archived_at(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 < value <= MAX_ARCHIVE_TIMESTAMP:
        raise ValueError("invalid stored thread archive timestamp")
    return value


@dataclass(frozen=True)
class ArchiveMutationRow:
    thread_id: str
    agent_id: str
    archived_at: int | None


@dataclass(frozen=True)
class ArchivedThreadSummary:
    thread_id: str
    agent_id: str
    title: str | None
    channel_type: str
    created_at: int
    last_active: int
    archived_at: int
    mode: str

    @classmethod
    def from_row(cls, row: DbRow) -> ArchivedThreadSummary:
        timestamp = parse_archived_at(row["archived_at"])
        assert timestamp is not None
        return cls(
            row["thread_id"],
            row["agent_id"],
            row["title"],
            row["channel_type"],
            row["created_at"],
            row["last_active"],
            timestamp,
            row["mode"],
        )


def _archive_owned_qualification() -> str:
    # All authority comes from held/current SQL rows, including the exact
    # dashboard DM identity. Session binding and project attribution are not
    # management prerequisites and are never changed by this operation.
    return (
        "t.user_id = ? AND ((a.runtime_kind = 'standard' AND "
        "(a.user_id = t.user_id OR a.is_shared = 1 OR ? = 1)) OR "
        "(a.runtime_kind = 'project_task_files' AND a.user_id = t.user_id "
        "AND t.channel_type = 'dashboard' "
        "AND t.session_key = a.agent_id || ':dashboard:' || CAST(t.user_id AS TEXT) || ':dm' "
        "AND NOT EXISTS (SELECT 1 FROM threads sibling "
        "WHERE sibling.agent_id = a.agent_id AND sibling.thread_id <> t.thread_id)))"
    )


def parse_thread_artifacts(raw: object) -> list[str]:
    """Decode the threads.artifacts JSON column into unique non-empty paths."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parsed: object = list(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return []
    else:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def merge_thread_artifacts(existing: Sequence[str], incoming: Sequence[str]) -> list[str]:
    """Append new paths, keeping insertion order and capping length."""
    merged = parse_thread_artifacts([*existing, *incoming])
    if len(merged) <= MAX_THREAD_ARTIFACTS:
        return merged
    return merged[-MAX_THREAD_ARTIFACTS:]


@dataclass(frozen=True)
class ThreadRow:
    id: int
    thread_id: str
    agent_id: str
    user_id: int
    channel_type: str
    session_key: str
    title: str | None
    last_active: int
    created_at: int
    pinned: bool = False
    model_ref: str | None = None
    reasoning_mode: str | None = None
    reasoning_effort: str | None = None
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    conversation_mode: str | None = None
    pending_plan_path: str | None = None
    hitl_policy: str | None = None
    archived_at: int | None = None

    @classmethod
    def from_row(cls, r: DbRow) -> ThreadRow:
        try:
            raw_artifacts = r["artifacts"]
        except (KeyError, IndexError):
            raw_artifacts = None
        try:
            conversation_mode = r["conversation_mode"]
        except (KeyError, IndexError):
            conversation_mode = None
        try:
            pending_plan_path = r["pending_plan_path"]
        except (KeyError, IndexError):
            pending_plan_path = None
        try:
            hitl_policy = r["hitl_policy"]
        except (KeyError, IndexError):
            hitl_policy = None
        return cls(
            id=r["id"],
            thread_id=r["thread_id"],
            agent_id=r["agent_id"],
            user_id=r["user_id"],
            channel_type=r["channel_type"],
            session_key=r["session_key"],
            title=r["title"],
            last_active=r["last_active"],
            created_at=r["created_at"],
            pinned=bool(r["pinned"]),
            model_ref=r["model_ref"],
            reasoning_mode=r["reasoning_mode"],
            reasoning_effort=r["reasoning_effort"],
            artifacts=tuple(parse_thread_artifacts(raw_artifacts)),
            conversation_mode=str(conversation_mode) if conversation_mode else None,
            pending_plan_path=str(pending_plan_path) if pending_plan_path else None,
            hitl_policy=str(hitl_policy) if hitl_policy else None,
            archived_at=parse_archived_at(r["archived_at"]),
        )


def clip_thread_title(title: str, *, max_len: int = 40) -> str:
    """Bound stored thread titles; longer names end with an ellipsis.

    Whitespace is collapsed so auto-titles from multi-line messages stay tidy.
    Exact-length titles (no longer source) are kept without forcing ``…``.
    """
    text = " ".join((title or "").split())
    if not text:
        return ""
    if max_len <= 1:
        return "…"
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1].rstrip()}…"


def repair_legacy_thread_title(title: str | None, *, max_len: int = 40) -> str | None:
    """Rewrite hard-cut titles that hit the cap without an ellipsis.

    Pre-clipping storage truncated at ``max_len`` with no ``…``. New writes
    from :func:`clip_thread_title` always end with ``…`` when shortened.
    Title-repair migration and optional repo helpers use this.

    Legitimate full-length titles of exactly ``max_len`` without ellipsis are
    indistinguishable and get the same rewrite once during migration — rare.
    """
    if title is None:
        return None
    text = " ".join(title.split())
    if not text:
        return None
    if text.endswith("…") or text.endswith("..."):
        fixed = clip_thread_title(text, max_len=max_len)
        return fixed or None
    if len(text) == max_len:
        return f"{text[: max_len - 1].rstrip()}…"
    fixed = clip_thread_title(text, max_len=max_len)
    return fixed or None


def repair_all_legacy_thread_titles(db: DatabasePool) -> int:
    """Persist :func:`repair_legacy_thread_title` for every row. Returns update count."""
    updated = 0
    with db.transaction() as conn:
        if db.dialect == "postgresql":
            has_key = (
                conn.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name='threads' "
                    "AND column_name='title_search_key'"
                ).fetchone()
                is not None
            )
        else:
            has_key = any(
                row["name"] == "title_search_key"
                for row in conn.execute("PRAGMA table_info(threads)")
            )
        rows = conn.execute(
            "SELECT thread_id, title FROM threads WHERE title IS NOT NULL"
        ).fetchall()
        for r in rows:
            thread_id = str(r["thread_id"])
            title = r["title"]
            fixed = repair_legacy_thread_title(title if isinstance(title, str) else str(title))
            if fixed is not None and fixed != title:
                if has_key:
                    conn.execute(
                        "UPDATE threads SET title = ?, title_search_key = ? WHERE thread_id = ?",
                        (fixed, normalize_project_plan_key(fixed), thread_id),
                    )
                else:
                    conn.execute(
                        "UPDATE threads SET title = ? WHERE thread_id = ?", (fixed, thread_id)
                    )
                updated += 1
    return updated


class ThreadRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def insert(
        self,
        *,
        thread_id: str,
        agent_id: str,
        user_id: int,
        channel_type: str,
        session_key: str,
        title: str | None = None,
        last_active: int | None = None,
    ) -> None:
        ts = now_ts()
        stored_title = clip_thread_title(title) if title else None
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO threads(thread_id, agent_id, user_id, channel_type, "
                "session_key, title, title_search_key, last_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    agent_id,
                    user_id,
                    channel_type,
                    session_key,
                    stored_title,
                    normalize_project_plan_key(stored_title or ""),
                    ts if last_active is None else last_active,
                    ts,
                ),
            )
            conn.execute(
                "INSERT INTO thread_history_projection(thread_id, status, updated_at, error) "
                "VALUES (?, 'ready', ?, NULL) ON CONFLICT(thread_id) DO NOTHING",
                (thread_id, ts),
            )

    def get(self, thread_id: str) -> ThreadRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return ThreadRow.from_row(r) if r else None

    def list_by_agent(self, *, agent_id: str, limit: int = 50) -> list[ThreadRow]:
        # last_active=0 is "no turns yet" (has_messages sentinel). Fall back to
        # created_at so brand-new empty threads sort to the top of the sidebar
        # instead of sinking below every previously active chat.
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM threads WHERE agent_id = ? "
                "ORDER BY pinned DESC, "
                "CASE WHEN last_active > 0 THEN last_active ELSE created_at END DESC, "
                "thread_id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        return map_rows(rows, ThreadRow)

    def list_by_agent_user(
        self,
        *,
        agent_id: str,
        user_id: int,
        limit: int = 50,
        q: str = "",
        archived: bool | None = False,
    ) -> list[ThreadRow]:
        needle = normalize_project_plan_key(q.strip())
        sql = "SELECT * FROM threads WHERE agent_id = ? AND user_id = ? "
        params: list[object] = [agent_id, user_id]
        if archived is not None:
            sql += "AND archived_at IS " + ("NOT NULL " if archived else "NULL ")
        if needle:
            expression = (
                'strpos(title_search_key COLLATE "C", ?)'
                if self._db.dialect == "postgresql"
                else "instr(title_search_key, ?)"
            )
            sql += f"AND {expression} > 0 "
            params.append(needle)
        sql += (
            "ORDER BY archived_at DESC, thread_id DESC LIMIT ?"
            if archived
            else (
                "ORDER BY pinned DESC, "
                "CASE WHEN last_active > 0 THEN last_active ELSE created_at END DESC, "
                "thread_id DESC LIMIT ?"
            )
        )
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ThreadRow)

    def set_archive_owned(
        self,
        *,
        thread_id: str,
        user_id: int,
        actor_is_admin: bool,
        archived: bool,
        now: int,
    ) -> ArchiveMutationRow | None:
        if type(archived) is not bool or type(actor_is_admin) is not bool:
            raise ValueError("archive flags must be booleans")
        timestamp = parse_archived_at(now)
        if timestamp is None:
            raise ValueError("archive time must be positive")
        with self._db.transaction() as conn:
            # PostgreSQL locks Agent before thread, then rechecks every fact.
            # SQLite BEGIN IMMEDIATE serializes this entire qualification/write.
            if self._db.dialect == "postgresql":
                candidate = conn.execute(
                    "SELECT agent_id FROM threads WHERE thread_id = ?", (thread_id,)
                ).fetchone()
                if candidate is None:
                    return None
                conn.execute(
                    "SELECT agent_id FROM agents WHERE agent_id = ? FOR UPDATE",
                    (candidate["agent_id"],),
                ).fetchone()
            sql = (
                "SELECT t.thread_id,t.agent_id,t.archived_at FROM threads t "
                "JOIN agents a ON a.agent_id=t.agent_id WHERE t.thread_id=? AND "
                + _archive_owned_qualification()
            )
            params: list[object] = [thread_id, user_id, bool_int(actor_is_admin)]
            if self._db.dialect == "postgresql":
                sql += " AND a.agent_id=? FOR UPDATE OF t"
                params.append(candidate["agent_id"])
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            previous = parse_archived_at(row["archived_at"])
            value = (previous if previous is not None else timestamp) if archived else None
            if value != previous:
                conn.execute(
                    "UPDATE threads SET archived_at=? WHERE thread_id=?", (value, thread_id)
                )
            return ArchiveMutationRow(row["thread_id"], row["agent_id"], value)

    def list_archived_by_user(
        self,
        *,
        user_id: int,
        actor_is_admin: bool,
        q: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> list[ArchivedThreadSummary]:
        sql = (
            "SELECT t.thread_id,t.agent_id,t.title,t.channel_type,t.created_at,t.last_active,"
            "t.archived_at,CASE WHEN a.runtime_kind='project_task_files' THEN 'files' ELSE 'chat' END AS mode "
            "FROM threads t JOIN agents a ON a.agent_id=t.agent_id "
            "WHERE t.archived_at IS NOT NULL AND " + _archive_owned_qualification()
        )
        params: list[object] = [user_id, bool_int(actor_is_admin)]
        needle = normalize_project_plan_key(q.strip())
        if needle:
            expression = (
                'strpos(t.title_search_key COLLATE "C", ?)'
                if self._db.dialect == "postgresql"
                else "instr(t.title_search_key, ?)"
            )
            sql += f" AND {expression} > 0"
            params.append(needle)
        sql += " ORDER BY t.archived_at DESC,t.thread_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, ArchivedThreadSummary)

    def list_by_session(self, *, session_key: str, limit: int = 50) -> list[ThreadRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM threads WHERE session_key = ? "
                "ORDER BY "
                "CASE WHEN last_active > 0 THEN last_active ELSE created_at END DESC, "
                "thread_id DESC LIMIT ?",
                (session_key, limit),
            ).fetchall()
        return map_rows(rows, ThreadRow)

    def set_title_if_null(self, thread_id: str, title: str) -> None:
        stored_title = clip_thread_title(title)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE threads SET title = ?, title_search_key = ? WHERE thread_id = ? AND title IS NULL",
                (stored_title, normalize_project_plan_key(stored_title), thread_id),
            )

    def update_title(self, thread_id: str, title: str) -> None:
        stored_title = clip_thread_title(title)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE threads SET title = ?, title_search_key = ? WHERE thread_id = ?",
                (stored_title, normalize_project_plan_key(stored_title), thread_id),
            )

    def set_pinned(self, thread_id: str, pinned: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE threads SET pinned = ? WHERE thread_id = ?",
                (bool_int(pinned), thread_id),
            )

    def update_composer(
        self,
        thread_id: str,
        *,
        model_ref: str | None | object = ...,
        reasoning_mode: str | None | object = ...,
        reasoning_effort: str | None | object = ...,
        conversation_mode: str | None | object = ...,
        pending_plan_path: str | None | object = ...,
        hitl_policy: str | None | object = ...,
    ) -> None:
        fields: list[str] = []
        params: list[object] = []
        for column, value in (
            ("model_ref", model_ref),
            ("reasoning_mode", reasoning_mode),
            ("reasoning_effort", reasoning_effort),
            ("conversation_mode", conversation_mode),
            ("pending_plan_path", pending_plan_path),
            ("hitl_policy", hitl_policy),
        ):
            if value is ...:
                continue
            fields.append(f"{column} = ?")
            params.append(value)
        if not fields:
            return
        params.append(thread_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE threads SET {', '.join(fields)} WHERE thread_id = ?",
                params,
            )

    def touch_last_active(self, thread_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE threads SET last_active = ? WHERE thread_id = ?",
                (now_ts(), thread_id),
            )

    def append_artifacts(self, thread_id: str, paths: Sequence[str]) -> None:
        incoming = [p.strip() for p in paths if isinstance(p, str) and p.strip()]
        if not incoming:
            return
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT artifacts FROM threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if row is None:
                return
            current = parse_thread_artifacts(row["artifacts"])
            merged = merge_thread_artifacts(current, incoming)
            if merged == current:
                return
            conn.execute(
                "UPDATE threads SET artifacts = ? WHERE thread_id = ?",
                (json.dumps(merged, ensure_ascii=False), thread_id),
            )

    def delete(self, thread_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
