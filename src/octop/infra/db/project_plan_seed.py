"""Initial project todo catalogs, written through the caller's transaction."""

from __future__ import annotations

import unicodedata
from typing import Any

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
