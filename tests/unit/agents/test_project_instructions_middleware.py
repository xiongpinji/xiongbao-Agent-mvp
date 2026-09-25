"""The project snapshot is model context, never a replacement for Agent policy."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import SystemMessage

from octop.infra.agents.middleware import project_instructions


def _request(base: str | None = None) -> ModelRequest:
    return ModelRequest(
        model=MagicMock(),
        messages=[],
        system_message=SystemMessage(content=base) if base is not None else None,
    )


def _snapshot(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    monkeypatch.setattr(
        project_instructions,
        "get_config",
        lambda: {"configurable": {project_instructions.CONFIG_KEY: value}},
    )


def test_snapshot_appends_after_base_without_mutating_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _snapshot(monkeypatch, "Cite the project brief.")
    original = _request("Agent base policy.")

    configured = project_instructions._configured_request(original)

    assert original.system_message.content == "Agent base policy."
    assert configured is not original
    assert configured.system_message.content.startswith("Agent base policy.\n\n")
    assert configured.system_message.content.endswith(
        "Cite the project brief.\n[End project instructions]"
    )
    assert "[Project instructions]" in configured.system_message.content


def test_snapshot_is_idempotent_and_empty_snapshot_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _snapshot(monkeypatch, "Use the current project spec.")
    first = project_instructions._configured_request(_request())
    second = project_instructions._configured_request(first)
    assert second.system_message.content == first.system_message.content
    assert second.system_message.content.count("[Project instructions]") == 1

    _snapshot(monkeypatch, "")
    assert project_instructions._configured_request(first) is first
    _snapshot(monkeypatch, {"instructions": "inbound impostor"})
    assert project_instructions._configured_request(first) is first


@pytest.mark.asyncio
async def test_sync_and_async_model_wrappers_receive_same_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _snapshot(monkeypatch, "Project-only rule.")
    middleware = project_instructions.ProjectInstructionsMiddleware()
    seen: list[str] = []

    def sync_handler(request: ModelRequest) -> object:
        seen.append(str(request.system_message.content))
        return object()

    async def async_handler(request: ModelRequest) -> object:
        seen.append(str(request.system_message.content))
        return object()

    middleware.wrap_model_call(_request("Base"), sync_handler)
    await middleware.awrap_model_call(_request("Base"), async_handler)
    assert seen[0] == seen[1]
    assert seen[0].startswith("Base\n\n[Project instructions]")
