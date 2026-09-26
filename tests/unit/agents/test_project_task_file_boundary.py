"""M0 evidence: managed project-task runtimes expose and execute only six file tools.

The installed harness ``ToolsFilterMiddleware`` only removes model-visible names;
the executor still dispatches a forged call. These tests pin the fail-closed
``ProjectTaskFileToolBoundaryMiddleware`` that hides disallowed tools *and*
rejects them before the handler runs, plus the private-config hardening helper.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harness_agent.config import HarnessAgentConfig, ModelConfig, ProviderConfig
from harness_agent.llm.factory import ChatModelFactory
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from octop.infra.agents.project_task_file_boundary import (
    _TOOL_ARG_SCHEMAS,
    PROJECT_TASK_FILE_TOOLS,
    ProjectTaskFileToolBoundaryMiddleware,
    apply_project_task_file_boundary,
    project_task_file_backend_spec,
    project_task_file_tools_disabled,
)

CANARY_TEXT = "CANARY-SECRET"

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")

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


# ---------------------------------------------------------------------------
# 030A RD-4: S4 tool boundary path veto. TM-err == error ToolMessage AND the
# handler spy never executed. Stable plants only — check-time refusals, no
# TOCTOU immunity claimed (design §6.3).
# ---------------------------------------------------------------------------


def _require_junction(link: Path, target: Path) -> None:
    """Create a real NTFS junction or fail loudly (never a silent skip)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not link.is_junction():
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        pytest.fail(
            f"environment: cannot create NTFS junction {link} -> {target}: "
            f"rc={result.returncode} stderr={stderr!r}"
        )
    assert os.path.realpath(link) == os.path.realpath(target)


def _plant_dir_link(link: Path, target: Path) -> Callable[[], None]:
    """Plant a stable directory link per platform; return its cleanup."""
    if os.name == "nt":
        _require_junction(link, target)
        return link.rmdir
    link.symlink_to(target, target_is_directory=True)
    return link.unlink


def _managed_root(tmp_path: Path) -> Path:
    """Real managed root with clean content, plus an outside canary tree."""
    root = tmp_path / "managed"
    (root / "sub").mkdir(parents=True)
    (root / "notes.txt").write_text("inner", encoding="utf-8")
    (root / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text(CANARY_TEXT, encoding="utf-8")
    return root


def _guard(root: Path, **kwargs: Any) -> ProjectTaskFileToolBoundaryMiddleware:
    return ProjectTaskFileToolBoundaryMiddleware(root=root, **kwargs)


def _assert_refused(result: Any, spy: _SpyHandler) -> ToolMessage:
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert spy.requests == []
    assert CANARY_TEXT not in str(result.content)
    return result


def _assert_dispatched(result: Any, spy: _SpyHandler) -> ToolMessage:
    assert isinstance(result, ToolMessage)
    assert result.status != "error"
    assert len(spy.requests) == 1
    return result


def _build_agent_at(
    root: Path,
    model: _RecordingChatModel,
    *,
    checkpointer: Any = False,
) -> Any:
    from harness_agent.agent import HarnessAgent

    cfg = apply_project_task_file_boundary(_base_config(root), root_dir=root)
    cfg.checkpointer = checkpointer
    return HarnessAgent(cfg, model_factory=_RecordingFactory(model))


def _run_scripts(name: str, args: dict[str, Any], *, call_id: str | None = None) -> list[AIMessage]:
    """One graph run: a model tool-call turn followed by a terminal turn."""
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": args,
                    "id": call_id or f"call_{name}",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done"),
    ]


def _run_thread(agent: Any, thread_id: str) -> Any:
    """Invoke on a checkpointed thread (the production multi-turn shape)."""
    return agent.graph.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        {"configurable": {"thread_id": thread_id}},
    )


def _only_tool_message(result: dict[str, Any]) -> ToolMessage:
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(messages) == 1
    return messages[0]


def _last_tool_message(result: dict[str, Any]) -> ToolMessage:
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert messages
    return messages[-1]


