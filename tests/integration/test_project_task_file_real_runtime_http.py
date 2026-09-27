"""030A PS-08 real-runtime HTTP integration for project task files.

This test intentionally does not use ``_install_runtime_fake``: the project
task is created through the public ASGI route, which calls the real B2
``AgentManager.create_project_task_file_runtime`` path and starts a real
harness filesystem runtime. The only fake is the local recording chat model,
so the test never calls a paid or external model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from octop.infra.agents.project_task_file_boundary import PROJECT_TASK_FILE_TOOLS
from octop.infra.projects import file_tasks
from octop.infra.projects.tasks import instructions_sha256
from tests.support.app import octop_client
from tests.support.auth import (
    auth_header,
    bootstrap_admin,
    create_agent,
    create_user,
    resolve_user_id,
    seed_openai_provider,
)
from tests.support.http import chat_ws_path, ws_connect

OWNER = "rt_owner"
MEMBER = "rt_member"
OUTSIDER = "rt_outsider"
PASSWORD = "TestPass12"
INSTRUCTIONS = "真实运行体文件任务：只在本人受控目录内读写。"


class _RecordingChatModel(BaseChatModel):
    """Local model stand-in; startup may bind it, but no network is used."""

    scripts: list[AIMessage] = []
    invocations: list[list[str]] = []
    tool_messages_seen: list[tuple[str, str, str]] = []
    _pending: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "octop-ps08-http-recording"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        self._pending = [
            str(
                (tool.get("function") or {}).get("name")
                if isinstance(tool, dict)
                else getattr(tool, "name", "")
            )
            for tool in tools
        ]
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.invocations.append(list(self._pending))
        self.tool_messages_seen.extend(
            (str(message.name), str(message.status), str(message.content))
            for message in messages
            if isinstance(message, ToolMessage)
        )
        reply = (
            self.scripts[min(len(self.invocations) - 1, len(self.scripts) - 1)]
            if self.scripts
            else AIMessage(content="recorded")
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])


@asynccontextmanager
async def _real_runtime_client(
    home: Path, monkeypatch: pytest.MonkeyPatch, *, scripts: list[AIMessage] | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, _RecordingChatModel]]:
    """ASGI client with real AgentManager/Harness runtime and a local model fake."""

    recording_model = _RecordingChatModel(scripts=scripts or [])
    monkeypatch.setattr(
        "harness_agent.llm.factory.ChatModelFactory.get_chat_model",
        lambda self, model_ref: recording_model,
    )
    async with octop_client(home, patch_llm=False) as (client, srv):
        yield client, srv, recording_model


async def _bootstrap_workspace_project(
    client: httpx.AsyncClient, srv: Any, home: Path
) -> dict[str, Any]:
    await bootstrap_admin(client, home)
    admin_auth = await auth_header(client)
    await seed_openai_provider(client, admin_auth)
    owner_auth = await create_user(client, admin_auth, username=OWNER, password=PASSWORD)
    member_auth = await create_user(client, admin_auth, username=MEMBER, password=PASSWORD)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER, password=PASSWORD)
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)

    project_resp = await client.post(
        "/api/projects",
        headers=owner_auth,
        json={"name": "真实运行体文件任务项目", "instructions": INSTRUCTIONS},
    )
    assert project_resp.status_code == 201, project_resp.text
    project_id = project_resp.json()["project_id"]
    srv.services.project_repo.add_member(project_id, member_uid, role="member")

    source_agent = await create_agent(client, owner_auth, name="real-runtime-shared-expert")
    with srv.services.db.transaction() as conn:
        conn.execute(
            "UPDATE agents SET is_shared = 1, default_model = ? WHERE agent_id = ?",
            ("openai/gpt-4o", source_agent),
        )

    return {
        "project_id": project_id,
        "source_agent": source_agent,
        "member_uid": member_uid,
        "member_auth": member_auth,
        "outsider_auth": outsider_auth,
    }


def _assert_internal_forbidden(response: httpx.Response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert response.json()["error"]["details"] == {"internal": True}


async def test_files_task_uses_real_runtime_http_workspace_and_survives_restart(
    tmp_octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_tasks, "PROJECT_TASK_FILES_MODE_ENABLED", True)

    async with _real_runtime_client(tmp_octop_home, monkeypatch) as (client, srv, _model):
        ctx = await _bootstrap_workspace_project(client, srv, tmp_octop_home)
        create_resp = await client.post(
            f"/api/projects/{ctx['project_id']}/tasks",
            headers=ctx["member_auth"],
            json={
                "agent_id": ctx["source_agent"],
                "expected_instructions_sha256": instructions_sha256(INSTRUCTIONS),
                "mode": "files",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        payload = create_resp.json()
        runtime_id = payload["chat_agent_id"]
        assert payload["mode"] == "files"
        assert payload["agent_id"] == ctx["source_agent"]
        assert payload["source_expert_id"] == ctx["source_agent"]
        assert runtime_id and runtime_id != ctx["source_agent"]

        root = srv.services.paths.project_task_file_runtime_dir(runtime_id)
        assert root.is_dir()
        row = srv.services.agent_repo.get(runtime_id)
        assert row is not None
        assert row.runtime_kind == "project_task_files"
        assert row.last_state == "running"
        assert (
            srv.services.project_task_repo.active_instructions_for_thread(
                thread_id=payload["thread_id"],
                owner_user_id=ctx["member_uid"],
                agent_id=runtime_id,
            )
            == INSTRUCTIONS
        )

        write_resp = await client.put(
            f"/api/agents/{runtime_id}/workspace/file",
            headers=ctx["member_auth"],
            params={"path": "notes/brief.txt", "from_workspace": "true"},
            json={"content": "private workspace bytes"},
        )
        assert write_resp.status_code == 200, write_resp.text
        assert (root / "notes" / "brief.txt").read_text(encoding="utf-8") == (
            "private workspace bytes"
        )

        read_resp = await client.get(
            f"/api/agents/{runtime_id}/workspace/file",
            headers=ctx["member_auth"],
            params={"path": "notes/brief.txt", "from_workspace": "true"},
        )
        assert read_resp.status_code == 200, read_resp.text
        assert read_resp.json()["content"] == "private workspace bytes"

        download_resp = await client.get(
            f"/api/agents/{runtime_id}/workspace/download",
            headers=ctx["member_auth"],
            params={"path": "notes/brief.txt", "from_workspace": "true"},
        )
        assert download_resp.status_code == 200, download_resp.text
        assert download_resp.content == b"private workspace bytes"

        outsider_resp = await client.get(
            f"/api/agents/{runtime_id}/workspace/file",
            headers=ctx["outsider_auth"],
            params={"path": "notes/brief.txt", "from_workspace": "true"},
        )
        _assert_internal_forbidden(outsider_resp)

    async with _real_runtime_client(tmp_octop_home, monkeypatch) as (client, srv, _model):
        member_auth = await auth_header(client, username=MEMBER, password=PASSWORD)
        outsider_auth = await auth_header(client, username=OUTSIDER, password=PASSWORD)

        read_after_restart = await client.get(
            f"/api/agents/{runtime_id}/workspace/file",
            headers=member_auth,
            params={"path": "notes/brief.txt", "from_workspace": "true"},
        )
        assert read_after_restart.status_code == 200, read_after_restart.text
        assert read_after_restart.json()["content"] == "private workspace bytes"

        outsider_after_restart = await client.get(
            f"/api/agents/{runtime_id}/workspace/download",
            headers=outsider_auth,
            params={"path": "notes/brief.txt", "from_workspace": "true"},
        )
        _assert_internal_forbidden(outsider_after_restart)

        owner_auth = await auth_header(client, username=OWNER, password=PASSWORD)
        removed = await client.delete(
            f"/api/projects/{ctx['project_id']}/members/{ctx['member_uid']}",
            headers=owner_auth,
        )
        assert removed.status_code == 204, removed.text

        detached_card = await client.get(
            f"/api/projects/{ctx['project_id']}/tasks/{payload['thread_id']}",
            headers=member_auth,
        )
        assert detached_card.status_code == 404, detached_card.text
        project_after_removal = await client.get(
            f"/api/projects/{ctx['project_id']}", headers=member_auth
        )
        assert project_after_removal.status_code == 404, project_after_removal.text
        assets_after_removal = await client.get(
            f"/api/projects/{ctx['project_id']}/assets", headers=member_auth
        )
        assert assets_after_removal.status_code == 404, assets_after_removal.text
        assert (
            srv.services.project_task_repo.active_instructions_for_thread(
                thread_id=payload["thread_id"],
                owner_user_id=ctx["member_uid"],
                agent_id=runtime_id,
            )
            is None
        )

        # Revoking project membership detaches project context, not the
        # creator's private file task or its managed workspace.
        private_after_removal = await client.get(
            f"/api/agents/{runtime_id}/workspace/file",
            headers=member_auth,
            params={"path": "notes/brief.txt", "from_workspace": "true"},
        )
        assert private_after_removal.status_code == 200, private_after_removal.text
        assert private_after_removal.json()["content"] == "private workspace bytes"


async def test_files_task_real_ws_turn_runs_file_tool_and_vetoes_forged_execute(
    tmp_octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bound-thread turn reaches the real graph; only the model is local."""
    monkeypatch.setattr(file_tasks, "PROJECT_TASK_FILES_MODE_ENABLED", True)
    scripts = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "model.txt", "content": "written by real tool"},
                    "id": "call_write",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "echo forbidden"},
                    "id": "call_execute",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done"),
    ]
    async with _real_runtime_client(tmp_octop_home, monkeypatch, scripts=scripts) as (
        client,
        srv,
        model,
    ):
        ctx = await _bootstrap_workspace_project(client, srv, tmp_octop_home)
        created = await client.post(
            f"/api/projects/{ctx['project_id']}/tasks",
            headers=ctx["member_auth"],
            json={
                "agent_id": ctx["source_agent"],
                "expected_instructions_sha256": instructions_sha256(INSTRUCTIONS),
                "mode": "files",
            },
        )
        assert created.status_code == 201, created.text
        runtime_id = created.json()["chat_agent_id"]
        thread_id = created.json()["thread_id"]
        app = client._octop_app  # type: ignore[attr-defined]
        async with ws_connect(app, chat_ws_path(runtime_id, ctx["member_auth"])) as ws:
            await ws.send_json(
                {
                    "type": "user_turn",
                    "text": "Write a private file",
                    "thread_id": thread_id,
                    "messages": [{"role": "user", "content": "Write a private file"}],
                }
            )
            frames = await ws.drain_turn(timeout=40)

        assert frames[-1]["type"] == "done", frames
        root = srv.services.paths.project_task_file_runtime_dir(runtime_id)
        assert (root / "model.txt").read_text(encoding="utf-8") == "written by real tool"
        assert len(model.invocations) >= 3
        assert all(set(names) == PROJECT_TASK_FILE_TOOLS for names in model.invocations)
        assert any(
            name == "execute" and status == "error" and "not available" in content
            for name, status, content in model.tool_messages_seen
        )
