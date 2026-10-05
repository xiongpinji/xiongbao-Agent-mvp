"""D2 real nonlive ASGI receipts, cursor qualification and typed schemas."""

import base64
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from octop.infra.projects import todos as todos_module
from tests.support.auth import create_user


async def test_children_receipts_cursor_and_old_delete(env: Any) -> None:
    client, server, admin = env
    auth = await create_user(client, admin, username="d2_owner")
    outsider = await create_user(client, admin, username="d2_outsider")
    project = await client.post("/api/projects", headers=auth, json={"name": "D2 owned"})
    assert project.status_code == 201
    pid = project.json()["project_id"]
    url = f"/api/projects/{pid}/todos"
    parent = (await client.post(url, headers=auth, json={"title": "parent"})).json()
    request = {"title": "child", "expected_children_revision": 1, "client_request_id": str(uuid4())}
    first = await client.post(f"{url}/{parent['todo_id']}/children", headers=auth, json=request)
    assert first.status_code == 201, first.text
    data = first.json()
    assert set(data) == {
        "item",
        "children_revision",
        "parent_display_revision",
        "active_count",
        "done_count",
        "replayed",
    }
    assert data["item"]["parent_todo_id"] == parent["todo_id"]
    assert data["item"]["children_revision"] is None
    replay = await client.post(f"{url}/{parent['todo_id']}/children", headers=auth, json=request)
    assert replay.status_code == 200
    assert replay.json()["item"]["todo_id"] == data["item"]["todo_id"]
    assert replay.json()["replayed"] is True
    second = await client.post(
        f"{url}/{parent['todo_id']}/children",
        headers=auth,
        json={**request, "client_request_id": str(uuid4()), "expected_children_revision": 2},
    )
    assert second.status_code == 201
    page = await client.get(f"{url}/{parent['todo_id']}/children?limit=1", headers=auth)
    assert page.status_code == 200
    assert set(page.json()) == {
        "items",
        "limit",
        "has_more",
        "next_cursor",
        "children_revision",
        "parent_display_revision",
        "active_count",
        "done_count",
    }
    assert page.json()["has_more"] is True
    last = await client.get(
        f"{url}/{parent['todo_id']}/children",
        headers=auth,
        params={"limit": 1, "cursor": page.json()["next_cursor"]},
    )
    assert last.status_code == 200
    assert last.json()["next_cursor"] is None
    assert last.json()["items"][0]["todo_id"] != page.json()["items"][0]["todo_id"]
    denied = await client.get(
        f"{url}/{parent['todo_id']}/children?cursor=invalid!", headers=outsider
    )
    assert denied.status_code == 404
    invalid = await client.get(f"{url}/{parent['todo_id']}/children?cursor=invalid!", headers=auth)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"]["reason"] == "invalid_cursor"
    roots = await client.get(url, headers=auth)
    assert [row["todo_id"] for row in roots.json()["items"]] == [parent["todo_id"]]
    old_delete = await client.delete(f"{url}/{parent['todo_id']}?expected_version=1", headers=auth)
    assert old_delete.status_code == 409
    assert old_delete.json()["error"]["details"]["reason"] == "children_confirmation_required"
    children = [data["item"], second.json()["item"]]
    tree = await client.post(
        f"{url}/{parent['todo_id']}/delete-tree",
        headers=auth,
        json={
            "expected_version": 1,
            "expected_children_revision": 3,
            "children": [
                {"todo_id": row["todo_id"], "expected_version": row["version"]} for row in children
            ],
        },
    )
    assert tree.status_code == 200, tree.text
    assert set(tree.json()) == {"deleted_todo_ids", "children_revision", "hierarchy_revision"}
    assert tree.json()["deleted_todo_ids"] == [
        parent["todo_id"],
        *sorted(row["todo_id"] for row in children),
    ]
    assert tree.json()["children_revision"] == 4
    schema = client._transport.app.openapi()
    for suffix, method, status in [
        ("children", "get", "200"),
        ("children", "post", "201"),
        ("delete-tree", "post", "200"),
    ]:
        operation = schema["paths"][f"/api/projects/{{project_id}}/todos/{{todo_id}}/{suffix}"][
            method
        ]
        assert operation["summary"] and operation["tags"]
        assert "$ref" in operation["responses"][status]["content"]["application/json"]["schema"]


async def test_child_request_uuid_is_validated_at_http_boundary(env: Any) -> None:
    client, _, admin = env
    auth = await create_user(client, admin, username="d2_uuid")
    pid = (await client.post("/api/projects", headers=auth, json={"name": "D2 UUID"})).json()[
        "project_id"
    ]
    url = f"/api/projects/{pid}/todos"
    parent = (await client.post(url, headers=auth, json={"title": "parent"})).json()
    result = await client.post(
        f"{url}/{parent['todo_id']}/children",
        headers=auth,
        json={"title": "child", "expected_children_revision": 1, "client_request_id": "x" * 36},
    )
    assert result.status_code == 422
    assert (await client.get(url, headers=auth)).json()["items"] == [parent]


@pytest.mark.parametrize("parser_fault", [False, True])
async def test_deep_cursor_qualification_and_parser_recursion_rejection(
    env: Any, monkeypatch, parser_fault: bool
) -> None:
    client, server, admin = env
    auth = await create_user(client, admin, username="d2_deep")
    outsider = await create_user(client, admin, username="d2_deep_out")
    pid = (await client.post("/api/projects", headers=auth, json={"name": "D2 deep"})).json()[
        "project_id"
    ]
    url = f"/api/projects/{pid}/todos"
    parent = (await client.post(url, headers=auth, json={"title": "parent"})).json()
    cursor = base64.urlsafe_b64encode(("[" * 700 + "0" + "]" * 700).encode()).decode().rstrip("=")
    assert len(cursor.encode()) <= 2048
    # At the default recursion budget this token is rejected by schema. This
    # separate, module-local fault exercises the real parser exception boundary
    # without changing the process recursion limit or other JSON consumers.
    if parser_fault:

        def exhausted_parser(*args, **kwargs):
            raise RecursionError("controlled parser budget exhaustion")

        monkeypatch.setattr(
            todos_module, "json", SimpleNamespace(loads=exhausted_parser, dumps=json.dumps)
        )
    with server.services.db.connect() as conn:
        before = tuple(conn.iterdump())
    member = await client.get(
        f"{url}/{parent['todo_id']}/children", headers=auth, params={"cursor": cursor}
    )
    assert member.status_code == 422, member.text
    assert member.json()["error"]["details"]["reason"] == "invalid_cursor"
    denied = await client.get(
        f"{url}/{parent['todo_id']}/children", headers=outsider, params={"cursor": cursor}
    )
    assert denied.status_code == 404
    with server.services.db.connect() as conn:
        assert tuple(conn.iterdump()) == before
