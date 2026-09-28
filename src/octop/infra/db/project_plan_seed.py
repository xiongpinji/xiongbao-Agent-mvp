"""Initial project todo catalogs, written through the caller's transaction."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from octop.infra.utils.project_plan_keys import normalize_project_plan_key
from octop.infra.utils.ulid import new_ulid

_DEFAULT_PRIORITIES = (("紧急", "red"), ("高", "orange"), ("中", "blue"), ("低", "gray"))


def seed_todo_catalog(conn: Any, project_id: str, ts: int) -> None:
    """Seed once on this connection without resetting an existing catalog."""
    inserted = conn.execute(
        "INSERT INTO project_todo_catalog_state(project_id,revision,updated_at) "
        "VALUES (?,1,?) ON CONFLICT (project_id) DO NOTHING",
        (project_id, ts),
    )
    if inserted.rowcount != 1:
        return
    _seed_default_todo_priorities(conn, project_id, ts)


def _seed_default_todo_priorities(conn: Any, project_id: str, ts: int) -> None:
    """Insert initial rows only after the caller has established an empty seed."""
    for position, (name, color) in enumerate(_DEFAULT_PRIORITIES):
        conn.execute(
            "INSERT INTO project_todo_priorities("
            "priority_id,project_id,name,name_key,color,position,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                new_ulid(),
                project_id,
                name,
                unicodedata.normalize("NFKC", name).casefold(),
                color,
                position,
                ts,
                ts,
            ),
        )


def seed_todo_views(conn: Any, project_id: str, ts: int) -> None:
    """Initialize a missing set or validate an existing set without guessing a seed."""
    state = conn.execute(
        "SELECT default_view_id FROM project_todo_view_state WHERE project_id=?", (project_id,)
    ).fetchone()
    views = conn.execute(
        "SELECT view_id,archived_at FROM project_todo_views WHERE project_id=?", (project_id,)
    ).fetchall()
    if state is not None or views:
        active = [row for row in views if row["archived_at"] is None]
        if (
            state is None
            or not active
            or len(active) > 30
            or len(views) > 100
            or state["default_view_id"] not in {row["view_id"] for row in active}
        ):
            raise RuntimeError("035 cannot safely complete an incomplete view seed")
        return

    conn.execute(
        "INSERT INTO project_todo_view_state(project_id,revision,default_view_id,updated_at) "
        "VALUES (?,1,NULL,?)",
        (project_id, ts),
    )
    table_id = new_ulid()
    for position, (view_id, name, kind, fields, group) in enumerate(
        (
            (
                table_id,
                "表格",
                "table",
                ["title", "status", "assignee", "priority", "tags", "start_date", "due_date"],
                None,
            ),
            (
                new_ulid(),
                "看板",
                "board",
                ["title", "status", "assignee", "priority", "tags"],
                "status",
            ),
        )
    ):
        definition = {
            "schema_version": 1,
            "fields": fields,
            "group_by": group,
            "filters": [],
            "sort": [{"field": "updated_at", "direction": "desc"}],
        }
        conn.execute(
            "INSERT INTO project_todo_views("
            "view_id,project_id,name,name_key,view_type,definition_json,version,position,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?,?)",
            (
                view_id,
                project_id,
                name,
                normalize_project_plan_key(name),
                kind,
                json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
                position,
                ts,
                ts,
            ),
        )
    conn.execute(
        "UPDATE project_todo_view_state SET default_view_id=? WHERE project_id=?",
        (table_id, project_id),
    )
