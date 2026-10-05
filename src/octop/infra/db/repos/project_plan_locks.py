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


def relationship_projection(
    conn: Any, project_id: str, todo_id: str
) -> tuple[str | None, int, int, int | None]:
    state = conn.execute(
        "SELECT revision FROM project_todo_children_state WHERE project_id=? AND todo_id=?",
        (project_id, todo_id),
    ).fetchone()
    revision = checked_display_revision(None if state is None else state["revision"])
    relation = conn.execute(
        "SELECT parent_todo_id FROM project_todo_children WHERE project_id=? AND child_todo_id=?",
        (project_id, todo_id),
    ).fetchone()
    if relation is not None:
        return str(relation["parent_todo_id"]), 0, 0, None
    counts = conn.execute(
        "SELECT COUNT(*) AS active,SUM(CASE WHEN c.status='done' THEN 1 ELSE 0 END) AS done FROM project_todo_children r JOIN project_todos c ON c.project_id=r.project_id AND c.todo_id=r.child_todo_id JOIN project_todos p ON p.project_id=r.project_id AND p.todo_id=r.parent_todo_id WHERE r.project_id=? AND r.parent_todo_id=? AND p.deleted_at IS NULL AND c.deleted_at IS NULL",
        (project_id, todo_id),
    ).fetchone()
    active, done = int(counts["active"]), int(counts["done"] or 0)
    if not 0 <= done <= active <= 100:
        raise RuntimeError("D2 active child counts are outside their range")
    return None, active, done, revision


RELATIONSHIP_PAGE_COLUMNS = (
    ", relation_state.revision AS __children_revision"
    ", incoming_relation.parent_todo_id AS __parent_todo_id"
    ", COALESCE(child_counts.active,0) AS __children_count"
    ", COALESCE(child_counts.done,0) AS __done_children_count"
)


def relationship_page_joins(todo_alias: str) -> str:
    """Join one project's relation projection into the existing page SELECT.

    The caller supplies a fixed SQL alias and one project-ID bind before its
    following WHERE binds. Missing required state remains NULL for validation.
    """
    return (
        " LEFT JOIN project_todo_children_state relation_state ON "
        f"relation_state.project_id={todo_alias}.project_id AND relation_state.todo_id={todo_alias}.todo_id"
        " LEFT JOIN project_todo_children incoming_relation ON "
        f"incoming_relation.project_id={todo_alias}.project_id AND incoming_relation.child_todo_id={todo_alias}.todo_id"
        " LEFT JOIN (SELECT r.project_id,r.parent_todo_id,COUNT(*) AS active,"
        "SUM(CASE WHEN c.status='done' THEN 1 ELSE 0 END) AS done "
        "FROM project_todo_children r "
        "JOIN project_todos c ON c.project_id=r.project_id AND c.todo_id=r.child_todo_id "
        "JOIN project_todos parent ON parent.project_id=r.project_id AND parent.todo_id=r.parent_todo_id "
        "WHERE r.project_id=? AND c.deleted_at IS NULL AND parent.deleted_at IS NULL "
        "GROUP BY r.project_id,r.parent_todo_id) child_counts ON "
        f"child_counts.project_id={todo_alias}.project_id AND child_counts.parent_todo_id={todo_alias}.todo_id"
    )


def relationship_from_page_row(row: Any) -> tuple[str | None, int, int, int | None]:
    """Validate the same relationship DTO contract without another SQL read."""
    revision = checked_display_revision(row["__children_revision"])
    if row["__parent_todo_id"] is not None:
        return str(row["__parent_todo_id"]), 0, 0, None
    active, done = int(row["__children_count"]), int(row["__done_children_count"])
    if not 0 <= done <= active <= 100:
        raise RuntimeError("D2 active child counts are outside their range")
    return None, active, done, revision


def bump_children(conn: Any, project_id: str, todo_id: str, timestamp: int) -> None:
    changed = conn.execute(
        "UPDATE project_todo_children_state SET revision=revision+1,updated_at=? WHERE project_id=? AND todo_id=? AND revision BETWEEN 1 AND ?",
        (timestamp, project_id, todo_id, DISPLAY_REVISION_MAX - 1),
    )
    if changed.rowcount != 1:
        raise RuntimeError("D2 children state is missing or exhausted")


def hierarchy_revision(conn: Any, project_id: str) -> int:
    row = conn.execute(
        "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?", (project_id,)
    ).fetchone()
    return checked_display_revision(None if row is None else row["revision"])


