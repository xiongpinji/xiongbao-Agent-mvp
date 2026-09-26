"""Unit tests for the 030A B4 default-deny HTTP ingress gate.

Covers the pure path/allowlist helpers and the middleware end-to-end against a
stub server: owner-only allowlist, admin and ``as_user`` refusal, header-based
agent selection, fail-closed row-lookup errors, and ordinary-agent
pass-through. Real DB-backed behavior is covered by
``tests/integration/test_project_task_files_access.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from octop.api.middleware.project_task_file_gate import (
    install,
    is_allowed_internal_route,
    is_project_task_file_row,
    path_agent_id,
    path_tail,
    refusal_response,
)
from octop.infra.db.repos.agents import RUNTIME_KIND_PROJECT_TASK_FILES, RUNTIME_KIND_STANDARD

INTERNAL_ID = "ptf-runtime-1"
ORDINARY_ID = "agent-ordinary"


def _internal_row(user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=INTERNAL_ID, runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES, user_id=user_id
    )


def _ordinary_row(user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=ORDINARY_ID, runtime_kind=RUNTIME_KIND_STANDARD, user_id=user_id
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_is_project_task_file_row() -> None:
    assert is_project_task_file_row(_internal_row())
    assert not is_project_task_file_row(_ordinary_row())
    assert not is_project_task_file_row(None)
    assert not is_project_task_file_row(SimpleNamespace())


def test_path_agent_id() -> None:
    assert path_agent_id("/api/agents/abc123") == "abc123"
    assert path_agent_id("/api/agents/abc123/threads/t1/history") == "abc123"
    assert path_agent_id("/api/agents/") is None
    assert path_agent_id("/api/agents") is None
    assert path_agent_id("/api/projects/p1/tasks") is None
    assert path_agent_id("/agents/abc123") is None
    assert path_agent_id("/") is None


def test_path_tail() -> None:
    assert path_tail("/api/agents/a1") == ()
    assert path_tail("/api/agents/a1/") == ()
    assert path_tail("/api/agents/a1/threads") == ("threads",)
    assert path_tail("/api/agents/a1/threads/t1/history") == ("threads", "t1", "history")
    assert path_tail("/api/agents/a1/workspace/file") == ("workspace", "file")
    assert path_tail("/api/projects/p1/tasks") == ()


def test_is_allowed_internal_route_allowlist() -> None:
    allowed = [
        ("GET", ("threads",)),
        ("GET", ("threads", "t1", "history")),
        ("POST", ("threads", "t1", "read")),
        ("GET", ("media", "preview")),
        ("GET", ("workspace", "tree")),
        ("GET", ("workspace", "download")),
        ("GET", ("workspace", "glob")),
        ("GET", ("workspace", "grep")),
        ("GET", ("workspace", "file")),
        ("PUT", ("workspace", "file")),
        ("GET", ("workspace", "doc")),
        ("PUT", ("workspace", "doc")),
        ("POST", ("workspace", "upload")),
    ]
    for method, tail in allowed:
        assert is_allowed_internal_route(method, tail), (method, tail)


def test_is_allowed_internal_route_denies_everything_else() -> None:
    denied = [
        ("GET", ()),  # agent detail
        ("PUT", ()),  # agent edit
        ("DELETE", ()),  # agent delete
        ("POST", ("start",)),
        ("POST", ("stop",)),
        ("POST", ("reload",)),
        ("POST", ("threads",)),  # thread create
        ("DELETE", ("threads", "t1")),  # thread delete
        ("POST", ("threads", "t1", "fork")),
        ("POST", ("threads", "t1", "rebind")),
        ("GET", ("threads", "t1", "export")),
        ("PATCH", ("threads", "t1")),
        ("GET", ("threads", "t1", "context-usage")),
        ("GET", ("threads", "t1")),  # bare thread get
        ("POST", ("threads", "t1", "write")),  # wrong verb on read
        ("GET", ("threads", "t1", "read")),  # wrong verb on read
        ("GET", ("terminal", "context")),
        ("POST", ("workspace", "mkdir")),
        ("POST", ("workspace", "move")),
        ("DELETE", ("workspace", "file")),
        ("GET", ("workspace", "archive")),
        ("POST", ("workspace", "archive")),
        ("POST", ("share",)),
        ("GET", ("media", "file")),
    ]
    for method, tail in denied:
        assert not is_allowed_internal_route(method, tail), (method, tail)


def test_refusal_response_envelope() -> None:
    resp = refusal_response()
    assert resp.status_code == 403
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"] == {"internal": True}


# ---------------------------------------------------------------------------
# Middleware end-to-end on a stub app
# ---------------------------------------------------------------------------


class _StubRegistry:
    def __init__(self, rows: dict[str, Any], *, raise_on_get: bool = False) -> None:
        self._rows = rows
        self._raise = raise_on_get

    def get_row(self, agent_id: str) -> Any:
        if self._raise:
            raise RuntimeError("control plane unavailable")
        return self._rows.get(agent_id)


def _build_app(
    rows: dict[str, Any],
    *,
    user: Any,
    raise_on_get: bool = False,
    unbound: bool = False,
) -> FastAPI:
    server = SimpleNamespace(
        app_runtime=(
            None
            if unbound
            else SimpleNamespace(agent_registry=_StubRegistry(rows, raise_on_get=raise_on_get))
        )
    )
    app = FastAPI()
    install(app, server)
    install(app, server)  # idempotent: the gate must run exactly once

    @app.middleware("http")
    async def _inject_user(request: Request, call_next: Any) -> Any:
        # Registered AFTER the gate, so it wraps it (Starlette runs middleware
        # in reverse registration order) — mirrors the real jwt-auth layer.
        if user is not None:
            request.state.octop_user = user
        return await call_next(request)

    @app.api_route(
        "/api/agents/{agent_id}/threads",
        methods=["GET", "POST"],
    )
    async def _threads(agent_id: str) -> dict[str, Any]:
        return {"ok": True, "agent_id": agent_id}

    @app.api_route(
        "/api/agents/{agent_id}/threads/{thread_id}/history",
        methods=["GET"],
    )
    async def _history(agent_id: str, thread_id: str) -> dict[str, Any]:
        return {"ok": True}

    @app.api_route("/api/echo", methods=["GET", "POST", "OPTIONS"])
    async def _echo() -> dict[str, Any]:
        return {"ok": True}

    return app


@pytest.fixture
async def gate_client():
    rows = {INTERNAL_ID: _internal_row(user_id=7), ORDINARY_ID: _ordinary_row(user_id=7)}
    app = _build_app(rows, user=SimpleNamespace(id=7, is_admin=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        yield c


def _forbidden(resp: httpx.Response) -> None:
    assert resp.status_code == 403, resp.text
    body = resp.json()["error"]
    assert body["code"] == "FORBIDDEN"
    assert body["details"] == {"internal": True}


async def test_owner_allowed_routes_pass(gate_client: httpx.AsyncClient) -> None:
    r = await gate_client.get(f"/api/agents/{INTERNAL_ID}/threads")
    assert r.status_code == 200, r.text
    r = await gate_client.get(f"/api/agents/{INTERNAL_ID}/threads/t1/history")
    assert r.status_code == 200, r.text


async def test_owner_denied_routes_are_refused(gate_client: httpx.AsyncClient) -> None:
    r = await gate_client.post(f"/api/agents/{INTERNAL_ID}/threads")
    _forbidden(r)
    # The middleware short-circuits before routing, so even a verb with no
    # stub route is answered by the gate itself.
    r = await gate_client.delete(f"/api/agents/{INTERNAL_ID}")
    _forbidden(r)
    r = await gate_client.get(f"/api/agents/{INTERNAL_ID}/terminal/context")
    _forbidden(r)


async def test_other_user_denied_even_on_allowlist(gate_client: httpx.AsyncClient) -> None:
    rows = {INTERNAL_ID: _internal_row(user_id=7)}
    app = _build_app(rows, user=SimpleNamespace(id=99, is_admin=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        r = await c.get(f"/api/agents/{INTERNAL_ID}/threads")
        _forbidden(r)


async def test_admin_denied_without_ownership(gate_client: httpx.AsyncClient) -> None:
    rows = {INTERNAL_ID: _internal_row(user_id=7)}
    app = _build_app(rows, user=SimpleNamespace(id=1, is_admin=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        r = await c.get(f"/api/agents/{INTERNAL_ID}/threads")
        _forbidden(r)


async def test_as_user_impersonation_denied_for_owner_too() -> None:
    rows = {INTERNAL_ID: _internal_row(user_id=7)}
    app = _build_app(rows, user=SimpleNamespace(id=1, is_admin=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        r = await c.get(f"/api/agents/{INTERNAL_ID}/threads", params={"as_user": "7"})
        _forbidden(r)


async def test_missing_user_fails_closed() -> None:
    rows = {INTERNAL_ID: _internal_row(user_id=7)}
    app = _build_app(rows, user=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        r = await c.get(f"/api/agents/{INTERNAL_ID}/threads")
        _forbidden(r)


async def test_header_agent_selection_is_gated(gate_client: httpx.AsyncClient) -> None:
    r = await gate_client.post("/api/echo", headers={"X-Octop-Agent-Id": INTERNAL_ID})
    _forbidden(r)
    # Allowed-shape routes outside /api/agents stay denied through the header.
    r = await gate_client.get("/api/echo", headers={"x-octop-agent-id": INTERNAL_ID})
    _forbidden(r)


async def test_ordinary_agents_are_untouched(gate_client: httpx.AsyncClient) -> None:
    r = await gate_client.post(f"/api/agents/{ORDINARY_ID}/threads")
    assert r.status_code == 200, r.text
    r = await gate_client.get("/api/echo")
    assert r.status_code == 200, r.text
    r = await gate_client.post("/api/echo", headers={"X-Octop-Agent-Id": ORDINARY_ID})
    assert r.status_code == 200, r.text


async def test_options_preflight_passes(gate_client: httpx.AsyncClient) -> None:
    r = await gate_client.options("/api/echo", headers={"X-Octop-Agent-Id": INTERNAL_ID})
    assert r.status_code == 200, r.text


async def test_row_lookup_failure_denies_before_handlers() -> None:
    app = _build_app({}, user=SimpleNamespace(id=7, is_admin=False), raise_on_get=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        # An unreadable row must deny, never fall through to the handler.
        r = await c.post(f"/api/agents/{INTERNAL_ID}/threads")
        assert r.status_code == 503, r.text
        body = r.json()["error"]
        assert body["code"] == "PROJECT_TASK_FILES_UNAVAILABLE"
        assert r.status_code != 200
        # Header-based agent selection fails closed the same way.
        r = await c.post("/api/echo", headers={"X-Octop-Agent-Id": INTERNAL_ID})
        assert r.status_code == 503, r.text
        # No agent candidate → ordinary behavior, even while lookups fail.
        r = await c.get("/api/echo")
        assert r.status_code == 200, r.text


async def test_unbound_control_plane_denies_targeted_requests() -> None:
    app = _build_app({}, user=SimpleNamespace(id=7, is_admin=False), unbound=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gate"
    ) as c:
        r = await c.get(f"/api/agents/{INTERNAL_ID}/threads")
        assert r.status_code == 503, r.text
        r = await c.get("/api/echo", headers={"X-Octop-Agent-Id": INTERNAL_ID})
        assert r.status_code == 503, r.text
        r = await c.get("/api/echo")
        assert r.status_code == 200, r.text
