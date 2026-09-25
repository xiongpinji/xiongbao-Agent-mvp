"""Fail-closed tool boundary for managed project-task file runtimes (030A).

A ``project_task_files`` runtime exposes only ``ls``, ``read_file``,
``write_file``, ``edit_file``, ``glob`` and ``grep``. The installed harness
``tools_disabled`` denylist (``ToolsFilterMiddleware``) hides names from the
model but does not veto execution, so this module adds
:class:`ProjectTaskFileToolBoundaryMiddleware`: it filters every model request
down to the allowlist and rejects any other tool call before the handler runs.

Nothing here changes normal agents until a host explicitly mounts the guard via
:func:`apply_project_task_file_boundary`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from harness_agent.config import HarnessAgentConfig
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.agents.tool_catalog import BUILTIN_TOOL_CATALOG

PROJECT_TASK_FILE_TOOLS: frozenset[str] = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
    }
)

_TOOLS_OUTSIDE_CATALOG: frozenset[str] = frozenset(
    {
        "task",
        "write_todos",
        "delete",
        "execute",
        "ask_user_question",
        "agent_list",
        "ask_agent",
        "acp_runner",
    }
)


def project_task_file_tools_disabled() -> frozenset[str]:
    """Denylist superset for ``HarnessAgentConfig.tools_disabled``.

    Built from the full builtin catalog plus harness tools that are mounted
    outside it. Unlike ``tool_catalog.normalize_tools_disabled`` this keeps
    ``task`` and ``write_todos`` listed, because they must stay hidden from a
    controlled file runtime.
    """
    names = {entry.name for entry in BUILTIN_TOOL_CATALOG}
    return frozenset((names | _TOOLS_OUTSIDE_CATALOG) - PROJECT_TASK_FILE_TOOLS)


def project_task_file_backend_spec(root_dir: str | Path) -> dict[str, Any]:
    """Return the fixed virtual filesystem spec pinned to *root_dir*."""
    return {
        "type": "filesystem",
        "root_dir": str(root_dir),
        "virtual_mode": True,
    }


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


class ProjectTaskFileToolBoundaryMiddleware(AgentMiddleware[Any, Any]):
    """Hide and veto every tool outside the six-name project-task allowlist."""

    def _filter_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        tools = [
            tool for tool in (request.tools or []) if _tool_name(tool) in PROJECT_TASK_FILE_TOOLS
        ]
        return request.override(tools=tools)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._filter_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._filter_request(request))

    @staticmethod
    def _rejection(request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "")
        if name in PROJECT_TASK_FILE_TOOLS:
            return None
        allowed = ", ".join(sorted(PROJECT_TASK_FILE_TOOLS))
        return ToolMessage(
            content=(
                f"Error: {name or '<unknown>'} is not available in a controlled file task. "
                f"Allowed tools: {allowed}."
            ),
            tool_call_id=str(tool_call.get("id") or ""),
            name=name or "project_task_file_boundary",
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._rejection(request)
        if rejection is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._rejection(request)
        if rejection is not None:
            return rejection
        return await handler(request)


def apply_project_task_file_boundary(
    cfg: HarnessAgentConfig,
    *,
    root_dir: str | Path,
) -> HarnessAgentConfig:
    """Harden a private config for a managed project-task file runtime.

    Pins a virtual filesystem backend, clears every tool source outside the
    allowlist, disables subagents/teams/plugins/MCP/ACP/media/browser/web search
    and mounts :class:`ProjectTaskFileToolBoundaryMiddleware` last so it sees
    tools injected by any host middleware. The input config is not mutated.
    """
    guard = ProjectTaskFileToolBoundaryMiddleware()
    return replace(
        cfg,
        backend=project_task_file_backend_spec(root_dir),
        tools_disabled=project_task_file_tools_disabled(),
        subagents_auto_load=False,
        subagents=None,
        subagents_path=None,
        tools=None,
        middleware=[*(cfg.middleware or []), guard],
        mcp_server_configs={},
        skills_dir=None,
        skills_disabled=frozenset(),
        acp_runners={},
        acp_delegate_enabled=False,
        media_generation=None,
        team_enabled=False,
        team_peers=(),
        ask_user_enabled=False,
        todos_enabled=False,
        web_search_tools=False,
        deferred_tools=frozenset(),
        defer_mcp_tools=False,
    )


__all__ = [
    "PROJECT_TASK_FILE_TOOLS",
    "ProjectTaskFileToolBoundaryMiddleware",
    "apply_project_task_file_boundary",
    "project_task_file_backend_spec",
    "project_task_file_tools_disabled",
]
