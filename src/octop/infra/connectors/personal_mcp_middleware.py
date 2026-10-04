"""Expose current-actor personal MCP descriptors and validate them again at dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import BaseModel

from octop.infra.connectors.mcp_actor_scope import (
    MCPActorScope,
    PersonalMCPDescriptorReceipt,
    PersonalMCPReceipt,
    TrustedMCPActor,
    current_mcp_actor_scope,
    personal_mcp_denied,
    require_mcp_actor_scope,
)


def fingerprint_personal_descriptor(tool: BaseTool) -> str:
    """Hash model-visible schema/description/metadata without retaining their bodies."""
    schema = tool.tool_call_schema
    payload = {
        "name": tool.name,
        "description": tool.description,
        "schema": (
            schema
            if isinstance(schema, dict)
            else schema.model_json_schema()
            if issubclass(schema, BaseModel)
            else schema.schema()
        ),
        "metadata": tool.metadata,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PersonalMCPDescriptor:
    """Authoritative resolver output bound to the current server-created actor."""

    actor: TrustedMCPActor
    server_name: str
    tool: BaseTool
    connection_fingerprint: str
    descriptor_fingerprint: str

    def receipt(self) -> PersonalMCPDescriptorReceipt:
        return PersonalMCPDescriptorReceipt(
            tool_name=self.tool.name,
            server_name=self.server_name,
            connection_fingerprint=self.connection_fingerprint,
            descriptor_fingerprint=self.descriptor_fingerprint,
        )


class PersonalMCPResolver(Protocol):
    """Root supplies current configuration/cache lookup; no blocking IO in this helper."""

    def __call__(self, actor: TrustedMCPActor) -> Awaitable[Sequence[PersonalMCPDescriptor]]: ...


def _tool_name(tool: BaseTool | dict[str, Any]) -> str:
    if isinstance(tool, BaseTool):
        return tool.name
    return str(tool.get("name") or (tool.get("function") or {}).get("name") or "")


class PersonalMCPMiddleware(AgentMiddleware[Any, Any]):
    """No actor-specific tools or catalogs live on this shared middleware instance."""

    def __init__(
        self,
        *,
        agent_id: str,
        resolver: PersonalMCPResolver,
        personal_tool_names: frozenset[str],
    ) -> None:
        super().__init__()
        if not agent_id or not isinstance(personal_tool_names, frozenset):
            raise ValueError("personal MCP middleware requires a bound agent and immutable names")
        self._agent_id = agent_id
        self._resolver = resolver
        self._personal_tool_names = personal_tool_names

    def _scope(self) -> MCPActorScope:
        scope = require_mcp_actor_scope()
        if scope.actor.agent_id != self._agent_id:
            raise personal_mcp_denied("personal_mcp_agent_binding", locale=scope.actor.locale)
        return scope

    def _ordinary_tools(self, request: ModelRequest[Any]) -> list[BaseTool | dict[str, Any]]:
        return [tool for tool in request.tools if _tool_name(tool) not in self._personal_tool_names]

    def _is_personal(self, request: ToolCallRequest) -> bool:
        name = request.tool_call["name"]
        if name in self._personal_tool_names:
            return True
        scope = current_mcp_actor_scope()
        receipt = scope.receipt if scope is not None else None
        return receipt is not None and any(item.tool_name == name for item in receipt.descriptors)

    async def _resolve(self, scope: MCPActorScope) -> tuple[PersonalMCPDescriptor, ...]:
        scope.require_active()
        try:
            records = tuple(await self._resolver(scope.actor))
        except Exception:
            raise personal_mcp_denied(
                "personal_mcp_resolver_failed", locale=scope.actor.locale
            ) from None
        scope.require_active()
        names: set[str] = set()
        for record in records:
            if (
                record.actor != scope.actor
                or record.server_name not in scope.actor.allowed_personal_servers
                or not record.connection_fingerprint
                or not record.tool.name
                or record.tool.name in names
            ):
                raise personal_mcp_denied(
                    "personal_mcp_descriptor_binding", locale=scope.actor.locale
                )
            try:
                fingerprint = fingerprint_personal_descriptor(record.tool)
            except (TypeError, ValueError):
                raise personal_mcp_denied(
                    "personal_mcp_descriptor_invalid", locale=scope.actor.locale
                ) from None
            if fingerprint != record.descriptor_fingerprint:
                raise personal_mcp_denied(
                    "personal_mcp_descriptor_fingerprint", locale=scope.actor.locale
                )
            names.add(record.tool.name)
        return records

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        scope = current_mcp_actor_scope()
        if scope is not None and scope.actor.allowed_personal_servers:
            raise personal_mcp_denied("personal_mcp_sync_unsupported", locale=scope.actor.locale)
        tools = self._ordinary_tools(request)
        return handler(request.override(tools=tools) if tools != request.tools else request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        ordinary = self._ordinary_tools(request)
        if current_mcp_actor_scope() is None:
            return await handler(
                request.override(tools=ordinary) if ordinary != request.tools else request
            )
        scope = self._scope()
        records = await self._resolve(scope)
        if {_tool_name(tool) for tool in ordinary} & {record.tool.name for record in records}:
            raise personal_mcp_denied(
                "personal_mcp_public_name_collision", locale=scope.actor.locale
            )
        receipt = PersonalMCPReceipt(scope.actor, tuple(record.receipt() for record in records))
        configured = request.override(tools=[*ordinary, *(record.tool for record in records)])
        scope.record_model_receipt(receipt)
        try:
            return await handler(configured)
        except BaseException:
            scope.clear_model_receipt(receipt)
            raise

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if not self._is_personal(request):
            return handler(request)
        scope = self._scope()
        raise personal_mcp_denied("personal_mcp_sync_unsupported", locale=scope.actor.locale)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if not self._is_personal(request):
            return await handler(request)
        scope = self._scope()
        receipt = scope.receipt
        if receipt is None:
            raise personal_mcp_denied("personal_mcp_receipt_missing", locale=scope.actor.locale)
        stamp = next(
            (item for item in receipt.descriptors if item.tool_name == request.tool_call["name"]),
            None,
        )
        if stamp is None:
            raise personal_mcp_denied("personal_mcp_tool_not_exposed", locale=scope.actor.locale)
        runtime = request.runtime
        configurable = runtime.config.get("configurable", {}) if runtime is not None else {}
        if configurable.get("thread_id") != scope.actor.thread_id or any(
            key in configurable and configurable[key] != getattr(scope.actor, key)
            for key in ("agent_id", "session_key", "source")
        ):
            raise personal_mcp_denied("personal_mcp_runtime_binding", locale=scope.actor.locale)
        # Runtime user fields are never an authority source; only this trusted scope is.
        records = await self._resolve(scope)
        record = next((item for item in records if item.tool.name == stamp.tool_name), None)
        if record is None or record.receipt() != stamp:
            raise personal_mcp_denied("personal_mcp_approval_changed", locale=scope.actor.locale)
        if not callable(getattr(request, "override", None)):
            raise personal_mcp_denied(
                "personal_mcp_override_unsupported", locale=scope.actor.locale
            )
        scope.require_active()
        try:
            configured = request.override(tool=record.tool)
        except (AttributeError, TypeError):
            raise personal_mcp_denied(
                "personal_mcp_override_unsupported", locale=scope.actor.locale
            ) from None
        return await handler(configured)
