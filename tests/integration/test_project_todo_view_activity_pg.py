"""Real owned-PG coverage for safe project view activity projections.

Capture SQL return columns before the Row/service/API metadata sanitizers.
The fixture requires a fresh isolated lease; this is database-level evidence.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

import pytest

from octop.api.routers.project_activity import _item_payload
from octop.infra.db.repos.project_activity import ProjectActivityRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.projects.activity import activity_item_view
from tests.integration.test_project_todo_fields_pg import postgres_pair as postgres_pair

VIEW_ID = "01M3M96KJMH2HTQ4KFPHRTBH25"
PRIVATE = "Q3-PG-PRIVATE-NAME-FILTER-BODY-NEVER-RETURN"
BASE_KEYS = {
    "event_id",
    "event_type",
    "actor_user_id",
    "actor_name",
    "object_kind",
    "object_id",
    "message_body",
    "created_at",
}
VIEW_KEYS = {"view_id", "version", "collection_revision", "action", "fields"}
ACTIONS = ("created", "updated", "ordered", "archived", "restored", "default_changed")
FIELD_NAMES = {"name", "type", "definition", "order", "archived", "default_view_id"}


class _CapturedCursor:
    def __init__(self, cursor: Any, captured: list[dict[str, Any]]) -> None:
        self.cursor, self.captured = cursor, captured

    def fetchall(self) -> Any:
        rows = self.cursor.fetchall()
        for row in rows:
            # Database row iteration yields values; capture the named SQL columns.
            keys = row.keys()
            self.captured.append({key: row[key] for key in keys})
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)


class _CapturedConnection:
    def __init__(self, conn: Any, captured: list[dict[str, Any]]) -> None:
        self.conn, self.captured = conn, captured

    def execute(self, sql: str, params: Any = None) -> Any:
        cursor = self.conn.execute(sql, params)
        if sql.lstrip().startswith("SELECT") and "FROM project_events e" in sql:
            return _CapturedCursor(cursor, self.captured)
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conn, name)


class _CapturedPool:
    dialect = "postgresql"

    def __init__(self, pool: Any, captured: list[dict[str, Any]]) -> None:
        self.pool, self.captured = pool, captured

    @contextmanager
    def transaction(self) -> Iterator[_CapturedConnection]:
        with self.pool.transaction() as conn:
            yield _CapturedConnection(conn, self.captured)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.pool, name)


@pytest.fixture
def activity_env(postgres_pair: dict[str, Any]) -> dict[str, Any]:
    env = postgres_pair
    users = UserRepo(env["first"])
    owner = users.create(username="q3-activity-owner", password_hash="synthetic", role="user")
    member = users.create(username="q3-activity-member", password_hash="synthetic", role="user")
    projects = ProjectRepo(env["first"])
    project = projects.create_with_owner(creator_user_id=owner, name="Synthetic Q3 activity")
    projects.add_member(project.project_id, member)
    env.update(project_id=project.project_id, owner=owner, member=member)
    return env


def append_event(
    env: dict[str, Any],
    actor: int,
    payload: dict[str, Any],
    *,
    event_type: str = "project.todo_view_updated",
    object_id: str = PRIVATE,
) -> int:
    with env["first"].transaction() as conn:
        row = conn.execute(
            "INSERT INTO project_events(project_id,event_type,actor_user_id,object_id,"
            "payload_json,created_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (
                env["project_id"],
                event_type,
                actor,
                object_id,
                json.dumps(payload, ensure_ascii=False, allow_nan=False),
                2_000_000_000,
            ),
        ).fetchone()
    assert row is not None
    return int(row[0])


def projected_row(env: dict[str, Any], event_id: int) -> Any:
    captured: list[dict[str, Any]] = []
    rows = ProjectActivityRepo(_CapturedPool(env["second"], captured)).list_activity(
        env["project_id"],
        user_id=env["member"],
        scope="members",
        limit=50,
    )
    assert rows is not None
    matching = [row for row in rows if row.event_id == event_id]
    assert len(matching) == 1
    row = matching[0]
    # Capture actual SELECT output before ActivityRow.from_row can sanitize it.
    raw = [entry for entry in captured if entry["event_id"] == event_id]
    assert len(raw) == 1
    sql_row = raw[0]
    assert "payload_json" not in sql_row and PRIVATE not in repr(sql_row)
    assert sql_row["safe_view_id"] == row.view_id
    assert sql_row["safe_view_version"] == row.version
    assert sql_row["safe_view_revision"] == row.collection_revision
    assert sql_row["safe_action"] == row.action
    assert sql_row["object_id"] == row.object_id and sql_row["message_body"] is None
    fields = json.loads(sql_row["safe_fields_json"])
    assert all(type(field) is str and field in FIELD_NAMES for field in fields)
    assert tuple(sorted(set(fields))) == row.fields
    # Check the mapped SQL row too, before the service/API sanitizers.
    assert not any(key in asdict(row) for key in ("payload_json", "name", "definition", "body"))
    assert PRIVATE not in repr(row)
    assert row.object_kind == "project" and row.message_body is None
    assert row.catalog_kind is None and row.catalog_revision is None and row.option_id is None
    return row


def public_payload(row: Any) -> dict[str, Any]:
    view = activity_item_view(row)
    assert view is not None
    result = _item_payload(view).model_dump(mode="json", exclude_unset=True)
    assert PRIVATE not in json.dumps(result, ensure_ascii=False)
    return result


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("actor_role", ("owner", "member"))
def test_pg_six_view_actions_safe_member_feed_and_related_actor_only(
    activity_env: dict[str, Any],
    action: str,
    actor_role: str,
) -> None:
    env = activity_env
    actor = env[actor_role]
    other = env["member" if actor_role == "owner" else "owner"]
    event_id = append_event(
        env,
        actor,
        {
            "view_id": VIEW_ID,
            "version": 7,
            "collection_revision": 13,
            "action": action,
            "fields": [
                "name",
                "definition",
                "name",
                "filters",
                PRIVATE,
                {"name": PRIVATE},
                ["order", PRIVATE],
                True,
                7,
                None,
            ],
            "name": PRIVATE,
            "definition": {"filters": [{"value": PRIVATE}]},
            "body": PRIVATE,
            "message_body": PRIVATE,
            "catalog_revision": 999,
            "catalog_kind": "tag",
            "option_id": VIEW_ID,
            "target_user_id": other,
            "assignee_user_id": other,
            "previous_assignee_user_id": other,
            "creator_user_id": other,
        },
    )
    row = projected_row(env, event_id)
    assert (row.view_id, row.object_id, row.version, row.collection_revision, row.action) == (
        VIEW_ID,
        VIEW_ID,
        7,
        13,
        action,
    )
    assert row.fields == ("definition", "name")
    dto = public_payload(row)
    assert set(dto) == BASE_KEYS | VIEW_KEYS
    assert dto["fields"] == ["definition", "name"]
    repo = ProjectActivityRepo(env["second"])
    related_actor = repo.list_activity(env["project_id"], user_id=actor, scope="related", limit=50)
    related_other = repo.list_activity(env["project_id"], user_id=other, scope="related", limit=50)
    assert related_actor is not None and event_id in {item.event_id for item in related_actor}
    assert related_other is not None and event_id not in {item.event_id for item in related_other}
    # Historical SQL injection by a member is not authorization to write views via HTTP.
    env["report"]["q3_activity_projection"] = {
        "action": action,
        "actor_role": actor_role,
        "safe_metadata": True,
        "related_actor_only": True,
    }


@pytest.mark.parametrize(
    "value,expected",
    (
        (1, 1),
        (2**63 - 1, 2**63 - 1),
        (None, None),
        (True, None),
        (False, None),
        (0, None),
        (-1, None),
        (1.0, None),
        (1.5, None),
        ("7", None),
        ("9223372036854775807", None),
        ([], None),
        ({"private": PRIVATE}, None),
        (2**63, None),
        (-(2**63), None),
        (2**100, None),
    ),
)
def test_pg_view_metadata_signed64_boundaries_do_not_overflow_or_accept_bool(
    activity_env: dict[str, Any],
    value: Any,
    expected: int | None,
) -> None:
    env = activity_env
    event_id = append_event(
        env,
        env["owner"],
        {
            "view_id": VIEW_ID,
            "action": "updated",
            "version": value,
            "collection_revision": value,
            "fields": ["order"],
        },
    )
    row = projected_row(env, event_id)
    assert row.version == expected and row.collection_revision == expected
    if expected is not None:
        assert type(row.version) is int and type(row.collection_revision) is int
    dto = public_payload(row)
    assert dto["version"] == expected and dto["collection_revision"] == expected
    env["report"]["q3_activity_projection"] = {
        "numeric_input_type": type(value).__name__,
        "numeric_expected": expected,
    }


@pytest.mark.parametrize(
    "view_id",
    (
        None,
        True,
        7,
        {"value": PRIVATE},
        [],
        VIEW_ID[:-1],
        VIEW_ID + "0",
        VIEW_ID.lower(),
        "I" * 26,
        "O" * 26,
        PRIVATE,
    ),
)
def test_pg_invalid_view_id_and_nested_action_are_not_projected(
    activity_env: dict[str, Any],
    view_id: Any,
) -> None:
    env = activity_env
    event_id = append_event(
        env,
        env["owner"],
        {
            "view_id": view_id,
            "version": 2,
            "collection_revision": 3,
            "action": {"default_changed": PRIVATE},
            "fields": ["order", {"name": PRIVATE}, ["archived"], PRIVATE, False, 2],
        },
    )
    row = projected_row(env, event_id)
    assert row.view_id is None and row.object_id is None and row.action is None
    assert row.fields == ("order",)
    dto = public_payload(row)
    assert set(dto) == BASE_KEYS | VIEW_KEYS
    assert dto["view_id"] is None and dto["object_id"] is None and dto["action"] is None
    env["report"]["q3_activity_projection"] = {
        "invalid_view_id_type": type(view_id).__name__,
        "nested_action_rejected": True,
    }


def test_pg_old_created_dto_unchanged_and_unknown_event_excluded(
    activity_env: dict[str, Any],
) -> None:
    env = activity_env
    append_event(
        env,
        env["owner"],
        {
            "view_id": VIEW_ID,
            "action": "default_changed",
            "version": 1,
            "collection_revision": 2,
            "fields": ["default_view_id"],
        },
    )
    unknown_id = append_event(
        env, env["owner"], {"body": PRIVATE}, event_type="project.q3_unknown_private"
    )
    rows = ProjectActivityRepo(env["second"]).list_activity(
        env["project_id"],
        user_id=env["member"],
        scope="members",
        limit=50,
    )
    assert rows is not None and unknown_id not in {row.event_id for row in rows}
    created = [row for row in rows if row.event_type == "project.created"]
    assert len(created) == 1
    dto = public_payload(created[0])
    assert set(dto) == BASE_KEYS
    assert dto["object_kind"] == "project" and dto["object_id"] == env["project_id"]
    assert dto["actor_user_id"] == env["owner"] and dto["message_body"] is None
    env["report"]["q3_activity_projection"] = {
        "old_created_exact_base_keys": True,
        "unknown_event_excluded": True,
    }
