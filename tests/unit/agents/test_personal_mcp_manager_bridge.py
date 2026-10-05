"""Server-owned personal MCP preparation and catalog plumbing, synthetic only."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool

from octop.infra.agents.manager import AgentManager
from octop.infra.connectors.mcp_actor_scope import (
    PersonalMCPReceipt,
    current_mcp_actor_scope,
    trusted_mcp_actor_scope,
)
from octop.infra.errors import ErrorCode, OctopError


def manager() -> AgentManager:
    instance = object.__new__(AgentManager)
    instance._repos = SimpleNamespace(
        user_repo=SimpleNamespace(get=lambda uid: SimpleNamespace(id=uid, role="user", disabled=0)),
        thread_repo=SimpleNamespace(
            get=lambda _tid: SimpleNamespace(
                agent_id="qa-agent",
                user_id=101,
                session_key="qa-session",
            )
        ),
    )
    instance.get_row = MagicMock(
        return_value=SimpleNamespace(
            agent_id="qa-agent",
            user_id=None,
            config_json="{}",
            enabled=1,
            runtime_kind="ordinary",
        )
    )
    instance.get_agent = MagicMock(
        return_value=SimpleNamespace(
            config=SimpleNamespace(
                deferred_tools=frozenset(), mcp_server_configs={"qa_personal": {}}
            ),
            _mcp_tool_name_set=frozenset({"qa_personal_other_actor_old_tool"}),
            append_mcp_tools=MagicMock(),
        )
    )
    instance._connector_uid_for = MagicMock(return_value=101)
    instance._connector_svc = SimpleNamespace(
        custom_harness_configs=lambda _uid: {
            "qa_personal": {"transport": "stdio", "command": "qa-synthetic-only"},
        }
    )
    instance.reload_connectors = AsyncMock()
    instance._mcp_tool_cache = {}
    instance._mcp_tool_cache_locks = {}
    instance._mcp_tool_cache_guard = asyncio.Lock()

    async def run(query: str) -> str:
        return "synthetic:" + query

    tool = StructuredTool.from_function(
        name="qa_personal_lookup",
        description="synthetic current actor descriptor",
        coroutine=run,
    )
    instance._get_or_load_mcp_tools = AsyncMock(return_value=[tool])
    instance._personal_mcp_connection_spec = AsyncMock(side_effect=lambda spec: spec)
    return instance


def make_actor(instance: AgentManager):
    return instance.personal_mcp_actor(
        "qa-agent",
        user_id=101,
        thread_id="qa-thread",
        session_key="qa-session",
        source="dashboard",
        servers=["qa_personal"],
        locale="zh",
    )


@pytest.mark.asyncio
async def test_prepare_checks_current_custom_config_without_global_append() -> None:
    instance = manager()
    assert await instance.prepare_chat_mcp("qa-agent", ["qa_personal"], connector_user_id=101) == []
    instance._get_or_load_mcp_tools.assert_awaited_once()
    instance.get_agent().append_mcp_tools.assert_not_called()
    assert instance.get_agent().config.mcp_server_configs == {"qa_personal": {}}


def test_actor_uses_authenticated_server_user_and_thread_binding() -> None:
    instance = manager()
    current = make_actor(instance)
    assert current.user_id == 101
    assert current.allowed_personal_servers == frozenset({"qa_personal"})
    assert current.thread_id == "qa-thread" and current.session_key == "qa-session"


def test_actor_rejects_stale_server_thread_ownership() -> None:
    instance = manager()
    instance._repos.thread_repo.get = lambda _tid: SimpleNamespace(
        agent_id="qa-agent",
        user_id=202,
        session_key="qa-session",
    )
    with pytest.raises(OctopError) as error:
        make_actor(instance)
    assert error.value.code is ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_catalog_is_current_actor_eager_and_not_global() -> None:
    instance = manager()
    current = make_actor(instance)
    tool = (await instance._get_or_load_mcp_tools(101, "qa_personal", {}))[0]
    tool.extras = {"defer_loading": True, "synthetic_other_extra": "retained"}
    records = await instance._resolve_personal_mcp_descriptors(current)
    assert len(records) == 1 and records[0].actor is current
    assert records[0].tool.extras == {"synthetic_other_extra": "retained"}
    assert tool.extras["defer_loading"] is True
    instance.get_agent().append_mcp_tools.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_personal_deferral_rejected_without_changing_ordinary_search() -> None:
    instance = manager()
    current = make_actor(instance)
    configured = frozenset({"qa_personal_lookup", "ordinary_builtin"})
    instance.get_agent().config.deferred_tools = configured
    with pytest.raises(OctopError) as error:
        await instance._resolve_personal_mcp_descriptors(current)
    assert error.value.code is ErrorCode.FORBIDDEN
    assert instance.get_agent().config.deferred_tools is configured
    instance.get_agent().append_mcp_tools.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_revalidates_disabled_user_before_tool_loading() -> None:
    instance = manager()
    current = make_actor(instance)
    instance._repos.user_repo.get = lambda uid: SimpleNamespace(id=uid, role="user", disabled=1)
    with pytest.raises(OctopError) as error:
        await instance._resolve_personal_mcp_descriptors(current)
    assert error.value.code is ErrorCode.FORBIDDEN
    instance._get_or_load_mcp_tools.assert_not_awaited()


def test_ordinary_nested_invocation_masks_parent_actor_without_closing_it() -> None:
    from octop.infra.connectors.mcp_actor_scope import (
        current_mcp_actor_scope,
        trusted_mcp_actor_scope,
    )

    instance = manager()
    with trusted_mcp_actor_scope(make_actor(instance)) as parent:
        with instance._personal_mcp_invocation_scope("ordinary-peer", "peer-thread", None):
            assert current_mcp_actor_scope() is None
            assert parent.active
        assert current_mcp_actor_scope() is parent


def invocation_manager() -> AgentManager:
    instance = manager()
    instance._thread_execution_locks = {}
    instance._history_backfills = {}
    instance._invocation_waiters = {}
    instance._active_invocations = {}
    instance._apply_pending_bootstrap_graph_refresh = MagicMock()
    instance._prepare_stream_request = lambda _aid, request: request
    return instance


@pytest.mark.asyncio
async def test_stream_and_call_scope_wrap_actual_harness_invocation() -> None:
    instance = invocation_manager()
    actor = make_actor(instance)
    scopes = []

    async def stream(_aid, _request):
        scope = current_mcp_actor_scope()
        assert scope is not None and scope.actor is actor
        assert instance.is_agent_active("qa-agent")
        assert instance._thread_execution_lock("qa-agent", "qa-thread").locked()
        scopes.append(scope)
        yield {"type": "token", "content": "synthetic"}

    async def call(_aid, _request):
        scope = current_mcp_actor_scope()
        assert scope is not None and scope.actor is actor
        scopes.append(scope)
        return {"result": "synthetic"}

    instance._harness_manager = SimpleNamespace(stream=stream, call=call)
    request = {"thread_id": "qa-thread"}
    assert [chunk async for chunk in instance.stream("qa-agent", request, trusted_actor=actor)]
    assert await instance.call("qa-agent", request, trusted_actor=actor) == {"result": "synthetic"}
    assert current_mcp_actor_scope() is None
    assert all(not scope.active for scope in scopes)
    assert not instance.is_agent_active("qa-agent")


@pytest.mark.asyncio
async def test_resume_restores_original_receipt_only_inside_manager_invocation() -> None:
    instance = invocation_manager()
    actor = make_actor(instance)
    receipt = PersonalMCPReceipt(actor=actor, descriptors=())
    seen = []

    async def resume(_aid, _tid, decisions):
        assert decisions == [{"type": "approve"}]
        scope = current_mcp_actor_scope()
        assert scope is not None and scope.receipt is receipt
        seen.append(scope)
        yield {"type": "done"}

    instance._harness_manager = SimpleNamespace(resume_hitl=resume)
    chunks = [
        chunk
        async for chunk in instance.resume_hitl(
            "qa-agent",
            "qa-thread",
            [{"type": "approve"}],
            trusted_actor=actor,
            personal_mcp_receipt=receipt,
            personal_mcp_receipt_expires_at=time.time() + 60,
        )
    ]
    assert chunks == [{"type": "done"}]
    assert not seen[0].active and current_mcp_actor_scope() is None


@pytest.mark.asyncio
async def test_resume_rechecks_expiry_after_waiting_for_thread_lock() -> None:
    instance = invocation_manager()
    actor = make_actor(instance)
    receipt = PersonalMCPReceipt(actor=actor, descriptors=())
    now = [100.0]
    resume = MagicMock()
    instance._harness_manager = SimpleNamespace(resume_hitl=resume)
    lock = instance._thread_execution_lock("qa-agent", "qa-thread")
    await lock.acquire()

    async def consume():
        return [
            chunk
            async for chunk in instance.resume_hitl(
                "qa-agent",
                "qa-thread",
                [{"type": "approve"}],
                trusted_actor=actor,
                personal_mcp_receipt=receipt,
                personal_mcp_receipt_expires_at=101.0,
            )
        ]

    from unittest.mock import patch

    with patch("octop.infra.agents.manager.time.time", side_effect=lambda: now[0]):
        pending = asyncio.create_task(consume())
        await asyncio.sleep(0)
        assert not pending.done()
        now[0] = 102.0
        lock.release()
        with pytest.raises(OctopError) as error:
            await pending
    assert error.value.code is ErrorCode.FORBIDDEN
    resume.assert_not_called()
    assert not instance.is_agent_active("qa-agent")
    assert current_mcp_actor_scope() is None


@pytest.mark.asyncio
async def test_ordinary_call_keeps_no_actor_and_restores_parent_scope() -> None:
    instance = invocation_manager()

    async def call(_aid, _request):
        assert current_mcp_actor_scope() is None
        return {"result": "ordinary"}

    instance._harness_manager = SimpleNamespace(call=call)
    with trusted_mcp_actor_scope(make_actor(instance)) as parent:
        assert await instance.call("qa-agent", {}) == {"result": "ordinary"}
        assert current_mcp_actor_scope() is parent and parent.active
