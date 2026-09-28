"""Shared view configuration through the authenticated mounted HTTP surface.

The original starting test uses only existing auth helpers. Provider records
belong to the patched ASGI fixture; no provider or model requests are sent.
"""

from __future__ import annotations

import json

import httpx
import pytest

from octop.infra.projects.plan_definition import default_definition
from tests.integration.test_project_todos_api import _add_user, _base
from tests.support.auth import TEST_PASSWORD, bearer, create_user

VIEW_KEYS = {
    "view_id",
    "project_id",
    "name",
    "type",
    "definition",
    "version",
    "position",
    "archived_at",
    "created_at",
    "updated_at",
}
DEFAULT_DEFINITIONS = {
    "table": {
        "schema_version": 1,
        "fields": ["title", "status", "assignee", "priority", "tags", "start_date", "due_date"],
        "group_by": None,
        "filters": [],
        "sort": [{"field": "updated_at", "direction": "desc"}],
    },
    "board": {
        "schema_version": 1,
        "fields": ["title", "status", "assignee", "priority", "tags"],
        "group_by": "status",
        "filters": [],
        "sort": [{"field": "updated_at", "direction": "desc"}],
    },
}


async def test_default_table_and_board_views_are_persisted_and_readable(
    env_with_provider: tuple[httpx.AsyncClient, object, dict[str, str]],
) -> None:
    client, _server, admin_auth = env_with_provider
    username = "q3_views_owner"
    await create_user(client, admin_auth, username=username)
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    owner_auth = bearer(login.json()["access_token"])
    me = await client.get("/api/auth/me", headers=owner_auth)
    assert me.status_code == 200, me.text
    assert me.json()["username"] == username

    created = await client.post(
        "/api/projects", headers=owner_auth, json={"name": "Q3 默认视图控制项目"}
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    project = await client.get(f"/api/projects/{project_id}", headers=owner_auth)
    assert project.status_code == 200, project.text
    catalog = await client.get(f"/api/projects/{project_id}/plan/catalog", headers=owner_auth)
    assert catalog.status_code == 200, catalog.text
    catalog_body = catalog.json()
    assert set(catalog_body) == {
        "project_id",
        "revision",
        "server_today",
        "server_timezone",
        "priorities",
        "tags",
    }
    assert catalog_body["project_id"] == project_id
    assert type(catalog_body["revision"]) is int and catalog_body["revision"] == 1
    assert catalog_body["priorities"] and catalog_body["tags"] == []
    assert isinstance(catalog_body["server_today"], str)
    assert isinstance(catalog_body["server_timezone"], str) and catalog_body["server_timezone"]

    # First intended feature failure after all real auth/project/C1 controls.
    # After GO, an unmounted route should yield an actual 404 against this 200.
    path = f"/api/projects/{project_id}/plan/views"
    response = await client.get(path, headers=owner_auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"project_id", "revision", "default_view_id", "items"}
    assert body["project_id"] == project_id
    assert type(body["revision"]) is int and body["revision"] == 1
    assert [item["name"] for item in body["items"]] == ["表格", "看板"]
    assert [item["type"] for item in body["items"]] == ["table", "board"]
    assert [item["position"] for item in body["items"]] == [0, 1]
    assert len({item["view_id"] for item in body["items"]}) == 2
    assert body["default_view_id"] == body["items"][0]["view_id"]
    for item in body["items"]:
        assert set(item) == VIEW_KEYS
        assert isinstance(item["view_id"], str) and item["view_id"]
        assert item["project_id"] == project_id
        assert type(item["version"]) is int and item["version"] == 1
        assert type(item["position"]) is int
        assert item["archived_at"] is None
        assert type(item["created_at"]) is int and item["created_at"] > 0
        assert type(item["updated_at"]) is int and item["updated_at"] >= item["created_at"]
        assert item["definition"] == DEFAULT_DEFINITIONS[item["type"]]

    refreshed = await client.get(path, headers=owner_auth)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json() == body


async def _context(env):
    ctx = await _base(env)
    ctx["plan"] = f"/api/projects/{ctx['pid']}/plan"
    catalog = await ctx["client"].get(ctx["plan"] + "/catalog", headers=ctx["owner_auth"])
    assert catalog.status_code == 200, catalog.text
    ctx["catalog"] = catalog.json()
    views = await ctx["client"].get(ctx["plan"] + "/views", headers=ctx["owner_auth"])
    assert views.status_code == 200, views.text
    ctx["views"] = views.json()
    return ctx


@pytest.mark.parametrize(
    "operation", ["create", "update", "order", "default", "archive", "restore"]
)
async def test_write_endpoint_is_mounted_and_returns_committed_snapshot(
    env_with_provider, operation
):
    ctx = await _context(env_with_provider)
    client, auth, views = ctx["client"], ctx["owner_auth"], ctx["views"]
    table, board = views["items"]
    root = ctx["plan"] + "/views"
    expected_status = 200
    if operation == "create":
        expected_status = 201
        response = await client.post(
            root,
            headers=auth,
            json={
                "expected_revision": 1,
                "expected_catalog_revision": 1,
                "name": "新列表",
                "type": "list",
                "definition": default_definition("list"),
            },
        )
    elif operation == "update":
        response = await client.patch(
            root + "/" + table["view_id"],
            headers=auth,
            json={
                "expected_version": 1,
                "name": " 新表格 ",
            },
        )
    elif operation == "order":
        response = await client.put(
            root + "/order",
            headers=auth,
            json={
                "expected_revision": 1,
                "view_ids": [board["view_id"], table["view_id"]],
            },
        )
    elif operation == "default":
        response = await client.put(
            root + "/default",
            headers=auth,
            json={
                "expected_revision": 1,
                "view_id": board["view_id"],
            },
        )
    elif operation == "archive":
        response = await client.post(
            root + "/" + table["view_id"] + "/archive", headers=auth, json={"expected_version": 1}
        )
    else:
        # A real persisted historical archived record; no future module import.
        with ctx["srv"].services.db.transaction() as conn:
            conn.execute(
                "UPDATE project_todo_views SET archived_at=1 WHERE project_id=? AND view_id=?",
                (ctx["pid"], board["view_id"]),
            )
        response = await client.post(
            root + "/" + board["view_id"] + "/restore", headers=auth, json={"expected_version": 1}
        )
    assert response.status_code == expected_status, response.text
    result = response.json()
    assert result["revision"] == 2
    current = await client.get(root, headers=auth)
    assert current.status_code == 200, current.text
    assert current.json()["revision"] == 2
    assert current.json()["default_view_id"] == result["default_view_id"]
    if "item" in result:
        item = result["item"]
        assert set(item) == VIEW_KEYS
        assert (
            next(row for row in current.json()["items"] if row["view_id"] == item["view_id"])
            == item
        )
        assert item["version"] == (1 if operation == "create" else 2)
    if operation == "update":
        assert result["item"]["name"] == "新表格"
    if operation == "order":
        assert set(result) == {"revision", "default_view_id", "items"}
        assert [item["view_id"] for item in result["items"]] == [board["view_id"], table["view_id"]]
        assert [item["version"] for item in result["items"]] == [2, 2]
    if operation == "default":
        assert set(result) == {"revision", "default_view_id"}
        assert [item["version"] for item in current.json()["items"]] == [1, 1]
    if operation in ("default", "archive"):
        assert result["default_view_id"] == board["view_id"]


def _create_body(kind="table", *, name="new", revision=1, catalog=1):
    return {
        "expected_revision": revision,
        "expected_catalog_revision": catalog,
        "name": name,
        "type": kind,
        "definition": default_definition(kind),
    }


def _raw_state(ctx):
    result = {}
    with ctx["srv"].services.db.connect() as conn:
        for table, order in (
            ("project_todo_views", "view_id"),
            ("project_todo_view_state", "project_id"),
            ("project_events", "id"),
        ):
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE project_id=? ORDER BY {order}", (ctx["pid"],)
            ).fetchall()
            result[table] = [dict(row) for row in rows]
    return result


