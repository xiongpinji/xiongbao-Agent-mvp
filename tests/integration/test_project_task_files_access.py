"""030A B4 default-deny ingress integration tests for internal file runtimes.

Proves against the real app + real DB that a ``runtime_kind=project_task_files``
agent is fenced off from the generic dashboard surface:

* GLM P1(a): the owner's ``POST /api/agents/{id}/threads`` fails with 403 and
  leaves zero new rows.
* GLM P1(b): a dashboard WS turn without an explicit EXISTING bound thread id
  is refused before any side effect (no thread/session rows appear), as are
  foreign thread ids and client-injected capability metadata.
* Admins, ``as_user`` impersonation, unrelated users, and project share
  readers are denied everywhere — allowlisted routes are owner-only.
* The owner keeps exactly the read surface promised by the minimal card:
  thread list, history, mark-read, and the chat WS on the bound thread.
* Generic management (detail/edit/share/avatar/start/stop/reload/delete),
  thread mutations (create/fork/rebind/export/patch/context-usage/migration/
  delete), and the terminal surface are denied for everyone.
* Ordinary agents are untouched by the gate.

``DELETE /api/project-task-files/{thread_id}`` (covered by
``test_project_task_file_mode_api.py``) stays the only complete delete.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from octop.api.routers import terminal as terminal_module
from tests.integration.test_project_task_file_mode_api import (
    MEMBER,
    _base,
    _count,
    _create_files,
    _install_runtime_fake,
    _share,
    _surface,
)
from tests.support.auth import create_agent
from tests.support.http import chat_ws_path, ws_connect, ws_token

# In the B3 fixture the project MEMBER creates the file task, so the member is
# the runtime owner; "owner" below always means ctx["auth"]["member"].


async def _files_ctx(env: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Open the gate, fake the runtime allocation, create one real file task."""
    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env), monkeypatch)
    r = await _create_files(ctx)
    assert r.status_code == 201, r.text
    payload = r.json()
    ctx["tid"] = payload["thread_id"]
    ctx["rt"] = payload["chat_agent_id"]
    ctx["owner_auth"] = ctx["auth"]["member"]
    ctx["owner_uid"] = ctx["uid"]["member"]
    return ctx


def _forbidden(resp: httpx.Response) -> None:
    assert resp.status_code == 403, resp.text
    body = resp.json()["error"]
    assert body["code"] == "FORBIDDEN"
    assert body["details"] == {"internal": True}


async def _grant_terminal(srv: Any, username: str) -> None:
    """Grant ``terminal`` so a refusal can never be a missing-permission one."""
    from octop.infra.users.permissions import BASELINE_PERMISSIONS

    await srv.user_manager.set_permissions(username, sorted({*BASELINE_PERMISSIONS, "terminal"}))


# ---------------------------------------------------------------------------
# GLM P1(a): owner thread creation on the internal runtime
# ---------------------------------------------------------------------------


