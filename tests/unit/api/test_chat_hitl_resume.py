"""Dashboard HITL resume SSE must persist follow-up approvals in the pending store."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from octop.api.routers.chat import routes as routes_mod
from octop.api.routers.chat.models import HitlResumeBody, HitlSessionPolicyBody
from octop.api.routers.chat.routes import iter_dashboard_hitl_resume_sse
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator


def _resume_request() -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []},
        receive=AsyncMock(return_value={"type": "http.request", "body": b"", "more_body": False}),
    )


def _resume_server(
    *,
    agent_id: str = "agent-1",
    agent_owner: int = 1,
    thread_owner: int = 1,
    thread_agent_id: str | None = None,
    thread_exists: bool = True,
) -> tuple[MagicMock, HitlChannelCoordinator, MagicMock]:
    server = MagicMock()
    server.app_runtime.agent_registry.get_row.return_value = SimpleNamespace(
        user_id=agent_owner, is_shared=1
    )
    thread_row = (
        SimpleNamespace(
            thread_id="thr-1",
            agent_id=thread_agent_id or agent_id,
            user_id=thread_owner,
            session_key=f"{agent_id}:dashboard:{thread_owner}:dm",
            channel_type="dashboard",
        )
        if thread_exists
        else None
    )
    server.app_runtime.gateway.thread_registry.get_thread.return_value = thread_row
    coordinator = HitlChannelCoordinator()
    processor = MagicMock()
    processor.hitl_coordinator = coordinator
    server.app_runtime.gateway.processor = processor
    return server, coordinator, processor


def _spy_pending_lookup(coordinator: HitlChannelCoordinator) -> MagicMock:
    spy = MagicMock(
        name="resolve_pending_for_thread",
        side_effect=AssertionError("pending lookup must not run before ownership check"),
    )
    coordinator._store.resolve_pending_for_thread = spy  # type: ignore[method-assign]
    return spy


def _spy_policy_write(coordinator: HitlChannelCoordinator) -> MagicMock:
    spy = MagicMock(
        name="session_policies.set",
        side_effect=AssertionError("policy write must not run before ownership check"),
    )
    coordinator.session_policies.set = spy  # type: ignore[method-assign]
    return spy


async def _frames(response: Any) -> list[str]:
    return [frame async for frame in response.body_iterator]


def _parse_sse_chunks(raw: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        data_line = next(
            (line[6:] for line in block.split("\n") if line.startswith("data: ")),
            None,
        )
        if data_line is None:
            continue
        chunks.append(json.loads(data_line))
    return chunks


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_registers_followup_hitl_required() -> None:
    async def _resume(*_args: object, **_kwargs: object):
        yield {"type": "token", "content": "ok"}
        yield {
            "type": "hitl_required",
            "request": {
                "action_requests": [
                    {"name": "execute", "args": {"command": "ls -la /private/etc"}},
                ],
                "review_configs": [
                    {"action_name": "execute", "allowed_decisions": ["approve", "reject"]},
                ],
            },
        }

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()
    first = hitl.store.register(
        thread_id="thr-follow",
        agent_id="agent-1",
        user_id=1,
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "execute", "args": {"command": "ls -la /etc"}}],
        review_configs=None,
    )

    frames: list[str] = []
    async for frame in iter_dashboard_hitl_resume_sse(
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="agent-1",
        thread_id="thr-follow",
        user_id=1,
        decisions=[{"type": "approve"}],
        pending=first,
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        locale="zh",
        is_disconnected=AsyncMock(return_value=False),
    ):
        frames.append(frame)

    chunks = _parse_sse_chunks("".join(frames))
    assert any(c.get("type") == "hitl_required" for c in chunks)
    assert any(c.get("type") == "done" for c in chunks)

    followup = hitl.store.resolve_pending_for_thread(
        "thr-follow",
        agent_id="agent-1",
        user_id=1,
    )
    assert followup is not None
    assert followup.pending_id != first.pending_id
    assert followup.action_requests[0]["args"]["command"] == "ls -la /private/etc"
    assert hitl.store.get(first.pending_id) is not None
    assert hitl.store.get(first.pending_id).status == "approved"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_registers_without_prior_pending() -> None:
    async def _resume(*_args: object, **_kwargs: object):
        yield {
            "type": "hitl_required",
            "request": {
                "action_requests": [{"name": "execute", "args": {"command": "echo hi"}}],
            },
        }

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()

    async for _ in iter_dashboard_hitl_resume_sse(
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="agent-1",
        thread_id="thr-orphan",
        user_id=9,
        decisions=[{"type": "approve"}],
        pending=None,
        session_key="sk-orphan",
        channel_type="dashboard",
        locale="en",
        is_disconnected=AsyncMock(return_value=False),
    ):
        pass

    pending = hitl.store.resolve_pending_for_thread(
        "thr-orphan",
        agent_id="agent-1",
        user_id=9,
    )
    assert pending is not None
    assert pending.action_requests[0]["args"]["command"] == "echo hi"


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_finishes_after_client_disconnect() -> None:
    completed = False

    async def _resume(*_args: object, **_kwargs: object):
        nonlocal completed
        yield {"type": "token", "content": "hidden after disconnect"}
        completed = True

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()

    frames = [
        frame
        async for frame in iter_dashboard_hitl_resume_sse(
            processor=processor,
            hitl_coordinator=hitl,
            agent_id="agent-1",
            thread_id="thr-disconnected",
            user_id=9,
            decisions=[{"type": "respond", "message": "answer"}],
            pending=None,
            session_key="sk-disconnected",
            channel_type="dashboard",
            locale="en",
            is_disconnected=AsyncMock(return_value=True),
        )
    ]

    assert completed is True
    assert frames == []


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_marks_pending_resolved_when_stream_errors() -> None:
    async def _resume(*_args: object, **_kwargs: object):
        raise RuntimeError("stream died")
        yield {}  # pragma: no cover

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()
    first = hitl.store.register(
        thread_id="thr-err",
        agent_id="agent-1",
        user_id=1,
        session_key="sk-err",
        channel_type="dashboard",
        action_requests=[{"name": "ask_user_question", "args": {"questions": []}}],
        review_configs=None,
    )

    frames: list[str] = []
    async for frame in iter_dashboard_hitl_resume_sse(
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="agent-1",
        thread_id="thr-err",
        user_id=1,
        decisions=[{"type": "respond", "message": "ok"}],
        pending=first,
        session_key="sk-err",
        channel_type="dashboard",
        locale="zh",
        is_disconnected=AsyncMock(return_value=False),
    ):
        frames.append(frame)

    chunks = _parse_sse_chunks("".join(frames))
    assert any(c.get("type") == "error" for c in chunks)
    resolved = hitl.store.get(first.pending_id)
    assert resolved is not None
    assert resolved.status == "expired"
    assert hitl.store.resolve_pending_for_thread("thr-err", agent_id="agent-1", user_id=1) is None


@pytest.mark.asyncio
async def test_resume_hitl_rejects_shared_agent_non_owner_thread_before_side_effects() -> None:
    """Shared-agent access must not let a user resume another user's paused task."""
    server, coordinator, processor = _resume_server(agent_owner=1, thread_owner=1)
    pending_spy = _spy_pending_lookup(coordinator)
    policy_spy = _spy_policy_write(coordinator)
    stream_spy = MagicMock(
        name="iter_hitl_resume_chunks",
        side_effect=AssertionError("resume stream must not start before ownership check"),
    )
    processor.iter_hitl_resume_chunks = stream_spy

    body = HitlResumeBody(
        thread_id="thr-1",
        decisions=[{"type": "approve"}],
        hitl_policy=HitlSessionPolicyBody(mode="allow_all"),
    )
    user = SimpleNamespace(id=2, is_admin=False)

    with pytest.raises(OctopError) as excinfo:
        await routes_mod.resume_hitl("agent-1", body, _resume_request(), user, server)

    assert excinfo.value.code is ErrorCode.FORBIDDEN
    pending_spy.assert_not_called()
    policy_spy.assert_not_called()
    stream_spy.assert_not_called()