async def test_all_five_types_are_persisted_and_typed_on_actual_create_patch_get(env_with_provider):
    ctx = await _context(env_with_provider)
    root, client, auth = ctx["plan"] + "/views", ctx["client"], ctx["owner_auth"]
    revision = 1
    for kind in ("list", "table", "board", "gantt", "calendar"):
        response = await client.post(
            root, headers=auth, json=_create_body(kind, name=kind, revision=revision)
        )
        assert response.status_code == 201, response.text
        item = response.json()["item"]
        assert set(item) == VIEW_KEYS
        assert item["type"] == kind and item["definition"] == default_definition(kind)
        assert item["version"] == 1 and response.json()["revision"] == revision + 1
        path = root + "/" + item["view_id"]
        changed = default_definition(kind)
        changed["filters"] = [{"field": "title", "op": "contains", "value": "  %_\\Ａß  "}]
        patched = await client.patch(
            path,
            headers=auth,
            json={"expected_version": 1, "definition": changed, "expected_catalog_revision": 1},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["item"]["definition"] == changed
        assert patched.json()["item"]["version"] == 2
        read = await client.get(path, headers=ctx["member_auth"])
        assert read.status_code == 200 and read.json() == patched.json()["item"]
        revision += 2
    final = await client.get(root, headers=auth)
    assert final.status_code == 200 and final.json()["revision"] == 11
    assert len(final.json()["items"]) == 7


async def test_every_write_checks_membership_role_and_archived_project(env_with_provider):
    ctx = await _context(env_with_provider)
    root, client, table, board = (ctx["plan"] + "/views", ctx["client"], *ctx["views"]["items"])
    writes = [
        ("POST", root, _create_body()),
        ("PATCH", root + "/" + table["view_id"], {"expected_version": 1, "name": "renamed"}),
        (
            "PUT",
            root + "/order",
            {"expected_revision": 1, "view_ids": [board["view_id"], table["view_id"]]},
        ),
        ("PUT", root + "/default", {"expected_revision": 1, "view_id": board["view_id"]}),
        ("POST", root + "/" + table["view_id"] + "/archive", {"expected_version": 1}),
        ("POST", root + "/" + board["view_id"] + "/restore", {"expected_version": 1}),
    ]
    before = _raw_state(ctx)
    for auth, expected in (
        (ctx["member_auth"], 403),
        (ctx["outsider_auth"], 404),
        (ctx["admin_auth"], 404),
    ):
        for method, path, body in writes:
            response = await client.request(method, path, headers=auth, json=body)
            assert response.status_code == expected, (method, path, response.text)
        assert _raw_state(ctx) == before
    for path in (root, root + "/" + table["view_id"]):
        assert (await client.get(path, headers=ctx["member_auth"])).status_code == 200
        assert (await client.get(path, headers=ctx["admin_auth"])).status_code == 404
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (ctx["pid"],))
    for method, path, body in writes:
        response = await client.request(method, path, headers=ctx["owner_auth"], json=body)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["details"] == {"reason": "project_archived"}
    assert _raw_state(ctx) == before
    assert (await client.get(root, headers=ctx["member_auth"])).status_code == 200


