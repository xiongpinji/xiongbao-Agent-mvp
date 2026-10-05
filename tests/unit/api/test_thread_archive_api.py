"""Real ASGI archive surface with owned SQLite and the existing file gate."""

import threading

import httpx
import pytest
from tests.unit.api.test_thread_metadata_api import _insert, _snapshot
from tests.unit.api.test_thread_metadata_api import metadata_api as metadata_api


@pytest.mark.asyncio
async def test_archive_receipt_normal_list_deep_link_and_restore(metadata_api):
    api = metadata_api
    _insert(api)
    api.sessions.bind_dashboard_owner(
        session_key="expert:dashboard:1:dm", agent_id="expert", user_id=1, thread_id="one"
    )
    before = _snapshot(api)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        saved = await client.post("/api/threads/one/archive", json={"archived": True})
        assert saved.status_code == 200, saved.text
        state = saved.json()
        assert set(state) == {"thread_id", "agent_id", "archived_at"}
        assert state["thread_id"] == "one" and state["agent_id"] == "expert"
        assert type(state["archived_at"]) is int and 0 < state["archived_at"] <= 9007199254740991
        again = await client.post("/api/threads/one/archive", json={"archived": True})
        assert again.json() == state
        assert (await client.get("/api/agents/expert/threads")).json() == []
        archived = (
            await client.get("/api/agents/expert/threads", params={"archived": "true"})
        ).json()
        detail = (await client.get("/api/agents/expert/threads/one")).json()
        assert archived == [detail] and len(detail) == 17 and detail["is_active"] is True
        assert detail["archived_at"] == state["archived_at"]
        page = (await client.get("/api/threads/archived")).json()
        assert page["items"][0]["thread_id"] == "one" and not page["has_more"]
        assert set(page["items"][0]) == {
            "thread_id",
            "agent_id",
            "title",
            "channel_type",
            "created_at",
            "last_active",
            "archived_at",
            "mode",
        }
        restored = await client.post("/api/threads/one/archive", json={"archived": False})
        assert restored.json() == {"thread_id": "one", "agent_id": "expert", "archived_at": None}
        assert (await client.get("/api/threads/archived")).json()["items"] == []
    assert _snapshot(api) == before
    assert not (api.home / "project-task-files").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"archived": 1},
        {"archived": "true"},
        {"archived": None},
        {},
        {"archived": True, "user_id": 2},
    ],
)
async def test_strict_body_no_write(metadata_api, body):
    _insert(metadata_api)
    before = _snapshot(metadata_api)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=metadata_api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.post("/api/threads/one/archive", json=body)
    assert response.status_code == 422
    assert _snapshot(metadata_api) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,admin,as_user", [(1, False, "1"), (3, True, "1"), (3, True, "3"), (1, False, "")]
)
async def test_all_impersonation_values_forbidden(metadata_api, actor, admin, as_user):
    api = metadata_api
    _insert(api)
    api.user.id, api.user.is_admin = actor, admin
    before = _snapshot(api)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        for method, path in (
            ("GET", "/api/threads/archived"),
            ("POST", "/api/threads/one/archive"),
        ):
            response = await client.request(
                method,
                path,
                params={"as_user": as_user},
                json={"archived": True} if method == "POST" else None,
            )
            assert response.status_code == 403, response.text
    assert _snapshot(api) == before


@pytest.mark.asyncio
async def test_foreign_admin_unknown_and_revoked_are_uniform_not_found(metadata_api):
    api = metadata_api
    _insert(api)
    _insert(api, "admin-own", user_id=3)
    api.user.id, api.user.is_admin = 3, True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        before = _snapshot(api)
        foreign = await client.post("/api/threads/one/archive", json={"archived": True})
        missing = await client.post("/api/threads/missing/archive", json={"archived": True})
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
        assert _snapshot(api) == before
        assert (
            await client.post("/api/threads/admin-own/archive", json={"archived": True})
        ).status_code == 200
        assert [
            r["thread_id"] for r in (await client.get("/api/threads/archived")).json()["items"]
        ] == ["admin-own"]


@pytest.mark.asyncio
async def test_page_literal_query_bounds_and_offloop_sql(metadata_api, monkeypatch):
    api = metadata_api
    for tid in ("a", "b", "c"):
        _insert(api, tid)
        api.threads.update_title(tid, "Straße %_")
        api.threads.set_archive_owned(
            thread_id=tid, user_id=1, actor_is_admin=False, archived=True, now=100
        )
    loop_thread = threading.get_ident()
    real = api.threads.list_archived_by_user
    observed = []

    def load(**kwargs):
        observed.append(threading.get_ident())
        assert observed[-1] != loop_thread
        return real(**kwargs)

    monkeypatch.setattr(api.threads, "list_archived_by_user", load)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        first = await client.get("/api/threads/archived", params={"q": "STRASSE %_", "limit": 2})
        assert first.status_code == 200, first.text
        assert [r["thread_id"] for r in first.json()["items"]] == ["c", "b"] and first.json()[
            "has_more"
        ]
        second = (
            await client.get("/api/threads/archived", params={"limit": 2, "offset": 2})
        ).json()
        assert [r["thread_id"] for r in second["items"]] == ["a"] and not second["has_more"]
        for params in (
            {"q": "x" * 257},
            {"q": "\x00"},
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"offset": 9007199254740992},
        ):
            assert (await client.get("/api/threads/archived", params=params)).status_code == 422
    assert observed


@pytest.mark.asyncio
async def test_exact_files_organization_and_illegal_header_gate(metadata_api):
    api = metadata_api
    _insert(api)
    with api.pool.transaction() as conn:
        conn.execute("UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='expert'")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        assert (
            await client.post("/api/threads/one/archive", json={"archived": True})
        ).status_code == 200
        page = (await client.get("/api/threads/archived")).json()
        assert page["items"][0]["mode"] == "files"
        assert "project_name" not in page["items"][0]
        assert (
            await client.get("/api/threads/archived", headers={"X-Octop-Agent-Id": "expert"})
        ).status_code == 403
        before = _snapshot(api)
        assert (
            await client.post(
                "/api/threads/one/archive",
                json={"archived": False},
                headers={"X-Octop-Agent-Id": "expert"},
            )
        ).status_code == 403
        assert _snapshot(api) == before
    assert not (api.home / "project-task-files").exists()


def test_archive_openapi_has_small_typed_receipt_and_page(metadata_api):
    spec = metadata_api.app.openapi()
    for path, method, expected in (
        ("/api/threads/{thread_id}/archive", "post", "ThreadArchiveResponse"),
        ("/api/threads/archived", "get", "ThreadArchivePageResponse"),
    ):
        operation = spec["paths"][path][method]
        assert operation["summary"] and operation["description"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith(expected)
    body = spec["components"]["schemas"]["ThreadArchiveBody"]
    assert (
        body["additionalProperties"] is False
        and body["properties"]["archived"]["type"] == "boolean"
    )


@pytest.mark.asyncio
async def test_per_agent_archive_filter_runs_sql_outside_event_loop(metadata_api, monkeypatch):
    api = metadata_api
    _insert(api)
    api.threads.set_archive_owned(
        thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100
    )
    real = api.threads.list_by_agent_user
    loop_thread = threading.get_ident()
    observed = []

    def load(**kwargs):
        observed.append(threading.get_ident())
        assert observed[-1] != loop_thread and kwargs["archived"] is True
        return real(**kwargs)

    monkeypatch.setattr(api.threads, "list_by_agent_user", load)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads", params={"archived": "true"})
    assert response.status_code == 200, response.text
    assert response.json()[0]["archived_at"] == 100 and observed
