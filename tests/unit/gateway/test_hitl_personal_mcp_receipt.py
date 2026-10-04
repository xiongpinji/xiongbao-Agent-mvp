"""Process-local approvals bind only the server's actual personal catalog receipt."""

from __future__ import annotations

from dataclasses import replace

import pytest

from octop.infra.connectors.mcp_actor_scope import (
    PersonalMCPDescriptorReceipt,
    PersonalMCPReceipt,
    TrustedMCPActor,
    trusted_mcp_actor_scope,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.hitl.coordinator import (
    HitlChannelCoordinator,
    HitlStreamContext,
    pending_hitl_payload,
)
from octop.infra.gateway.hitl.store import HitlPendingStore


def receipt() -> PersonalMCPReceipt:
    actor = TrustedMCPActor(
        user_id=101,
        agent_id="qa-agent",
        thread_id="qa-thread",
        session_key="qa-session",
        source="dashboard",
        allowed_personal_servers=frozenset({"qa-personal"}),
        locale="zh",
    )
    return PersonalMCPReceipt(
        actor,
        (
            PersonalMCPDescriptorReceipt(
                "qa_lookup", "qa-personal", "synthetic-cfg", "synthetic-schema"
            ),
        ),
    )


def register(coordinator: HitlChannelCoordinator):
    return coordinator.register_from_request(
        {"action_requests": [{"name": "qa_lookup", "args": {"query": "synthetic"}}]},
        ctx=HitlStreamContext("qa-thread", "qa-agent", 101, "qa-session", "dashboard"),
    )


def test_pending_binds_actual_scope_receipt_without_exposing_it_to_clients() -> None:
    coordinator = HitlChannelCoordinator()
    original = receipt()
    with trusted_mcp_actor_scope(original.actor, receipt=original):
        pending = register(coordinator)
    assert pending.personal_mcp_receipt is original
    payload = pending_hitl_payload(
        coordinator.store,
        thread_id="qa-thread",
        agent_id="qa-agent",
        user_id=101,
    )
    assert set(payload or {}) == {"pending_id", "action_requests", "review_configs"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", 202),
        ("agent_id", "other-agent"),
        ("thread_id", "other-thread"),
        ("session_key", "other-session"),
        ("source", "other-channel"),
    ],
)
def test_pending_rejects_mismatched_server_receipt_identity(field: str, value: object) -> None:
    coordinator = HitlChannelCoordinator()
    original = receipt()
    mismatched = replace(original, actor=replace(original.actor, **{field: value}))
    with (
        trusted_mcp_actor_scope(mismatched.actor, receipt=mismatched),
        pytest.raises(OctopError) as error,
    ):
        register(coordinator)
    assert error.value.code is ErrorCode.FORBIDDEN
    assert not coordinator.store._records


def test_nested_pending_captures_current_model_phase_and_expires_old_record() -> None:
    coordinator = HitlChannelCoordinator()
    original = receipt()
    changed = replace(
        original,
        descriptors=(replace(original.descriptors[0], descriptor_fingerprint="next-schema"),),
    )
    with trusted_mcp_actor_scope(original.actor, receipt=original) as scope:
        first = register(coordinator)
        scope.record_model_receipt(changed)
        second = register(coordinator)
    assert first.status == "expired"
    assert first.personal_mcp_receipt is original
    assert second.personal_mcp_receipt is changed
    assert second.pending_id != first.pending_id


def test_ordinary_pending_and_new_process_have_no_personal_receipt() -> None:
    coordinator = HitlChannelCoordinator()
    pending = register(coordinator)
    assert pending.personal_mcp_receipt is None
    assert (
        HitlPendingStore().resolve_pending_for_thread(
            "qa-thread",
            agent_id="qa-agent",
            user_id=101,
        )
        is None
    )
