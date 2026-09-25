"""Append a server-resolved project-task instruction snapshot at model-call time."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

# Only GlobalProcessor may write this per-turn configurable key. Its value is
# resolved from the persisted task snapshot; inbound metadata is never trusted.
CONFIG_KEY = "octop_project_instructions_snapshot"
_OPEN = "[Project instructions]"
_CLOSE = "[End project instructions]"


def _configured_request(request: ModelRequest[Any]) -> ModelRequest[Any]:
    configurable = get_config().get("configurable") or {}
    if not isinstance(configurable, Mapping):
        return request
    snapshot = configurable.get(CONFIG_KEY)
    if not isinstance(snapshot, str) or not snapshot:
        return request

    block = f"{_OPEN}\n{snapshot}\n{_CLOSE}"
    base_message = request.system_message
    if base_message is None:
        return request.override(system_message=SystemMessage(content=block))

    base_content = base_message.content
    if isinstance(base_content, str):
        if base_content.endswith(block):
            return request
        content: str | list[Any] = f"{base_content}\n\n{block}" if base_content else block
    elif isinstance(base_content, list):
        part = {"type": "text", "text": block}
        if base_content and base_content[-1] == part:
            return request
        content = [*base_content, part]
    else:
        return request
    return request.override(system_message=base_message.model_copy(update={"content": content}))


class ProjectInstructionsMiddleware(AgentMiddleware[Any, Any]):
    """Keep Agent policy intact while adding the task's immutable project context."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_configured_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_configured_request(request))


__all__ = ["CONFIG_KEY", "ProjectInstructionsMiddleware"]
