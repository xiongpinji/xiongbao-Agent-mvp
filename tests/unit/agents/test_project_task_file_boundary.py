"""M0 evidence: managed project-task runtimes expose and execute only six file tools.

The installed harness ``ToolsFilterMiddleware`` only removes model-visible names;
the executor still dispatches a forged call. These tests pin the fail-closed
``ProjectTaskFileToolBoundaryMiddleware`` that hides disallowed tools *and*
rejects them before the handler runs, plus the private-config hardening helper.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from harness_agent.config import HarnessAgentConfig, ModelConfig, ProviderConfig
from harness_agent.llm.factory import ChatModelFactory
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from octop.infra.agents.project_task_file_boundary import (
    PROJECT_TASK_FILE_TOOLS,
    ProjectTaskFileToolBoundaryMiddleware,
    apply_project_task_file_boundary,
    project_task_file_backend_spec,
    project_task_file_tools_disabled,
)

FORGED_TOOL_NAMES = (
    "task",
    "execute",
    "web_fetch",
    "read_env_file",
    "write_env_file",
    "unknown_plugin_tool",
)

DANGEROUS_NAMES = (
    "task",
    "execute",
    "write_todos",
    "ask_user_question",
    "web_fetch",
    "browser_use",
    "desktop_screenshot",
    "send_file_to_user",
    "current_time",
    "read_env_file",
    "write_env_file",
    "memory_search",
    "memory_get",
    "acp_runner",
    "cronjob_create",
    "search_knowledge",
    "mobile_tap",
    "generate_image",
    "generate_video",
    "delete",
)


def _name(tool: Any) -> str:
    if isinstance(tool, dict):
        fn = tool.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
        return str(tool.get("name", ""))
    return str(getattr(tool, "name", "") or "")


class _ToolCallRequest:
    """Minimal stand-in for ``ToolCallRequest`` for handler-veto tests."""

    def __init__(
        self, name: str, args: dict[str, Any] | None = None, call_id: str = "call_1"
    ) -> None:
        self.tool_call = {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


class _SpyHandler:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def __call__(self, request: Any) -> ToolMessage:
        self.requests.append(request)
        return ToolMessage(
            content="executed",
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
        )


class _RecordingChatModel(BaseChatModel):
    """LangChain chat model that records the exact tools bound per model call."""

    scripts: list[AIMessage] = []
    invocations: list[list[str]] = []
    _pending: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "octop-m0-recording"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        self._pending = [_name(tool) for tool in tools]
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.invocations.append(list(self._pending))
        index = min(len(self.invocations) - 1, len(self.scripts) - 1)
        return ChatResult(generations=[ChatGeneration(message=self.scripts[index])])


class _RecordingFactory(ChatModelFactory):
    def __init__(self, model: _RecordingChatModel) -> None:
        super().__init__(
            [
                ProviderConfig(
                    id="fake",
                    base_url="http://127.0.0.1:1",
                    api_key="test-key",
                    models=[ModelConfig(id="m")],
                )
            ]
        )
        self._model = model

    def get_chat_model(self, model_ref: str) -> BaseChatModel:
        return self._model


def _base_config(tmp_path: Any) -> HarnessAgentConfig:
    return HarnessAgentConfig(
        name="m0-boundary-probe",
        workspace_dir=tmp_path,
        backend="local_shell",
        checkpointer=False,
        pii_enabled=False,
        media_offload_enabled=False,
        model_retry_enabled=False,
        tool_search_mode="eager",
        memory=(),
    )


def _build_harness_agent(tmp_path: Any, model: _RecordingChatModel) -> Any:
    from harness_agent.agent import HarnessAgent

    cfg = apply_project_task_file_boundary(_base_config(tmp_path), root_dir=tmp_path)
    return HarnessAgent(cfg, model_factory=_RecordingFactory(model))


async def _run_graph(agent: Any) -> dict[str, Any]:
    return await agent.graph.ainvoke({"messages": [{"role": "user", "content": "hi"}]})


def test_allowlist_is_exactly_the_six_file_tools() -> None:
    assert (
        frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep"})
        == PROJECT_TASK_FILE_TOOLS
    )


def test_config_denylist_is_a_superset_of_dangerous_tools() -> None:
    disabled = project_task_file_tools_disabled()
    assert set(DANGEROUS_NAMES).issubset(disabled)
    assert disabled.isdisjoint(PROJECT_TASK_FILE_TOOLS)


def test_backend_spec_is_virtual_filesystem_without_execute(tmp_path: Any) -> None:
    spec = project_task_file_backend_spec(tmp_path)
    assert spec["type"] == "filesystem"
    assert spec["virtual_mode"] is True
    assert spec["root_dir"] == str(tmp_path)
    assert "execute" not in spec


def test_apply_boundary_hardens_private_config_without_mutating_input(tmp_path: Any) -> None:
    base = HarnessAgentConfig(
        name="base",
        workspace_dir=tmp_path,
        backend="local_shell",
        tools_disabled=frozenset({"ls"}),
        subagents_auto_load=True,
        team_enabled=True,
        ask_user_enabled=True,
        todos_enabled=True,
        web_search_tools=True,
        skills_dir=["/skills"],
        tools=[SimpleNamespace(name="web_fetch")],
        middleware=[SimpleNamespace(name="existing")],
        mcp_server_configs={"c": {"url": "http://x"}},
        acp_runners={"r": SimpleNamespace(name="runner")},
        acp_delegate_enabled=True,
        media_generation=SimpleNamespace(enabled=True),
    )

    hardened = apply_project_task_file_boundary(base, root_dir=tmp_path)

    assert hardened is not base
    assert base.tools_disabled == frozenset({"ls"})
    assert base.subagents_auto_load is True
    assert hardened.tools_disabled == project_task_file_tools_disabled()
    assert hardened.tools_disabled.isdisjoint(PROJECT_TASK_FILE_TOOLS)
    assert hardened.subagents_auto_load is False
    assert hardened.subagents is None
    assert hardened.tools is None
    assert hardened.skills_dir is None
    assert hardened.mcp_server_configs == {}
    assert hardened.acp_runners == {}
    assert hardened.acp_delegate_enabled is False
    assert hardened.media_generation is None
    assert hardened.team_enabled is False
    assert hardened.ask_user_enabled is False
    assert hardened.todos_enabled is False
    assert hardened.web_search_tools is False
    assert isinstance(hardened.backend, dict)
    assert hardened.backend["type"] == "filesystem"
    assert hardened.backend["virtual_mode"] is True
    assert isinstance(hardened.middleware, list)
    assert isinstance(hardened.middleware[-1], ProjectTaskFileToolBoundaryMiddleware)
    assert [m.name for m in hardened.middleware[:-1]] == ["existing"]


@pytest.mark.parametrize("tool_name", FORGED_TOOL_NAMES)
async def test_guard_rejects_forged_calls_without_invoking_handler(tool_name: str) -> None:
    guard = ProjectTaskFileToolBoundaryMiddleware()
    spy = _SpyHandler()

    result = await guard.awrap_tool_call(_ToolCallRequest(tool_name), spy)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_1"
    assert tool_name in str(result.content)
    assert spy.requests == []


async def test_guard_rejects_call_with_missing_name() -> None:
    guard = ProjectTaskFileToolBoundaryMiddleware()
    spy = _SpyHandler()
    request = _ToolCallRequest("")
    request.tool_call["name"] = None

    result = await guard.awrap_tool_call(request, spy)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert spy.requests == []


@pytest.mark.parametrize("tool_name", sorted(PROJECT_TASK_FILE_TOOLS))
async def test_guard_allows_six_names_to_reach_handler(tool_name: str) -> None:
    guard = ProjectTaskFileToolBoundaryMiddleware()
    spy = _SpyHandler()

    result = await guard.awrap_tool_call(_ToolCallRequest(tool_name), spy)

    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    assert len(spy.requests) == 1
    assert spy.requests[0].tool_call["name"] == tool_name


def test_guard_model_request_filter_is_allowlist_only() -> None:
    guard = ProjectTaskFileToolBoundaryMiddleware()
    seen: list[list[str]] = []
    tools = [
        SimpleNamespace(name="ls"),
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="write_file"),
        SimpleNamespace(name="edit_file"),
        SimpleNamespace(name="glob"),
        SimpleNamespace(name="grep"),
        SimpleNamespace(name="task"),
        SimpleNamespace(name="web_fetch"),
        {"name": "browser_use"},
        {"function": {"name": "write_todos"}},
        SimpleNamespace(name="totally_unknown_plugin_tool"),
    ]
    request = ModelRequest(model=SimpleNamespace(), messages=[], tools=tools)

    def handler(inner: ModelRequest) -> Any:
        seen.append([_name(tool) for tool in inner.tools])
        return "model-response"

    result = guard.wrap_model_call(request, handler)

    assert result == "model-response"
    assert seen == [[_name(tool) for tool in tools[:6]]]
    assert set(seen[0]) == set(PROJECT_TASK_FILE_TOOLS)
    assert len(request.tools) == len(tools)


async def test_real_harness_graph_exposes_only_allowlisted_tools(tmp_path: Any) -> None:
    model = _RecordingChatModel(scripts=[AIMessage(content="done")])
    agent = _build_harness_agent(tmp_path, model)

    await _run_graph(agent)

    assert model.invocations
    assert set(model.invocations[0]) == set(PROJECT_TASK_FILE_TOOLS)


async def test_real_harness_graph_refuses_forged_task_call(tmp_path: Any) -> None:
    model = _RecordingChatModel(
        scripts=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "escape"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    agent = _build_harness_agent(tmp_path, model)

    result = await _run_graph(agent)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].name == "task"
    assert tool_messages[0].status == "error"
    assert "not available" in str(tool_messages[0].content)
    assert len(model.invocations) == 2


async def test_real_harness_graph_allows_allowlisted_forged_call(tmp_path: Any) -> None:
    target = tmp_path / "forged-positive.txt"
    model = _RecordingChatModel(
        scripts=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "forged-positive.txt", "content": "hello"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    agent = _build_harness_agent(tmp_path, model)

    result = await _run_graph(agent)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status != "error"
    assert target.read_text(encoding="utf-8") == "hello"
    assert len(model.invocations) == 2


async def test_installed_tools_disabled_alone_does_not_veto_forged_call(tmp_path: Any) -> None:
    """Documents the installed-harness gap the boundary middleware must close."""

    from harness_agent.agent import HarnessAgent

    model = _RecordingChatModel(
        scripts=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "current_time", "args": {}, "id": "call_1", "type": "tool_call"}
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    base = _base_config(tmp_path)
    base.tools_disabled = project_task_file_tools_disabled()
    agent = HarnessAgent(base, model_factory=_RecordingFactory(model))

    result = await _run_graph(agent)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert set(model.invocations[0]) == set(PROJECT_TASK_FILE_TOOLS)
    assert len(tool_messages) == 1
    assert tool_messages[0].status != "error", (
        "installed tools_disabled only hides; execution still runs"
    )
