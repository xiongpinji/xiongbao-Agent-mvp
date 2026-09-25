"""Project task instructions enter the harness only through server state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from harness_gateway.models import ChannelSubject, InboundMessage, TextContent

from octop.infra.agents.middleware.project_instructions import CONFIG_KEY
from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.slash.dispatcher import SlashDispatcher


def _processor(snapshot: str | None) -> tuple[GlobalProcessor, MagicMock]:
    agent_manager = MagicMock()
    agent_manager.merge_turn_mcp_servers.return_value = None
    agent_manager.get_row.return_value = None
    agent_manager.default_mcp_servers.return_value = []
    agent_manager.default_knowledge_base_ids.return_value = []
    agent_manager.providers.is_model_ref_usable.return_value = False
    agent_manager.providers.resolve_explicit_default_model.return_value = None
    agent_manager.providers.resolve_model_for_multimodal_turn.side_effect = lambda ref, **_k: ref
    agent_manager.get_thread_model.return_value = None
    thread_registry = MagicMock()
    thread_registry.get_thread.return_value = SimpleNamespace(
        conversation_mode="craft", pending_plan_path=None, model_ref=None
    )
    project_tasks = MagicMock()
    project_tasks.active_instructions_for_thread.return_value = snapshot
    processor = GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=thread_registry,
        audit_repo=MagicMock(),
        agent_repo=MagicMock(get=MagicMock(return_value=MagicMock(default_model=None))),
        user_repo=MagicMock(
            get=MagicMock(return_value=SimpleNamespace(locale="zh", preferences_json=None))
        ),
        connector_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        project_task_repo=project_tasks,
    )
    return processor, project_tasks


@pytest.mark.asyncio
async def test_dashboard_turn_uses_server_snapshot_and_drops_client_collision() -> None:
    processor, project_tasks = _processor("Cite the frozen project brief.")
    msg = InboundMessage(
        channel_id="ws",
        channel_type="dashboard",
        tenant_id="agent-1",
        channel_subject=ChannelSubject(subject_id="1"),
        content=[TextContent(text="hello")],
        metadata={
            "configurable": {CONFIG_KEY: "client-forged instructions"},
            CONFIG_KEY: "another client-forged value",
            "project_id": "foreign-project",
        },
    )

    request = await processor._build_dashboard_request(
        msg,
        agent_id="agent-1",
        user_id=1,
        session_key="sk",
        thread_id="thr",
        meta=msg.metadata,
    )

    assert request["configurable"][CONFIG_KEY] == "Cite the frozen project brief."
    project_tasks.active_instructions_for_thread.assert_called_with(
        thread_id="thr", owner_user_id=1, agent_id="agent-1"
    )

    # A detach or member removal makes the next turn lose the context even if
    # the client keeps sending the same forged metadata.
    project_tasks.active_instructions_for_thread.return_value = None
    next_request = await processor._build_dashboard_request(
        msg,
        agent_id="agent-1",
        user_id=1,
        session_key="sk",
        thread_id="thr",
        meta=msg.metadata,
    )
    assert CONFIG_KEY not in next_request.get("configurable", {})


def test_gateway_stamp_overwrites_or_removes_inbound_collision() -> None:
    processor, project_tasks = _processor("first snapshot")
    request = {"configurable": {CONFIG_KEY: "forged", "session_key": "sk"}}
    processor._stamp_project_task_context(request, thread_id="thr", user_id=1, agent_id="agent-1")
    assert request["configurable"] == {CONFIG_KEY: "first snapshot", "session_key": "sk"}

    project_tasks.active_instructions_for_thread.return_value = None
    processor._stamp_project_task_context(request, thread_id="thr", user_id=1, agent_id="agent-1")
    assert request["configurable"] == {"session_key": "sk"}
