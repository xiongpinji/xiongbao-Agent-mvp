"""030A B4 turn-level gates for internal project-task file runtimes.

Covers the processor's trust-boundary re-validation (``__call__`` blanket
refusal, ``iter_turn_chunks`` conditional refusal, registry checker
attachment) plus the ThreadRegistry creation guards and the CLI turn entry
refusal. HTTP-layer behavior lives in
``tests/integration/test_project_task_files_access.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from harness_gateway.models import (
    ChannelSubject,
    InboundMessage,
    MessageEventType,
    TextContent,
)

from octop.infra.db.repos.agents import RUNTIME_KIND_PROJECT_TASK_FILES
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.cli.cli_channel import CLI_CHANNEL_ID
from octop.infra.gateway.cli.turn import prepare_cli_turn
from octop.infra.gateway.process.message_keys import INBOUND_ATTACHMENTS_KEY
from octop.infra.gateway.process.processor import GlobalProcessor, _row_is_project_task_runtime
from octop.infra.gateway.slash.dispatcher import SlashDispatcher
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.gateway.ws import WS_CHANNEL_ID

INTERNAL_ID = "ptf-runtime-1"
ORDINARY_ID = "agent-ordinary"
OWNER = 7


def _row(agent_id: str, *, internal: bool, user_id: int = OWNER) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=agent_id,
        runtime_kind=RUNTIME_KIND_PROJECT_TASK_FILES if internal else "standard",
        user_id=user_id,
        default_model=None,
        config_json=None,
    )


def _processor(
    rows: dict[str, Any],
    *,
    thread: SimpleNamespace | None = None,
) -> tuple[GlobalProcessor, MagicMock]:
    agent_manager = MagicMock()
    agent_manager.merge_turn_mcp_servers = MagicMock(return_value=None)
    agent_manager.get_row = MagicMock(return_value=None)
    agent_manager.default_mcp_servers = MagicMock(return_value=[])
    agent_manager.default_knowledge_base_ids = MagicMock(return_value=[])
    agent_manager.providers = MagicMock()
    agent_manager.providers.is_model_ref_usable = MagicMock(return_value=False)
    agent_manager.providers.resolve_explicit_default_model = MagicMock(return_value=None)
    agent_manager.providers.resolve_model_for_multimodal_turn = MagicMock(
        side_effect=lambda ref, **_k: ref
    )
    agent_manager.get_thread_model = MagicMock(return_value=None)
    thread_registry = MagicMock()
    thread_registry.get_thread = MagicMock(return_value=thread)
    thread_registry.update_composer = MagicMock()
    processor = GlobalProcessor(
        agent_manager=agent_manager,
        thread_registry=thread_registry,
        audit_repo=MagicMock(),
        agent_repo=MagicMock(get=MagicMock(side_effect=lambda aid: rows.get(aid))),
        user_repo=MagicMock(
            get=MagicMock(return_value=SimpleNamespace(locale="zh", preferences_json=None))
        ),
        connector_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        usage_repo=None,
        gateway=None,
    )
    return processor, thread_registry


def _msg(
    *,
    channel_id: str = WS_CHANNEL_ID,
    channel_type: str = ThreadRegistry.CHANNEL_DASHBOARD,
    subject: str = str(OWNER),
    text: str = "hello",
    metadata: dict[str, Any] | None = None,
    agent_id: str = INTERNAL_ID,
) -> InboundMessage:
    return InboundMessage(
        channel_id=channel_id,
        channel_type=channel_type,
        tenant_id=agent_id,
        channel_subject=ChannelSubject(subject_id=subject),
        content=[TextContent(text=text)],
        metadata=metadata if metadata is not None else {},
    )


def _bound_thread(*, agent_id: str = INTERNAL_ID, user_id: int = OWNER) -> SimpleNamespace:
    return SimpleNamespace(thread_id="thr1", agent_id=agent_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Row classification + checker attachment
# ---------------------------------------------------------------------------


def test_row_is_project_task_runtime() -> None:
    assert _row_is_project_task_runtime(_row(INTERNAL_ID, internal=True))
    assert not _row_is_project_task_runtime(_row(ORDINARY_ID, internal=False))
    assert not _row_is_project_task_runtime(None)
    assert not _row_is_project_task_runtime(SimpleNamespace())


def test_processor_attaches_registry_checker() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    processor, registry = _processor(rows)
    registry.set_internal_runtime_checker.assert_called_once()
    checker = registry.set_internal_runtime_checker.call_args[0][0]
    assert checker == processor._agent_is_project_task_runtime
    assert checker(INTERNAL_ID) is True
    assert checker(ORDINARY_ID) is False


# ---------------------------------------------------------------------------
# _project_task_file_turn_refusal
# ---------------------------------------------------------------------------


def _refusal(
    rows: dict[str, Any],
    msg: InboundMessage,
    meta: dict[str, Any],
    *,
    agent_id: str = INTERNAL_ID,
    user_id: int = OWNER,
    thread: SimpleNamespace | None = None,
) -> str | None:
    processor, _ = _processor(rows, thread=thread)
    return processor._project_task_file_turn_refusal(msg, meta, agent_id=agent_id, user_id=user_id)


def test_ordinary_agent_turns_are_never_refused() -> None:
    rows = {ORDINARY_ID: _row(ORDINARY_ID, internal=False)}
    msg = _msg(agent_id=ORDINARY_ID, channel_id=CLI_CHANNEL_ID, text="/reset")
    meta = {"mcp_servers": ["x"], "hitl_policy": "always"}
    assert _refusal(rows, msg, meta, agent_id=ORDINARY_ID) is None


def test_allowed_owner_dashboard_turn_passes() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    msg = _msg()
    meta = {"thread_id": "thr1", "session_key": "sk", "mcp_servers": []}
    assert _refusal(rows, msg, meta, thread=_bound_thread()) is None


@pytest.mark.parametrize(
    ("channel_id", "channel_type"),
    [
        (CLI_CHANNEL_ID, ThreadRegistry.CHANNEL_DASHBOARD),
        (WS_CHANNEL_ID, ThreadRegistry.CHANNEL_CLI),
        ("dingtalk", "dingtalk"),
    ],
)
def test_non_dashboard_ws_channels_are_refused(channel_id: str, channel_type: str) -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    msg = _msg(channel_id=channel_id, channel_type=channel_type)
    meta = {"thread_id": "thr1"}
    assert _refusal(rows, msg, meta, thread=_bound_thread()) is not None


def test_slash_commands_are_refused() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    msg = _msg(text="/reset all")
    assert _refusal(rows, msg, {"thread_id": "thr1"}, thread=_bound_thread()) is not None


@pytest.mark.parametrize("meta", [{}, {"thread_id": ""}, {"thread_id": "  "}, {"thread_id": 7}])
def test_missing_or_invalid_thread_id_is_refused(meta: dict[str, Any]) -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    assert _refusal(rows, _msg(), meta, thread=_bound_thread()) is not None


def test_unknown_or_foreign_thread_is_refused() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    msg = _msg()
    meta = {"thread_id": "thr1"}
    # Thread row missing entirely.
    assert _refusal(rows, msg, meta, thread=None) is not None
    # Thread belongs to another agent.
    assert _refusal(rows, msg, meta, thread=_bound_thread(agent_id="other")) is not None
    # Thread belongs to another user.
    assert _refusal(rows, msg, meta, thread=_bound_thread(user_id=99)) is not None


def test_non_owner_caller_is_refused() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True, user_id=8)}
    msg = _msg(subject="7")
    meta = {"thread_id": "thr1"}
    assert _refusal(rows, msg, meta, thread=_bound_thread(user_id=8)) is not None


@pytest.mark.parametrize(
    "extra",
    [
        {"mcp_servers": ["evil"]},
        {"skills": ["s"]},
        {"knowledge_base_ids": [1]},
        {"target_agent_ids": ["a"]},
        {"hitl_policy": "always"},
        {INBOUND_ATTACHMENTS_KEY: [{"path": "/etc/passwd"}]},
    ],
)
def test_capability_metadata_is_refused(extra: dict[str, Any]) -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    meta = {"thread_id": "thr1", **extra}
    assert _refusal(rows, _msg(), meta, thread=_bound_thread()) is not None


# ---------------------------------------------------------------------------
# __call__ (IM / cron / team channels): blanket refusal
# ---------------------------------------------------------------------------


async def test_call_refuses_internal_runtime_before_any_side_effect() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    processor, registry = _processor(rows)
    msg = _msg(channel_id="dingtalk", channel_type="dingtalk", text="hi")
    events = [event async for event in processor(msg)]
    assert [e.type for e in events] == [MessageEventType.ERROR, MessageEventType.COMPLETED]
    registry.get_or_create.assert_not_called()
    registry.get_or_create_by_key.assert_not_called()
    registry.create_thread.assert_not_called()
    # Ordinary agents never enter the refusal branch (classification is the
    # only gate); their downstream flow is covered by the existing processor
    # suites.
    ordinary, _ = _processor({ORDINARY_ID: _row(ORDINARY_ID, internal=False)})
    ordinary_refusal = ordinary._project_task_file_turn_refusal(
        _msg(agent_id=ORDINARY_ID), {}, agent_id=ORDINARY_ID, user_id=OWNER
    )
    assert ordinary_refusal is None


# ---------------------------------------------------------------------------
# iter_turn_chunks (dashboard WS + CLI channel): conditional refusal
# ---------------------------------------------------------------------------


async def test_iter_turn_chunks_refuses_turn_without_bound_thread() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    processor, registry = _processor(rows)
    frames = [chunk async for chunk in processor.iter_turn_chunks(_msg(metadata={}))]
    assert frames[0]["type"] == "error"
    assert "internal project-task" in frames[0]["message"]
    assert frames[-1]["type"] == "done"
    registry.get_or_create.assert_not_called()
    registry.get_or_create_by_key.assert_not_called()
    registry.create_thread.assert_not_called()
    registry.reset.assert_not_called()
    registry.rebind.assert_not_called()


async def test_iter_turn_chunks_refuses_cli_channel_turn() -> None:
    rows = {INTERNAL_ID: _row(INTERNAL_ID, internal=True)}
    processor, registry = _processor(rows)
    msg = _msg(
        channel_id=CLI_CHANNEL_ID,
        channel_type=ThreadRegistry.CHANNEL_CLI,
        metadata={"thread_id": "thr1"},
    )
    frames = [chunk async for chunk in processor.iter_turn_chunks(msg)]
    assert frames[0]["type"] == "error"
    assert frames[-1]["type"] == "done"
    registry.create_thread.assert_not_called()


# ---------------------------------------------------------------------------
# ThreadRegistry creation guards
# ---------------------------------------------------------------------------


def _registry(*, internal_ids: frozenset[str]) -> tuple[ThreadRegistry, MagicMock, MagicMock]:
    session_repo = MagicMock()
    session_repo.get = MagicMock(return_value=None)
    thread_repo = MagicMock()
    registry = ThreadRegistry(session_repo=session_repo, thread_repo=thread_repo)
    registry.set_internal_runtime_checker(lambda aid: aid in internal_ids)
    return registry, session_repo, thread_repo


async def test_registry_refuses_internal_thread_creation() -> None:
    registry, session_repo, thread_repo = _registry(internal_ids=frozenset({INTERNAL_ID}))
    with pytest.raises(OctopError) as excinfo:
        await registry.get_or_create(
            agent_id=INTERNAL_ID, user_id=OWNER, channel_type="dashboard", channel_subject_id="7"
        )
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(OctopError):
        await registry.get_or_create_by_key(
            session_key="sk",
            agent_id=INTERNAL_ID,
            user_id=OWNER,
            channel_type="dashboard",
        )
    with pytest.raises(OctopError):
        await registry.reset(
            agent_id=INTERNAL_ID, user_id=OWNER, channel_type="dashboard", channel_subject_id="7"
        )
    with pytest.raises(OctopError):
        registry.create_thread(
            agent_id=INTERNAL_ID, user_id=OWNER, channel_type="dashboard", session_key="sk"
        )
    thread_repo.insert.assert_not_called()
    session_repo.upsert.assert_not_called()


async def test_registry_refuses_reset_by_session_key_for_internal() -> None:
    registry, session_repo, thread_repo = _registry(internal_ids=frozenset({INTERNAL_ID}))
    session_repo.get = MagicMock(
        return_value=SimpleNamespace(
            agent_id=INTERNAL_ID,
            user_id=OWNER,
            channel_type="dashboard",
            chat_type="dm",
            channel_subject_id="7",
            channel_chat_type="dm",
            channel_metadata={},
            channel_id=None,
        )
    )
    with pytest.raises(OctopError) as excinfo:
        await registry.reset_by_session_key("sk")
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    thread_repo.insert.assert_not_called()


async def test_registry_without_checker_keeps_ordinary_flow() -> None:
    registry, _session_repo, thread_repo = _registry(internal_ids=frozenset())
    tid = await registry.get_or_create(
        agent_id=ORDINARY_ID, user_id=OWNER, channel_type="dashboard", channel_subject_id="7"
    )
    assert isinstance(tid, str) and tid.startswith("thr_")
    thread_repo.insert.assert_called_once()


# ---------------------------------------------------------------------------
# CLI turn entry refusal
# ---------------------------------------------------------------------------


async def test_prepare_cli_turn_refuses_internal_runtime() -> None:
    registry, session_repo, thread_repo = _registry(internal_ids=frozenset({INTERNAL_ID}))
    with pytest.raises(OctopError) as excinfo:
        await prepare_cli_turn(
            registry, agent_id=INTERNAL_ID, user_id=OWNER, thread_id=None, session_key=None
        )
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    assert excinfo.value.details == {"internal": True}
    # Refused before any session lookup or registry write.
    session_repo.get.assert_not_called()
    thread_repo.insert.assert_not_called()


async def test_prepare_cli_turn_refuses_internal_even_with_explicit_thread() -> None:
    registry, _session_repo, thread_repo = _registry(internal_ids=frozenset({INTERNAL_ID}))
    with pytest.raises(OctopError):
        await prepare_cli_turn(
            registry, agent_id=INTERNAL_ID, user_id=OWNER, thread_id="thr1", session_key=None
        )
    thread_repo.insert.assert_not_called()


async def test_prepare_cli_turn_keeps_ordinary_agents_working() -> None:
    registry, _session_repo, thread_repo = _registry(internal_ids=frozenset())
    tid, sk = await prepare_cli_turn(
        registry, agent_id=ORDINARY_ID, user_id=OWNER, thread_id=None, session_key=None
    )
    assert tid.startswith("thr_")
    assert sk == ThreadRegistry.cli_key(agent_id=ORDINARY_ID, user_id=OWNER)
    thread_repo.insert.assert_called_once()