def test_tool_arg_matrix_matches_installed_deepagents_schemas() -> None:
    """IT-1 pin: drift in the installed schemas fails this test loudly."""
    from deepagents.middleware.filesystem import (
        EditFileSchema,
        GlobSchema,
        GrepSchema,
        LsSchema,
        ReadFileSchema,
        WriteFileSchema,
    )

    installed = {
        "ls": LsSchema,
        "read_file": ReadFileSchema,
        "write_file": WriteFileSchema,
        "edit_file": EditFileSchema,
        "glob": GlobSchema,
        "grep": GrepSchema,
    }
    for name, schema in installed.items():
        required, optional = _TOOL_ARG_SCHEMAS[name]
        fields = schema.model_fields
        assert set(fields) == set(required) | set(optional), f"{name}: schema drift"
        assert set(required) == {key for key, field in fields.items() if field.is_required()}, (
            f"{name}: required-set drift"
        )
        assert set(optional) == {key for key, field in fields.items() if not field.is_required()}, (
            f"{name}: optional-set drift"
        )


def test_guard_root_property_pins_injection(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    assert _guard(root).root == root
    assert ProjectTaskFileToolBoundaryMiddleware().root is None
    hardened = apply_project_task_file_boundary(_base_config(root), root_dir=root)
    assert isinstance(hardened.middleware, list)
    guard = hardened.middleware[-1]
    assert isinstance(guard, ProjectTaskFileToolBoundaryMiddleware)
    assert guard.root == Path(root)


# --- A1/A2: read_file through planted final / intermediate links ------------


@posix_only
async def test_guard_refuses_read_through_planted_posix_links(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = tmp_path / "outside" / "canary.txt"
    (root / "escape.txt").symlink_to(canary)
    (root / "link").symlink_to(canary.parent, target_is_directory=True)
    guard = _guard(root)

    for args in (
        {"file_path": "escape.txt"},  # A1 final component
        {"file_path": "/escape.txt"},  # same check via single-leading-slash form
        {"file_path": "link/canary.txt"},  # A2 intermediate component
        {"file_path": "/link/canary.txt"},
    ):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest("read_file", args), spy)
        _assert_refused(result, spy)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


@windows_only
async def test_guard_refuses_read_through_windows_junction(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    outside = tmp_path / "outside"
    canary = outside / "canary.txt"
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    link = root / "junction"
    _require_junction(link, outside)
    guard = _guard(root)
    try:
        for args in ({"file_path": "junction/canary.txt"}, {"file_path": "/junction/canary.txt"}):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest("read_file", args), spy)
            _assert_refused(result, spy)
    finally:
        link.rmdir()
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


# --- A3: write_file / edit_file through planted links -----------------------


@posix_only
async def test_guard_refuses_write_and_edit_through_planted_links(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    outside = tmp_path / "outside"
    canary = outside / "canary.txt"
    (root / "escape.txt").symlink_to(canary)
    (root / "link").symlink_to(outside, target_is_directory=True)
    guard = _guard(root)

    calls = (
        ("write_file", {"file_path": "escape.txt", "content": "evil"}),
        ("write_file", {"file_path": "link/evil.txt", "content": "evil"}),
        ("edit_file", {"file_path": "escape.txt", "old_string": "C", "new_string": "X"}),
        ("edit_file", {"file_path": "link/canary.txt", "old_string": "C", "new_string": "X"}),
    )
    for name, args in calls:
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
        _assert_refused(result, spy)
    # Zero side effects outside: canary bytes unchanged, no new files.
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT
    assert sorted(p.name for p in outside.iterdir()) == ["canary.txt"]

    # Positives: clean write / edit dispatch, both path forms.
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(
        _ToolCallRequest("write_file", {"file_path": "new.txt", "content": "x"}), spy
    )
    _assert_dispatched(result, spy)
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(
        _ToolCallRequest(
            "edit_file", {"file_path": "/notes.txt", "old_string": "i", "new_string": "j"}
        ),
        spy,
    )
    _assert_dispatched(result, spy)


@windows_only
async def test_guard_refuses_write_through_windows_junction(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    outside = tmp_path / "outside"
    link = root / "junction"
    _require_junction(link, outside)
    guard = _guard(root)
    try:
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(
            _ToolCallRequest("write_file", {"file_path": "junction/evil.txt", "content": "x"}),
            spy,
        )
        _assert_refused(result, spy)
        assert not (outside / "evil.txt").exists()
    finally:
        link.rmdir()


# --- A9: FIFO (POSIX) and hard links (both platforms, S7) -------------------


@posix_only
async def test_guard_refuses_fifo_final_component(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    os.mkfifo(root / "pipe")
    guard = _guard(root)
    for name, args in (
        ("read_file", {"file_path": "pipe"}),
        ("write_file", {"file_path": "pipe", "content": "x"}),
        ("edit_file", {"file_path": "pipe", "old_string": "a", "new_string": "b"}),
    ):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
        _assert_refused(result, spy)


async def test_guard_refuses_hardlinked_final_component(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = tmp_path / "outside" / "canary.txt"
    os.link(canary, root / "linked.txt")
    try:
        guard = _guard(root)
        for name, args in (
            ("read_file", {"file_path": "linked.txt"}),
            ("write_file", {"file_path": "linked.txt", "content": "evil"}),
        ):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
            _assert_refused(result, spy)
        assert canary.read_text(encoding="utf-8") == CANARY_TEXT
        # Positive: a single-link regular file dispatches.
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(
            _ToolCallRequest("read_file", {"file_path": "notes.txt"}), spy
        )
        _assert_dispatched(result, spy)
    finally:
        (root / "linked.txt").unlink()


# --- A4: ls — link anywhere in the base subtree, siblings unaffected --------


async def test_guard_ls_and_grep_refuse_hardlinked_entries_in_subtree(
    tmp_path: Path,
) -> None:
    """S3 discovered entries: a hard-linked regular file refuses listings.

    ``ls``/``glob``/``grep`` would otherwise enumerate or read an outside
    inode planted as a hard link. A clean sibling base stays functional.
    """
    root = _managed_root(tmp_path)
    canary = tmp_path / "outside" / "canary.txt"
    (root / "dirty").mkdir()
    os.link(canary, root / "dirty" / "linked.txt")
    guard = _guard(root)
    try:
        for name, args in (
            ("ls", {"path": "/"}),
            ("ls", {"path": "dirty"}),
            ("glob", {"pattern": "**/*.txt"}),
            ("glob", {"pattern": "*.txt", "path": "dirty"}),
            ("grep", {"pattern": CANARY_TEXT}),
            ("grep", {"pattern": CANARY_TEXT, "path": "dirty"}),
            ("grep", {"pattern": CANARY_TEXT, "path": "dirty/linked.txt"}),
        ):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
            _assert_refused(result, spy)
        assert canary.read_text(encoding="utf-8") == CANARY_TEXT
        # Anti-over-refusal: the clean sibling subtree still dispatches.
        for name, args in (("ls", {"path": "sub"}), ("grep", {"pattern": "nested", "path": "sub"})):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
            _assert_dispatched(result, spy)
    finally:
        (root / "dirty" / "linked.txt").unlink()


@posix_only
async def test_guard_grep_refuses_fifo_search_base(tmp_path: Path) -> None:
    """A FIFO search base never dispatches (S2 on the file form)."""
    root = _managed_root(tmp_path)
    os.mkfifo(root / "pipe")
    guard = _guard(root)
    for args in ({"pattern": "x", "path": "pipe"}, {"pattern": "x", "path": "/pipe"}):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest("grep", args), spy)
        _assert_refused(result, spy)


@posix_only
async def test_guard_ls_refuses_fifo_entry_in_subtree(tmp_path: Path) -> None:
    """S3 discovered entries: a FIFO anywhere in the base subtree refuses."""
    root = _managed_root(tmp_path)
    (root / "dirty").mkdir()
    os.mkfifo(root / "dirty" / "pipe")
    guard = _guard(root)
    try:
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest("ls", {"path": "/"}), spy)
        _assert_refused(result, spy)
    finally:
        (root / "dirty" / "pipe").unlink()


# --- A4: ls — link anywhere in the base subtree, siblings unaffected --------


@posix_only
async def test_guard_ls_refuses_link_in_base_subtree_but_not_siblings(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = tmp_path / "outside" / "canary.txt"
    (root / "dirty" / "deep").mkdir(parents=True)
    (root / "dirty" / "deep" / "escape").symlink_to(canary)
    (root / "clean").mkdir()
    (root / "clean" / "ok.txt").write_text("x", encoding="utf-8")
    guard = _guard(root)

    for args in ({"path": "/"}, {"path": "."}, {"path": "dirty"}, {"path": "/dirty/deep"}):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest("ls", args), spy)
        _assert_refused(result, spy)
    # Anti-over-refusal: the clean sibling subtree still lists.
    for args in ({"path": "clean"}, {"path": "/clean"}, {"path": "sub"}):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest("ls", args), spy)
        _assert_dispatched(result, spy)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


@windows_only
async def test_guard_ls_refuses_junction_in_base_subtree(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    (root / "dirty").mkdir()
    outside = tmp_path / "outside"
    link = root / "dirty" / "junction"
    _require_junction(link, outside)
    guard = _guard(root)
    try:
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest("ls", {"path": "/"}), spy)
        _assert_refused(result, spy)
    finally:
        link.rmdir()
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(_ToolCallRequest("ls", {"path": "/"}), spy)
    _assert_dispatched(result, spy)


# --- A5: glob / grep — base subtree and pattern-prefix links ----------------


@posix_only
async def test_guard_glob_and_grep_refuse_links_in_base_or_pattern_prefix(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = tmp_path / "outside" / "canary.txt"
    (root / "linked").symlink_to(canary.parent, target_is_directory=True)
    guard = _guard(root)

    calls = (
        # Link in the base subtree (base omitted → root scan finds it).
        ("glob", {"pattern": "*.txt"}),
        ("glob", {"pattern": "**/*.txt", "path": "/"}),
        ("grep", {"pattern": "inner"}),
        # Pattern literal-prefix component is the link itself.
        ("glob", {"pattern": "linked/*.txt"}),
        # Root-anchored pattern: the whole root must be proven (fail-closed
        # under the unverified anchor walk semantics).
        ("glob", {"pattern": "/notes.txt"}),
        # Base explicitly the linked directory.
        ("glob", {"pattern": "*.txt", "path": "linked"}),
        ("grep", {"pattern": "inner", "path": "/linked"}),
    )
    for name, args in calls:
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
        _assert_refused(result, spy)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT

    # Anti-over-refusal: a clean sibling base still searches while the link
    # sits outside that base.
    for name, args in (
        ("glob", {"pattern": "*.txt", "path": "sub"}),
        ("grep", {"pattern": "nested", "path": "/sub", "glob": "*.txt"}),
    ):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
        _assert_dispatched(result, spy)

    # Post-removal positive: the same root-wide calls dispatch again.
    (root / "linked").unlink()
    for name, args in (("glob", {"pattern": "*.txt"}), ("grep", {"pattern": "inner"})):
        spy = _SpyHandler()
        result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
        _assert_dispatched(result, spy)


@windows_only
async def test_guard_glob_refuses_junction_in_base_subtree(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    (root / "dirty").mkdir()
    outside = tmp_path / "outside"
    link = root / "dirty" / "junction"
    _require_junction(link, outside)
    guard = _guard(root)
    try:
        for name, args in (
            ("glob", {"pattern": "*.txt"}),
            ("grep", {"pattern": "inner"}),
        ):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
            _assert_refused(result, spy)
    finally:
        link.rmdir()
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(_ToolCallRequest("glob", {"pattern": "*.txt"}), spy)
    _assert_dispatched(result, spy)


# --- A6: fail-closed argument contract --------------------------------------

BAD_ARG_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    # Missing required fields.
    ("ls", {}),
    ("read_file", {}),
    ("write_file", {"file_path": "a.txt"}),
    ("write_file", {"content": "x"}),
    ("edit_file", {"file_path": "a.txt", "old_string": "x"}),
    ("glob", {}),
    ("grep", {}),
    # Wrong types (schema drift never widens the surface).
    ("ls", {"path": 5}),
    ("read_file", {"file_path": ["notes.txt"]}),
    ("read_file", {"file_path": "notes.txt", "offset": True}),  # bool is not int
    ("read_file", {"file_path": "notes.txt", "limit": "10"}),
    ("write_file", {"file_path": "a.txt", "content": 1}),
    (
        "edit_file",
        {"file_path": "a.txt", "old_string": "x", "new_string": "y", "replace_all": "yes"},
    ),
    ("glob", {"pattern": 5}),
    ("glob", {"pattern": "*.txt", "path": 5}),
    ("grep", {"pattern": "x", "glob": 5}),
    ("grep", {"pattern": "x", "output_mode": 5}),
    ("grep", {"pattern": "x", "max_count": 0.5}),
    ("grep", {"pattern": "x", "max_count": True}),
    # Unknown fields.
    ("ls", {"path": "/", "extra": "x"}),
    ("read_file", {"file_path": "notes.txt", "follow_symlinks": True}),
    ("glob", {"pattern": "*.txt", "root_dir": "/"}),
    # Host-path shapes in path arguments (both legal forms stay intact).
    ("ls", {"path": "//etc"}),
    ("ls", {"path": "//host/share"}),  # UNC
    ("ls", {"path": "~/x"}),
    ("ls", {"path": "../x"}),
    ("ls", {"path": "a/../b"}),
    ("ls", {"path": "\\dir"}),
    ("ls", {"path": "\\\\server\\share"}),
    ("ls", {"path": "a\\b"}),
    ("read_file", {"file_path": "C:notes.txt"}),  # drive-relative form
    ("read_file", {"file_path": "C:/x.txt"}),
    ("read_file", {"file_path": "c:\\x.txt"}),
    ("read_file", {"file_path": "file:///etc/passwd"}),
    ("read_file", {"file_path": "a\x00b"}),
    ("read_file", {"file_path": "/etc/passwd\x00"}),
    # ... and the same shapes hidden behind the single leading "/" virtual
    # anchor: the anchor is stripped for a second host-shape pass.
    ("ls", {"path": "/C:/Windows"}),
    ("read_file", {"file_path": "/C:relative.txt"}),
    ("read_file", {"file_path": "/file:///etc/passwd"}),
    ("ls", {"path": "/~/x"}),
    ("read_file", {"file_path": "/../x"}),
    ("ls", {"path": "/a\\b"}),  # virtual anchor + mixed separators
    ("ls", {"path": "/a//b/../../c"}),
    ("read_file", {"file_path": "///x"}),
    # Independent ".." segments in pattern fields (never normalized).
    ("glob", {"pattern": "../*.md"}),
    ("glob", {"pattern": "a/../b/*.md"}),
    ("glob", {"pattern": "*.md", "path": "//x"}),
    ("glob", {"pattern": "/C:/*.md"}),
    ("glob", {"pattern": "/file://x"}),
    ("grep", {"pattern": "x", "glob": "../y"}),
    ("grep", {"pattern": "x", "glob": "C:/*.md"}),
    ("grep", {"pattern": "x", "glob": "\\\\host\\share"}),
)


@pytest.mark.parametrize(("tool_name", "args"), BAD_ARG_CALLS)
async def test_guard_rejects_bad_argument_shapes(
    tmp_path: Path, tool_name: str, args: dict[str, Any]
) -> None:
    root = _managed_root(tmp_path)
    guard = _guard(root)
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(_ToolCallRequest(tool_name, args), spy)
    _assert_refused(result, spy)


async def test_guard_rejects_non_dict_args(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    guard = _guard(root)
    spy = _SpyHandler()
    request = _ToolCallRequest("ls")
    request.tool_call["args"] = "path=/"
    result = await guard.awrap_tool_call(request, spy)
    _assert_refused(result, spy)


GOOD_ARG_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    # ls: "/" alone is the root (dir), bare-relative and virtual-absolute,
    # leading "./" by lexical normalization.
    ("ls", {"path": "/"}),
    ("ls", {"path": "."}),
    ("ls", {"path": ""}),
    ("ls", {"path": "sub"}),
    ("ls", {"path": "/sub"}),
    ("ls", {"path": "./sub"}),
    # read_file: both legal forms + pinned optional ints.
    ("read_file", {"file_path": "notes.txt"}),
    ("read_file", {"file_path": "/notes.txt"}),
    ("read_file", {"file_path": "./notes.txt"}),
    ("read_file", {"file_path": "notes.txt", "offset": 0, "limit": 10}),
    ("read_file", {"file_path": "sub/nested.txt"}),
    # write_file: whitespace inside names is preserved, never stripped.
    ("write_file", {"file_path": "new.txt", "content": "x"}),
    ("write_file", {"file_path": "/new2.txt", "content": "x"}),
    ("write_file", {"file_path": "sub dir/f name.txt", "content": "x"}),
    # edit_file with the optional bool.
    ("edit_file", {"file_path": "notes.txt", "old_string": "i", "new_string": "j"}),
    (
        "edit_file",
        {"file_path": "/notes.txt", "old_string": "i", "new_string": "j", "replace_all": True},
    ),
    # glob: omitted path (None default and absent), explicit None, base "/",
    # root-anchored pattern (virtual search-root anchor stays legal).
    ("glob", {"pattern": "**/*.txt"}),
    ("glob", {"pattern": "*.txt", "path": "/"}),
    ("glob", {"pattern": "*.txt", "path": None}),
    ("glob", {"pattern": "*.txt", "path": "sub"}),
    ("glob", {"pattern": "/notes.txt"}),
    # grep: pattern is LITERAL TEXT — never path-checked ("..//\\~" inside is
    # legal); optional base omitted / None; pinned optional fields. The base
    # may be a single regular file OR a directory (installed backend).
    ("grep", {"pattern": "inner"}),
    ("grep", {"pattern": "../../etc/passwd", "path": None, "glob": None}),
    ("grep", {"pattern": "inner", "path": "sub", "glob": "*.txt"}),
    ("grep", {"pattern": "inner", "path": "notes.txt"}),
    ("grep", {"pattern": "inner", "path": "/notes.txt", "glob": "*.txt"}),
    ("grep", {"pattern": "nested", "path": "/sub/nested.txt"}),
    (
        "grep",
        {
            "pattern": "inner",
            "path": "/",
            "glob": "**/*.txt",
            "output_mode": "content",
            "max_count": 5,
        },
    ),
)


@pytest.mark.parametrize(("tool_name", "args"), GOOD_ARG_CALLS)
async def test_guard_dispatches_legal_tool_calls(
    tmp_path: Path, tool_name: str, args: dict[str, Any]
) -> None:
    root = _managed_root(tmp_path)
    guard = _guard(root)
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(_ToolCallRequest(tool_name, args), spy)
    _assert_dispatched(result, spy)


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("read_file", {"file_path": "/"}),
        ("read_file", {"file_path": ""}),
        ("read_file", {"file_path": "."}),
        ("write_file", {"file_path": "/", "content": "x"}),
        ("edit_file", {"file_path": ".", "old_string": "a", "new_string": "b"}),
    ],
)
async def test_guard_refuses_root_as_file_target(
    tmp_path: Path, tool_name: str, args: dict[str, Any]
) -> None:
    """RD-4 pin: "." / "/" is a dir-search-root, never a content-tool target.

    The positive half of the pair lives in GOOD_ARG_CALLS (ls/glob/grep all
    accept the root) and in test_s2 of the resolver suite.
    """
    root = _managed_root(tmp_path)
    guard = _guard(root)
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(_ToolCallRequest(tool_name, args), spy)
    _assert_refused(result, spy)


# --- A13: injectable listing cap at the tool boundary -----------------------


async def test_guard_listing_cap_is_injectable_and_fail_closed(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    for index in range(3):
        (root / f"f{index}.txt").write_text("x", encoding="utf-8")

    tight = _guard(root, max_entries=2)
    spy = _SpyHandler()
    result = await tight.awrap_tool_call(_ToolCallRequest("ls", {"path": "/"}), spy)
    refusal = _assert_refused(result, spy)
    assert "bounded proof cap" in str(refusal.content)

    roomy = _guard(root, max_entries=100)
    spy = _SpyHandler()
    result = await roomy.awrap_tool_call(_ToolCallRequest("ls", {"path": "/"}), spy)
    _assert_dispatched(result, spy)


# --- sync wrapper + legacy name-only mode -----------------------------------


@posix_only
def test_sync_wrapper_runs_the_same_proofs(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = tmp_path / "outside" / "canary.txt"
    (root / "escape.txt").symlink_to(canary)
    guard = _guard(root)
    calls: list[Any] = []

    def sync_spy(request: Any) -> ToolMessage:
        calls.append(request)
        return ToolMessage(
            content="executed",
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
        )

    refusal = guard.wrap_tool_call(
        _ToolCallRequest("read_file", {"file_path": "escape.txt"}), sync_spy
    )
    assert isinstance(refusal, ToolMessage)
    assert refusal.status == "error"
    assert calls == []

    ok = guard.wrap_tool_call(_ToolCallRequest("read_file", {"file_path": "notes.txt"}), sync_spy)
    assert isinstance(ok, ToolMessage)
    assert ok.status != "error"
    assert len(calls) == 1


async def test_guard_without_root_keeps_name_only_veto() -> None:
    """Legacy construction (no root) is name-only: args are not path-checked."""
    guard = ProjectTaskFileToolBoundaryMiddleware()
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(
        _ToolCallRequest("read_file", {"file_path": "../../etc/passwd"}), spy
    )
    _assert_dispatched(result, spy)


# --- A15/A16: running root / managed ancestor replacement -------------------


async def test_guard_refuses_replaced_running_root_and_ancestor(tmp_path: Path) -> None:
    """A15/A16 guard level: stable root or ancestor replacement refuses."""
    home = tmp_path / "home"
    octop = home / ".octop"
    tasks = octop / "project-task-files"
    root = tasks / "agent01"
    root.mkdir(parents=True)
    (root / "notes.txt").write_text("inner", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    guard = _guard(root)

    # Baseline positive on the clean chain.
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(_ToolCallRequest("ls", {"path": "/"}), spy)
    _assert_dispatched(result, spy)

    # A15: the running root itself is replaced by a stable link.
    real_root = tasks / "real-agent01"
    root.rename(real_root)
    cleanup = _plant_dir_link(root, real_root)
    try:
        for name, args in (
            ("read_file", {"file_path": "notes.txt"}),
            ("write_file", {"file_path": "pwn.txt", "content": "x"}),
            ("ls", {"path": "/"}),
            ("glob", {"pattern": "*.txt"}),
            ("grep", {"pattern": "inner"}),
        ):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
            _assert_refused(result, spy)
        assert not (real_root / "pwn.txt").exists()
        assert sorted(p.name for p in outside.iterdir()) == ["canary.txt"]
    finally:
        cleanup()
        real_root.rename(root)

    # A16: a managed ancestor (.octop) is replaced; the leaf itself stays real.
    real_octop = home / "real-octop"
    octop.rename(real_octop)
    cleanup = _plant_dir_link(octop, real_octop)
    try:
        for name, args in (
            ("read_file", {"file_path": "notes.txt"}),
            ("ls", {"path": "/"}),
        ):
            spy = _SpyHandler()
            result = await guard.awrap_tool_call(_ToolCallRequest(name, args), spy)
            _assert_refused(result, spy)
    finally:
        cleanup()
        real_octop.rename(octop)

    # Post-restoration positives.
    spy = _SpyHandler()
    result = await guard.awrap_tool_call(
        _ToolCallRequest("read_file", {"file_path": "notes.txt"}), spy
    )
    _assert_dispatched(result, spy)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


async def test_real_harness_running_root_replacement_is_refused(tmp_path: Path) -> None:
    """A15 real harness: the backend resolves ``root_dir`` at construction, so
    only the guard's per-call S1 walk on the literal root catches a stable
    replacement — through the real tool dispatch, no live provider.

    Phase ordering (installed dependency, diagnosed in this worktree): on a
    FRESH graph state, ``deepagents.middleware.skills.SkillsMiddleware``
    (``before_agent`` → ``_alist_skills_with_errors`` → ``backend.als`` →
    ``FilesystemBackend._resolve_path``) lists ``/_builtin_skills`` through the
    node at the start of ``before_agent`` — running BEFORE any model call and
    before this guard's user middleware — and raises the backend's containment
    ``ValueError`` once the root is replaced. That upstream raise is
    fail-closed (no model dispatch, no tool result, no outside bytes) but its
    message carries host paths, so it is pinned here only as a refusal
    boundary, never as a controlled veto. On a WARM thread (the production
    steady state: ``skills_metadata`` already lives in the session checkpoint)
    the listing is skipped, the composed graph reaches the real tool dispatch,
    and this guard's controlled error ToolMessage is what the model sees for
    every intercepted call.
    """
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "notes.txt").write_text("inner", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_text(CANARY_TEXT, encoding="utf-8")

    model = _RecordingChatModel(
        scripts=[
            *_run_scripts(
                "write_file", {"file_path": "phase1.txt", "content": "p1"}, call_id="call_phase1"
            ),
            *_run_scripts("read_file", {"file_path": "notes.txt"}, call_id="call_read_blocked"),
            *_run_scripts(
                "write_file",
                {"file_path": "phase2.txt", "content": "p2"},
                call_id="call_write_blocked",
            ),
            *_run_scripts("ls", {"path": "/"}, call_id="call_ls_blocked"),
            *_run_scripts("glob", {"pattern": "*.txt"}, call_id="call_glob_blocked"),
            *_run_scripts("read_file", {"file_path": "notes.txt"}, call_id="call_read_restored"),
        ]
    )
    agent = _build_agent_at(managed, model, checkpointer=MemorySaver())
    thread = "a15-real-harness"

    baseline = await _run_thread(agent, thread)
    message = _only_tool_message(baseline)
    assert message.status != "error"
    assert (managed / "phase1.txt").read_text(encoding="utf-8") == "p1"

    real_root = tmp_path / "real-root"
    try:
        managed.rename(real_root)
    except PermissionError as exc:
        if os.name == "nt":
            pytest.skip(
                f"Windows holds the active backend directory open; live rename unproven: {exc}"
            )
        raise
    cleanup = _plant_dir_link(managed, real_root)
    try:
        # Fresh state: the installed skills listing refuses the replaced root
        # before any model or tool dispatch (fail-closed refusal boundary).
        invocations_before = len(model.invocations)
        with pytest.raises(ValueError) as excinfo:
            await _run_thread(agent, "a15-fresh-replaced")
        assert "outside root directory" in str(excinfo.value), (
            "expected the installed backend containment refusal at the skills "
            "listing phase; a different failure needs re-diagnosis"
        )
        assert len(model.invocations) == invocations_before, (
            "the replaced-root refusal must happen before any model/tool dispatch"
        )
        assert CANARY_TEXT not in str(excinfo.value)
        assert not (real_root / "phase2.txt").exists()

        # Warm thread: real dispatch reaches the guard's controlled veto.
        for _ in range(4):
            result = await _run_thread(agent, thread)
            message = _last_tool_message(result)
            assert message.status == "error"
            assert "controlled file task" in str(message.content)
            assert CANARY_TEXT not in str(message.content)
        assert not (real_root / "phase2.txt").exists()
        assert sorted(p.name for p in outside.iterdir()) == ["canary.txt"]
        assert canary.read_text(encoding="utf-8") == CANARY_TEXT
    finally:
        cleanup()
        real_root.rename(managed)

    restored = await _run_thread(agent, thread)
    message = _last_tool_message(restored)
    assert message.status != "error"
    assert "inner" in str(message.content)


async def test_real_harness_managed_ancestor_replacement_is_refused(tmp_path: Path) -> None:
    """A16 real harness: a replaced ``.octop`` ancestor refuses while the leaf
    directory itself stays real; restoration brings the clean positive back.

    Same installed phase ordering as the running-root test above: the fresh
    state refuses inside ``SkillsMiddleware.before_agent`` (fail-closed, no
    model dispatch), while the warm production thread reaches the guard's
    controlled tool veto.
    """
    home = tmp_path / "home"
    octop = home / ".octop"
    managed = octop / "project-task-files" / "agent01"
    managed.mkdir(parents=True)
    (managed / "notes.txt").write_text("inner", encoding="utf-8")

    model = _RecordingChatModel(
        scripts=[
            *_run_scripts(
                "write_file", {"file_path": "phase1.txt", "content": "p1"}, call_id="call_phase1"
            ),
            *_run_scripts(
                "write_file",
                {"file_path": "phase2.txt", "content": "p2"},
                call_id="call_write_blocked",
            ),
            *_run_scripts("read_file", {"file_path": "notes.txt"}, call_id="call_read_blocked"),
            *_run_scripts("read_file", {"file_path": "notes.txt"}, call_id="call_read_restored"),
        ]
    )
    agent = _build_agent_at(managed, model, checkpointer=MemorySaver())
    thread = "a16-real-harness"

    baseline = await _run_thread(agent, thread)
    assert _only_tool_message(baseline).status != "error"
    assert (managed / "phase1.txt").read_text(encoding="utf-8") == "p1"

    real_octop = home / "real-octop"
    try:
        octop.rename(real_octop)
    except PermissionError as exc:
        if os.name == "nt":
            pytest.skip(
                f"Windows holds the active backend ancestor open; live rename unproven: {exc}"
            )
        raise
    cleanup = _plant_dir_link(octop, real_octop)
    try:
        invocations_before = len(model.invocations)
        with pytest.raises(ValueError) as excinfo:
            await _run_thread(agent, "a16-fresh-replaced")
        assert "outside root directory" in str(excinfo.value)
        assert len(model.invocations) == invocations_before
        assert not (managed / "phase2.txt").exists()
        assert not (real_octop / "project-task-files" / "agent01" / "phase2.txt").exists()

        result = await _run_thread(agent, thread)
        message = _last_tool_message(result)
        assert message.status == "error"
        assert "controlled file task" in str(message.content)
        assert not (managed / "phase2.txt").exists()
        assert not (real_octop / "project-task-files" / "agent01" / "phase2.txt").exists()
    finally:
        cleanup()
        real_octop.rename(octop)

    restored = await _run_thread(agent, thread)
    message = _last_tool_message(restored)
    assert message.status != "error"
    assert "inner" in str(message.content)
    assert not (managed / "phase2.txt").exists()  # the refused write never ran
