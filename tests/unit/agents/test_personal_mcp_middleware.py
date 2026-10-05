"""Current authorized descriptors and real dynamic ToolNode override, offline only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from octop.infra.connectors.mcp_actor_scope import (
    TrustedMCPActor,
    export_personal_mcp_receipt,
    require_mcp_actor_scope,
    trusted_mcp_actor_scope,
    without_trusted_mcp_actor_scope,
)
from octop.infra.connectors.personal_mcp_middleware import (
    PersonalMCPDescriptor,
    PersonalMCPMiddleware,
    fingerprint_personal_descriptor,
)
from octop.infra.errors import ErrorCode, OctopError


class QueryArgs(BaseModel):
    query: str = Field(description="synthetic user101 query field")


class TopicArgs(BaseModel):
    topic: str = Field(description="synthetic user202 topic field")


def actor(user_id: int = 101) -> TrustedMCPActor:
    return TrustedMCPActor(
        user_id=user_id,
        agent_id="shared-synthetic-agent",
        thread_id=f"thread-{user_id}",
        session_key=f"session-{user_id}",
        source="dashboard",
        allowed_personal_servers=frozenset({"synthetic_personal"}),
        locale="zh",
    )


def public_tool(name: str = "personal_public_builtin") -> BaseTool:
    def run(query: str) -> str:
        return "public:" + query

    return StructuredTool.from_function(run, name=name, description="public ordinary sentinel")


class Scenario:
    def __init__(self) -> None:
        self.connection = "synthetic-connection-v1"
        self.description_version = "v1"
        self.enabled = True
        self.calls: list[tuple[int, dict[str, Any]]] = []
        self.resolutions: list[TrustedMCPActor] = []
        self.tool_name = "personal_lookup"
        self.invalid_descriptor_fingerprint = False

    async def resolve(self, current: TrustedMCPActor) -> Sequence[PersonalMCPDescriptor]:
        self.resolutions.append(current)
        if not self.enabled:
            return ()

        async def call(**kwargs: Any) -> str:
            self.calls.append((current.user_id, kwargs))
            return f"authorized-sentinel-{current.user_id}"

        tool = StructuredTool.from_function(
            name=self.tool_name,
            description=f"actor-{current.user_id}-description-{self.description_version}",
            args_schema=QueryArgs if current.user_id == 101 else TopicArgs,
            metadata={"synthetic_actor_sentinel": current.user_id},
            coroutine=call,
        )
        return (
            PersonalMCPDescriptor(
                actor=current,
                server_name="synthetic_personal",
                tool=tool,
                connection_fingerprint=self.connection,
                descriptor_fingerprint=(
                    "invalid-synthetic-fingerprint"
                    if self.invalid_descriptor_fingerprint
                    else fingerprint_personal_descriptor(tool)
                ),
            ),
        )

    def middleware(self) -> PersonalMCPMiddleware:
        return PersonalMCPMiddleware(
            agent_id="shared-synthetic-agent",
            resolver=self.resolve,
            personal_tool_names=frozenset({"personal_lookup"}),
        )


def model_request(tools: list[Any] | None = None) -> ModelRequest[Any]:
    return ModelRequest(model=MagicMock(), messages=[], tools=tools or [])


async def expose(middleware: PersonalMCPMiddleware, request: ModelRequest[Any]) -> list[Any]:
    seen: list[Any] = []

    async def handler(current: ModelRequest[Any]) -> ModelResponse[Any]:
        seen.extend(current.tools)
        return ModelResponse(result=[AIMessage(content="synthetic authorized model phase")])

    await middleware.awrap_model_call(request, handler)
    return seen


def tool_request(current: TrustedMCPActor, name: str = "personal_lookup") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {"query": "synthetic"}, "id": "owned-call"},
        tool=None,
        state={"messages": []},
        runtime=ToolRuntime(
            state={},
            context=None,
            config={"configurable": {"thread_id": current.thread_id}},
            stream_writer=lambda _: None,
            tool_call_id="owned-call",
            store=None,
        ),
    )


async def execute(request: ToolCallRequest) -> ToolMessage:
    assert request.tool is not None
    result = await request.tool.ainvoke({**request.tool_call, "type": "tool_call"})
    assert isinstance(result, ToolMessage)
    return result


@pytest.mark.asyncio
async def test_current_descriptor_preserves_ordinary_references_order_and_request() -> None:
    scenario, ordinary = Scenario(), public_tool()
    ordinary_dict = {"name": "ordinary_dict", "description": "public"}
    legacy = public_tool("personal_lookup")
    request = model_request([ordinary, legacy, ordinary_dict])
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()):
        tools = await expose(middleware, request)
        assert tools[:2] == [ordinary, ordinary_dict]
        assert tools[0] is ordinary and tools[1] is ordinary_dict
        assert tools[2].description == "actor-101-description-v1"
        assert tools[2].args_schema is QueryArgs
        assert tools[2].metadata == {"synthetic_actor_sentinel": 101}
        receipt = export_personal_mcp_receipt()
        assert receipt.actor == actor()
        assert receipt.descriptors[0].tool_name == "personal_lookup"
        assert "description" not in receipt.descriptors[0].__dataclass_fields__
        result = await middleware.awrap_tool_call(tool_request(actor()), execute)
        assert result.content == "authorized-sentinel-101"
    assert request.tools == [ordinary, legacy, ordinary_dict]
    assert scenario.calls == [(101, {"query": "synthetic"})]
    assert len(scenario.resolutions) == 2


@pytest.mark.asyncio
async def test_without_scope_model_hides_only_explicit_personal_names() -> None:
    scenario, ordinary = Scenario(), public_tool()
    legacy = public_tool("personal_lookup")
    tools = await expose(scenario.middleware(), model_request([ordinary, legacy]))
    assert tools == [ordinary] and tools[0] is ordinary
    assert not scenario.resolutions


@pytest.mark.asyncio
async def test_ordinary_tool_call_handler_unchanged_without_scope() -> None:
    scenario = Scenario()
    request = tool_request(actor(), name="personal_public_builtin")
    seen = []

    async def handler(current: ToolCallRequest) -> ToolMessage:
        seen.append(current)
        return ToolMessage(content="ordinary-sentinel", tool_call_id="owned-call")

    result = await scenario.middleware().awrap_tool_call(request, handler)
    assert result.content == "ordinary-sentinel"
    assert seen == [request] and seen[0] is request
    assert not scenario.resolutions


@pytest.mark.asyncio
@pytest.mark.parametrize("with_scope", [False, True])
async def test_personal_call_requires_scope_and_previous_model_receipt(with_scope: bool) -> None:
    scenario = Scenario()
    middleware = scenario.middleware()
    if with_scope:
        with trusted_mcp_actor_scope(actor()), pytest.raises(OctopError) as error:
            await middleware.awrap_tool_call(tool_request(actor()), execute)
    else:
        with pytest.raises(OctopError) as error:
            await middleware.awrap_tool_call(tool_request(actor()), execute)
    assert error.value.code is ErrorCode.FORBIDDEN
    assert not scenario.calls and not scenario.resolutions


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["connection", "descriptor", "removed"])
async def test_fresh_config_and_descriptor_changes_reject_before_invocation(change: str) -> None:
    scenario = Scenario()
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        if change == "connection":
            scenario.connection = "synthetic-connection-v2"
        elif change == "descriptor":
            scenario.description_version = "v2"
        else:
            scenario.enabled = False
        with pytest.raises(OctopError) as error:
            await middleware.awrap_tool_call(tool_request(actor()), execute)
        assert error.value.code is ErrorCode.FORBIDDEN
    assert not scenario.calls


@pytest.mark.asyncio
async def test_invalid_descriptor_fingerprint_rejects_before_model_handler() -> None:
    scenario = Scenario()
    scenario.invalid_descriptor_fingerprint = True
    with trusted_mcp_actor_scope(actor()):
        with pytest.raises(OctopError):
            await expose(scenario.middleware(), model_request())
        with pytest.raises(OctopError):
            export_personal_mcp_receipt()
    assert not scenario.calls


@pytest.mark.asyncio
async def test_descriptor_name_collision_does_not_replace_public_tool() -> None:
    scenario = Scenario()
    scenario.tool_name = "personal_public_builtin"
    ordinary = public_tool()
    request = model_request([ordinary])
    with trusted_mcp_actor_scope(actor()), pytest.raises(OctopError):
        await expose(scenario.middleware(), request)
    assert request.tools[0] is ordinary and not scenario.calls


@pytest.mark.asyncio
async def test_bound_agent_and_runtime_thread_are_checked() -> None:
    scenario = Scenario()
    with (
        trusted_mcp_actor_scope(replace(actor(), agent_id="other-synthetic-agent")),
        pytest.raises(OctopError),
    ):
        await expose(scenario.middleware(), model_request())
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        request = tool_request(replace(actor(), thread_id="other-synthetic-thread"))
        with pytest.raises(OctopError):
            await middleware.awrap_tool_call(request, execute)
    assert not scenario.calls


@pytest.mark.asyncio
async def test_resume_uses_prior_authorized_model_receipt_and_rechecks_current_config() -> None:
    scenario = Scenario()
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        original_receipt = export_personal_mcp_receipt()
    with trusted_mcp_actor_scope(actor(), receipt=original_receipt):
        result = await middleware.awrap_tool_call(tool_request(actor()), execute)
        assert result.content == "authorized-sentinel-101"
    scenario.connection = "synthetic-resume-config-v2"
    with trusted_mcp_actor_scope(actor(), receipt=original_receipt), pytest.raises(OctopError):
        await middleware.awrap_tool_call(tool_request(actor()), execute)
    with (
        pytest.raises(OctopError),
        trusted_mcp_actor_scope(
            replace(actor(), thread_id="new-synthetic-thread"), receipt=original_receipt
        ),
    ):
        pytest.fail("mismatched server binding must not enter scope")
    assert len(scenario.calls) == 1


@pytest.mark.asyncio
async def test_scope_closed_during_fresh_resolution_rejects_detached_call() -> None:
    scenario = Scenario()
    entered, released = asyncio.Event(), asyncio.Event()

    async def delayed(current: TrustedMCPActor) -> Sequence[PersonalMCPDescriptor]:
        entered.set()
        await released.wait()
        return await scenario.resolve(current)

    middleware = scenario.middleware()
    delayed_middleware = PersonalMCPMiddleware(
        agent_id=actor().agent_id,
        resolver=delayed,
        personal_tool_names=frozenset({"personal_lookup"}),
    )
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        child = asyncio.create_task(
            delayed_middleware.awrap_tool_call(tool_request(actor()), execute)
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
    released.set()
    with pytest.raises(OctopError):
        await asyncio.wait_for(child, timeout=2)
    assert not scenario.calls and child.done()


@pytest.mark.asyncio
async def test_nested_model_phase_refreshes_exported_receipt_without_tool_phase_minting() -> None:
    scenario = Scenario()
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        first = export_personal_mcp_receipt()
        scenario.connection = "synthetic-next-model-connection"
        scenario.description_version = "next-model"
        await expose(middleware, model_request())
        second = export_personal_mcp_receipt()
        assert first is not second and first.descriptors != second.descriptors
        assert first.descriptors[0].connection_fingerprint == "synthetic-connection-v1"
        await middleware.awrap_tool_call(tool_request(actor()), execute)
        assert export_personal_mcp_receipt() is second
    assert scenario.calls == [(101, {"query": "synthetic"})]


@pytest.mark.asyncio
async def test_unscoped_nested_model_keeps_ordinary_tools_and_outer_receipt() -> None:
    scenario, ordinary = Scenario(), public_tool()
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()) as outer:
        await expose(middleware, model_request())
        receipt = export_personal_mcp_receipt()
        with without_trusted_mcp_actor_scope():
            assert await expose(middleware, model_request([ordinary])) == [ordinary]
        assert outer.active and export_personal_mcp_receipt() is receipt
    assert len(scenario.resolutions) == 1


@pytest.mark.asyncio
async def test_runtime_user_marker_cannot_supply_or_replace_actor_authority() -> None:
    scenario = Scenario()
    middleware = scenario.middleware()
    request = tool_request(actor())
    request.runtime.config["configurable"]["user"] = "synthetic-non-authority-marker"
    with pytest.raises(OctopError):
        await middleware.awrap_tool_call(request, execute)
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        result = await middleware.awrap_tool_call(request, execute)
        assert result.content == "authorized-sentinel-101"
    assert scenario.calls == [(101, {"query": "synthetic"})]


@pytest.mark.asyncio
async def test_failed_model_phase_does_not_leave_approval_receipt() -> None:
    scenario = Scenario()

    async def handler(_: ModelRequest[Any]) -> ModelResponse[Any]:
        raise RuntimeError("synthetic model failed before a usable turn")

    with trusted_mcp_actor_scope(actor()):
        with pytest.raises(RuntimeError):
            await scenario.middleware().awrap_model_call(model_request(), handler)
        with pytest.raises(OctopError):
            export_personal_mcp_receipt()
    assert not scenario.calls


@pytest.mark.asyncio
async def test_unsupported_dynamic_override_rejects_before_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = Scenario()
    middleware = scenario.middleware()
    with trusted_mcp_actor_scope(actor()):
        await expose(middleware, model_request())
        monkeypatch.setattr(ToolCallRequest, "override", None)
        with pytest.raises(OctopError) as error:
            await middleware.awrap_tool_call(tool_request(actor()), execute)
    assert error.value.details["reason"] == "personal_mcp_override_unsupported"
    assert not scenario.calls


@pytest.mark.asyncio
async def test_resolver_failure_has_localized_permission_error_without_raw_detail() -> None:
    async def resolver(_: TrustedMCPActor) -> Sequence[PersonalMCPDescriptor]:
        raise RuntimeError("synthetic resolver private detail")

    middleware = PersonalMCPMiddleware(
        agent_id=actor().agent_id,
        resolver=resolver,
        personal_tool_names=frozenset({"personal_lookup"}),
    )
    with trusted_mcp_actor_scope(actor()), pytest.raises(OctopError) as error:
        await expose(middleware, model_request())
    assert error.value.code is ErrorCode.FORBIDDEN
    assert "synthetic resolver private detail" not in str(error.value)
    assert error.value.message == OctopError.localized(ErrorCode.FORBIDDEN, "zh").message


def test_sync_ordinary_handler_unchanged_and_personal_path_rejects() -> None:
    middleware = Scenario().middleware()
    ordinary_request = tool_request(actor(), "personal_public_builtin")
    seen = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        seen.append(request)
        return ToolMessage(content="ordinary", tool_call_id="owned-call")

    assert middleware.wrap_tool_call(ordinary_request, handler).content == "ordinary"
    assert seen[0] is ordinary_request
    with pytest.raises(OctopError):
        middleware.wrap_tool_call(tool_request(actor()), handler)
    assert len(seen) == 1


class DynamicObserver(AgentMiddleware[Any, Any]):
    def __init__(self, observations: list[bool]) -> None:
        super().__init__()
        self.observations = observations

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        self.observations.append(request.tool is None)
        return await handler(request)


class DescriptorFakeModel(BaseChatModel):
    observations: list[tuple[int, str, list[str]]]
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "offline_personal_descriptor_sentinel"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> DescriptorFakeModel:
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            response = AIMessage(content=str(messages[-1].content))
        else:
            current = require_mcp_actor_scope().actor
            tool = next(tool for tool in self.bound_tools if tool.name == "personal_lookup")
            fields = sorted(tool.args_schema.model_fields)
            self.observations.append((current.user_id, tool.description, fields))
            field = "query" if current.user_id == 101 else "topic"
            response = AIMessage(
                content="",
                tool_calls=[
                    {"name": tool.name, "args": {field: "owned"}, "id": f"call-{current.user_id}"}
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.mark.asyncio
async def test_real_graph_two_authorized_scopes_use_distinct_schema_and_dynamic_override() -> None:
    scenario, dynamic_seen = Scenario(), []
    model = DescriptorFakeModel(observations=[])
    model_seen = model.observations
    middleware = scenario.middleware()
    graph = create_agent(
        model=model,
        tools=[public_tool()],
        middleware=[DynamicObserver(dynamic_seen), middleware],
    )

    async def run(user_id: int) -> str:
        current = actor(user_id)
        with trusted_mcp_actor_scope(current):
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="synthetic authorized task")]},
                config={"configurable": {"thread_id": current.thread_id}},
            )
            return result["messages"][-1].content

    first, second = await asyncio.gather(run(101), run(202))
    assert first == "authorized-sentinel-101" and second == "authorized-sentinel-202"
    assert sorted(model_seen) == [
        (101, "actor-101-description-v1", ["query"]),
        (202, "actor-202-description-v1", ["topic"]),
    ]
    assert dynamic_seen == [True, True]
    assert sorted(scenario.calls) == [(101, {"query": "owned"}), (202, {"topic": "owned"})]
    assert not any(isinstance(value, (dict, list)) for value in vars(middleware).values())
