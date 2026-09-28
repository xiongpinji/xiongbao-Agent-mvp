"""Shared SQL lock order for project plan readers and writers.

Acquire sorted membership rows, the project row, catalog state, sorted
catalog options, and finally sorted todos. Catalog state also serializes
association reads with all todo writes on PostgreSQL. SQLite transactions
hold their writer lock before the first read.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from octop.infra.db.pool import DatabasePool


def lock_suffix(db: DatabasePool, *, write: bool) -> str:
    if db.dialect != "postgresql":
        return ""
    return " FOR UPDATE" if write else " FOR SHARE"


def member_roles(
    db: DatabasePool, conn: Any, project_id: str, user_ids: Sequence[int], *, write: bool
) -> dict[int, str]:
    roles: dict[int, str] = {}
    for user_id in sorted(set(user_ids)):
        row = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?"
            + lock_suffix(db, write=write),
            (project_id, user_id),
        ).fetchone()
        if row is not None:
            roles[user_id] = str(row["role"])
    return roles


def project_row(db: DatabasePool, conn: Any, project_id: str, *, write: bool) -> Any:
    return conn.execute(
        "SELECT archived FROM project_spaces WHERE project_id = ?" + lock_suffix(db, write=write),
        (project_id,),
    ).fetchone()


def catalog_revision(db: DatabasePool, conn: Any, project_id: str, *, write: bool) -> int:
    row = conn.execute(
        "SELECT revision FROM project_todo_catalog_state WHERE project_id = ?"
        + lock_suffix(db, write=write),
        (project_id,),
    ).fetchone()
    if row is None:
        # Missing migrated state is a schema failure, never a synthetic catalog.
        raise RuntimeError("project todo catalog state is missing")
    return int(row["revision"])


def catalog_items(db: DatabasePool, conn: Any, project_id: str) -> tuple[list[Any], list[Any]]:
    """Lock the bounded catalog in category then public-ID order."""
    priorities = conn.execute(
        "SELECT * FROM project_todo_priorities WHERE project_id = ? ORDER BY priority_id"
        + lock_suffix(db, write=True),
        (project_id,),
    ).fetchall()
    tags = conn.execute(
        "SELECT * FROM project_todo_tags WHERE project_id = ? ORDER BY tag_id"
        + lock_suffix(db, write=True),
        (project_id,),
    ).fetchall()
    return priorities, tags


def server_today(timezone: str) -> str:
    """Called after acquiring the transaction's locks, including after waits."""
    return datetime.now(ZoneInfo(timezone)).date().isoformat()
