"""Trusted scope lifetime uses synthetic server actors, never RunnableConfig authority."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from octop.infra.connectors.mcp_actor_scope import (
    TrustedMCPActor,
    current_mcp_actor_scope,
    export_personal_mcp_receipt,
    require_mcp_actor_scope,
    trusted_mcp_actor_scope,
    without_trusted_mcp_actor_scope,
)
from octop.infra.errors import ErrorCode, OctopError


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


def test_actor_is_immutable_and_scope_restores_nested_context() -> None:
    first, second = actor(), actor(202)
    with pytest.raises(FrozenInstanceError):
        first.user_id = 202  # type: ignore[misc]
    assert current_mcp_actor_scope() is None
    with trusted_mcp_actor_scope(first) as outer:
        assert require_mcp_actor_scope().actor is first
        with trusted_mcp_actor_scope(second) as inner:
            assert require_mcp_actor_scope().actor is second
            assert inner is not outer
        assert not inner.active
        assert require_mcp_actor_scope() is outer
    assert not outer.active
    assert current_mcp_actor_scope() is None


@pytest.mark.parametrize("user_id", [0, -1, True])
def test_actor_requires_positive_non_bool_server_user_id(user_id: int) -> None:
    with pytest.raises(ValueError):
        actor(user_id)


@pytest.mark.parametrize("field", ["agent_id", "thread_id", "session_key", "source"])
def test_actor_requires_server_resolved_identity_fields(field: str) -> None:
    with pytest.raises(ValueError):
        replace(actor(), **{field: ""})


def test_missing_scope_and_model_receipt_fail_closed() -> None:
    with pytest.raises(OctopError) as error:
        require_mcp_actor_scope()
    assert error.value.code is ErrorCode.FORBIDDEN
    with trusted_mcp_actor_scope(actor()):
        with pytest.raises(OctopError) as error:
            export_personal_mcp_receipt()
        assert error.value.code is ErrorCode.FORBIDDEN
        assert error.value.details == {"reason": "personal_mcp_receipt_missing"}


def test_unscoped_nested_entry_masks_actor_without_closing_outer_lease() -> None:
    with trusted_mcp_actor_scope(actor()) as outer:
        with without_trusted_mcp_actor_scope():
            assert current_mcp_actor_scope() is None
            with pytest.raises(OctopError):
                require_mcp_actor_scope()
            assert outer.active
        assert require_mcp_actor_scope() is outer
        assert outer.active
    assert not outer.active


@pytest.mark.asyncio
async def test_simultaneous_actor_scopes_keep_independent_leases() -> None:
    ready = asyncio.Event()
    arrivals = 0

    async def observe(user_id: int) -> tuple[int, object]:
        nonlocal arrivals
        with trusted_mcp_actor_scope(actor(user_id)) as scope:
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=2)
            assert require_mcp_actor_scope() is scope
            return scope.actor.user_id, scope

    first, second = await asyncio.gather(observe(101), observe(202))
    assert first[0] == 101 and second[0] == 202
    assert first[1] is not second[1]
    assert current_mcp_actor_scope() is None


@pytest.mark.asyncio
async def test_scope_exit_invalidates_inherited_detached_task_access() -> None:
    released = asyncio.Event()

    async def detached() -> str:
        await released.wait()
        with pytest.raises(OctopError) as error:
            require_mcp_actor_scope()
        return error.value.details["reason"]

    with trusted_mcp_actor_scope(actor()):
        child = asyncio.create_task(detached())
    released.set()
    assert await asyncio.wait_for(child, timeout=2) == "personal_mcp_scope_inactive"
    assert child.done()


@pytest.mark.asyncio
async def test_cancellation_closes_scope_and_resets_context() -> None:
    entered = asyncio.Event()
    leases = []

    async def run() -> None:
        with trusted_mcp_actor_scope(actor()) as scope:
            leases.append(scope)
            entered.set()
            await asyncio.Event().wait()

    child = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), timeout=2)
    child.cancel()
    with pytest.raises(asyncio.CancelledError):
        await child
    assert not leases[0].active
    assert current_mcp_actor_scope() is None
