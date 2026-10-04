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


DISPLAY_REVISION_MAX = 9007199254740991


def checked_display_revision(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= DISPLAY_REVISION_MAX:
        raise RuntimeError("todo display state is missing or outside its range")
    return value


def display_revision(
    db: DatabasePool, conn: Any, project_id: str, todo_id: str, *, write: bool
) -> int:
    row = conn.execute(
        "SELECT revision FROM project_todo_display_state WHERE project_id=? AND todo_id=?"
        + lock_suffix(db, write=write),
        (project_id, todo_id),
    ).fetchone()
    return checked_display_revision(None if row is None else row["revision"])


def bump_display(conn: Any, project_id: str, todo_id: str, timestamp: int) -> None:
    changed = conn.execute(
        "UPDATE project_todo_display_state SET revision=revision+1,updated_at=? "
        "WHERE project_id=? AND todo_id=? AND revision>=1 AND revision<?",
        (timestamp, project_id, todo_id, DISPLAY_REVISION_MAX),
    )
    if changed.rowcount != 1:
        raise RuntimeError("todo display state is missing or exhausted")


def _user_delete_targets(conn: Any, user_ids: Sequence[int]) -> dict[str, set[Any]]:
    marks = ",".join("?" for _ in user_ids)
    todos = conn.execute(
        "SELECT project_id,todo_id FROM project_todos WHERE creator_user_id IN ("
        + marks
        + ") OR assignee_user_id IN ("
        + marks
        + ")",
        (*user_ids, *user_ids),
    ).fetchall()
    projects = {str(row["project_id"]) for row in todos}
    for table, column in (("project_members", "user_id"), ("project_spaces", "creator_user_id")):
        rows = conn.execute(
            "SELECT project_id FROM " + table + " WHERE " + column + " IN (" + marks + ")", user_ids
        ).fetchall()
        projects.update(str(row["project_id"]) for row in rows)
    memberships: set[Any] = set()
    if projects:
        memberships = {
            (int(row["user_id"]), str(row["project_id"]))
            for row in conn.execute(
                "SELECT user_id,project_id FROM project_members WHERE project_id IN ("
                + ",".join("?" for _ in projects)
                + ")",
                sorted(projects),
            ).fetchall()
        }
    todo_ids = {(str(row["todo_id"]), str(row["project_id"])) for row in todos}
    metadata: set[Any] = set()
    for todo_id, project_id in sorted(todo_ids):
        metadata.update(
            (str(row["attachment_id"]), str(row["object_key"]))
            for row in conn.execute(
                "SELECT attachment_id,object_key FROM project_todo_attachments WHERE project_id=? AND todo_id=?",
                (project_id, todo_id),
            ).fetchall()
        )
    return {"projects": projects, "members": memberships, "todos": todo_ids, "metadata": metadata}


def prepare_user_delete_in_connection(db: DatabasePool, conn: Any, user_ids: Sequence[int]) -> None:
    """Acquire all project resource phases before the original users DELETE."""
    ids = sorted(set(user_ids))
    if not ids:
        return
    targets = _user_delete_targets(conn, ids)
    suffix = lock_suffix(db, write=True)
    for user_id, project_id in sorted(targets["members"]):
        member_roles(db, conn, project_id, [user_id], write=True)
    for project_id in sorted(targets["projects"]):
        project_row(db, conn, project_id, write=True)
    for project_id in sorted(targets["projects"]):
        catalog_revision(db, conn, project_id, write=True)
    for table, public_id in (
        ("project_todo_priorities", "priority_id"),
        ("project_todo_tags", "tag_id"),
    ):
        for project_id in sorted(targets["projects"]):
            conn.execute(
                "SELECT "
                + public_id
                + " FROM "
                + table
                + " WHERE project_id=? ORDER BY "
                + public_id
                + suffix,
                (project_id,),
            ).fetchall()
    for todo_id, project_id in sorted(targets["todos"]):
        conn.execute(
            "SELECT todo_id FROM project_todos WHERE project_id=? AND todo_id=?" + suffix,
            (project_id, todo_id),
        ).fetchone()
    for todo_id, project_id in sorted(targets["todos"]):
        display_revision(db, conn, project_id, todo_id, write=True)
    for project_id in sorted(targets["projects"]):
        row = conn.execute(
            "SELECT project_id FROM project_todo_attachment_usage WHERE project_id=?" + suffix,
            (project_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("D1 project usage state is missing")
    for todo_id, project_id in sorted(targets["todos"]):
        row = conn.execute(
            "SELECT todo_id FROM project_todo_attachment_state WHERE project_id=? AND todo_id=?"
            + suffix,
            (project_id, todo_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("D1 attachment state is missing")
    for attachment_id, _ in sorted(targets["metadata"]):
        conn.execute(
            "SELECT attachment_id FROM project_todo_attachments WHERE attachment_id=?" + suffix,
            (attachment_id,),
        ).fetchone()
    for _, key in sorted(targets["metadata"]):
        conn.execute(
            "SELECT object_key FROM project_todo_attachment_retained_objects WHERE object_key=?"
            + suffix,
            (key,),
        ).fetchone()
    for user_id in ids:
        conn.execute("SELECT id FROM users WHERE id=?" + suffix, (user_id,)).fetchone()
    located = _user_delete_targets(conn, ids)
    if any(not located[key].issubset(targets[key]) for key in targets):
        raise RuntimeError("user delete resource set expanded; transaction must be retried")
