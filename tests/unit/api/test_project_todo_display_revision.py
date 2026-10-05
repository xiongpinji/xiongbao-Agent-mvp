"""Required display projection and cursor behavior through real ASGI routes."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.db.test_project_plan_query import create_view, query
from tests.unit.db.test_project_plan_query import query_case as query_case
from tests.unit.db.test_project_todo_d1_foundation import case as case

from octop.api.app import _install_exception_handlers
from octop.api.deps import current_user, get_server
from octop.api.routers.project_todo_views import router as plan_router
from octop.api.routers.project_todos import router
from octop.infra.projects.plan_definition import default_definition


def app_for(services, owner):
    app = FastAPI()
    _install_exception_handlers(app)
    app.include_router(router, prefix="/api")
    app.include_router(plan_router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=owner)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(services=services)
    return app


@pytest.mark.asyncio
async def test_real_get_and_patch_expose_required_display(case):
    _, _, projects, todos, owner, _, pid, row = case
    services = SimpleNamespace(
        project_repo=projects,
        project_todo_repo=todos,
        config=SimpleNamespace(default_timezone="UTC"),
    )
    app = app_for(services, owner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/projects/{pid}/todos/{row.todo_id}")
        assert response.status_code == 200
        assert response.json()["display_revision"] == 1
        response = await client.patch(
            f"/api/projects/{pid}/todos/{row.todo_id}", json={"expected_version": 1, "title": "new"}
        )
        assert response.status_code == 200
        assert response.json()["display_revision"] == 2
        response = await client.get(f"/api/projects/{pid}/todos")
        assert response.status_code == 200
        assert response.json()["items"][0]["display_revision"] == 2
        response = await client.post(f"/api/projects/{pid}/todos", json={"title": "created"})
        assert response.status_code == 201
        assert response.json()["display_revision"] == 1
        response = await client.post(
            f"/api/projects/{pid}/todos/bulk",
            json={"items": [{"todo_id": row.todo_id, "expected_version": 2}], "status": "done"},
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["display_revision"] == 3
    schema = app.openapi()["components"]["schemas"]["TodoResponse"]
    assert "display_revision" in schema["required"]
    assert schema["properties"]["display_revision"]["maximum"] == 9007199254740991


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["table", "list", "board", "calendar", "gantt"])
async def test_all_five_real_query_responses_expose_display(query_case, kind):
    app = app_for(query_case["service"]._services, query_case["owner"])
    view_id = query_case["view_id"] if kind == "table" else create_view(query_case, kind)
    data = {
        "view_id": view_id,
        "expected_view_version": 1,
        "expected_catalog_revision": query_case["revision"],
    }
    if kind in ("calendar", "gantt"):
        data.update(
            window={"start_date": "2027-01-01", "end_date": "2027-01-31"}, bucket="unscheduled"
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/projects/{query_case['pid']}/plan/query", json=data)
        assert response.status_code == 200
        assert response.json()["total"] == 4
        assert {item["display_revision"] for item in response.json()["items"]} == {1}


@pytest.mark.asyncio
async def test_v2_display_anchor_and_v1_retirement_keep_http_error_contract(query_case):
    first = query(query_case, limit=1)
    decoded = json.loads(
        base64.urlsafe_b64decode(first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4))
    )
    assert decoded["v"] == 2
    assert set(decoded) == {"v", "query_fingerprint", "last_todo_id", "last_display_revision"}
    with query_case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_display_state SET revision=revision+1 WHERE todo_id=?",
            (decoded["last_todo_id"],),
        )
    legacy = {
        "v": 1,
        "query_fingerprint": decoded["query_fingerprint"],
        "last_todo_id": decoded["last_todo_id"],
        "last_version": 1,
    }
    legacy_token = base64.urlsafe_b64encode(json.dumps(legacy).encode()).decode().rstrip("=")
    data = {
        "view_id": query_case["view_id"],
        "expected_view_version": 1,
        "expected_catalog_revision": query_case["revision"],
        "limit": 1,
        "override_definition": default_definition("table"),
    }
    app = app_for(query_case["service"]._services, query_case["owner"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for token in (first["next_cursor"], legacy_token):
            response = await client.post(
                f"/api/projects/{query_case['pid']}/plan/query", json=dict(data, cursor=token)
            )
            assert response.status_code == 409
            assert response.json()["error"]["details"] == {"reason": "query_changed"}
        response = await client.post(
            f"/api/projects/{query_case['pid']}/plan/query", json=dict(data, cursor="bad")
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"] == {"reason": "invalid_query_request"}
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=query_case["outsider"])
        response = await client.post(
            f"/api/projects/{query_case['pid']}/plan/query", json=dict(data, cursor=legacy_token)
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_missing_display_has_localized_internal_error_and_preserves_acl(case):
    pool, _, projects, todos, owner, _, pid, row = case
    with pool.transaction() as conn:
        conn.execute("DELETE FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,))
    services = SimpleNamespace(
        project_repo=projects,
        project_todo_repo=todos,
        config=SimpleNamespace(default_timezone="UTC"),
    )
    app = app_for(services, owner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/projects/{pid}/todos/{row.todo_id}", headers={"Accept-Language": "zh"}
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "display" not in response.text and "state" not in response.text
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=999999)
        response = await client.get(f"/api/projects/{pid}/todos/{row.todo_id}")
        assert response.status_code == 404
