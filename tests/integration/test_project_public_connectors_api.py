"""Real isolated HTTP/SQLite connector settings; no remote MCP calls."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from tests.support.auth import create_user, resolve_user_id

ITEM_KEYS = {
    "connector_id",
    "kind",
    "display_name",
    "description",
    "state",
    "grant_revision",
    "created_at",
    "updated_at",
}
COMMAND = {
    "expected_project_revision": 1,
    "kind": "http_mcp_static_bearer",
    "display_name": "Synthetic",
    "description": "Safe",
    "endpoint": "https://synthetic.invalid/mcp",
    "bearer_token": "private-synthetic-token",
}


async def test_public_connector_http_lifecycle_and_safe_shapes(env: Any) -> None:
    client, _srv, auth = env
    project = (await client.post("/api/projects", headers=auth, json={"name": "Public QA"})).json()
    pid = project["project_id"]
    url = f"/api/projects/{pid}/public-connectors"
    initial = await client.get(url, headers=auth)
    assert initial.status_code == 200
    assert initial.json() == {"project_id": pid, "public_connectors_revision": 1, "items": []}
    saved = await client.post(url, headers=auth, json=COMMAND)
    assert saved.status_code == 201
    body = saved.json()
    assert set(body) == {"project_id", "public_connectors_revision", "items"}
    assert body["public_connectors_revision"] == 2
    item = body["items"][0]
    assert set(item) == ITEM_KEYS
    assert COMMAND["endpoint"] not in saved.text and COMMAND["bearer_token"] not in saved.text
    cid = item["connector_id"]
    renamed = await client.patch(
        f"{url}/{cid}",
        headers=auth,
        json={
            "expected_project_revision": 2,
            "expected_grant_revision": 1,
            "display_name": "Renamed",
            "description": "Safe",
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["public_connectors_revision"] == 3
    noop = await client.put(
        f"{url}/{cid}/credentials",
        headers=auth,
        json={
            "expected_project_revision": 3,
            "expected_grant_revision": 1,
            "endpoint": COMMAND["endpoint"],
            "bearer_token": COMMAND["bearer_token"],
        },
    )
    assert noop.status_code == 200 and noop.json() == renamed.json()
    stale = await client.post(
        f"{url}/{cid}/revoke",
        headers=auth,
        json={"expected_project_revision": 2, "expected_grant_revision": 1},
    )
    assert (
        stale.status_code == 409
        and stale.json()["error"]["code"] == "PROJECT_PUBLIC_CONNECTORS_CHANGED"
    )
    revoked = await client.post(
        f"{url}/{cid}/revoke",
        headers=auth,
        json={"expected_project_revision": 3, "expected_grant_revision": 1},
    )
    assert revoked.status_code == 200
    assert revoked.json()["items"][0]["state"] == "revoked"
    assert revoked.json()["items"][0]["grant_revision"] == 2
    terminal = await client.put(
        f"{url}/{cid}/credentials",
        headers=auth,
        json={
            "expected_project_revision": 4,
            "expected_grant_revision": 2,
            "endpoint": COMMAND["endpoint"],
            "bearer_token": "another-synthetic",
        },
    )
    assert (
        terminal.status_code == 409
        and terminal.json()["error"]["code"] == "PROJECT_PUBLIC_CONNECTOR_REVOKED"
    )


@pytest.mark.parametrize(
    "suffix,method,payload",
    [
        ("", "post", {**COMMAND, "secret-key-from-caller": "secret-marker"}),
        (
            "/synthetic",
            "patch",
            {
                "expected_project_revision": 1,
                "expected_grant_revision": 1,
                "display_name": "Safe",
                "description": "",
                "bearer_token": "secret-marker",
            },
        ),
        (
            "/synthetic/revoke",
            "post",
            {
                "expected_project_revision": 1,
                "expected_grant_revision": 1,
                "bearer_token": "secret-marker",
            },
        ),
    ],
)
async def test_extra_secret_is_rejected_without_echo(
    env: Any, suffix: str, method: str, payload: dict[str, Any], caplog: Any
) -> None:
    client, _srv, auth = env
    pid = (await client.post("/api/projects", headers=auth, json={"name": "Redaction"})).json()[
        "project_id"
    ]
    response = await getattr(client, method)(
        f"/api/projects/{pid}/public-connectors{suffix}", headers=auth, json=payload
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"reason": "invalid_payload"}
    assert "secret-marker" not in response.text and "secret-key-from-caller" not in response.text
    assert "secret-marker" not in caplog.text


async def test_project_archived_detail_and_safe_list_separation(env: Any) -> None:
    client, srv, auth = env
    project = (await client.post("/api/projects", headers=auth, json={"name": "Archive QA"})).json()
    assert project["archived"] is False
    pid = project["project_id"]
    url = f"/api/projects/{pid}/public-connectors"
    created = await client.post(url, headers=auth, json=COMMAND)
    assert created.status_code == 201
    cid = created.json()["items"][0]["connector_id"]
    assert "archived" not in (await client.get("/api/projects", headers=auth)).json()["items"][0]
    with srv.services.db.connect() as conn:
        conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (pid,))
    assert (await client.get(f"/api/projects/{pid}", headers=auth)).json()["archived"] is True
    assert (await client.get(url, headers=auth)).status_code == 200
    denied = await client.post(url, headers=auth, json={**COMMAND, "expected_project_revision": 2})
    assert denied.status_code == 403 and denied.json()["error"]["details"] == {
        "reason": "project_archived"
    }
    with srv.services.db.connect() as conn:
        before = list(conn.iterdump())
    for method, suffix, payload in [
        (
            "patch",
            f"/{cid}",
            {
                "expected_project_revision": 2,
                "expected_grant_revision": 1,
                "display_name": "Safe",
                "description": "",
            },
        ),
        (
            "put",
            f"/{cid}/credentials",
            {
                "expected_project_revision": 2,
                "expected_grant_revision": 1,
                "endpoint": COMMAND["endpoint"],
                "bearer_token": COMMAND["bearer_token"],
            },
        ),
    ]:
        response = await getattr(client, method)(url + suffix, headers=auth, json=payload)
        assert response.status_code == 403 and response.json()["error"]["details"] == {
            "reason": "project_archived"
        }
    with srv.services.db.connect() as conn:
        assert list(conn.iterdump()) == before
    revoked = await client.post(
        f"{url}/{cid}/revoke",
        headers=auth,
        json={"expected_project_revision": 2, "expected_grant_revision": 1},
    )
    assert revoked.status_code == 200 and revoked.json()["items"][0]["state"] == "revoked"
    with srv.services.db.connect() as conn:
        row = conn.execute(
            "SELECT credential_blob,key_ref FROM project_public_connectors WHERE connector_id=?",
            (cid,),
        ).fetchone()
        assert tuple(row) == (None, None)
    repeated = await client.post(
        f"{url}/{cid}/revoke",
        headers=auth,
        json={"expected_project_revision": 3, "expected_grant_revision": 2},
    )
    assert repeated.status_code == 200 and repeated.json() == revoked.json()


async def test_fresh_roles_nonmember_admin_removed_disabled_and_cross_project(env: Any) -> None:
    client, srv, global_admin = env
    owner = await create_user(client, global_admin, username="pc_owner")
    admin = await create_user(client, global_admin, username="pc_manager")
    member = await create_user(client, global_admin, username="pc_member")
    pid = (await client.post("/api/projects", headers=owner, json={"name": "Role QA"})).json()[
        "project_id"
    ]
    manager_id = await resolve_user_id(client, global_admin, "pc_manager")
    member_id = await resolve_user_id(client, global_admin, "pc_member")
    srv.services.project_repo.add_member(pid, manager_id, role="admin")
    srv.services.project_repo.add_member(pid, member_id, role="member")
    url = f"/api/projects/{pid}/public-connectors"
    for auth, read_status, write_status in [(member, 200, 403), (global_admin, 404, 404)]:
        assert (await client.get(url, headers=auth)).status_code == read_status
        assert (await client.post(url, headers=auth, json=COMMAND)).status_code == write_status
    saved = await client.post(url, headers=admin, json=COMMAND)
    assert saved.status_code == 201
    cid = saved.json()["items"][0]["connector_id"]
    member_safe = await client.get(url, headers=member)
    assert member_safe.status_code == 200 and set(member_safe.json()["items"][0]) == ITEM_KEYS
    assert COMMAND["bearer_token"] not in member_safe.text
    other_pid = (
        await client.post("/api/projects", headers=owner, json={"name": "Other QA"})
    ).json()["project_id"]
    wrong = await client.post(
        f"/api/projects/{other_pid}/public-connectors/{cid}/revoke",
        headers=owner,
        json={"expected_project_revision": 1, "expected_grant_revision": 1},
    )
    assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "NOT_FOUND"
    removed = await client.delete(f"/api/projects/{pid}/members/{member_id}", headers=owner)
    assert removed.status_code == 204
    assert (await client.get(url, headers=member)).status_code == 404
    assert (await client.post(url, headers=member, json=COMMAND)).status_code == 404
    srv.services.user_repo.set_disabled(manager_id, True)
    rejected = await client.get(url, headers=admin)
    assert rejected.status_code == 404 and rejected.json()["error"]["code"] == "NOT_FOUND"
    disabled_write = await client.post(url, headers=admin, json=COMMAND)
    assert (
        disabled_write.status_code == 404 and disabled_write.json()["error"]["code"] == "NOT_FOUND"
    )
    assert (await client.get(url, headers=owner)).json() == saved.json()


async def test_actual_write_is_off_event_loop(env: Any, monkeypatch: Any) -> None:
    from octop.infra.projects.connectors import ProjectPublicConnectorService

    client, _srv, auth = env
    pid = (await client.post("/api/projects", headers=auth, json={"name": "Offload QA"})).json()[
        "project_id"
    ]
    main_thread = threading.get_ident()
    called = []
    original = ProjectPublicConnectorService.create

    def observed(self, *args, **kwargs):
        called.append(threading.get_ident())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ProjectPublicConnectorService, "create", observed)
    response = await client.post(
        f"/api/projects/{pid}/public-connectors", headers=auth, json=COMMAND
    )
    assert response.status_code == 201
    assert len(called) == 1 and called[0] != main_thread


async def test_openapi_flat_writeonly_and_typed_responses(env: Any) -> None:
    from fastapi.routing import APIRoute

    from octop.api.routers.projects import router

    client, _srv, _auth = env
    app = client._octop_app
    schema = app.openapi()
    expected = {
        ("/api/projects/{project_id}/public-connectors", "post"): set(COMMAND),
        ("/api/projects/{project_id}/public-connectors/{connector_id}", "patch"): {
            "expected_project_revision",
            "expected_grant_revision",
            "display_name",
            "description",
        },
        ("/api/projects/{project_id}/public-connectors/{connector_id}/credentials", "put"): {
            "expected_project_revision",
            "expected_grant_revision",
            "endpoint",
            "bearer_token",
        },
        ("/api/projects/{project_id}/public-connectors/{connector_id}/revoke", "post"): {
            "expected_project_revision",
            "expected_grant_revision",
        },
    }
    endpoints = [*expected, ("/api/projects/{project_id}/public-connectors", "get")]
    observations = []
    for path, method in endpoints:
        entry = schema["paths"][path][method]
        assert entry["summary"] and entry["description"] and entry["tags"]
        code = "201" if method == "post" and path.endswith("public-connectors") else "200"
        assert entry["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/PublicConnectorCollectionResponse"
        )
        observations.append(
            {
                "path": path,
                "method": method,
                "summary": entry["summary"],
                "tags": entry["tags"],
                "response_schema": entry["responses"][code]["content"]["application/json"][
                    "schema"
                ],
                "request_schema": entry.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema"),
            }
        )
        if method == "get":
            assert "requestBody" not in entry
            continue
        command = entry["requestBody"]["content"]["application/json"]["schema"]
        assert command["additionalProperties"] is False
        assert set(command["properties"]) == set(command["required"]) == expected[(path, method)]
        assert "credential" not in command["properties"]
        for name in ("endpoint", "bearer_token"):
            if name in command["properties"]:
                assert command["properties"][name]["writeOnly"] is True
        route = next(
            r
            for r in router.routes
            if isinstance(r, APIRoute) and "/api" + r.path == path and method.upper() in r.methods
        )
        assert route.body_field is None
    collection = schema["components"]["schemas"]["PublicConnectorCollectionResponse"]
    item = schema["components"]["schemas"]["PublicConnectorItemResponse"]
    assert set(collection["properties"]) == {"project_id", "public_connectors_revision", "items"}
    assert set(item["properties"]) == ITEM_KEYS
    print("PS07_OPENAPI_SCHEMA_OBSERVATIONS " + json.dumps(observations, sort_keys=True))
