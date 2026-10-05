"""Normal synthetic caller bridges preserve server-owned personal MCP receipts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from harness_gateway.models import ChannelSubject, InboundMessage, TextContent

from octop.api.routers.chat.routes import iter_dashboard_hitl_resume_sse
from octop.infra.connectors.mcp_actor_scope import (
    PersonalMCPDescriptorReceipt,
    PersonalMCPReceipt,
    TrustedMCPActor,
    trusted_mcp_actor_scope,
)
from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator, HitlStreamContext
from octop.infra.gateway.hitl.store import HitlPendingRecord, HitlPendingStore
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.process.stream_project import project_resume_stream, project_stream
from octop.infra.gateway.slash.ctx import SlashCtx
from octop.infra.gateway.slash.dispatcher import SlashDispatcher
from octop.infra.gateway.slash.parser import parse_slash


def _receipt(channel: str = "dashboard") -> PersonalMCPReceipt:
    actor = TrustedMCPActor(
        user_id=101,
        agent_id="qa-bridge-agent",
        thread_id="qa-bridge-thread",
        session_key=f"qa-bridge-agent:{channel}:101:dm",
        source=channel,
        allowed_personal_servers=frozenset({"qa-personal"}),
        locale="zh",
    )
    return PersonalMCPReceipt(
        actor,
        (PersonalMCPDescriptorReceipt("qa_lookup", "qa-personal", "qa-config", "qa-schema"),),
    )


def _pending(
    hitl: HitlChannelCoordinator,
    receipt: PersonalMCPReceipt,
    *,
    actions: list[dict[str, Any]] | None = None,
) -> HitlPendingRecord:
    actor = receipt.actor
    return hitl.store.register(
        thread_id=actor.thread_id,
        agent_id=actor.agent_id,
        user_id=actor.user_id,
        session_key=actor.session_key,
        channel_type=actor.source,
        action_requests=actions or [{"name": "qa_lookup", "args": {"query": "synthetic"}}],
        review_configs=None,
        personal_mcp_receipt=receipt,
    )


def _processor(manager: Any, hitl: HitlChannelCoordinator) -> GlobalProcessor:
    return GlobalProcessor(
        agent_manager=manager,
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        hitl=hitl,
    )


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.get_agent.side_effect = RuntimeError("synthetic manager has no workspace")
    manager.get_row.return_value = None
    return manager


def _assert_binding(
    captured: dict[str, Any],
    receipt: PersonalMCPReceipt,
    pending: HitlPendingRecord,
    hitl: HitlChannelCoordinator,
) -> None:
    assert captured["trusted_actor"] is receipt.actor
    assert captured["personal_mcp_receipt"] is receipt
    assert captured["personal_mcp_receipt_expires_at"] == (
        pending.created_at + hitl.store.ttl_seconds
    )


async def _dashboard_frames(
    processor: Any,
    hitl: HitlChannelCoordinator,
    receipt: PersonalMCPReceipt,
    pending: HitlPendingRecord | None,
) -> list[dict[str, Any]]:
    actor = receipt.actor
    frames = [
        frame
        async for frame in iter_dashboard_hitl_resume_sse(
            processor=processor,
            hitl_coordinator=hitl,
            agent_id=actor.agent_id,
            thread_id=actor.thread_id,
            user_id=actor.user_id,
            decisions=[{"type": "approve"}],
            pending=pending,
            session_key=actor.session_key,
            channel_type=actor.source,
            locale=actor.locale,
            is_disconnected=AsyncMock(return_value=False),
        )
    ]
    return [
        json.loads(line[6:])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_dashboard_real_helper_and_processor_forward_original_receipt_and_expiry() -> None:
    receipt = _receipt()
    hitl = HitlChannelCoordinator(HitlPendingStore(ttl_seconds=75))
    pending = _pending(hitl, receipt)
    captured: dict[str, Any] = {}
    manager = _manager()

    async def resume(
        agent_id: str, thread_id: str, decisions: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        assert (agent_id, thread_id) == (receipt.actor.agent_id, receipt.actor.thread_id)
        assert decisions == [{"type": "approve"}]
        captured.update(kwargs)
        yield {"type": "token", "content": "synthetic-resumed"}

    manager.resume_hitl = resume
    chunks = await _dashboard_frames(_processor(manager, hitl), hitl, receipt, pending)
    _assert_binding(captured, receipt, pending, hitl)
    assert {"type": "token", "content": "synthetic-resumed"} in chunks
    assert chunks[-1] == {"type": "done"}
    assert pending.status == "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["dashboard", "telegram"])
async def test_shared_resume_projection_forwards_receipt_and_store_expiry(channel: str) -> None:
    receipt = _receipt(channel)
    hitl = HitlChannelCoordinator(HitlPendingStore(ttl_seconds=125))
    pending = _pending(hitl, receipt)
    captured: dict[str, Any] = {}
    manager = _manager()

    async def resume(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        assert args[:2] == (receipt.actor.agent_id, receipt.actor.thread_id)
        captured.update(kwargs)
        yield {"type": "token", "content": "synthetic-shared-projection"}

    manager.resume_hitl = resume
    events = [
        event
        async for event in project_resume_stream(
            manager,
            receipt.actor.agent_id,
            receipt.actor.thread_id,
            [{"type": "approve"}],
            pending=pending,
            hitl_coordinator=hitl,
            hitl_ctx=HitlStreamContext(
                receipt.actor.thread_id,
                receipt.actor.agent_id,
                receipt.actor.user_id,
                receipt.actor.session_key,
                receipt.actor.source,
            ),
        )
    ]
    _assert_binding(captured, receipt, pending, hitl)
    assert events


@pytest.mark.asyncio
async def test_initial_projection_passes_explicit_server_actor_separately_from_request() -> None:
    receipt = _receipt("telegram")
    manager = _manager()
    captured: dict[str, Any] = {}
    request = {"messages": [{"role": "user", "content": "synthetic"}]}

    async def stream(agent_id: str, actual: dict[str, Any], **kwargs: Any):
        assert agent_id == receipt.actor.agent_id
        assert actual is request
        captured.update(kwargs)
        yield {"type": "token", "content": "synthetic-initial"}

    manager.stream = stream
    events = [
        event
        async for event in project_stream(
            manager,
            receipt.actor.agent_id,
            request,
            trusted_actor=receipt.actor,
        )
    ]
    assert captured == {"trusted_actor": receipt.actor}
    assert set(request) == {"messages"}
    assert events


@pytest.mark.asyncio
async def test_ordinary_projection_keeps_old_stream_and_resume_manager_signatures() -> None:
    manager = _manager()
    calls: list[str] = []

    async def stream(agent_id: str, request: dict[str, Any]):
        calls.append("stream")
        yield {"type": "token", "content": "ordinary"}

    async def resume(agent_id: str, thread_id: str, decisions: list[dict[str, Any]]):
        calls.append("resume")
        yield {"type": "token", "content": "ordinary"}

    manager.stream, manager.resume_hitl = stream, resume
    assert [event async for event in project_stream(manager, "qa-agent", {"messages": []})]
    assert [
        event
        async for event in project_resume_stream(
            manager,
            "qa-agent",
            "qa-thread",
            [{"type": "approve"}],
        )
    ]
    assert calls == ["stream", "resume"]


@pytest.mark.asyncio
async def test_ordinary_dashboard_no_pending_keeps_old_manager_resume_signature() -> None:
    receipt = _receipt()
    hitl, manager = HitlChannelCoordinator(), _manager()
    calls: list[str] = []

    async def resume(agent_id: str, thread_id: str, decisions: list[dict[str, Any]]):
        calls.append(thread_id)
        yield {"type": "token", "content": "ordinary-dashboard"}

    manager.resume_hitl = resume
    chunks = await _dashboard_frames(_processor(manager, hitl), hitl, receipt, None)
    assert calls == [receipt.actor.thread_id]
    assert chunks[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_dashboard_nested_interrupt_binds_latest_server_scope_receipt_without_client_receipt() -> (
    None
):
    receipt = _receipt()
    hitl = HitlChannelCoordinator()
    first = _pending(hitl, receipt)
    latest = replace(
        receipt,
        descriptors=(
            replace(
                receipt.descriptors[0],
                descriptor_fingerprint="qa-next-model-schema",
            ),
        ),
    )
    manager = _manager()

    async def resume(*args: Any, **kwargs: Any):
        _assert_binding(kwargs, receipt, first, hitl)
        with trusted_mcp_actor_scope(receipt.actor, receipt=receipt) as scope:
            scope.record_model_receipt(latest)
            yield {
                "type": "hitl_required",
                "request": {
                    "action_requests": [{"name": "qa_lookup", "args": {"query": "next"}}],
                },
            }

    manager.resume_hitl = resume
    chunks = await _dashboard_frames(_processor(manager, hitl), hitl, receipt, first)
    followup = hitl.store.resolve_pending_for_thread(
        receipt.actor.thread_id,
        agent_id=receipt.actor.agent_id,
        user_id=receipt.actor.user_id,
    )
    assert followup is not None and followup.pending_id != first.pending_id
    assert followup.personal_mcp_receipt is latest
    assert first.personal_mcp_receipt is receipt
    assert first.status == "approved"
    serialized = json.dumps(chunks)
    assert "personal_mcp_receipt" not in serialized
    assert "connection_fingerprint" not in serialized
    assert "qa-next-model-schema" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["answer", "slash"])
async def test_im_normal_answer_and_slash_pass_pending_through_real_projection(mode: str) -> None:
    receipt = _receipt("telegram")
    hitl = HitlChannelCoordinator(HitlPendingStore(ttl_seconds=210))
    actions = (
        [
            {
                "name": "ask_user_question",
                "args": {
                    "questions": [
                        {"question": "Synthetic choice?", "options": ["one", "two"]},
                    ]
                },
            }
        ]
        if mode == "answer"
        else None
    )
    pending = _pending(hitl, receipt, actions=actions)
    captured: dict[str, Any] = {}
    manager = _manager()

    async def resume(*args: Any, **kwargs: Any):
        captured.update(kwargs)
        yield {"type": "token", "content": "synthetic-im-resumed"}

    manager.resume_hitl = resume
    if mode == "answer":
        events = [
            event
            async for event in hitl.iter_answer_resolution(
                pending,
                "one",
                agent_manager=manager,
                locale="zh",
            )
        ]
    else:
        cmd = parse_slash(f"/approve {pending.pending_id}")
        assert cmd is not None
        ctx = SlashCtx(
            receipt.actor.agent_id,
            receipt.actor.user_id,
            receipt.actor.source,
            receipt.actor.session_key,
            MagicMock(),
        )
        events = [
            event
            async for event in hitl.iter_slash_resolution(
                cmd,
                ctx,
                agent_manager=manager,
                locale="zh",
            )
        ]
    _assert_binding(captured, receipt, pending, hitl)
    assert events


@pytest.mark.asyncio
async def test_initial_projection_registers_receipt_while_server_model_scope_is_active() -> None:
    receipt = _receipt("telegram")
    hitl, manager = HitlChannelCoordinator(), _manager()
    actor = receipt.actor

    async def stream(agent_id: str, request: dict[str, Any], *, trusted_actor: TrustedMCPActor):
        assert trusted_actor is actor
        with trusted_mcp_actor_scope(actor, receipt=receipt):
            yield {
                "type": "hitl_required",
                "request": {
                    "action_requests": [{"name": "qa_lookup", "args": {"query": "initial"}}],
                },
            }

    manager.stream = stream
    events = [
        event
        async for event in project_stream(
            manager,
            actor.agent_id,
            {"messages": []},
            trusted_actor=actor,
            hitl_coordinator=hitl,
            hitl_ctx=HitlStreamContext(
                actor.thread_id,
                actor.agent_id,
                actor.user_id,
                actor.session_key,
                actor.source,
            ),
        )
    ]
    pending = hitl.store.resolve_pending_for_thread(
        actor.thread_id,
        agent_id=actor.agent_id,
        user_id=actor.user_id,
    )
    assert events and pending is not None
    assert pending.personal_mcp_receipt is receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["expired", "consumed"])
async def test_dashboard_bounded_claim_rejects_expired_or_consumed_synthetic_record(
    case: str,
) -> None:
    receipt = _receipt()
    hitl, manager = HitlChannelCoordinator(HitlPendingStore(ttl_seconds=60)), _manager()
    pending = _pending(hitl, receipt)
    calls: list[str] = []

    async def resume(*args: Any, **kwargs: Any):
        calls.append("resumed")
        yield {"type": "token", "content": "synthetic-claim"}

    manager.resume_hitl = resume
    processor = _processor(manager, hitl)
    if case == "consumed":
        first = await _dashboard_frames(processor, hitl, receipt, pending)
        assert first[-1] == {"type": "done"}
        assert calls == ["resumed"]
    else:
        pending.created_at -= hitl.store.ttl_seconds + 1
    before = list(calls)
    chunks = await _dashboard_frames(processor, hitl, receipt, pending)
    assert calls == before
    assert any(chunk.get("error_code") == "FORBIDDEN" for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["dashboard", "telegram"])
async def test_initial_processor_builds_actor_from_resolved_message_context(channel: str) -> None:
    receipt = _receipt(channel)
    actor = receipt.actor
    hitl, manager = HitlChannelCoordinator(), _manager()
    manager.merge_turn_mcp_servers.return_value = ["qa-personal"]
    manager.default_mcp_servers.return_value = []
    manager.default_knowledge_base_ids.return_value = []
    manager.prepare_chat_mcp = AsyncMock(return_value=[])
    manager.providers.is_model_ref_usable.return_value = False
    manager.providers.resolve_explicit_default_model.return_value = None
    manager.providers.resolve_model_for_multimodal_turn.side_effect = lambda ref, **kwargs: ref
    manager.get_thread_model.return_value = None
    manager.personal_mcp_actor.return_value = actor
    processor = _processor(manager, hitl)
    processor._user_repo.get.return_value = SimpleNamespace(locale="zh", preferences_json=None)
    processor._agent_repo.get.return_value = SimpleNamespace(
        user_id=101,
        default_model=None,
        runtime_kind=None,
        config_json="{}",
        system_prompt=None,
        kind="normal",
    )
    processor._thread_registry.get_or_create_by_key = AsyncMock(return_value=actor.thread_id)
    captured: dict[str, Any] = {}

    async def stream(agent_id: str, request: dict[str, Any], **kwargs: Any):
        captured.update(kwargs)
        assert "trusted_actor" not in request
        assert "personal_mcp_receipt" not in request
        yield {"type": "token", "content": "synthetic-initial-processor"}

    manager.stream = stream
    metadata = (
        {"thread_id": actor.thread_id, "session_key": actor.session_key, "locale": "zh"}
        if channel == "dashboard"
        else {"locale": "zh"}
    )
    message = InboundMessage(
        channel_id="qa-channel",
        channel_type=channel,
        tenant_id=actor.agent_id,
        channel_subject=ChannelSubject(subject_id="101"),
        content=[TextContent(text="synthetic")],
        metadata=metadata,
    )
    if channel == "dashboard":
        output = [chunk async for chunk in processor.iter_turn_chunks(message)]
    else:
        output = [event async for event in processor(message)]
    assert output
    manager.personal_mcp_actor.assert_called_once_with(
        actor.agent_id,
        user_id=101,
        thread_id=actor.thread_id,
        session_key=actor.session_key,
        source=channel,
        servers=["qa-personal"],
        locale="zh",
    )
    assert captured == {"trusted_actor": actor}