def bump_hierarchy(conn: Any, project_id: str, timestamp: int) -> None:
    changed = conn.execute(
        "UPDATE project_plan_hierarchy_state SET revision=revision+1,updated_at=? WHERE project_id=? AND revision BETWEEN 1 AND ?",
        (timestamp, project_id, DISPLAY_REVISION_MAX - 1),
    )
    if changed.rowcount != 1:
        raise RuntimeError("D2 hierarchy state is missing or exhausted")


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
    doomed = {
        str(row["todo_id"])
        for row in conn.execute(
            "SELECT todo_id FROM project_todos WHERE creator_user_id IN (" + marks + ")", user_ids
        ).fetchall()
    }
    relations: set[Any] = set()
    results: set[Any] = set()
    for row in conn.execute(
        "SELECT project_id,parent_todo_id,child_todo_id,created_by FROM project_todo_children r WHERE created_by IN ("
        + marks
        + ") OR EXISTS (SELECT 1 FROM project_todos t WHERE t.creator_user_id IN ("
        + marks
        + ") AND (t.todo_id=r.parent_todo_id OR t.todo_id=r.child_todo_id))",
        (*user_ids, *user_ids),
    ).fetchall():
        if (
            str(row["parent_todo_id"]) in doomed
            or str(row["child_todo_id"]) in doomed
            or row["created_by"] in user_ids
        ):
            pid = str(row["project_id"])
            relations.add((pid, str(row["parent_todo_id"]), str(row["child_todo_id"])))
            projects.add(pid)
            todo_ids.update((str(row[key]), pid) for key in ("parent_todo_id", "child_todo_id"))
    for row in conn.execute(
        "SELECT project_id,parent_todo_id,child_todo_id,actor_user_id,client_request_id FROM project_todo_child_create_requests q WHERE actor_user_id IN ("
        + marks
        + ") OR EXISTS (SELECT 1 FROM project_todos t WHERE t.creator_user_id IN ("
        + marks
        + ") AND (t.todo_id=q.parent_todo_id OR t.todo_id=q.child_todo_id))",
        (*user_ids, *user_ids),
    ).fetchall():
        if (
            row["actor_user_id"] in user_ids
            or str(row["parent_todo_id"]) in doomed
            or str(row["child_todo_id"]) in doomed
        ):
            pid = str(row["project_id"])
            projects.add(pid)
            results.add(
                (
                    pid,
                    str(row["parent_todo_id"]),
                    int(row["actor_user_id"]),
                    str(row["client_request_id"]),
                )
            )
            for key in ("parent_todo_id", "child_todo_id"):
                if conn.execute(
                    "SELECT todo_id FROM project_todos WHERE project_id=? AND todo_id=?",
                    (pid, row[key]),
                ).fetchone():
                    todo_ids.add((str(row[key]), pid))
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
    metadata: set[Any] = set()
    for todo_id, project_id in sorted(todo_ids):
        metadata.update(
            (str(row["attachment_id"]), str(row["object_key"]))
            for row in conn.execute(
                "SELECT attachment_id,object_key FROM project_todo_attachments WHERE project_id=? AND todo_id=?",
                (project_id, todo_id),
            ).fetchall()
        )
    return {
        "projects": projects,
        "members": memberships,
        "todos": todo_ids,
        "metadata": metadata,
        "relations": relations,
        "results": results,
    }


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
    for project_id, parent, child in sorted(targets["relations"]):
        conn.execute(
            "SELECT child_todo_id FROM project_todo_children WHERE project_id=? AND parent_todo_id=? AND child_todo_id=?"
            + suffix,
            (project_id, parent, child),
        ).fetchone()
    for todo_id, project_id in sorted(targets["todos"]):
        row = conn.execute(
            "SELECT revision FROM project_todo_children_state WHERE project_id=? AND todo_id=?"
            + suffix,
            (project_id, todo_id),
        ).fetchone()
        checked_display_revision(None if row is None else row["revision"])
    for project_id in sorted(targets["projects"]):
        row = conn.execute(
            "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?" + suffix,
            (project_id,),
        ).fetchone()
        checked_display_revision(None if row is None else row["revision"])
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
    for key in sorted(targets["results"]):
        conn.execute(
            "SELECT result_state FROM project_todo_child_create_requests WHERE project_id=? AND parent_todo_id=? AND actor_user_id=? AND client_request_id=?"
            + suffix,
            key,
        ).fetchone()
    for user_id in ids:
        conn.execute("SELECT id FROM users WHERE id=?" + suffix, (user_id,)).fetchone()
    located = _user_delete_targets(conn, ids)
    if any(not located[key].issubset(targets[key]) for key in targets):
        raise RuntimeError("user delete resource set expanded; transaction must be retried")

    # Plan once for the entire prune/delete batch, before the unchanged users DELETE.
    marks = ",".join("?" for _ in ids)
    doomed = {
        (str(row["todo_id"]), str(row["project_id"]))
        for row in conn.execute(
            "SELECT todo_id,project_id FROM project_todos WHERE creator_user_id IN (" + marks + ")",
            ids,
        ).fetchall()
    }
    assignee_survivors = {
        (str(row["todo_id"]), str(row["project_id"]))
        for row in conn.execute(
            "SELECT todo_id,project_id FROM project_todos WHERE assignee_user_id IN ("
            + marks
            + ") AND creator_user_id NOT IN ("
            + marks
            + ")",
            (*ids, *ids),
        ).fetchall()
    }
    removed = {
        (pid, parent, child)
        for pid, parent, child in targets["relations"]
        if (parent, pid) in doomed or (child, pid) in doomed
    }
    surviving_parents = {(parent, pid) for pid, parent, _ in removed if (parent, pid) not in doomed}
    promotions = {
        (child, pid)
        for pid, parent, child in removed
        if (parent, pid) in doomed and (child, pid) not in doomed
    }
    stamp = int(datetime.now().timestamp())
    for key in sorted(targets["results"]):
        conn.execute(
            "UPDATE project_todo_child_create_requests SET result_state='invalidated',invalidated_at=? WHERE project_id=? AND parent_todo_id=? AND actor_user_id=? AND client_request_id=? AND result_state='recorded'",
            (stamp, *key),
        )
    for pid, parent, child in sorted(removed):
        changed = conn.execute(
            "DELETE FROM project_todo_children WHERE project_id=? AND parent_todo_id=? AND child_todo_id=?",
            (pid, parent, child),
        )
        if changed.rowcount != 1:
            raise RuntimeError("D2 user delete relationship changed")
    for parent, pid in sorted(surviving_parents):
        bump_children(conn, pid, parent, stamp)
    for pid in sorted({row[0] for row in removed}):
        bump_hierarchy(conn, pid, stamp)
    for tid, pid in sorted((promotions | surviving_parents) - assignee_survivors):
        bump_display(conn, pid, tid, stamp)