async def test_admin_revoke_and_cross_project_ids_recheck_current_acl(env_with_provider):
    ctx = await _context(env_with_provider)
    auth, uid = await _add_user(ctx, "q3-admin", role="admin")
    root, client = ctx["plan"] + "/views", ctx["client"]
    table = ctx["views"]["items"][0]
    response = await client.patch(
        root + "/" + table["view_id"],
        headers=auth,
        json={"expected_version": 1, "name": "manager"},
    )
    assert response.status_code == 200 and response.json()["item"]["version"] == 2
    projects = ctx["srv"].services.project_repo
    assert (
        projects.set_member_role(
            project_id=ctx["pid"], user_id=uid, role="member", actor_user_id=ctx["owner_uid"]
        ).outcome
        == "changed"
    )
    before = _raw_state(ctx)
    response = await client.patch(
        root + "/" + table["view_id"],
        headers=auth,
        json={"expected_version": 2, "name": "must not write"},
    )
    assert response.status_code == 403 and _raw_state(ctx) == before
    assert (await client.get(root, headers=auth)).status_code == 200
    assert (
        projects.remove_member(
            project_id=ctx["pid"], user_id=uid, actor_user_id=ctx["owner_uid"]
        ).outcome
        == "removed"
    )
    before = _raw_state(ctx)
    assert (await client.get(root, headers=auth)).status_code == 404
    response = await client.patch(
        root + "/" + table["view_id"],
        headers=auth,
        json={"expected_version": 2, "name": "still not allowed"},
    )
    assert response.status_code == 404 and _raw_state(ctx) == before
    second = await client.post("/api/projects", headers=ctx["owner_auth"], json={"name": "other"})
    assert second.status_code == 201
    other = f"/api/projects/{second.json()['project_id']}/plan/views"
    for path in (other + "/" + table["view_id"], root + "/unknown-view"):
        assert (await client.get(path, headers=ctx["owner_auth"])).status_code == 404
        result = await client.patch(
            path, headers=ctx["member_auth"], json={"expected_version": 2, "name": "x"}
        )
        assert result.status_code == 404