@pytest.mark.asyncio
async def test_resume_hitl_rejects_shared_agent_when_pending_belongs_to_owner() -> None:
    """An attacker's decision must never reach the owner's pending approval."""
    server, coordinator, processor = _resume_server(agent_owner=1, thread_owner=1)
    pending = coordinator.store.register(
        thread_id="thr-1",
        agent_id="agent-1",
        user_id=1,
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "execute", "args": {"command": "rm -rf /tmp/x"}}],
        review_configs=None,
    )
    pending_spy = _spy_pending_lookup(coordinator)
    policy_spy = _spy_policy_write(coordinator)

    body = HitlResumeBody(thread_id="thr-1", decisions=[{"type": "approve"}])
    user = SimpleNamespace(id=2, is_admin=False)

    with pytest.raises(OctopError) as excinfo:
        await routes_mod.resume_hitl("agent-1", body, _resume_request(), user, server)

    assert excinfo.value.code is ErrorCode.FORBIDDEN
    pending_spy.assert_not_called()
    policy_spy.assert_not_called()
    intact = coordinator.store.get(pending.pending_id)
    assert intact is not None
    assert intact.status == "pending"


@pytest.mark.asyncio
async def test_resume_hitl_rejects_thread_of_another_agent() -> None:
    server, coordinator, processor = _resume_server(
        thread_owner=1,
        thread_agent_id="agent-2",
    )
    pending_spy = _spy_pending_lookup(coordinator)
    policy_spy = _spy_policy_write(coordinator)

    body = HitlResumeBody(
        thread_id="thr-1",
        decisions=[{"type": "approve"}],
        hitl_policy=HitlSessionPolicyBody(mode="allow_all"),
    )
    user = SimpleNamespace(id=1, is_admin=False)

    with pytest.raises(OctopError) as excinfo:
        await routes_mod.resume_hitl("agent-1", body, _resume_request(), user, server)

    assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
    pending_spy.assert_not_called()
    policy_spy.assert_not_called()


