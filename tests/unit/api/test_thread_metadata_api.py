"""Metadata-only reads through the HTTP gate and owned SQLite repositories."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from octop.api.app import _install_exception_handlers
from octop.api.deps import current_user, get_server
from octop.api.middleware.project_task_file_gate import install
from octop.api.routers.chat import history
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.utils.paths import PathLayout


def _unexpected(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Metadata GET must not access runtime, history or mutate workspace")


@pytest.fixture
def metadata_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    pool = SqlitePool(tmp_path / "metadata.sqlite")
    try:
        run_migrations(pool)
        users = UserRepo(pool)
        for name in ("owner", "other", "admin"):
            users.create(username=name, password_hash="synthetic", role="user")
        agents = AgentRepo(pool)
        agents.create(agent_id="expert", user_id=1, name="Expert")
        agents.create(agent_id="other-expert", user_id=2, name="Other")
        threads = ThreadRepo(pool)
        sessions = SessionRepo(pool)
        registry = ThreadRegistry(session_repo=sessions, thread_repo=threads)
        principal = SimpleNamespace(id=1, is_admin=False, locale="en")
        configs: dict[str, dict[str, Any]] = {"expert": {"workspace_dir": str(tmp_path)}}
        server = SimpleNamespace(
            paths=PathLayout(tmp_path),
            user_manager=SimpleNamespace(get_by_id=users.get),
            services=SimpleNamespace(thread_message_repo=SimpleNamespace(get=_unexpected)),
            app_runtime=SimpleNamespace(
                gateway=SimpleNamespace(thread_registry=registry),
                agent_registry=SimpleNamespace(
                    get_row=agents.get,
                    get_config=lambda aid: configs.get(aid, {}),
                    get_agent=_unexpected,
                    resolve_workspace_dir=_unexpected,
                    persist_harness_config=_unexpected,
                ),
            ),
        )
        app = FastAPI()
        _install_exception_handlers(app)
        app.include_router(history.router, prefix="/api")
        app.dependency_overrides[current_user] = lambda: principal
        app.dependency_overrides[get_server] = lambda: server
        install(app, server)

        @app.middleware("http")
        async def inject_user(request: Request, call_next: Any) -> Any:
            request.state.octop_user = principal
            return await call_next(request)

        monkeypatch.setattr(history, "_backfill_thread_projection", _unexpected)
        monkeypatch.setattr(history, "_load_projected_thread_messages", _unexpected)
        monkeypatch.setattr(registry, "rebind", _unexpected)
        monkeypatch.setattr(registry, "reset", _unexpected)
        yield SimpleNamespace(
            app=app,
            pool=pool,
            agents=agents,
            threads=threads,
            sessions=sessions,
            registry=registry,
            user=principal,
            server=server,
            configs=configs,
            home=tmp_path,
        )
    finally:
        pool.close()


def _insert(
    api: SimpleNamespace, tid: str = "one", *, user_id: int = 1, agent_id: str = "expert"
) -> None:
    api.threads.insert(
        thread_id=tid,
        agent_id=agent_id,
        user_id=user_id,
        channel_type="dashboard",
        session_key=ThreadRegistry.dashboard_key(agent_id=agent_id, user_id=user_id),
        title=None,
        last_active=0,
    )


def _snapshot(api: SimpleNamespace) -> dict[str, list[tuple[Any, ...]]]:
    with api.pool.connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("agents", "threads", "sessions", "thread_messages")
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "allow_all", "allow_tools"])
async def test_metadata_equals_list_and_is_read_only(
    metadata_api: SimpleNamespace, mode: str
) -> None:
    api = metadata_api
    _insert(api)
    api.sessions.bind_dashboard_owner(
        session_key=ThreadRegistry.dashboard_key(agent_id="expert", user_id=1),
        agent_id="expert",
        user_id=1,
        thread_id="one",
    )
    policy = {"mode": mode}
    if mode == "allow_tools":
        policy["tools"] = ["read_file"]
    with api.pool.transaction() as conn:
        conn.execute(
            "UPDATE threads SET title=?, last_active=123, pinned=1, model_ref=?, "
            "reasoning_mode=?, reasoning_effort=?, conversation_mode=?, pending_plan_path=?, "
            "hitl_policy=?, artifacts=? WHERE thread_id='one'",
            (
                "Stored title",
                "synthetic/model",
                "enabled",
                "high",
                "plan",
                "plans/one.md",
                json.dumps(policy),
                json.dumps(["outbound/report.pdf", "outbound/report.pdf"]),
            ),
        )
    before = _snapshot(api)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        listed = await client.get("/api/agents/expert/threads")
        first = await client.get("/api/agents/expert/threads/one")
        again = await client.get("/api/agents/expert/threads/one")
    assert first.status_code == 200, first.text
    assert first.json() == again.json() == listed.json()[0]
    assert first.json()["hitl_policy"] == policy
    assert first.json()["is_active"] is True
    assert first.json()["has_messages"] is True
    assert first.json()["artifacts"] == [(api.home / "outbound" / "report.pdf").as_posix()]
    assert _snapshot(api) == before


@pytest.mark.asyncio
async def test_missing_workspace_read_does_not_create_or_persist(
    metadata_api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = metadata_api
    api.configs.clear()
    _insert(api)
    api.threads.append_artifacts("one", ["outbound/report.pdf"])
    before = _snapshot(api)
    monkeypatch.setattr(PathLayout, "ensure_agent_workspace", _unexpected)
    monkeypatch.setattr(PathLayout, "ensure_project_task_file_runtime_dir", _unexpected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads/one")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["title"] is None
    assert payload["model_ref"] is None
    assert payload["reasoning_mode"] is None
    assert payload["reasoning_effort"] is None
    assert payload["pending_plan_path"] is None
    assert payload["conversation_mode"] == "craft"
    assert payload["is_active"] is False
    assert payload["has_messages"] is False
    assert payload["hitl_policy"] == {"mode": "ask"}
    assert payload["artifacts"] == [
        (api.home / "agents" / "expert" / "outbound" / "report.pdf").as_posix()
    ]
    assert not (api.home / "agents").exists()
    assert not (api.home / "project-task-files").exists()
    assert _snapshot(api) == before


@pytest.mark.asyncio
async def test_scoped_config_keeps_agent_facing_spelling(metadata_api: SimpleNamespace) -> None:
    api = metadata_api
    api.configs["expert"] = {
        "workspace_dir": "/.octop/workspaces/expert",
        "backend": {"type": "filesystem", "root_dir": str(api.home)},
    }
    _insert(api)
    api.threads.append_artifacts("one", ["outbound/report.pdf"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads/one")
    assert response.status_code == 200, response.text
    assert response.json()["artifacts"] == [
        (Path("/.octop/workspaces/expert") / "outbound" / "report.pdf").as_posix()
    ]


@pytest.mark.asyncio
async def test_normal_permitted_owner_shared_and_admin_reads(metadata_api: SimpleNamespace) -> None:
    api = metadata_api
    _insert(api, "owner")
    _insert(api, "shared-own", user_id=2)
    _insert(api, "other-own", user_id=2, agent_id="other-expert")
    with api.pool.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared=1 WHERE agent_id='expert'")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        api.user.id = 2
        shared = await client.get("/api/agents/expert/threads/shared-own")
        api.user.id, api.user.is_admin = 3, True
        admin = await client.get(
            "/api/agents/other-expert/threads/other-own", params={"as_user": 2}
        )
    assert shared.status_code == 200, shared.text
    assert admin.status_code == 200, admin.text
    assert shared.json()["thread_id"] == "shared-own"
    assert admin.json()["thread_id"] == "other-own"


@pytest.mark.asyncio
async def test_missing_and_forbidden_are_distinct(metadata_api: SimpleNamespace) -> None:
    api = metadata_api
    _insert(api)
    _insert(api, "other-own", user_id=2, agent_id="other-expert")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        missing = await client.get("/api/agents/expert/threads/absent")
        mismatch = await client.get("/api/agents/expert/threads/other-own")
        absent_agent = await client.get("/api/agents/absent/threads/one")
        forbidden = await client.get("/api/agents/other-expert/threads/other-own")
        impersonation = await client.get("/api/agents/expert/threads/one", params={"as_user": 2})
    assert missing.status_code == mismatch.status_code == absent_agent.status_code == 404
    assert forbidden.status_code == impersonation.status_code == 403


@pytest.mark.asyncio
async def test_internal_owner_read_uses_trusted_root_and_gate(
    metadata_api: SimpleNamespace,
) -> None:
    api = metadata_api
    with api.pool.transaction() as conn:
        conn.execute("UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='expert'")
    api.configs["expert"] = {"workspace_dir": str(api.home / "untrusted-config")}
    _insert(api)
    api.threads.append_artifacts("one", ["outbound/report.pdf"])
    before = _snapshot(api)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        owner = await client.get("/api/agents/expert/threads/one")
        as_user = await client.get("/api/agents/expert/threads/one", params={"as_user": 1})
        api.user.id, api.user.is_admin = 3, True
        admin = await client.get("/api/agents/expert/threads/one")
    assert owner.status_code == 200, owner.text
    assert owner.json()["artifacts"] == [
        (api.home / "project-task-files" / "expert" / "outbound" / "report.pdf").as_posix()
    ]
    assert as_user.status_code == admin.status_code == 403
    assert _snapshot(api) == before
    assert not (api.home / "project-task-files").exists()


@pytest.mark.asyncio
async def test_new_handler_repo_read_runs_outside_event_loop(
    metadata_api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = metadata_api
    _insert(api)
    loop_thread = threading.get_ident()
    observed: list[int] = []
    real = api.registry.get_thread

    def observed_get(tid: str) -> Any:
        observed.append(threading.get_ident())
        assert observed[-1] != loop_thread
        return real(tid)

    monkeypatch.setattr(api.registry, "get_thread", observed_get)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads/one")
    assert response.status_code == 200, response.text
    assert observed


def test_openapi_has_typed_metadata_without_history(metadata_api: SimpleNamespace) -> None:
    spec = metadata_api.app.openapi()
    operation = spec["paths"]["/api/agents/{agent_id}/threads/{thread_id}"]["get"]
    assert operation["summary"] and operation["description"]
    schema_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    properties = spec["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]["properties"]
    assert {"thread_id", "has_messages", "is_active", "artifacts", "hitl_policy"} <= set(properties)
    assert "messages" not in properties
    assert "title_search_key" not in properties