async def test_owner_thread_create_is_refused_before_side_effects(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, srv, rt = ctx["client"], ctx["srv"], ctx["rt"]
    threads_before = _count(srv, "threads", "agent_id = ?", (rt,))
    sessions_before = _count(srv, "sessions", "agent_id = ?", (rt,))
    assert threads_before == 1

    r = await client.post(f"/api/agents/{rt}/threads", headers=ctx["owner_auth"])
    _forbidden(r)
    # No implicit /new thread, no session row, for the owner either.
    assert _count(srv, "threads", "agent_id = ?", (rt,)) == 1
    assert _count(srv, "sessions", "agent_id = ?", (rt,)) == sessions_before


# ---------------------------------------------------------------------------
# Identity matrix on allowlisted and denied routes
# ---------------------------------------------------------------------------


async def test_admin_and_impersonation_denied_on_allowlist(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]

    # Admin without ownership: denied even on owner-allowlisted routes.
    r = await client.get(f"/api/agents/{rt}/threads", headers=ctx["admin_auth"])
    _forbidden(r)
    # Admin impersonating the owner: still denied.
    r = await client.get(
        f"/api/agents/{rt}/threads",
        headers=ctx["admin_auth"],
        params={"as_user": ctx["owner_uid"]},
    )
    _forbidden(r)
    # Owner with any as_user: denied (no delegation into internal runtimes).
    r = await client.get(
        f"/api/agents/{rt}/threads",
        headers=ctx["owner_auth"],
        params={"as_user": ctx["uid"]["other"]},
    )
    _forbidden(r)


async def test_unrelated_user_and_share_reader_denied(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, srv, rt, tid = ctx["client"], ctx["srv"], ctx["rt"], ctx["tid"]

    for role in ("other", "outsider"):
        r = await client.get(f"/api/agents/{rt}/threads", headers=ctx["auth"][role])
        _forbidden(r)

    # A project share grant on the task card does NOT open the agent surface.
    _share(srv, tid, ctx["owner_uid"], ctx["uid"]["other"], ctx["pid"])
    r = await client.get(f"/api/agents/{rt}/threads", headers=ctx["auth"]["other"])
    _forbidden(r)
    r = await client.get(f"/api/agents/{rt}/threads/{tid}/history", headers=ctx["auth"]["other"])
    _forbidden(r)


async def test_owner_keeps_bound_thread_read_surface(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, rt, tid = ctx["client"], ctx["rt"], ctx["tid"]

    r = await client.get(f"/api/agents/{rt}/threads", headers=ctx["owner_auth"])
    assert r.status_code == 200, r.text
    assert [t["thread_id"] for t in r.json()] == [tid]

    r = await client.get(f"/api/agents/{rt}/threads/{tid}/history", headers=ctx["owner_auth"])
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/agents/{rt}/threads/{tid}/read", headers=ctx["owner_auth"])
    assert r.status_code == 204, r.text


async def test_owner_denied_all_thread_mutations(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, rt, tid = ctx["client"], ctx["rt"], ctx["tid"]
    auth = ctx["owner_auth"]

    denied = [
        ("post", f"/api/agents/{rt}/threads/{tid}/fork", None),
        ("get", f"/api/agents/{rt}/threads/{tid}/history/export", None),
        ("patch", f"/api/agents/{rt}/threads/{tid}", {"title": "renamed"}),
        ("get", f"/api/agents/{rt}/threads/{tid}/context-usage", None),
        ("get", f"/api/agents/{rt}/history-migration/status", None),
        ("post", f"/api/agents/{rt}/history-migration/start", {}),
        ("patch", f"/api/agents/{rt}/session", {"thread_id": tid}),
        ("delete", f"/api/agents/{rt}/threads/{tid}", None),
    ]
    for method, url, body in denied:
        r = await client.request(method.upper(), url, headers=auth, json=body)
        _forbidden(r)

    # The bound thread survives every refused mutation.
    r = await client.get(f"/api/agents/{rt}/threads", headers=auth)
    assert [t["thread_id"] for t in r.json()] == [tid]


async def test_generic_agent_surface_denied_for_owner_and_admin(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]

    owner_denied = [
        ("get", f"/api/agents/{rt}", None),
        ("patch", f"/api/agents/{rt}", {"name": "renamed-runtime"}),
        ("delete", f"/api/agents/{rt}", None),
        ("post", f"/api/agents/{rt}/start", None),
        ("post", f"/api/agents/{rt}/stop", None),
        ("post", f"/api/agents/{rt}/reload", None),
        ("delete", f"/api/agents/{rt}/avatar", None),
    ]
    for method, url, body in owner_denied:
        r = await client.request(method.upper(), url, headers=ctx["owner_auth"], json=body)
        _forbidden(r)

    r = await client.get(f"/api/agents/{rt}", headers=ctx["admin_auth"])
    _forbidden(r)

    # The runtime row and its name are untouched.
    row = ctx["srv"].services.agent_repo.get(rt)
    assert row is not None and row.name.startswith("task-file-fake-")


# ---------------------------------------------------------------------------
# Chat websocket: pre-accept ownership + turn-level binding/metadata gates
# ---------------------------------------------------------------------------


async def test_chat_ws_denies_non_owners_before_accept(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, srv, rt, tid = ctx["client"], ctx["srv"], ctx["rt"], ctx["tid"]
    app = client._octop_app  # type: ignore[attr-defined]

    _share(srv, tid, ctx["owner_uid"], ctx["uid"]["other"], ctx["pid"])
    for auth in (ctx["auth"]["other"], ctx["auth"]["outsider"], ctx["admin_auth"]):
        with pytest.raises(WebSocketDisconnect) as excinfo:
            async with ws_connect(app, chat_ws_path(rt, auth)):
                pass
        assert excinfo.value.code in (4003, 4404)


async def test_chat_ws_owner_turn_without_bound_thread_has_no_side_effects(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, srv, rt = ctx["client"], ctx["srv"], ctx["rt"]
    app = client._octop_app  # type: ignore[attr-defined]
    threads_before = _count(srv, "threads", "agent_id = ?", (rt,))
    sessions_before = _count(srv, "sessions", "agent_id = ?", (rt,))

    async with ws_connect(app, chat_ws_path(rt, ctx["owner_auth"])) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "hi",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
        frames = await ws.drain_turn()
        tail = await ws.receive_json()

    assert frames[0]["type"] == "error"
    assert tail["type"] == "done"
    # GLM P1(b): refusal BEFORE any side effect — no implicit thread/session.
    assert _count(srv, "threads", "agent_id = ?", (rt,)) == threads_before
    assert _count(srv, "sessions", "agent_id = ?", (rt,)) == sessions_before


async def test_chat_ws_refuses_foreign_thread_and_injected_metadata(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, srv, rt, tid = ctx["client"], ctx["srv"], ctx["rt"], ctx["tid"]
    app = client._octop_app  # type: ignore[attr-defined]
    threads_before = _count(srv, "threads", "agent_id = ?", (rt,))

    hostile = [
        {"thread_id": "thr_does_not_exist"},
        {"thread_id": tid, "mcp_servers": ["evil"]},
    ]
    for extra in hostile:
        async with ws_connect(app, chat_ws_path(rt, ctx["owner_auth"])) as ws:
            await ws.send_json(
                {
                    "type": "user_turn",
                    "text": "hi",
                    "messages": [{"role": "user", "content": "hi"}],
                    **extra,
                }
            )
            frames = await ws.drain_turn()
            tail = await ws.receive_json()
        assert frames[0]["type"] == "error", frames
        assert tail["type"] == "done"

    assert _count(srv, "threads", "agent_id = ?", (rt,)) == threads_before


async def test_chat_ws_owner_turn_on_bound_thread_is_not_gate_refused(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, rt, tid = ctx["client"], ctx["rt"], ctx["tid"]
    app = client._octop_app  # type: ignore[attr-defined]

    async with ws_connect(app, chat_ws_path(rt, ctx["owner_auth"])) as ws:
        await ws.send_json(
            {
                "type": "user_turn",
                "text": "hello",
                "thread_id": tid,
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        frames = await ws.drain_turn()

    # The allowed path must never trip a B4 refusal, whatever the harness
    # itself does with the fake runtime afterwards.
    blob = json.dumps(frames, ensure_ascii=False)
    for needle in ("internal project-task", "bound thread id", "not manageable"):
        assert needle not in blob, frames


# ---------------------------------------------------------------------------
# Terminal surface
# ---------------------------------------------------------------------------


async def test_terminal_surface_denied_for_everyone(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _files_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]
    app = client._octop_app  # type: ignore[attr-defined]

    r = await client.get(f"/api/agents/{rt}/terminal/context", headers=ctx["owner_auth"])
    _forbidden(r)
    r = await client.get(f"/api/agents/{rt}/terminal/context", headers=ctx["admin_auth"])
    _forbidden(r)

    # The owner is a real terminal-capable user, so the WS refusal below can
    # only come from the internal-runtime gate — never a missing permission.
    await _grant_terminal(ctx["srv"], MEMBER)
    spawn_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _record_spawn(*args: Any, **kwargs: Any) -> Any:
        spawn_calls.append((args, kwargs))
        raise RuntimeError("terminal PTY must never spawn for an internal runtime")

    monkeypatch.setattr(terminal_module, "_spawn_pty_session", _record_spawn)

    with pytest.raises(WebSocketDisconnect) as excinfo:
        async with ws_connect(
            app, f"/api/agents/{rt}/terminal/ws?token={ws_token(ctx['owner_auth'])}"
        ):
            pass
    assert excinfo.value.code == 4003
    assert excinfo.value.reason == "internal runtime"
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# Ordinary agents keep working through the same middleware
# ---------------------------------------------------------------------------


async def test_ordinary_agent_surface_unaffected(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    ordinary = await create_agent(client, ctx["auth"]["member"], name="ptf-ordinary-b4")

    r = await client.get(f"/api/agents/{ordinary}", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/agents/{ordinary}/threads", headers=ctx["auth"]["member"])
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]
    r = await client.get(f"/api/agents/{ordinary}/threads", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    assert tid in [t["thread_id"] for t in r.json()]
    # Like-for-like permissions: the same terminal-capable member succeeds on
    # an ordinary agent, while the internal runtime is refused above.
    await _grant_terminal(ctx["srv"], MEMBER)
    r = await client.get(f"/api/agents/{ordinary}/terminal/context", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