@pytest.mark.asyncio
async def test_resume_hitl_rejects_unknown_thread() -> None:
    server, coordinator, processor = _resume_server(thread_exists=False)
    pending_spy = _spy_pending_lookup(coordinator)
    policy_spy = _spy_policy_write(coordinator)

    body = HitlResumeBody(
        thread_id="thr-1",
        decisions=[{"type": "approve"}],
        hitl_policy=HitlSessionPolicyBody(mode="allow_all"),
    )
    user = SimpleNamespace(id=1, is_admin=False)

    with pytest.raises(OctopError) as excinfo:
        await routes_mod.resume_hitl("agent-1", body, _resume_request(), user, server)

    assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
    pending_spy.assert_not_called()
    policy_spy.assert_not_called()


@pytest.mark.asyncio
async def test_resume_hitl_owner_with_pending_keeps_stream_and_policy() -> None:
    server, coordinator, processor = _resume_server(agent_owner=1, thread_owner=1)
    pending = coordinator.store.register(
        thread_id="thr-1",
        agent_id="agent-1",
        user_id=1,
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "execute", "args": {"command": "echo hi"}}],
        review_configs=None,
    )
    pending_spy = MagicMock(wraps=coordinator.store.resolve_pending_for_thread)
    coordinator._store.resolve_pending_for_thread = pending_spy  # type: ignore[method-assign]

    async def _resume_chunks(**_kwargs: object):
        yield {"type": "token", "content": "ok"}

    processor.iter_hitl_resume_chunks = _resume_chunks
    body = HitlResumeBody(
        thread_id="thr-1",
        decisions=[{"type": "approve"}],
        hitl_policy=HitlSessionPolicyBody(mode="allow_all"),
    )
    user = SimpleNamespace(id=1, is_admin=False)

    response = await routes_mod.resume_hitl("agent-1", body, _resume_request(), user, server)

    chunks = _parse_sse_chunks("".join(await _frames(response)))
    assert any(c.get("type") == "done" for c in chunks)
    pending_spy.assert_called_once_with("thr-1", agent_id="agent-1", user_id=1)
    assert coordinator.session_policies.get("thr-1").mode == "allow_all"
    assert coordinator.store.get(pending.pending_id).status == "approved"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_resume_hitl_owner_without_pending_keeps_existing_path() -> None:
    server, coordinator, processor = _resume_server(agent_owner=1, thread_owner=1)
    streamed: list[str] = []

    async def _resume_chunks(**_kwargs: object):
        streamed.append("started")
        yield {"type": "token", "content": "ok"}

    processor.iter_hitl_resume_chunks = _resume_chunks
    body = HitlResumeBody(thread_id="thr-1", decisions=[{"type": "approve"}])
    user = SimpleNamespace(id=1, is_admin=False)

    response = await routes_mod.resume_hitl("agent-1", body, _resume_request(), user, server)

    chunks = _parse_sse_chunks("".join(await _frames(response)))
    assert streamed == ["started"]
    assert any(c.get("type") == "done" for c in chunks)