async def test_transport_validation_hides_nested_input_and_rejects_coercion(env_with_provider):
    ctx = await _context(env_with_provider)
    root, client, auth = ctx["plan"] + "/views", ctx["client"], ctx["owner_auth"]
    table = ctx["views"]["items"][0]["view_id"]
    before = _raw_state(ctx)
    cases = []
    for field in ("expected_revision", "expected_catalog_revision"):
        for value in (True, "1", 1.0, 0, None):
            body = _create_body()
            body[field] = value
            cases.append(("POST", root, body))
    bad_definition = default_definition("table")
    bad_definition["filters"] = [
        {
            "field": "title",
            "op": "contains",
            "value": "PRIVATE_VALUE",
            "PRIVATE_NESTED_KEY": {"token": "PRIVATE_TOKEN"},
        }
    ]
    cases.append(("POST", root, {**_create_body(), "definition": bad_definition}))
    cases.append(("POST", root, {**_create_body(), "PRIVATE_TOP_KEY": "PRIVATE_TOKEN"}))
    for field in ("name", "type", "definition", "expected_catalog_revision"):
        cases.append(("PATCH", root + "/" + table, {"expected_version": 1, field: None}))
    for version in (True, "1", 0):
        cases.append(("POST", root + "/" + table + "/archive", {"expected_version": version}))
    for method, path, body in cases:
        response = await client.request(method, path, headers=auth, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"] == {"reason": "invalid_view_request"}
        assert all(
            value not in response.text for value in ("PRIVATE_", '"input"', '"loc"', '"ctx"')
        )
        assert _raw_state(ctx) == before
    malformed = await client.post(
        root, headers={**auth, "Content-Type": "application/json"}, content='{"PRIVATE_BROKEN":'
    )
    assert malformed.status_code == 422 and "PRIVATE_BROKEN" not in malformed.text
    assert malformed.json()["error"]["details"] == {"reason": "invalid_view_request"}
    assert _raw_state(ctx) == before


async def test_independent_view_versions_conflicts_and_patch_presence_on_http(env_with_provider):
    ctx = await _context(env_with_provider)
    root, client, auth = ctx["plan"] + "/views", ctx["client"], ctx["owner_auth"]
    table, board = ctx["views"]["items"]
    for item, name in ((table, "table changed"), (board, "board changed")):
        response = await client.patch(
            root + "/" + item["view_id"],
            headers=auth,
            json={"expected_version": 1, "name": name, "expected_catalog_revision": 999},
        )
        assert response.status_code == 200 and response.json()["item"]["version"] == 2
    before = _raw_state(ctx)
    failures = [
        (
            "PATCH",
            root + "/" + table["view_id"],
            {"expected_version": 1, "name": "stale"},
            409,
            "view_version_conflict",
        ),
        (
            "PUT",
            root + "/default",
            {"expected_revision": 1, "view_id": board["view_id"]},
            409,
            "view_revision_conflict",
        ),
        ("POST", root, _create_body(name="table changed", revision=3), 409, "name_conflict"),
        ("PATCH", root + "/" + table["view_id"], {"expected_version": 2}, 422, "no_change"),
        (
            "PATCH",
            root + "/" + table["view_id"],
            {"expected_version": 2, "type": "calendar"},
            422,
            "invalid_definition",
        ),
        (
            "PATCH",
            root + "/" + table["view_id"],
            {"expected_version": 2, "definition": default_definition("table")},
            422,
            "invalid_view_request",
        ),
    ]
    for method, path, body, status, reason in failures:
        response = await client.request(method, path, headers=auth, json=body)
        assert response.status_code == status, response.text
        assert response.json()["error"]["details"] == {"reason": reason}
        assert _raw_state(ctx) == before
    value = default_definition("table")
    value["fields"] = ["title", "status"]
    result = await client.patch(
        root + "/" + table["view_id"],
        headers=auth,
        json={
            "expected_version": 2,
            "type": "table",
            "definition": value,
            "expected_catalog_revision": 1,
        },
    )
    assert result.status_code == 200 and result.json()["item"]["definition"] == value
    assert result.json()["revision"] == 4 and result.json()["item"]["version"] == 3


async def test_saved_member_reference_survives_removal_and_is_repaired_on_same_id(
    env_with_provider,
):
    ctx = await _context(env_with_provider)
    root, client, auth = ctx["plan"] + "/views", ctx["client"], ctx["owner_auth"]
    body = _create_body()
    body["definition"]["filters"] = [
        {"field": "assignee", "op": "in", "values": [ctx["member_uid"]]}
    ]
    response = await client.post(root, headers=auth, json=body)
    assert response.status_code == 201
    vid = response.json()["item"]["view_id"]
    assert (
        ctx["srv"]
        .services.project_repo.remove_member(
            project_id=ctx["pid"], user_id=ctx["member_uid"], actor_user_id=ctx["owner_uid"]
        )
        .outcome
        == "removed"
    )
    path = root + "/" + vid
    assert (await client.get(path, headers=auth)).json()["definition"] == body["definition"]
    name = await client.patch(path, headers=auth, json={"expected_version": 1, "name": "kept"})
    assert name.status_code == 200 and name.json()["item"]["version"] == 2
    archived = await client.post(path + "/archive", headers=auth, json={"expected_version": 2})
    assert archived.status_code == 200
    restored = await client.post(path + "/restore", headers=auth, json={"expected_version": 3})
    assert (
        restored.status_code == 200 and restored.json()["item"]["definition"] == body["definition"]
    )
    before = _raw_state(ctx)
    rejected = await client.patch(
        path,
        headers=auth,
        json={
            "expected_version": 4,
            "definition": body["definition"],
            "expected_catalog_revision": 1,
        },
    )
    assert rejected.status_code == 422 and rejected.json()["error"]["details"] == {
        "reason": "invalid_assignee"
    }
    assert _raw_state(ctx) == before
    fixed = await client.patch(
        path,
        headers=auth,
        json={
            "expected_version": 4,
            "definition": default_definition("table"),
            "expected_catalog_revision": 1,
        },
    )
    assert fixed.status_code == 200 and fixed.json()["item"]["version"] == 5
    assert (
        fixed.json()["item"]["view_id"] == vid
        and fixed.json()["item"]["definition"]["filters"] == []
    )


async def test_incoming_missing_cross_project_and_storage_bound_references_are_safe(
    env_with_provider,
):
    ctx = await _context(env_with_provider)
    client, auth, root = ctx["client"], ctx["owner_auth"], ctx["plan"] + "/views"
    other = await client.post("/api/projects", headers=auth, json={"name": "other catalog"})
    assert other.status_code == 201
    other_catalog = await client.get(
        f"/api/projects/{other.json()['project_id']}/plan/catalog", headers=auth
    )
    assert other_catalog.status_code == 200
    foreign = other_catalog.json()["priorities"][0]["priority_id"]
    before = _raw_state(ctx)
    for condition, reason in (
        ({"field": "assignee", "op": "in", "values": [ctx["outsider_uid"]]}, "invalid_assignee"),
        ({"field": "assignee", "op": "in", "values": [2**100]}, "invalid_assignee"),
        ({"field": "priority", "op": "in", "values": [foreign]}, "invalid_catalog_reference"),
        ({"field": "tags", "op": "all", "values": ["missing-tag"]}, "invalid_catalog_reference"),
    ):
        body = _create_body()
        body["definition"]["filters"] = [condition]
        response = await client.post(root, headers=auth, json=body)
        assert response.status_code == 422 and response.json()["error"]["details"] == {
            "reason": reason
        }
        assert str(2**100) not in response.text and foreign not in response.text
        assert _raw_state(ctx) == before
    stale = await client.post(root, headers=auth, json=_create_body(catalog=999))
    assert stale.status_code == 409 and stale.json()["error"]["details"] == {
        "reason": "catalog_revision_conflict"
    }
    assert _raw_state(ctx) == before


async def test_created_event_contains_only_safe_view_identity_and_field_names(env_with_provider):
    ctx = await _context(env_with_provider)
    body = _create_body(name="PRIVATE_VIEW_NAME")
    body["definition"]["filters"] = [
        {"field": "title", "op": "contains", "value": "PRIVATE_TITLE_FILTER"}
    ]
    created = await ctx["client"].post(ctx["plan"] + "/views", headers=ctx["owner_auth"], json=body)
    assert created.status_code == 201, created.text
    vid = created.json()["item"]["view_id"]
    with ctx["srv"].services.db.connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM project_events WHERE project_id=? AND event_type=? ORDER BY id DESC",
            (ctx["pid"], "project.todo_view_updated"),
        ).fetchone()
    assert json.loads(row["payload_json"]) == {
        "view_id": vid,
        "action": "created",
        "version": 1,
        "collection_revision": 2,
        "fields": ["name", "type", "definition"],
    }
    response = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/activity", headers=ctx["member_auth"]
    )
    assert response.status_code == 200, response.text
    assert "PRIVATE_" not in response.text and "payload_json" not in response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["event_type"] == "project.todo_view_updated"
    )
    assert item["view_id"] == vid and item["version"] == 1 and item["collection_revision"] == 2
    assert item["action"] == "created" and set(item["fields"]) == {"name", "type", "definition"}
