"""Owned ASGI/SQLite title-search checks; no model or service startup."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from octop.api.app import _install_exception_handlers
from octop.api.deps import current_user, get_server
from octop.api.routers.chat import history
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.gateway.threads import ThreadRegistry


@pytest.fixture
def search_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    pool = SqlitePool(tmp_path / "api.sqlite")
    try:
        run_migrations(pool)
        users = UserRepo(pool)
        for name in ("owner", "other", "admin"):
            users.create(username=name, password_hash="synthetic", role="user")
        agents = AgentRepo(pool)
        agents.create(agent_id="expert", user_id=1, name="Expert")
        agents.create(agent_id="other-expert", user_id=2, name="Other")
        threads = ThreadRepo(pool)
        registry = ThreadRegistry(session_repo=SessionRepo(pool), thread_repo=threads)
        principal = SimpleNamespace(id=1, is_admin=False, locale="en")
        server = SimpleNamespace(
            user_manager=SimpleNamespace(get_by_id=users.get),
            app_runtime=SimpleNamespace(
                gateway=SimpleNamespace(thread_registry=registry),
                agent_registry=SimpleNamespace(
                    get_row=agents.get, resolve_workspace_dir=lambda _: tmp_path
                ),
            ),
        )
        app = FastAPI()
        _install_exception_handlers(app)
        app.include_router(history.router, prefix="/api")
        app.dependency_overrides[current_user] = lambda: principal
        app.dependency_overrides[get_server] = lambda: server
        yield SimpleNamespace(
            app=app, pool=pool, threads=threads, registry=registry, user=principal, server=server
        )
    finally:
        pool.close()


def _insert(api: SimpleNamespace, tid: str, title: str | None, **kwargs: object) -> None:
    api.threads.insert(
        thread_id=tid,
        agent_id=str(kwargs.get("agent_id", "expert")),
        user_id=int(kwargs.get("user_id", 1)),
        channel_type="dashboard",
        session_key="synthetic-session",
        title=title,
        last_active=int(kwargs.get("last_active", 1)),
    )


@pytest.mark.asyncio
async def test_http_matches_title_beyond_original_first_50(search_api: SimpleNamespace) -> None:
    for index in range(65):
        _insert(
            search_api,
            f"thread-{index:03}",
            "Ｓｔｒａße target" if index == 56 else "ordinary title",
            last_active=1000 - index,
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get(
            "/api/agents/expert/threads", params={"q": "STRASSE", "limit": 10}
        )
    assert response.status_code == 200
    assert [row["thread_id"] for row in response.json()] == ["thread-056"]
    assert "title_search_key" not in response.json()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["", " \t\u2003\n"])
async def test_empty_q_and_direct_helper_keep_old_kwargs(
    search_api: SimpleNamespace, q: str
) -> None:
    _insert(search_api, "one", "Hello")
    real = search_api.registry.list_threads

    def old_signature(*, agent_id: str, user_id: int, limit: int = 50) -> object:
        return real(agent_id=agent_id, user_id=user_id, limit=limit)

    search_api.registry.list_threads = old_signature
    direct = await history.list_threads("expert", user=search_api.user, server=search_api.server)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        baseline = await client.get("/api/agents/expert/threads")
        empty = await client.get("/api/agents/expert/threads", params={"q": q})
    assert direct == baseline.json() == empty.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params, status",
    [
        ({"q": "x" * 256}, 200),
        ({"q": " " * 257}, 422),
        ({"q": "a\x00b"}, 422),
        ({"limit": 0}, 422),
        ({"limit": -1}, 422),
        ({"limit": "bad"}, 422),
        ({"limit": 101}, 200),
    ],
)
async def test_http_query_validation(
    search_api: SimpleNamespace, params: dict, status: int
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads", params=params)
    assert response.status_code == status


@pytest.mark.asyncio
async def test_authorized_scope_and_original_array_fields(search_api: SimpleNamespace) -> None:
    _insert(search_api, "own", "target")
    _insert(search_api, "other-user", "target", user_id=2)
    _insert(search_api, "other-agent", "target", user_id=2, agent_id="other-expert")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads", params={"q": "target"})
        denied = await client.get(
            "/api/agents/expert/threads", params={"q": "target", "as_user": 2}
        )
        search_api.user.id, search_api.user.is_admin = 3, True
        allowed = await client.get(
            "/api/agents/other-expert/threads", params={"q": "target", "as_user": 2}
        )
    assert denied.status_code == 403
    assert [row["thread_id"] for row in response.json()] == ["own"]
    assert [row["thread_id"] for row in allowed.json()] == ["other-agent"]
    assert set(response.json()[0]) == {
        "thread_id",
        "title",
        "channel_type",
        "session_key",
        "last_active",
        "created_at",
        "is_active",
        "has_messages",
        "pinned",
        "model_ref",
        "reasoning_mode",
        "reasoning_effort",
        "conversation_mode",
        "pending_plan_path",
        "hitl_policy",
        "artifacts",
    }


@pytest.mark.asyncio
async def test_internal_owner_title_read_does_not_activate_runtime(
    search_api: SimpleNamespace,
) -> None:
    with search_api.pool.transaction() as conn:
        conn.execute("UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='expert'")
    _insert(search_api, "bound", "target")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get("/api/agents/expert/threads", params={"q": "target"})
        denied = await client.get(
            "/api/agents/expert/threads", params={"q": "target", "as_user": 1}
        )
    assert response.status_code == 200
    assert [row["thread_id"] for row in response.json()] == ["bound"]
    assert denied.status_code == 403
    with search_api.pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 11, 51, 101])
async def test_positive_limits_preserve_prefix_without_a_new_cap(search_api, limit: int) -> None:
    for index in range(65):
        _insert(search_api, f"prefix-{index:03}", "target", last_active=1000 - index)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        response = await client.get(
            "/api/agents/expert/threads", params={"q": "target", "limit": limit}
        )
    assert response.status_code == 200
    assert [r["thread_id"] for r in response.json()] == [
        f"prefix-{index:03}" for index in range(min(limit, 65))
    ]


@pytest.mark.asyncio
async def test_shared_agent_keeps_effective_user_and_original_denials(search_api) -> None:
    with search_api.pool.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared=1 WHERE agent_id='expert'")
    _insert(search_api, "owner-target", "target")
    _insert(search_api, "shared-target", "target", user_id=2)
    search_api.user.id = 2
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        shared = await client.get("/api/agents/expert/threads", params={"q": "target"})
        search_api.user.id = 1
        private = await client.get("/api/agents/other-expert/threads", params={"q": "target"})
        search_api.user.id, search_api.user.is_admin = 3, True
        missing = await client.get(
            "/api/agents/expert/threads", params={"q": "target", "as_user": 999}
        )
        wrong_owner = await client.get(
            "/api/agents/expert/threads", params={"q": "target", "as_user": 2}
        )
    assert [r["thread_id"] for r in shared.json()] == ["shared-target"]
    assert private.status_code == wrong_owner.status_code == 403
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_internal_other_user_and_admin_keep_owner_only_read(search_api) -> None:
    with search_api.pool.transaction() as conn:
        conn.execute("UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='expert'")
    _insert(search_api, "bound", "target")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        for uid, admin, params in (
            (2, False, {"q": "target"}),
            (3, True, {"q": "target"}),
            (3, True, {"q": "target", "as_user": 1}),
        ):
            search_api.user.id, search_api.user.is_admin = uid, admin
            response = await client.get("/api/agents/expert/threads", params=params)
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_search_preserves_full_metadata_and_does_not_inject_active_thread(search_api) -> None:
    _insert(search_api, "active", "ordinary title")
    _insert(search_api, "match", "target", last_active=0)
    search_api.threads.set_pinned("match", True)
    search_api.threads.update_composer(
        "match", model_ref="synthetic/model", conversation_mode="craft"
    )
    key = ThreadRegistry.dashboard_key(agent_id="expert", user_id=1)
    SessionRepo(search_api.pool).upsert(
        session_key=key,
        agent_id="expert",
        user_id=1,
        channel_type="dashboard",
        chat_type="dm",
        thread_id="active",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_api.app), base_url="http://owned-asgi"
    ) as client:
        baseline = await client.get("/api/agents/expert/threads")
        filtered = await client.get("/api/agents/expert/threads", params={"q": "target"})
    assert filtered.status_code == 200
    assert filtered.json() == [r for r in baseline.json() if r["thread_id"] == "match"]
    assert filtered.json()[0]["is_active"] is False
    # Existing metadata treats any nonempty title as a conversation with messages.
    assert filtered.json()[0]["has_messages"] is True
    assert search_api.registry.get_bound_thread_id(key) == "active"
